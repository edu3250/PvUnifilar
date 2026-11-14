"""Sizing with several string inverters on one shared breaker (Stage 3.12.2, owner 2026-11-13).

The owner's rules: the fewest inverters that reach the request, the strings spread evenly (every
inverter gets the same configuration), mixed models only when the user names them, and all the
outputs joined at one AC junction box (``CJ-CA1``) before one breaker rated 1.25 x the sum of
their currents.

Each inverter is evaluated alone with the string evaluation of :mod:`pvsld.sizing.strings` on its
share of the request; the design is then built the way the editor's drawing is
(:func:`pvsld.graph.graph_to_spec`: shared breaker, junction box, circuits ``C-INV<n>`` and
``C-INVT``, conductors and raceways), the DC box of each inverter is added
(:func:`pvsld.sizing.dcbox.dc_box`) and the whole design must pass the rule pack.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pvsld.capabilities import MAX_STRINGS
from pvsld.catalogue import ComponentRegistry
from pvsld.catalogue import Inverter as CatalogueInverter
from pvsld.catalogue import PVModule as CatalogueModule
from pvsld.core.model import Module, Standards
from pvsld.core.rules import Finding
from pvsld.core.severity import Severity
from pvsld.graph import graph_to_spec
from pvsld.sizing.dcbox import apply_dc_box, dc_box
from pvsld.sizing.models import Issue, SizingRequest
from pvsld.sizing.strings import EvalContext, Evaluation, enumerate_configs, evaluate_config

MAX_AUTO_INVERTERS = 6
"""Most inverters the automatic search tries (a larger system: name the count)."""
JUNCTION_ID = "CJ-CA1"
"""The AC junction box where the inverter outputs join."""
GRID_ID = "RED-1"
MPPT_PORTS = ("A", "B")
UNDRAWN = (
    "modules",
    "inverters",
    "strings",
    "circuits",
    "dc_bos",
    "ac_branches",
    "ac_combiner",
)


@dataclass(frozen=True)
class InverterUnit:
    """One inverter of the design and its strings."""

    inverter_id: str
    n_strings: int
    n_series: int
    p_dc_w: float
    p_ac_w: float

    @property
    def modules(self) -> int:
        return self.n_strings * self.n_series

    @property
    def label(self) -> str:
        return f"{self.n_strings}x{self.n_series}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "inverter": self.inverter_id,
            "strings": self.n_strings,
            "modules_per_string": self.n_series,
            "modules": self.modules,
            "p_dc_w": self.p_dc_w,
            "p_ac_w": self.p_ac_w,
        }


@dataclass(frozen=True)
class MultiCandidate:
    """A design with several string inverters that passed the rule pack."""

    rank: int
    units: tuple[InverterUnit, ...]
    breaker_a: float
    issues: tuple[Issue, ...]
    findings: tuple[Finding, ...]
    spec: dict[str, Any] = field(repr=False)
    explanation_es: str = ""

    @property
    def modules(self) -> int:
        return sum(u.modules for u in self.units)

    @property
    def p_dc_w(self) -> float:
        return sum(u.p_dc_w for u in self.units)

    @property
    def p_ac_w(self) -> float:
        return sum(u.p_ac_w for u in self.units)

    @property
    def inverter_ids(self) -> tuple[str, ...]:
        return tuple(u.inverter_id for u in self.units)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def label(self) -> str:
        models = list(dict.fromkeys(self.inverter_ids))
        if len(models) == 1:
            return f"{len(self.units)} x {models[0]} ({self.units[0].label} cada uno)"
        return " + ".join(f"{u.inverter_id} ({u.label})" for u in self.units)

    def to_dict(self, *, include_spec: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "rank": self.rank,
            "kind": "multi_inverter",
            "inverter": "+".join(self.inverter_ids),
            "explanation_es": self.explanation_es,
            "inverters": [u.to_dict() for u in self.units],
            "modules": self.modules,
            "p_dc_w": self.p_dc_w,
            "p_ac_w": self.p_ac_w,
            "shared_breaker_a": self.breaker_a,
            "issues": [i.to_dict() for i in self.issues],
            "rule_pack": {
                "errors": 0,
                "warnings": sum(1 for f in self.findings if f.severity is Severity.WARNING),
                "findings": [f.to_dict() for f in self.findings],
            },
        }
        if include_spec:
            import json

            data["spec"] = json.loads(json.dumps(self.spec, default=str))
        return data


# --- Per-inverter evaluation ----------------------------------------------------------------


def _share(value: int | None, part: float, *, up: bool) -> int | None:
    if value is None:
        return None
    share = value * part
    return max(1, math.ceil(share - 1e-9) if up else math.floor(share + 1e-9))


def unit_evaluations(
    request: SizingRequest,
    module: CatalogueModule,
    spec_module: Module,
    inverter: CatalogueInverter,
    standards: Standards,
    part: float,
) -> list[Evaluation]:
    """The feasible configurations of ``inverter`` on its ``part`` (0..1] of the request."""
    ctx = EvalContext(
        module=module,
        spec_module=spec_module,
        inverter=inverter,
        site=request.site,
        voc_method=standards.voc_method,
        cell_temp_rise_c=request.cell_temp_rise_c,
        optimizers_on_all_modules=request.optimizers_on_all_modules,
        policy=request.dc_ac_policy,
        module_count_min=_share(request.module_count_min, part, up=True),
        module_count_max=_share(request.module_count_max, part, up=False),
        n_strings=request.n_strings,
        n_series=request.n_series,
    )
    found = []
    for n_strings, n_series in enumerate_configs(ctx):
        if n_strings > MAX_STRINGS:
            continue
        evaluation = evaluate_config(ctx, n_strings, n_series)
        if evaluation.feasible:
            found.append(evaluation)
    return found


# --- The specification ----------------------------------------------------------------------


def _template(request: SizingRequest) -> dict[str, Any]:
    template = {k: copy.deepcopy(v) for k, v in request.template.items() if k not in UNDRAWN}
    ac = template.setdefault("ac_bos", {})
    breaker = (ac.get("point_of_connection") or {}).get("breaker")
    # the shared breaker comes from the drawing: 1.25 x the sum of the inverter currents
    ac["ocpds"] = [o for o in ac.get("ocpds") or [] if o.get("id") != breaker]
    template["layout"] = {**template.get("layout", {}), "template": request.layout_template}
    return template


def _graph(
    request: SizingRequest,
    module: CatalogueModule,
    units: Sequence[tuple[CatalogueInverter, Evaluation]],
    template: dict[str, Any],
) -> dict[str, Any]:
    """The design drawn as the editor would: strings, inverters, junction box, shared breaker,
    and the service of the template."""
    routing = request.routing
    nodes: list[dict[str, Any]] = []
    cables: list[dict[str, Any]] = []
    string_number = 0
    for number, (inverter, evaluation) in enumerate(units, start=1):
        inverter_id = f"INV{number}"
        nodes.append(
            {"id": inverter_id, "type": "inverter", "values": {"model": inverter.component_id}}
        )
        for index in range(evaluation.config.n_strings):
            string_number += 1
            string_id = f"S{string_number}"
            port = MPPT_PORTS[index % min(inverter.mppt_count, len(MPPT_PORTS))]
            nodes.append(
                {
                    "id": string_id,
                    "type": "pv_string",
                    "values": {
                        "module": module.component_id,
                        "n_series": evaluation.config.n_series,
                        "tilt_deg": routing.tilt_deg,
                        "azimuth_deg": routing.azimuth_deg,
                    },
                }
            )
            cables.append(
                _cable(
                    f"{string_id}.OUT",
                    f"{inverter_id}.{port}",
                    length_m=routing.dc_string_length_m,
                    ambient_c=routing.dc_ambient_c,
                    size=request.dc_conductor_size,
                    raceway_trade_size_mm=routing.dc_raceway_trade_size_mm,
                )
            )
        cables.append(
            _cable(
                f"{inverter_id}.AC",
                f"{JUNCTION_ID}.W",
                length_m=routing.ac_inverter_to_junction_m,
                ambient_c=routing.ac_ambient_c,
                size=request.ac_conductor_size,
                raceway_trade_size_mm=routing.ac_raceway_trade_size_mm,
            )
        )
    ac = template.get("ac_bos") or {}
    poc = ac.get("point_of_connection") or {}
    breaker = poc.get("breaker") or "ITM-1"
    nodes += [{"id": JUNCTION_ID, "type": "junction"}, {"id": breaker, "type": "ac_breaker"}]
    cables.append(
        _cable(
            f"{JUNCTION_ID}.E",
            f"{breaker}.IN",
            length_m=routing.ac_output_length_m,
            ambient_c=routing.ac_ambient_c,
            size=request.ac_conductor_size,
            raceway_trade_size_mm=routing.ac_raceway_trade_size_mm,
        )
    )
    upstream = f"{breaker}.OUT"
    panel = poc.get("panel")
    if panel:
        nodes.append({"id": panel, "type": "load_centre"})
        cables.append({"from": upstream, "to": f"{panel}.LEFT"})
        upstream = f"{panel}.RIGHT"
    for main in ac.get("main_breakers") or []:
        if main.get("id") != breaker:
            nodes.append({"id": main["id"], "type": "ac_breaker"})
            cables.append({"from": upstream, "to": f"{main['id']}.IN"})
            upstream = f"{main['id']}.OUT"
        break
    for meter in ac.get("meters") or []:
        nodes.append({"id": meter["id"], "type": "meter"})
        cables.append({"from": upstream, "to": f"{meter['id']}.IN"})
        upstream = f"{meter['id']}.OUT"
    nodes.append({"id": GRID_ID, "type": "grid"})
    cables.append({"from": upstream, "to": f"{GRID_ID}.IN"})
    return {"format": "pvsld.graph/1", "nodes": nodes, "cables": cables}


def _cable(a: str, b: str, **extra: Any) -> dict[str, Any]:
    return {"from": a, "to": b, **{k: v for k, v in extra.items() if v is not None}}


def build_multi_spec(
    request: SizingRequest,
    module: CatalogueModule,
    units: Sequence[tuple[CatalogueInverter, Evaluation]],
    registry: ComponentRegistry,
) -> tuple[dict[str, Any] | None, list[Issue]]:
    """The specification of ``units`` on one shared breaker, with each inverter's DC box."""
    template = _template(request)
    result = graph_to_spec(_graph(request, module, units, template), template, registry=registry)
    if not result.ok:
        return None, [
            Issue("GEN-001", Severity.ERROR, issue.message_es, subject="multi_inverter")
            for issue in result.issues
        ]
    spec = result.spec
    box = dc_box(spec, device=request.dc_ocpd_device, registry=registry)
    if not box.built:
        return None, [Issue("GEN-001", Severity.ERROR, box.skipped_es or "", subject="dc_box")]
    spec = apply_dc_box(spec, box)
    from pvsld.sizing.engine import _restate_size

    kwp = sum(e.metrics.p_dc_w for _i, e in units) / 1000
    kwac = sum(i.rated_ac_power_w for i, _e in units) / 1000
    spec["project"] = _restate_size(spec["project"], kwp, kwac)
    spec["title_block"] = _restate_size(spec["title_block"], kwp, kwac)
    return spec, [Issue("GEN-004", Severity.INFO, a, subject="dc_box") for a in box.assumptions]


def explain_multi(
    units: Sequence[tuple[CatalogueInverter, Evaluation]], single_w: float | None
) -> str:
    """Why several inverters, in plain Spanish."""
    count = len(units)
    kwp = sum(e.metrics.p_dc_w for _i, e in units) / 1000
    kw = sum(i.rated_ac_power_w for i, _e in units) / 1000
    models = list(dict.fromkeys(i.component_id for i, _e in units))
    what = (
        f"{count} inversores {models[0]}"
        if len(models) == 1
        else "los inversores " + ", ".join(i.component_id for i, _e in units)
    )
    text = (
        f"Se usan {what} ({kw:g} kW en total) para {kwp:.2f} kWp, con sus salidas unidas en la "
        f"caja {JUNCTION_ID} y un solo ITM compartido (1.25 × la suma de sus corrientes)."
    )
    if single_w is not None:
        text += (
            f" Con un solo inversor del catálogo se llegaba a lo más a {single_w / 1000:.2f} kWp."
        )
    return text
