"""The sizing orchestrator: enumerate, check, rank, size the BOS, build and validate the spec.

Pipeline of :func:`size_pv_system` (deterministic Python, no LLM):

1. Every catalogue inverter requested (or all) is screened against the utility service
   (single phase, rated AC voltage) -> an *inverter* rejection with rule ``GEN-004`` otherwise.
2. Every string configuration of every inverter is evaluated by :mod:`pvsld.sizing.strings`;
   any error rejects it with the rule that failed (all reasons are kept).
3. The surviving configurations are ranked (see :data:`OBJECTIVE`).
4. In rank order, the BOS is sized (:mod:`pvsld.sizing.bos`), the full specification is built and
   run through the rule pack (the gate). A candidate that the pack rejects moves to the rejected
   list with the rule that failed; the first ``max_candidates`` that pass are the result, so the
   selected specification always validates with 0 errors.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from typing import Any

from pvsld.catalogue import (
    AcBreaker,
    Cable,
    ComponentRegistry,
    DcBreaker,
    DcFuse,
    DcSpd,
    DcSwitch,
    UnknownComponentError,
)
from pvsld.catalogue import Inverter as CatalogueInverter
from pvsld.catalogue import PVModule as CatalogueModule
from pvsld.core import calc
from pvsld.core.model import SCHEMA_VERSION, Standards
from pvsld.core.severity import Severity
from pvsld.core.tables import NomTables, get_tables
from pvsld.core.validation import validate_pv_design
from pvsld.sizing import adapters, bos
from pvsld.sizing.models import (
    SUPPORTED_SYSTEMS,
    BosSpec,
    Candidate,
    DcOcpdChoice,
    DeviceChoice,
    Issue,
    Rejection,
    SizingInputError,
    SizingRequest,
    SizingResult,
    StringConfig,
    StringMetrics,
)
from pvsld.sizing.strings import EvalContext, Evaluation, enumerate_configs, evaluate_config

OBJECTIVE = (
    "Closest deliverable DC power to the target (array STC power minus the estimated current "
    "clipping; with no target the largest wins); then a DC/AC ratio inside 1.10-1.25 or the "
    "closest to it (owner decision 2026-10-09), less clipping, fewer warnings, the smaller "
    "inverter, lower cold string voltage relative to the inverter limit, fewer strings, "
    "inverter id."
)
DC_AC_PREFERRED = (1.10, 1.25)
"""DC/AC ratio the ranking prefers (owner decision 2026-10-09): enough DC to use the inverter,
little clipping."""
AC_BREAKER_VOLTAGE_V = 240.0
"""Rated voltage class of the inverter-output breaker (120/240 V class devices)."""
DC_SWITCH_ASSUMPTION = (
    "DCD-1 (desconectador integrado): tensión e intensidad tomadas de los límites de entrada del "
    "inversor; el catálogo no registra la capacidad del interruptor integrado."
)
STRING_BREAKER_PREFIX = "DCB-"
"""Id prefix of a string breaker of the DC protection box (the layout draws ``PVSLD_CB_DC``)."""
STRING_FUSE_PREFIX = "FUS-"
"""Id prefix of a string fuse-disconnector (gPV) of the box (``PVSLD_FUSE_DISC_DC``)."""
BOX_SWITCH_ID = "DCD-CD1"
"""The disconnect of the DC protection box, after the string breakers and the DC SPD."""
BOX_SWITCH_ASSUMPTION = (
    f"{BOX_SWITCH_ID} (seccionador de la caja de protecciones CD): el seccionador FV del catálogo "
    "con dos polos por cadena; sin uno adecuado, tensión y corriente de la protección de cadena."
)
ITM_ASSUMPTION = (
    "ITM-1 e ITM-P: interruptores de CA del catálogo con su capacidad interruptiva; sin uno "
    "adecuado, el kAIC se iguala a la corriente de falla disponible del servicio."
)
OPTIMIZER_ASSUMPTION = (
    "Optimizadores en todos los módulos: solo se usa el límite de potencia FV con optimizadores; "
    "los límites de tensión y corriente del optimizador no se modelan (STR-009 pendiente)."
)


def _lookup(registry: ComponentRegistry, component_id: str, kind: type[Any], label: str) -> Any:
    try:
        component = registry.get(component_id)
    except UnknownComponentError as error:
        raise SizingInputError(f"{label}: {error}") from error
    if not isinstance(component, kind):
        raise SizingInputError(
            f"{label}: {component_id} is a {component.component_type}, not a {kind.__name__}"
        )
    return component


# --- Screening of inverters against the service ------------------------------------------------


def _service_problem(inverter: CatalogueInverter, nominal_v: float) -> str | None:
    """Why the inverter cannot connect to a single-phase service of ``nominal_v``, if it cannot."""
    if inverter.grid != "single_phase":
        return (
            f"El inversor {inverter.component_id} es {inverter.grid}; esta versión dimensiona "
            f"servicios monofásicos (127 V fase-neutro o 220 V fase-fase)."
        )
    accepted = {*inverter.rated_ac_voltage_v}
    if inverter.max_ac_current_reference_voltage_v is not None:
        accepted.add(inverter.max_ac_current_reference_voltage_v)
    if nominal_v not in accepted:
        shown = ", ".join(f"{v:g}" for v in sorted(accepted))
        return (
            f"El inversor {inverter.component_id} opera a {shown} V; el servicio es de "
            f"{nominal_v:g} V."
        )
    return None


# --- Ranking ------------------------------------------------------------------------------------


def _target_w(request: SizingRequest, module: CatalogueModule) -> float | None:
    if request.target_dc_power_w is not None:
        return request.target_dc_power_w
    if request.module_count_max is not None:
        return request.module_count_max * module.pmax_w
    return None


def dc_ac_gap(ratio: float) -> float:
    """How far ``ratio`` is from the preferred DC/AC band (0 inside it)."""
    low, high = DC_AC_PREFERRED
    return max(low - ratio, ratio - high, 0.0)


def _rank_key(
    evaluation: Evaluation, inverter: CatalogueInverter, target_w: float | None
) -> tuple[float, float, float, int, float, float, int, str]:
    metrics = evaluation.metrics
    distance = (
        abs(metrics.deliverable_dc_w - target_w)
        if target_w is not None
        else -metrics.deliverable_dc_w
    )
    return (
        round(distance, 3),
        round(dc_ac_gap(metrics.dc_ac_ratio), 4),
        round(metrics.clipping_pct, 3),
        len(evaluation.warnings),
        inverter.rated_ac_power_w,
        round(metrics.voc_cold_string_v / inverter.max_input_voltage_v, 6),
        evaluation.config.n_strings,
        inverter.component_id,
    )


def explain_choice(candidate_metrics: StringMetrics, inverter: CatalogueInverter) -> str:
    """One or two plain Spanish sentences on why this inverter: its DC/AC ratio against the
    preferred band and, when it is outside, why no other inverter in the catalogue did better."""
    ratio = candidate_metrics.dc_ac_ratio
    kwp = candidate_metrics.p_dc_w / 1000
    kw = inverter.rated_ac_power_w / 1000
    low, high = DC_AC_PREFERRED
    name = f"{inverter.manufacturer} {inverter.component_id.split('-', 1)[-1]}"
    head = (
        f"Se eligió el inversor {name} ({kw:g} kW): con {kwp:.2f} kWp de módulos la relación "
        f"CD/CA es {ratio:.2f}"
    )
    if dc_ac_gap(ratio) == 0:
        return f"{head}, dentro del rango recomendado de {low:.2f} a {high:.2f}."
    if ratio < low:
        why = (
            f"Ningún inversor del catálogo queda en el rango recomendado de {low:.2f} a "
            f"{high:.2f} con esta cantidad de módulos: este es el más cercano y uno más grande "
            "quedaría aún más sobrado"
        )
    else:
        why = (
            f"Ningún inversor del catálogo queda en el rango recomendado de {low:.2f} a "
            f"{high:.2f} con esta cantidad de módulos: este es el más cercano y uno más chico "
            "recortaría más potencia"
        )
    clip = (
        f" Se estima un recorte de {candidate_metrics.clipping_pct:.1f} % en horas pico."
        if candidate_metrics.clipping_pct > 0
        else ""
    )
    return f"{head}. {why}.{clip}"


# --- BOS and specification ------------------------------------------------------------------


def _size_bos(
    *,
    request: SizingRequest,
    module: CatalogueModule,
    inverter: CatalogueInverter,
    evaluation: Evaluation,
    standards: Standards,
    tables: NomTables,
    breakers: Sequence[DcBreaker],
    fuses: Sequence[DcFuse] = (),
    cables: Sequence[Cable] = (),
    ac_breakers: Sequence[AcBreaker] = (),
    dc_switches: Sequence[DcSwitch] = (),
    main_breakers: Sequence[AcBreaker] = (),
) -> tuple[BosSpec | None, list[Issue]]:
    metrics, config = evaluation.metrics, evaluation.config
    site, routing = request.site, request.routing
    phases = SUPPORTED_SYSTEMS[request.utility.system]
    issues: list[Issue] = []

    needed, reason = bos.string_ocpd_required(
        module, metrics.isc_design_a, metrics.strings_per_mppt
    )
    if request.dc_ocpd == "always" and not needed:
        needed = True
        reason = "Una protección por rama por decisión de diseño (dc_ocpd: always)."
    window = {
        "module": module,
        "isc_a": metrics.isc_design_a,
        "strings_per_input": metrics.strings_per_mppt,
        "voc_cold_string_v": metrics.voc_cold_string_v,
        "required": needed,
    }
    dc_ocpd = None
    if request.dc_ocpd_device == "fuse":
        dc_ocpd = bos.select_string_fuse(**window, fuses=fuses, reason_es=reason)
        if dc_ocpd.required and dc_ocpd.device_id is None:
            if not request.dc_fuse_fallback:  # the user fixed the fuse: report, do not swap
                return None, [
                    Issue("OCP-002", Severity.ERROR, dc_ocpd.reason_es, subject=config.label)
                ]
            # No catalogue fuse fits this module: the box keeps a breaker, and says why.
            reason = f"{dc_ocpd.reason_es} Se usa un ITM de CD."
            dc_ocpd = None
    if dc_ocpd is None:
        dc_ocpd = bos.select_string_breaker(**window, breakers=breakers, reason_es=reason)
    if dc_ocpd.required and dc_ocpd.device_id is None:
        return None, [Issue("OCP-002", Severity.ERROR, dc_ocpd.reason_es, subject=config.label)]

    ac_ocpd = bos.ac_ocpd_rating_a(inverter.max_ac_output_current_a, tables)
    string_i_max = calc.ISC_SAFETY_FACTOR * metrics.isc_design_a
    vmp_stc_string = config.n_series * module.vmp_v
    dc_length = routing.dc_string_length_m
    ac_length = routing.ac_output_length_m
    ac_voltage = request.utility.nominal_voltage_v

    dc_choice, dc_issues = bos.size_conductor(
        circuit_id="C-S",
        kind="pv_source",
        i_max_a=string_i_max,
        ocpd_a=None,
        current_carrying=2 * config.n_strings,
        ambient_c=routing.dc_ambient_c if routing.dc_ambient_c is not None else site.t_amb_max_c,
        clearance_mm=routing.rooftop_clearance_mm,
        length_m=dc_length,
        drop_pct=lambda size: calc.voltage_drop_dc_pct(
            size, dc_length, module.imp_a, vmp_stc_string, tables
        ),
        drop_limit_pct=standards.vd_limits_pct.dc,
        drop_rule="VD-001",
        tables=tables,
        given_size=request.dc_conductor_size,
    )
    ac_choice, ac_issues = bos.size_conductor(
        circuit_id="C-INV",
        kind="inverter_output",
        i_max_a=inverter.max_ac_output_current_a,
        ocpd_a=ac_ocpd,
        current_carrying=_ac_current_carrying(phases),
        ambient_c=routing.ac_ambient_c if routing.ac_ambient_c is not None else site.t_amb_max_c,
        clearance_mm=None,
        length_m=ac_length,
        drop_pct=lambda size: calc.voltage_drop_ac_pct(
            size, ac_length, inverter.max_ac_output_current_a, ac_voltage, phases, tables
        ),
        drop_limit_pct=standards.vd_limits_pct.ac,
        drop_rule="VD-002",
        tables=tables,
        given_size=request.ac_conductor_size,
    )
    issues += dc_issues + ac_issues
    if dc_choice is None or ac_choice is None:
        return None, issues
    cable = bos.pv_cable(dc_choice.size, cables)
    dc_raceway, dc_raceway_issues = bos.size_raceway(
        circuit_id="C-S",
        conductors=2 * config.n_strings,
        conductor_area_mm2=calc.conductor_area_mm2(cable.outer_diameter_mm) if cable else None,
        egc_size=dc_choice.size,
        given_trade_size_mm=routing.dc_raceway_trade_size_mm,
        insulation=_pv_insulation(cable),
        tables=tables,
        cable=cable,
    )
    thhw = calc.insulated_area_mm2(ac_choice.size, bos.AC_INSULATION, None, tables)
    ac_raceway, ac_raceway_issues = bos.size_raceway(
        circuit_id="C-INV",
        conductors=phases + 1,  # the phases and the neutral
        conductor_area_mm2=thhw,
        egc_size=ac_choice.size,
        given_trade_size_mm=routing.ac_raceway_trade_size_mm,
        insulation=bos.AC_INSULATION,
        tables=tables,
    )
    raceway_issues = dc_raceway_issues + ac_raceway_issues
    issues += raceway_issues
    if any(i.severity == Severity.ERROR for i in raceway_issues):
        return None, issues
    devices, device_issues = _catalogue_devices(
        request,
        dc_ocpd,
        ac_ocpd,
        phases,
        string_i_max,
        metrics,
        config,
        ac_breakers,
        dc_switches,
        main_breakers,
    )
    issues += device_issues
    return (
        BosSpec(
            dc_ocpd=dc_ocpd,
            ac_ocpd_a=ac_ocpd,
            conductors=(dc_choice, ac_choice),
            todo=bos.TODO_STAGE_3_4B,
            raceways=(dc_raceway, ac_raceway),
            devices=devices,
        ),
        issues,
    )


def _catalogue_devices(
    request: SizingRequest,
    dc_ocpd: DcOcpdChoice,
    ac_ocpd_a: float,
    phases: int,
    string_i_max_a: float,
    metrics: StringMetrics,
    config: StringConfig,
    ac_breakers: Sequence[AcBreaker],
    dc_switches: Sequence[DcSwitch],
    main_breakers: Sequence[AcBreaker],
) -> tuple[tuple[DeviceChoice, ...], list[Issue]]:
    """ITM-1, ITM-P and the box disconnect from the catalogue (Stage 3.4b, owner 2026-10-09)."""
    ac_bos = request.template["ac_bos"]
    fault_ka = request.utility.available_fault_current_ka
    devices: list[DeviceChoice] = []
    issues: list[Issue] = []
    itm, found = bos.select_ac_breaker(
        position=ac_bos["point_of_connection"]["breaker"],
        rating_a=ac_ocpd_a,
        poles=phases,
        voltage_v=AC_BREAKER_VOLTAGE_V,
        fault_ka=fault_ka,
        breakers=ac_breakers,
    )
    issues += found
    devices += [itm] if itm else []
    for main in ac_bos.get("main_breakers", []):
        choice, found = bos.select_ac_breaker(
            position=main["id"],
            rating_a=main["rating_a"],
            poles=main["poles"],
            voltage_v=AC_BREAKER_VOLTAGE_V,
            fault_ka=fault_ka,
            breakers=main_breakers,
            exact=True,  # the service's main keeps its rating; the catalogue gives the device
        )
        issues += found
        devices += [choice] if choice else []
    if dc_ocpd.device_id is not None:
        switch, found = bos.select_box_switch(
            position=BOX_SWITCH_ID,
            n_strings=config.n_strings,
            voc_cold_string_v=metrics.voc_cold_string_v,
            string_i_max_a=string_i_max_a,
            switches=dc_switches,
        )
        issues += found
        devices += [switch] if switch else []
    return tuple(devices), issues


def _pv_insulation(cable: Cable | None) -> str:
    """The insulation text of the string conductors: the catalogue cable, or generic PV."""
    if cable is None:
        return "PV"
    return f"PV {cable.rated_voltage_v / 1000:g} kV"


def _ac_current_carrying(phases: int) -> int:
    """Phase conductors, plus the neutral when the circuit is line-to-neutral (1 phase)."""
    return phases + (1 if phases == 1 else 0)


_KWP = re.compile(r"\d+(?:\.\d+)?(\s*kWp)")
_KWAC = re.compile(r"\d+(?:\.\d+)?(\s*kWac)")


def _restate_size(node: Any, kwp: float, kwac: float) -> Any:
    """Rewrite the system size quoted in template text (``7.70 kWp``, ``{kwp}``, ``6.00 kWac``).

    A template written for another design (the sample says "7.70 kWp") must not leak its size into
    the sized specification: every string under ``project`` and ``title_block`` is regenerated from
    the designed power.
    """
    if isinstance(node, str):
        text = node.replace("{kwp}", f"{kwp:.2f}").replace("{kwac}", f"{kwac:.2f}")
        text = _KWP.sub(lambda m: f"{kwp:.2f}{m.group(1)}", text)
        return _KWAC.sub(lambda m: f"{kwac:.2f}{m.group(1)}", text)
    if isinstance(node, dict):
        return {key: _restate_size(value, kwp, kwac) for key, value in node.items()}
    if isinstance(node, list):
        return [_restate_size(value, kwp, kwac) for value in node]
    return node


def _build_spec(
    *,
    request: SizingRequest,
    module: CatalogueModule,
    inverter: CatalogueInverter,
    evaluation: Evaluation,
    bos_spec: BosSpec,
    standards: Standards,
    spds: Sequence[DcSpd] = (),
) -> dict[str, Any]:
    config, metrics = evaluation.config, evaluation.metrics
    routing, utility = request.routing, request.utility
    phases = SUPPORTED_SYSTEMS[utility.system]
    spec_module = adapters.to_spec_module(module)
    spec_inverter = adapters.to_spec_inverter(
        inverter,
        ac_voltage_v=utility.nominal_voltage_v,
        phases=phases,
        optimizers_on_all_modules=request.optimizers_on_all_modules,
        ocpd_max_a=bos_spec.ac_ocpd_a,
    )
    dc_conductor, ac_conductor = bos_spec.conductors
    dc_raceway, ac_raceway = bos_spec.raceways
    mppt_names = adapters.mppt_ids(inverter)

    spec = copy.deepcopy(request.template)
    spec["schema_version"] = SCHEMA_VERSION
    spec["layout"] = {**spec["layout"], "template": request.layout_template}
    spec["standards"] = standards.model_dump(mode="json")
    spec["modules"] = [spec_module.model_dump(mode="json")]
    spec["inverters"] = [spec_inverter.model_dump(mode="json")]
    spec["project"] = _restate_size(
        spec["project"], metrics.p_dc_w / 1000, inverter.rated_ac_power_w / 1000
    )
    spec["title_block"] = _restate_size(
        spec["title_block"], metrics.p_dc_w / 1000, inverter.rated_ac_power_w / 1000
    )

    strings = []
    for index in range(config.n_strings):
        strings.append(
            {
                "id": f"S{index + 1}",
                "module": adapters.MODULE_ID,
                "n_series": config.n_series,
                "inverter": adapters.INVERTER_ID,
                "mppt": mppt_names[index % len(mppt_names)],
                "tilt_deg": routing.tilt_deg,
                "azimuth_deg": routing.azimuth_deg,
            }
        )
    spec["strings"] = strings

    disconnects: list[dict[str, Any]] = []
    if spec_inverter.dc_switch_integrated:
        disconnects.append(
            {
                "id": "DCD-1",
                "integrated_in": adapters.INVERTER_ID,
                "poles": 2,
                "ue_v": spec_inverter.vdc_max_v,
                "ie_a": inverter.max_input_current_per_mppt_a,
            }
        )
    dc_ocpd = bos_spec.dc_ocpd
    if dc_ocpd.device_id is not None and dc_ocpd.rating_a is not None:
        prefix = STRING_FUSE_PREFIX if dc_ocpd.device == "fuse" else STRING_BREAKER_PREFIX
        disconnects += [
            {
                "id": f"{prefix}S{index + 1}",
                "integrated_in": None,
                "poles": dc_ocpd.poles,
                "ue_v": dc_ocpd.ue_v,
                "ie_a": dc_ocpd.rating_a,
                "model": dc_ocpd.device_id,
            }
            for index in range(config.n_strings)
        ]
        # The box opens every string ahead of the inverter (owner's reference box, 2026-10-08).
        disconnects.append(
            {
                "id": BOX_SWITCH_ID,
                "integrated_in": None,
                "poles": dc_ocpd.poles * config.n_strings,
                **(
                    {
                        "poles": switch.poles or dc_ocpd.poles * config.n_strings,
                        "ue_v": switch.voltage_v,
                        "ie_a": switch.rating_a,
                        "model": switch.device_id,
                    }
                    if (switch := bos_spec.device(BOX_SWITCH_ID)) is not None
                    else {"ue_v": dc_ocpd.ue_v, "ie_a": dc_ocpd.rating_a}
                ),
            }
        )
    dc_bos = spec.get("dc_bos") or {}
    box_spds = [dict(spd) for spd in dc_bos.get("spds", [])]
    if dc_ocpd.device_id is not None:
        # The box SPD comes from the catalogue, as in pvsld.sizing.dcbox (owner 2026-10-30).
        need = bos.spd_need(metrics.voc_cold_string_v, config.n_strings * metrics.isc_design_a)
        chosen_spd = bos.spd_for(need, spds)
        for entry in box_spds:
            if chosen_spd is not None and str(entry.get("id", "")).startswith("DPS-CD"):
                entry.update(
                    spd_type=chosen_spd.spd_type,
                    ucpv_v=chosen_spd.ucpv_v,
                    in_ka=chosen_spd.in_ka,
                    iscpv_a=chosen_spd.iscpv_ka * 1000,
                    model=chosen_spd.component_id,
                )
    spec["dc_bos"] = {"combiners": [], "spds": box_spds, "disconnects": disconnects}

    ac_bos = spec["ac_bos"]
    itm_id = ac_bos["point_of_connection"]["breaker"]
    existing = {o["id"]: o for o in ac_bos.get("ocpds", [])}
    itm = {
        **existing.get(itm_id, {}),
        "id": itm_id,
        "role": "I1",
        "poles": phases,
        "rating_a": bos_spec.ac_ocpd_a,
        "voltage_v": AC_BREAKER_VOLTAGE_V,
        "kaic_ka": utility.available_fault_current_ka,
        "backfed": True,
        "at": ac_bos["point_of_connection"]["panel"],
    }
    chosen = bos_spec.device(itm_id)
    if chosen is not None:
        itm.update(rating_a=chosen.rating_a, kaic_ka=chosen.interrupting_ka, model=chosen.device_id)
    for main in ac_bos.get("main_breakers", []):
        if (chosen := bos_spec.device(main["id"])) is not None:
            main.update(kaic_ka=chosen.interrupting_ka, model=chosen.device_id)
    ac_bos["ocpds"] = [o for o in ac_bos.get("ocpds", []) if o["id"] != itm_id] + [itm]

    dc_ambient = (
        routing.dc_ambient_c if routing.dc_ambient_c is not None else request.site.t_amb_max_c
    )
    ac_ambient = (
        routing.ac_ambient_c if routing.ac_ambient_c is not None else request.site.t_amb_max_c
    )
    circuits: list[dict[str, Any]] = []
    for index, string in enumerate(strings):
        circuit: dict[str, Any] = {
            "id": f"C-S{index + 1}",
            "kind": "pv_source",
            "from": string["id"],
            "to": f"{adapters.INVERTER_ID}.{string['mppt']}",
            "conductors": {
                "qty": 2,
                "size": dc_conductor.size,
                "material": "Cu",
                "insulation": dc_raceway.insulation,
                **(
                    {"outer_diameter_mm": dc_raceway.outer_diameter_mm}
                    if dc_raceway.outer_diameter_mm is not None
                    else {}
                ),
            },
            "egc": {"size": dc_conductor.size, "type": bos.EGC_TYPE},
            "raceway": (
                {
                    "type": dc_raceway.type,
                    "trade_size_mm": dc_raceway.trade_size_mm,
                    "rooftop_clearance_mm": routing.rooftop_clearance_mm,
                    "ccc_count": 2 * config.n_strings,
                }
                if index == 0
                else {"ref": "C-S1"}
            ),
            "length_m": routing.dc_string_length_m,
            "ambient_c": dc_ambient,
        }
        circuits.append(circuit)
    circuits.append(
        {
            "id": "C-INV",
            "kind": "inverter_output",
            "from": f"{adapters.INVERTER_ID}.ac",
            "to": itm_id,
            "conductors": {
                "qty": phases,
                "size": ac_conductor.size,
                "material": "Cu",
                "insulation": ac_raceway.insulation,
            },
            "neutral": {"size": ac_conductor.size},
            "egc": {"size": ac_conductor.size, "type": bos.EGC_TYPE},
            "raceway": {
                "type": ac_raceway.type,
                "trade_size_mm": ac_raceway.trade_size_mm,
                "ccc_count": _ac_current_carrying(phases),
            },
            "length_m": routing.ac_output_length_m,
            "ambient_c": ac_ambient,
        }
    )
    spec["circuits"] = circuits
    return spec


# --- Orchestrator -------------------------------------------------------------------------------


def _standards(template: dict[str, Any]) -> Standards:
    declared = template.get("standards")
    if declared is None:
        return Standards(nom_edition="NOM-001-SEDE-2012", rulepack="mx-gd-2026.10")
    return Standards.model_validate(declared)


def size_pv_system(request: SizingRequest, registry: ComponentRegistry) -> SizingResult:
    """Size a PV system from the catalogue: ranked candidates, rejections and the selected spec.

    Raises:
        SizingInputError: the module or an inverter id is unknown or of the wrong type, or the
            module cannot be expressed in schema 0.1.0.
    """
    module: CatalogueModule = _lookup(registry, request.module, CatalogueModule, "module")
    try:
        spec_module = adapters.to_spec_module(module)
    except adapters.UnsupportedComponentError as error:
        raise SizingInputError(str(error)) from error
    inverter_ids = (
        [i.component_id for i in registry.inverters()]
        if request.inverters == "auto"
        else list(request.inverters)
    )
    if not inverter_ids:
        raise SizingInputError("the catalogue has no inverters to evaluate")
    inverters: list[CatalogueInverter] = [
        _lookup(registry, name, CatalogueInverter, "inverter") for name in inverter_ids
    ]
    breakers: list[DcBreaker] = (
        registry.dc_breakers()
        if request.dc_breakers == "auto"
        else [_lookup(registry, name, DcBreaker, "dc_breaker") for name in request.dc_breakers]
    )
    ac_breakers: list[AcBreaker] = (
        registry.ac_breakers()
        if request.ac_breakers == "auto"
        else [_lookup(registry, name, AcBreaker, "ac_breaker") for name in request.ac_breakers]
    )
    main_breakers: list[AcBreaker] = (
        [_lookup(registry, request.main_breaker, AcBreaker, "ac_breaker")]
        if request.main_breaker is not None
        else registry.ac_breakers()
    )
    dc_switches: list[DcSwitch] = (
        registry.dc_switches()
        if request.dc_switches == "auto"
        else [_lookup(registry, name, DcSwitch, "dc_switch") for name in request.dc_switches]
    )
    cables: list[Cable] = [
        c
        for c in registry.cables()
        if request.dc_cable == "auto" or c.family_id == request.dc_cable
    ]
    if request.dc_cable != "auto" and not cables:
        raise SizingInputError(f"no cable family {request.dc_cable!r} in the catalogue")
    fuses: list[DcFuse] = (
        registry.dc_fuses()
        if request.dc_fuses == "auto"
        else [_lookup(registry, name, DcFuse, "dc_fuse") for name in request.dc_fuses]
    )

    site = request.site
    standards = _standards(request.template)
    tables = get_tables(standards.nom_edition)
    target_w = _target_w(request, module)

    rejected: list[Rejection] = []
    feasible: list[tuple[Evaluation, CatalogueInverter]] = []
    for inverter in inverters:
        problem = _service_problem(inverter, request.utility.nominal_voltage_v)
        if problem is not None:
            rejected.append(
                Rejection(
                    StringConfig(inverter.component_id, 0, 0),
                    (Issue("GEN-004", Severity.ERROR, problem, subject=inverter.component_id),),
                    stage="inverter",
                )
            )
            continue
        ctx = EvalContext(
            module=module,
            spec_module=spec_module,
            inverter=inverter,
            site=site,
            voc_method=standards.voc_method,
            cell_temp_rise_c=request.cell_temp_rise_c,
            optimizers_on_all_modules=request.optimizers_on_all_modules,
            policy=request.dc_ac_policy,
            module_count_min=request.module_count_min,
            module_count_max=request.module_count_max,
            n_strings=request.n_strings,
            n_series=request.n_series,
        )
        for n_strings, n_series in enumerate_configs(ctx):
            evaluation = evaluate_config(ctx, n_strings, n_series)
            if evaluation.feasible:
                feasible.append((evaluation, inverter))
            else:
                rejected.append(
                    Rejection(evaluation.config, evaluation.issues, "strings", evaluation.metrics)
                )

    feasible.sort(key=lambda item: _rank_key(item[0], item[1], target_w))
    candidates: list[Candidate] = []
    for evaluation, inverter in feasible:
        if len(candidates) >= request.max_candidates:
            break
        bos_spec, bos_issues = _size_bos(
            request=request,
            module=module,
            inverter=inverter,
            evaluation=evaluation,
            standards=standards,
            tables=tables,
            breakers=breakers,
            fuses=fuses,
            cables=cables,
            ac_breakers=ac_breakers,
            dc_switches=dc_switches,
            main_breakers=main_breakers,
        )
        if bos_spec is None:
            rejected.append(
                Rejection(
                    evaluation.config,
                    (*evaluation.issues, *bos_issues),
                    "bos",
                    evaluation.metrics,
                )
            )
            continue
        spec = _build_spec(
            request=request,
            module=module,
            inverter=inverter,
            evaluation=evaluation,
            bos_spec=bos_spec,
            standards=standards,
            spds=registry.dc_spds(),
        )
        report = validate_pv_design(spec, dc_ac_policy=request.dc_ac_policy)
        if not report.ok:
            rejected.append(
                Rejection(
                    evaluation.config,
                    (
                        *evaluation.issues,
                        *(Issue.from_finding(f) for f in report.errors),
                    ),
                    "rule_pack",
                    evaluation.metrics,
                )
            )
            continue
        candidates.append(
            Candidate(
                rank=len(candidates) + 1,
                config=evaluation.config,
                metrics=evaluation.metrics,
                issues=(*evaluation.issues, *bos_issues),
                bos=bos_spec,
                findings=report.findings,
                spec=spec,
                explanation_es=explain_choice(evaluation.metrics, inverter),
            )
        )

    assumptions = [
        *adapters.ASSUMPTIONS,
        ITM_ASSUMPTION,
        DC_SWITCH_ASSUMPTION,
        BOX_SWITCH_ASSUMPTION,
    ]
    if request.optimizers_on_all_modules:
        assumptions.append(OPTIMIZER_ASSUMPTION)
    return SizingResult(
        module_id=module.component_id,
        objective=OBJECTIVE,
        target_dc_power_w=target_w,
        candidates=tuple(candidates),
        rejected=tuple(rejected),
        assumptions=tuple(assumptions),
        inverters_evaluated=tuple(inverter_ids),
    )


__all__ = ["OBJECTIVE", "size_pv_system"]
