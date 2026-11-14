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
from pvsld.core.model import SCHEMA_VERSION, Module, Standards
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
        # 1 pole on a grounded array, 2 on an ungrounded one (owner request 2026-11-06)
        "poles": bos.string_ocpd_poles((request.template.get("grounding") or {}).get("dc_system")),
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
                "poles": bos.STRING_SWITCH_POLES * config.n_strings,
                **(
                    {
                        "poles": switch.poles or bos.STRING_SWITCH_POLES * config.n_strings,
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
    if request.microinverters is not None:
        return _size_micro(request, registry, module, spec_module)
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
    multi = _multi_candidates(
        request, registry, module, spec_module, inverters, standards, target_w, candidates, rejected
    )
    if multi is not None and (multi or request.inverter_count or request.inverter_set):
        candidates = multi
        assumptions.append(MULTI_ASSUMPTION)
    elif multi is not None:
        # the automatic search found no valid design with several inverters: keep one
        assumptions.append(MULTI_REJECTED_ASSUMPTION)
    return SizingResult(
        module_id=module.component_id,
        objective=OBJECTIVE,
        target_dc_power_w=target_w,
        candidates=tuple(candidates),
        rejected=tuple(rejected),
        assumptions=tuple(assumptions),
        inverters_evaluated=tuple(inverter_ids),
    )


MULTI_ASSUMPTION = (
    "Varios inversores: sus salidas se unen en la caja de unión CJ-CA1 y llegan a un solo ITM "
    "compartido de 1.25 × la suma de sus corrientes; cada tramo de inversor queda protegido por "
    "ese ITM (decisión del propietario, 2026-11-13). Tramo de cada inversor a la caja: 5 m por "
    "omisión."
)


def _fixed_bos(request: SizingRequest) -> bool:
    """Whether the request fixes a protection, switch, breaker or cable of the design."""
    return (
        any(
            choice != "auto"
            for choice in (
                request.dc_fuses,
                request.dc_breakers,
                request.dc_switches,
                request.ac_breakers,
                request.dc_cable,
            )
        )
        or request.dc_ocpd != "always"
        or request.main_breaker is not None
        or not request.dc_fuse_fallback
    )


MULTI_REJECTED_ASSUMPTION = (
    "Con varios inversores se habría llegado más cerca de la potencia pedida, pero ninguna "
    "opción pasó las revisiones (ver «rejected»); se propone un solo inversor."
)


def _multi_candidates(
    request: SizingRequest,
    registry: ComponentRegistry,
    module: CatalogueModule,
    spec_module: Module,
    inverters: list[CatalogueInverter],
    standards: Standards,
    target_w: float | None,
    singles: list[Candidate],
    rejected: list[Rejection],
) -> list[Any] | None:
    """Designs with several string inverters, or None to keep the single-inverter result.

    ``inverter_set`` gives the units (mixed models); ``inverter_count`` > 1 fixes how many of
    each model; otherwise, with a target, the fewest inverters whose best design comes within one
    module of the best reachable one (owner 2026-11-13).
    """
    from pvsld.sizing import multi_sizing as ms

    usable = [
        i for i in inverters if _service_problem(i, request.utility.nominal_voltage_v) is None
    ]

    def best_units(count: int) -> list[tuple[CatalogueInverter, Evaluation]]:
        """Each model's feasible unit configurations on 1/count of the request, best first."""
        part_target = target_w / count if target_w is not None else None
        found = [
            (inverter, evaluation)
            for inverter in usable
            for evaluation in ms.unit_evaluations(
                request, module, spec_module, inverter, standards, 1 / count
            )
        ]
        found.sort(key=lambda item: _rank_key(item[1], item[0], part_target))
        return found

    fixed_note = (
        (
            Issue(
                "GEN-004",
                Severity.WARNING,
                "Con varios inversores el motor elige del catálogo los equipos de la caja de CD, "
                "el ITM compartido y los cables; los que se fijaron todavía no se aplican.",
                subject="multi_inverter",
            ),
        )
        if _fixed_bos(request)
        else ()
    )

    def build(units_list: list[list[tuple[CatalogueInverter, Evaluation]]]) -> list[Any]:
        built: list[Any] = []
        for units in units_list:
            if len(built) >= request.max_candidates:
                break
            spec, issues = ms.build_multi_spec(request, module, units, registry)
            config = units[0][1].config
            if spec is None:
                rejected.append(Rejection(config, tuple(issues), "bos", units[0][1].metrics))
                continue
            report = validate_pv_design(spec, dc_ac_policy=request.dc_ac_policy)
            if not report.ok:
                rejected.append(
                    Rejection(
                        config,
                        tuple(Issue.from_finding(f) for f in report.errors),
                        "rule_pack",
                        units[0][1].metrics,
                    )
                )
                continue
            breaker = spec["ac_bos"]["point_of_connection"]["breaker"]
            ocpds = {o["id"]: o for o in spec["ac_bos"].get("ocpds", [])}
            mains = {m["id"]: m for m in spec["ac_bos"].get("main_breakers", [])}
            shared = next(
                (o for o in ocpds.values() if o.get("role") == "I1"), mains.get(breaker, {})
            )
            built.append(
                ms.MultiCandidate(
                    rank=len(built) + 1,
                    units=tuple(
                        ms.InverterUnit(
                            inverter.component_id,
                            evaluation.config.n_strings,
                            evaluation.config.n_series,
                            evaluation.metrics.p_dc_w,
                            inverter.rated_ac_power_w,
                        )
                        for inverter, evaluation in units
                    ),
                    breaker_a=float(shared.get("rating_a") or 0),
                    issues=tuple(i for _inv, e in units for i in e.issues)
                    + tuple(issues)
                    + fixed_note,
                    findings=report.findings,
                    spec=spec,
                    explanation_es=ms.explain_multi(
                        units, singles[0].metrics.p_dc_w if singles else None
                    ),
                )
            )
        return built

    if request.inverter_set:
        chosen = [_lookup(registry, i, CatalogueInverter, "inverter") for i in request.inverter_set]
        total_ac = sum(i.rated_ac_power_w for i in chosen)
        units: list[tuple[CatalogueInverter, Evaluation]] = []
        for inverter in chosen:
            part = inverter.rated_ac_power_w / total_ac
            options = ms.unit_evaluations(request, module, spec_module, inverter, standards, part)
            if not options:
                return []
            part_target = target_w * part if target_w is not None else None
            options.sort(key=lambda e, i=inverter: _rank_key(e, i, part_target))
            units.append((inverter, options[0]))
        return build([units])

    count = request.inverter_count
    if count == 1:
        return None
    if count is None:
        # Fixed protections or cables (professional mode) are not carried into the designs with
        # several inverters yet: the automatic search stays with one inverter then.
        if target_w is None or _fixed_bos(request):
            return None
        best: dict[int, float] = {}
        if singles:
            best[1] = abs(singles[0].metrics.deliverable_dc_w - target_w)
        for n in range(2, ms.MAX_AUTO_INVERTERS + 1):
            found = best_units(n)
            if found:
                best[n] = abs(n * found[0][1].metrics.deliverable_dc_w - target_w)
        if not best:
            return None
        closest = min(best.values())
        count = min(n for n, gap in best.items() if gap <= closest + module.pmax_w)
        if count == 1:
            return None
    found = best_units(count)
    seen: set[tuple[str, int, int]] = set()
    options: list[list[tuple[CatalogueInverter, Evaluation]]] = []
    for inverter, evaluation in found:
        key = (inverter.component_id, evaluation.config.n_strings, evaluation.config.n_series)
        if key not in seen:
            seen.add(key)
            options.append([(inverter, evaluation)] * count)
    return build(options)


def _size_micro(
    request: SizingRequest,
    registry: ComponentRegistry,
    module: CatalogueModule,
    spec_module: Any,
) -> SizingResult:
    from pvsld.sizing.micro_sizing import (
        MICRO_ASSUMPTIONS,
        OBJECTIVE_MICRO,
        module_count,
        size_micro_system,
    )

    standards = _standards(request.template)
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
    candidates, rejected, evaluated = size_micro_system(
        request,
        registry,
        module=module,
        spec_module=spec_module,
        standards=standards,
        tables=get_tables(standards.nom_edition),
        ac_breakers=ac_breakers,
        main_breakers=main_breakers,
    )
    return SizingResult(
        module_id=module.component_id,
        objective=OBJECTIVE_MICRO,
        target_dc_power_w=module_count(request, module) * module.pmax_w,
        candidates=tuple(candidates),
        rejected=tuple(rejected),
        assumptions=(*adapters.ASSUMPTIONS[1:], ITM_ASSUMPTION, *MICRO_ASSUMPTIONS),
        inverters_evaluated=tuple(evaluated),
    )


__all__ = ["OBJECTIVE", "size_pv_system"]
