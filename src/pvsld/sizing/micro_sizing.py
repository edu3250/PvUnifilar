"""Sizing of a microinverter design (Stage 3.10.1, owner decisions of 2026-11-07).

:func:`size_pv_system` calls :func:`size_micro_system` when the request names microinverters
(``microinverters``: ids or ``"auto"``); the quick mode does so only when the user asks for them.
The steps are deterministic and shared by the plugin, the editor and the editor's chatbot:

1. **Modules.** The count asked for (``module_count_max``, else ``module_count_min``), or the count
   closest to ``target_dc_power_w``.
2. **Model.** Each microinverter is screened (service voltage MIC-010, module on each input
   MIC-001/002); the survivors rank by the module power inside the recommended range, then the
   distance to it, then the smaller rated power, then the id.
3. **Units.** ``ceil(modules / inputs)``, inputs filled in order; free inputs on the last unit are
   a warning (MIC-015).
4. **Branches.** The fewest branches (the trunk size that takes the most units), units spread
   evenly, each branch on the smallest trunk size that takes its units (MIC-011..013).
5. **Protection and conductors.** Each branch breaker is the catalogue breaker of the next
   standard rating >= 1.25 x the sum of its units' currents; with ``ac_combiner`` the breakers go
   in combiner ``CCA-1`` and its main breaker ``ITM-1`` (1.25 x every unit) feeds the load centre;
   the home run of each branch (and the combiner output) is the smallest conductor that carries
   the current with the drop along the trunk inside the AC limit.
6. **Gate.** The specification (schema 0.2.0) is validated by the rule pack; a model whose design
   fails moves to the rejections with the rule.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pvsld.catalogue import AcBreaker, Microinverter
from pvsld.catalogue import PVModule as CatalogueModule
from pvsld.core import calc
from pvsld.core import micro as micro_rules
from pvsld.core.model import MICRO_SCHEMA_VERSION, Module, Standards
from pvsld.core.rules import Finding
from pvsld.core.severity import Severity
from pvsld.core.tables import NomTables
from pvsld.core.validation import validate_pv_design
from pvsld.sizing import adapters, bos
from pvsld.sizing.micro import limits_of
from pvsld.sizing.models import (
    SUPPORTED_SYSTEMS,
    Issue,
    Rejection,
    SizingInputError,
    SizingRequest,
    StringConfig,
)

INVERTER_ID = "INVM1"
COMBINER_ID = "CCA-1"
COMBINER_MAIN_ID = "ITM-1"
SPACING_PER_INPUT_M = 1.2
"""Default trunk length between two units, per module connected to each (same as the editor)."""
DEFAULT_TRUNK_SIZES = ("12 AWG", "10 AWG")
AC_BREAKER_VOLTAGE_V = 240.0
OBJECTIVE_MICRO = (
    "Microinverter whose recommended module power holds the module, else the closest; then the "
    "smaller rated power. Fewest AC branches, units spread evenly, smallest trunk per branch."
)
MICRO_ASSUMPTIONS = (
    f"Separación entre microinversores sobre el troncal: {SPACING_PER_INPUT_M:g} m por módulo "
    "conectado, salvo que se indique otra.",
    "Tramo de cada rama al tablero (y de la caja combinadora al ITM general): la longitud de la "
    "salida de CA (ac_output_length_m).",
)


@dataclass(frozen=True)
class MicroBranchChoice:
    """One AC branch of a sized microinverter design."""

    branch_id: str
    units: int
    trunk_size: str
    current_a: float
    ocpd_id: str
    ocpd_a: float
    breaker_model: str | None
    home_run_size: str
    drop_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch_id,
            "units": self.units,
            "trunk_size": self.trunk_size,
            "current_a": round(self.current_a, 2),
            "ocpd": self.ocpd_id,
            "ocpd_a": self.ocpd_a,
            "breaker_model": self.breaker_model,
            "home_run_size": self.home_run_size,
            "drop_pct": round(self.drop_pct, 2),
        }


@dataclass(frozen=True)
class MicroCandidate:
    """A microinverter design that passed the rule pack."""

    rank: int
    micro_id: str
    modules: int
    units: int
    free_inputs: int
    branches: tuple[MicroBranchChoice, ...]
    combiner: bool
    issues: tuple[Issue, ...]
    findings: tuple[Finding, ...]
    spec: dict[str, Any] = field(repr=False)
    explanation_es: str = ""
    p_dc_w: float = 0.0
    p_ac_w: float = 0.0

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def label(self) -> str:
        return f"{self.units} micro(s), {len(self.branches)} rama(s)"

    def to_dict(self, *, include_spec: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "rank": self.rank,
            "kind": "microinverter",
            "inverter": self.micro_id,
            "explanation_es": self.explanation_es,
            "modules": self.modules,
            "units": self.units,
            "free_inputs": self.free_inputs,
            "p_dc_w": self.p_dc_w,
            "p_ac_w": self.p_ac_w,
            "branches": [b.to_dict() for b in self.branches],
            "ac_combiner": self.combiner,
            "issues": [i.to_dict() for i in self.issues],
            "rule_pack": {
                "errors": 0,
                "warnings": sum(1 for f in self.findings if f.severity is Severity.WARNING),
                "findings": [f.to_dict() for f in self.findings],
            },
        }
        if include_spec:
            data["spec"] = json.loads(json.dumps(self.spec, default=str))
        return data


def module_count(request: SizingRequest, module: CatalogueModule) -> int:
    """The modules of the design: the count asked for, else the closest to the target power."""
    if request.module_count_max is not None:
        return request.module_count_max
    if request.module_count_min is not None:
        return request.module_count_min
    if request.target_dc_power_w is not None:
        return max(1, round(request.target_dc_power_w / module.pmax_w))
    raise SizingInputError(
        "a microinverter design needs the number of modules (module_count_min/max) or "
        "target_dc_power_w"
    )


def rank_key(micro: Microinverter, pmax_w: float) -> tuple[int, float, float, str]:
    """Recommended range holding the module first, then the closest, the smaller, the id."""
    low, high = micro.recommended_module_power_wp
    gap = 0.0 if low <= pmax_w <= high else min(abs(pmax_w - low), abs(pmax_w - high))
    return (0 if gap == 0 else 1, gap, micro.rated_ac_power_w, micro.component_id)


def split_branches(units: int, capacity: int) -> list[int]:
    """The fewest branches of at most ``capacity`` units, spread evenly (larger ones first)."""
    count = math.ceil(units / capacity)
    base, extra = divmod(units, count)
    return [base + 1] * extra + [base] * (count - extra)


def _awg_rank(size: str) -> float:
    gauge = size.split(" ", 1)[0]
    return 100 + int(gauge.split("/", 1)[0]) if "/" in gauge else -float(gauge)


def _connection_voltage(request: SizingRequest) -> float:
    utility = request.utility
    # A microinverter is wired between two phases on a 2F-3H service, phase to neutral on 1F-2H.
    return float(utility.nominal_voltage_v)


def _restate(node: Any, kwp: float, kwac: float) -> Any:
    from pvsld.sizing.engine import _restate_size

    return _restate_size(node, kwp, kwac)


def size_micro_system(
    request: SizingRequest,
    registry: Any,
    *,
    module: CatalogueModule,
    spec_module: Module,
    standards: Standards,
    tables: NomTables,
    ac_breakers: Sequence[AcBreaker],
    main_breakers: Sequence[AcBreaker],
) -> tuple[list[MicroCandidate], list[Rejection], list[str]]:
    """Candidates (best first), rejections and the ids evaluated (see the module docstring)."""
    from pvsld.sizing.engine import _lookup

    requested = request.microinverters
    micros: list[Microinverter] = (
        registry.microinverters()
        if requested == "auto"
        else [_lookup(registry, name, Microinverter, "microinverter") for name in requested or []]
    )
    if not micros:
        raise SizingInputError("the catalogue has no microinverters to evaluate")
    site = request.site
    n_modules = module_count(request, module)
    voltage = _connection_voltage(request)
    rejected: list[Rejection] = []
    feasible: list[Microinverter] = []
    for micro in micros:
        limits = limits_of(micro)
        problems = [
            f
            for f in (
                micro_rules.service_voltage_finding(limits, voltage),
                *micro_rules.module_input_findings(
                    limits,
                    spec_module,
                    t_min_c=site.t_min_c,
                    t_amb_max_c=site.t_amb_max_c,
                    voc_method=standards.voc_method,
                    cell_temp_rise_c=request.cell_temp_rise_c,
                ),
            )
            if f is not None and f.severity is Severity.ERROR
        ]
        if problems:
            rejected.append(
                Rejection(
                    StringConfig(micro.component_id, 0, 0),
                    tuple(
                        Issue(f.rule_id, f.severity, f.message_es, subject=micro.component_id)
                        for f in problems
                    ),
                    stage="inverter",
                )
            )
            continue
        feasible.append(micro)
    feasible.sort(key=lambda m: rank_key(m, module.pmax_w))

    candidates: list[MicroCandidate] = []
    for micro in feasible:
        if len(candidates) >= request.max_candidates:
            break
        built = _design(
            request,
            micro,
            module,
            spec_module,
            n_modules,
            standards,
            tables,
            ac_breakers,
            main_breakers,
        )
        if isinstance(built, Rejection):
            rejected.append(built)
            continue
        spec, branches, issues, units, free = built
        report = validate_pv_design(spec, dc_ac_policy=request.dc_ac_policy)
        if not report.ok:
            rejected.append(
                Rejection(
                    StringConfig(micro.component_id, units, 1),
                    (*issues, *(Issue.from_finding(f) for f in report.errors)),
                    "rule_pack",
                )
            )
            continue
        candidates.append(
            MicroCandidate(
                rank=len(candidates) + 1,
                micro_id=micro.component_id,
                modules=n_modules,
                units=units,
                free_inputs=free,
                branches=tuple(branches),
                combiner=request.ac_combiner,
                issues=tuple(issues),
                findings=report.findings,
                spec=spec,
                explanation_es=_explain(micro, module, n_modules, units, branches),
                p_dc_w=n_modules * module.pmax_w,
                p_ac_w=units * micro.rated_ac_power_w,
            )
        )
    return candidates, rejected, [m.component_id for m in micros]


def _explain(
    micro: Microinverter,
    module: CatalogueModule,
    modules: int,
    units: int,
    branches: Sequence[MicroBranchChoice],
) -> str:
    low, high = micro.recommended_module_power_wp
    watts, window = f"{module.pmax_w:g} W", f"{low:g} a {high:g} W"
    fit = (
        f"el módulo de {watts} está dentro de su rango recomendado ({window})"
        if low <= module.pmax_w <= high
        else f"es el más cercano al módulo de {watts} (recomendado {window})"
    )
    layout = ", ".join(f"{b.branch_id}: {b.units} en {b.trunk_size}" for b in branches)
    return (
        f"Microinversor {adapters.display_model(micro)}: {fit}. {modules} módulos en {units} "
        f"microinversores de {micro.mppt_count} entradas, {len(branches)} rama(s) de CA ({layout})."
    )


def _design(
    request: SizingRequest,
    micro: Microinverter,
    module: CatalogueModule,
    spec_module: Module,
    n_modules: int,
    standards: Standards,
    tables: NomTables,
    ac_breakers: Sequence[AcBreaker],
    main_breakers: Sequence[AcBreaker],
) -> tuple[dict[str, Any], list[MicroBranchChoice], list[Issue], int, int] | Rejection:
    limits = limits_of(micro)
    inputs = micro.mppt_count
    units = math.ceil(n_modules / inputs)
    free = units * inputs - n_modules
    issues: list[Issue] = []
    if free:
        issues.append(
            Issue(
                "MIC-015",
                Severity.WARNING,
                f"El último microinversor queda con {free} entrada(s) libre(s): {n_modules} "
                f"módulos en {units} microinversores de {inputs} entradas.",
                subject=micro.component_id,
            )
        )
    sizes = sorted(limits.trunk_max_ocpd_a or DEFAULT_TRUNK_SIZES, key=_awg_rank)
    capacity = {size: micro_rules.branch_limit(limits, size, tables).units for size in sizes}
    best = max(capacity.values(), default=0)
    if best == 0:
        return Rejection(
            StringConfig(micro.component_id, units, 1),
            (
                Issue(
                    "MIC-011",
                    Severity.ERROR,
                    f"Ni un solo {micro.component_id} cabe en una rama con los calibres de troncal "
                    f"del fabricante ({', '.join(sizes)}).",
                    subject=micro.component_id,
                ),
            ),
            "bos",
        )
    counts = split_branches(units, best)

    utility, routing = request.utility, request.routing
    phases = SUPPORTED_SYSTEMS[utility.system]
    fault = utility.available_fault_current_ka
    spacing = routing.micro_spacing_m or round(SPACING_PER_INPUT_M * inputs, 2)
    length = routing.ac_output_length_m
    ambient = routing.ac_ambient_c if routing.ac_ambient_c is not None else request.site.t_amb_max_c
    current_unit = micro.max_ac_output_current_a
    voltage = float(utility.nominal_voltage_v)

    spec = copy.deepcopy(request.template)
    spec["schema_version"] = MICRO_SCHEMA_VERSION
    spec["layout"] = {**spec["layout"], "template": request.layout_template}
    spec["standards"] = standards.model_dump(mode="json")
    spec["modules"] = [spec_module.model_dump(mode="json")]
    spec["inverters"] = [
        adapters.to_spec_microinverter(
            micro, inverter_id=INVERTER_ID, qty=units, ac_voltage_v=voltage, phases=phases
        ).model_dump(mode="json")
    ]
    kwp, kwac = n_modules * module.pmax_w / 1000, units * micro.rated_ac_power_w / 1000
    spec["project"] = _restate(spec["project"], kwp, kwac)
    spec["title_block"] = _restate(spec["title_block"], kwp, kwac)
    spec["strings"] = []
    spec["dc_bos"] = {"combiners": [], "disconnects": [], "spds": []}

    ac_bos = spec["ac_bos"]
    poc = ac_bos["point_of_connection"]
    panel = poc["panel"]
    placeholder = poc["breaker"]
    ocpds = [o for o in ac_bos.get("ocpds", []) if o["id"] != placeholder]
    combiner = request.ac_combiner

    def breaker(position: str, rating: float, **extra: Any) -> tuple[dict[str, Any], str | None]:
        choice, found = bos.select_ac_breaker(
            position=position,
            rating_a=rating,
            poles=phases,
            voltage_v=AC_BREAKER_VOLTAGE_V,
            fault_ka=fault,
            breakers=ac_breakers,
        )
        issues.extend(found)
        entry = {
            "id": position,
            "poles": phases,
            "rating_a": choice.rating_a if choice else rating,
            "voltage_v": AC_BREAKER_VOLTAGE_V,
            "kaic_ka": choice.interrupting_ka if choice and choice.interrupting_ka else fault,
            **extra,
        }
        if choice is not None:
            entry["model"] = choice.device_id
        return entry, choice.device_id if choice else None

    branches: list[dict[str, Any]] = []
    choices: list[MicroBranchChoice] = []
    circuits: list[dict[str, Any]] = []
    numbers = iter(range(1, units + 1))
    left = n_modules
    for index, count in enumerate(counts, start=1):
        branch_id, ocpd_id = f"R{index}", f"ITM-R{index}"
        trunk = next(s for s in sizes if capacity[s] >= count)
        units_list = []
        for _ in range(count):
            used = min(inputs, left)
            left -= used
            letters = adapters.mppt_ids(micro)[:used]
            units_list.append(
                {
                    "id": f"MI{next(numbers)}",
                    "inverter": INVERTER_ID,
                    "inputs": dict.fromkeys(letters, adapters.MODULE_ID),
                }
            )
        rating = micro_rules.branch_ocpd_a(limits, count, tables)
        derived = (
            {"role": "other", "backfed": False, "at": COMBINER_ID}
            if combiner
            else {"role": "I1", "backfed": True, "at": panel}
        )
        entry, model = breaker(ocpd_id, rating, **derived)
        ocpds.append(entry)
        current = count * current_unit
        conductor, found = bos.size_conductor(
            circuit_id=f"C-{branch_id}",
            kind="inverter_output",
            i_max_a=current,
            ocpd_a=entry["rating_a"],
            current_carrying=2,
            ambient_c=ambient,
            clearance_mm=None,
            length_m=length,
            drop_pct=lambda size, t=trunk, n=count: calc.voltage_drop_trunk_pct(
                size,
                t,
                home_run_m=length,
                spacing_m=spacing,
                units=n,
                unit_current_a=current_unit,
                voltage_v=voltage,
                tables=tables,
            ),
            drop_limit_pct=standards.vd_limits_pct.ac,
            drop_rule="VD-002",
            tables=tables,
        )
        issues.extend(found)
        if conductor is None:
            return Rejection(StringConfig(micro.component_id, units, 1), tuple(issues), "bos")
        circuits.append(
            _ac_circuit(
                f"C-{branch_id}",
                branch_id,
                ocpd_id,
                conductor.size,
                length,
                ambient,
                phases,
                tables,
                routing.ac_raceway_trade_size_mm,
                issues,
            )
        )
        branches.append(
            {
                "id": branch_id,
                "trunk_size": trunk,
                "spacing_m": spacing,
                "ocpd": ocpd_id,
                "microinverters": units_list,
            }
        )
        choices.append(
            MicroBranchChoice(
                branch_id,
                count,
                trunk,
                current,
                ocpd_id,
                entry["rating_a"],
                model,
                conductor.size,
                conductor.vd_pct,
            )
        )

    spds = [dict(s) for s in ac_bos.get("spds", [])]
    if combiner:
        total = units * current_unit
        main, _model = breaker(
            COMBINER_MAIN_ID,
            calc.next_standard_ocpd_a(calc.ocpd_min_a(total), tables),
            role="I1",
            backfed=True,
            at=panel,
        )
        ocpds = [o for o in ocpds if o["id"] != COMBINER_MAIN_ID] + [main]
        conductor, found = bos.size_conductor(
            circuit_id="C-CCA",
            kind="inverter_output",
            i_max_a=total,
            ocpd_a=main["rating_a"],
            current_carrying=2,
            ambient_c=ambient,
            clearance_mm=None,
            length_m=length,
            drop_pct=lambda size: calc.voltage_drop_ac_pct(size, length, total, voltage, 2, tables),
            drop_limit_pct=standards.vd_limits_pct.ac,
            drop_rule="VD-002",
            tables=tables,
        )
        issues.extend(found)
        if conductor is None:
            return Rejection(StringConfig(micro.component_id, units, 1), tuple(issues), "bos")
        circuits.append(
            _ac_circuit(
                "C-CCA",
                COMBINER_ID,
                COMBINER_MAIN_ID,
                conductor.size,
                length,
                ambient,
                phases,
                tables,
                routing.ac_raceway_trade_size_mm,
                issues,
            )
        )
        spd_id = None
        if spds:
            spds[0]["at"] = COMBINER_ID
            spd_id = spds[0]["id"]
        spec["ac_combiner"] = {"id": COMBINER_ID, "main_ocpd": COMBINER_MAIN_ID, "spd": spd_id}
        poc["breaker"] = COMBINER_MAIN_ID
    else:
        poc["breaker"] = branches[0]["ocpd"]

    for main_breaker in ac_bos.get("main_breakers", []):
        choice, found = bos.select_ac_breaker(
            position=main_breaker["id"],
            rating_a=main_breaker["rating_a"],
            poles=main_breaker["poles"],
            voltage_v=AC_BREAKER_VOLTAGE_V,
            fault_ka=fault,
            breakers=main_breakers,
            exact=True,
        )
        issues.extend(found)
        if choice is not None:
            main_breaker.update(kaic_ka=choice.interrupting_ka, model=choice.device_id)
    ac_bos["ocpds"] = ocpds
    ac_bos["spds"] = spds
    spec["ac_branches"] = branches
    spec["circuits"] = circuits
    return spec, choices, issues, units, free


def _ac_circuit(
    circuit_id: str,
    source: str,
    target: str,
    size: str,
    length: float,
    ambient: float,
    phases: int,
    tables: NomTables,
    trade_size_mm: float | None,
    issues: list[Issue],
) -> dict[str, Any]:
    raceway, found = bos.size_raceway(
        circuit_id=circuit_id,
        conductors=phases + 1,
        conductor_area_mm2=calc.insulated_area_mm2(size, bos.AC_INSULATION, None, tables),
        egc_size=size,
        given_trade_size_mm=trade_size_mm,
        insulation=bos.AC_INSULATION,
        tables=tables,
    )
    issues.extend(found)
    return {
        "id": circuit_id,
        "kind": "inverter_output",
        "from": source,
        "to": target,
        "conductors": {
            "qty": phases,
            "size": size,
            "material": "Cu",
            "insulation": bos.AC_INSULATION,
        },
        "egc": {"size": size, "type": bos.EGC_TYPE},
        "raceway": {"type": "EMT", "trade_size_mm": raceway.trade_size_mm, "ccc_count": 2},
        "length_m": length,
        "ambient_c": ambient,
    }


__all__ = [
    "MICRO_ASSUMPTIONS",
    "OBJECTIVE_MICRO",
    "MicroBranchChoice",
    "MicroCandidate",
    "module_count",
    "rank_key",
    "size_micro_system",
    "split_branches",
]
