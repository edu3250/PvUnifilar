"""Catalogue and sizing for the MCP server (Stage 3.5): output shapes and their builders.

``list_components`` and ``get_component`` read the reviewed component records; ``size_pv_system``
runs the sizing engine and returns the selected specification ready for ``validate_pv_design`` and
``generate_single_line_diagram``. Results stay small (the selected spec plus brief alternatives and
grouped rejections), well inside the 25k-token budget of a tool result in Claude Code.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pvsld.catalogue.cli import _rating
from pvsld.catalogue.registry import Component, ComponentRegistry
from pvsld.resources import data_path
from pvsld.sizing import SizingResult
from pvsld.sizing.micro_sizing import MicroCandidate
from pvsld.sizing.models import Candidate
from pvsld.sizing.multi_sizing import MultiCandidate

__all__ = [
    "CATALOGUE_DIR_ENV",
    "MAX_ALTERNATIVES",
    "ComponentBrief",
    "ComponentListOutput",
    "ComponentOutput",
    "SizingOutput",
    "catalogue_dir_from_environment",
    "component_list_output",
    "component_output",
    "sizing_output",
    "sizing_text",
]

CATALOGUE_DIR_ENV = "PVSLD_CATALOGUE_DIR"
_REPO_RECORDS = data_path("datasheets/records")
MAX_ALTERNATIVES = 5
"""Ranked alternatives listed besides the selection (their full specs are never sent)."""
COMPONENT_TYPES = (
    "pv_module",
    "string_inverter",
    "hybrid_inverter",
    "microinverter",
    "dc_breaker",
    "dc_fuse",
    "ac_breaker",
    "dc_switch",
    "conductor",
)


def catalogue_dir_from_environment() -> Path:
    """``$PVSLD_CATALOGUE_DIR``, else the bundled records (:func:`pvsld.resources.data_path`)."""
    configured = os.environ.get(CATALOGUE_DIR_ENV)
    return Path(configured) if configured else _REPO_RECORDS


class ComponentBrief(BaseModel):
    component_id: str = Field(description="Id to use in size_pv_system and get_component.")
    component_type: str
    manufacturer: str
    rating: str = Field(description="Main rating: Wp, kW AC and kWp DC limit, or amperes.")
    reviewed: bool = Field(description="False: the owner has not checked it against its datasheet.")
    local: bool = Field(
        default=False,
        description="True: the user added it to the local catalogue from a datasheet.",
    )


class ComponentListOutput(BaseModel):
    """Result of ``list_components``."""

    ok: bool = Field(default=True, description="Always true; an invalid catalogue is a tool error.")
    count: int
    components: list[ComponentBrief]
    skipped_unreviewed: list[str] = Field(
        description="Record families left out because the owner has not reviewed them yet."
    )
    next_step: str


class ComponentOutput(BaseModel):
    """Result of ``get_component``: the whole record of one component."""

    ok: bool = Field(default=True, description="Always true; an unknown id is a tool error.")
    component_id: str
    component_type: str
    reviewed: bool
    data: dict[str, Any] = Field(
        description="Every datasheet value with its unit in the key, and source: the datasheet "
        "file, its SHA-256, pages, extraction method and who reviewed it."
    )
    next_step: str = Field(
        default="Use these values as they are; an unreviewed component must not go into a design "
        "that gets issued."
    )


class SizingOutput(BaseModel):
    """Result of ``size_pv_system``."""

    ok: bool = Field(description="True when a configuration was selected.")
    summary: str
    selected: dict[str, Any] | None = Field(
        default=None,
        description="The best candidate: inverter, strings x modules, kWp, DC/AC, voltages, "
        "protection and conductors, warnings.",
    )
    spec: dict[str, Any] | None = Field(
        default=None,
        description="Complete parameter spec of the selected design. Pass it unchanged to "
        "validate_pv_design and generate_single_line_diagram.",
    )
    alternatives: list[dict[str, Any]] = Field(
        description=f"Next best candidates, at most {MAX_ALTERNATIVES} (without their specs)."
    )
    rejected: list[dict[str, Any]] = Field(
        description="Refused configurations grouped by rule: rule_id, count, configurations and "
        "one Spanish reason."
    )
    assumptions: list[str] = Field(description="What the engine assumed and what it left to do.")
    next_step: str


# --- builders -----------------------------------------------------------------------------------


def _brief(component: Component, registry: ComponentRegistry) -> ComponentBrief:
    return ComponentBrief(
        component_id=component.component_id,
        component_type=component.component_type,
        manufacturer=component.manufacturer,
        rating=_rating(component),
        reviewed=component.reviewed,
        local=registry.is_local(component.component_id),
    )


def component_list_output(
    registry: ComponentRegistry, component_type: str | None, manufacturer: str | None
) -> ComponentListOutput:
    found = registry.find(component_type=component_type, manufacturer=manufacturer)
    return ComponentListOutput(
        count=len(found),
        components=[_brief(c, registry) for c in found],
        skipped_unreviewed=list(registry.skipped_unreviewed),
        next_step=(
            "Use a component_id in size_pv_system (module and inverters) or get_component for "
            "its datasheet values. Never invent a component that is not listed: ask the user for "
            "its datasheet and add it with add_component_to_catalogue."
        ),
    )


def component_output(registry: ComponentRegistry, component_id: str) -> ComponentOutput:
    component = registry.get(component_id)
    data: dict[str, Any] = component.model_dump(mode="json", exclude_none=True)
    data["source"] = component.source.model_dump(mode="json")  # keep reviewed_by: null visible
    return ComponentOutput(
        component_id=component.component_id,
        component_type=component.component_type,
        reviewed=component.reviewed,
        data=data,
    )


def _grouped_rejections(result: SizingResult) -> list[dict[str, Any]]:
    """One row per rule: how many configurations it refused, on which inverters, one reason."""
    groups: dict[str, list[Any]] = defaultdict(list)
    for rejection in result.rejected:
        groups[rejection.rule_id].append(rejection)
    return [
        {
            "rule_id": rule_id,
            "count": len(items),
            "inverters": sorted({r.config.inverter_id for r in items}),
            "example_es": items[0].errors[0].message_es,
        }
        for rule_id, items in groups.items()
    ]


def _alternative(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, MultiCandidate):
        return {
            "rank": candidate.rank,
            "inverter": "+".join(candidate.inverter_ids),
            "config": candidate.label,
            "modules": candidate.modules,
            "kwp": round(candidate.p_dc_w / 1000, 3),
            "dc_ac": round(candidate.p_dc_w / candidate.p_ac_w, 2),
            "warnings": [issue.rule_id for issue in candidate.warnings],
        }
    if isinstance(candidate, MicroCandidate):
        return {
            "rank": candidate.rank,
            "inverter": candidate.micro_id,
            "config": candidate.label,
            "modules": candidate.modules,
            "kwp": round(candidate.p_dc_w / 1000, 3),
            "dc_ac": round(candidate.p_dc_w / candidate.p_ac_w, 2),
            "warnings": [issue.rule_id for issue in candidate.warnings],
        }
    metrics = candidate.metrics
    return {
        "rank": candidate.rank,
        "inverter": candidate.config.inverter_id,
        "config": candidate.config.label,
        "modules": candidate.config.n_modules,
        "kwp": round(metrics.p_dc_w / 1000, 3),
        "dc_ac": round(metrics.dc_ac_ratio, 2),
        "warnings": [issue.rule_id for issue in candidate.warnings],
    }


def sizing_output(result: SizingResult) -> SizingOutput:
    selected = result.selected
    assumptions = list(result.assumptions)
    if isinstance(selected, Candidate):
        assumptions += [f"TODO 3.4b: {text}" for text in selected.bos.todo]
    if selected is None:
        return SizingOutput(
            ok=False,
            summary=(
                f"NO CANDIDATE: every configuration of {result.module_id} on "
                f"{', '.join(result.inverters_evaluated) or 'the inverters'} is rejected."
            ),
            alternatives=[],
            rejected=_grouped_rejections(result),
            assumptions=assumptions,
            next_step=(
                "Explain the rejections to the user (example_es) and propose another module, "
                "inverter, module count or target."
            ),
        )
    brief = selected.to_dict()
    brief.pop("rule_pack", None)
    if isinstance(selected, MultiCandidate):
        headline = (
            f"SELECTED: {selected.label} ({selected.modules} modules, "
            f"{selected.p_dc_w / 1000:.2f} kWp, shared breaker {selected.breaker_a:g} A)"
        )
    elif isinstance(selected, MicroCandidate):
        headline = (
            f"SELECTED: {selected.micro_id} {selected.label} ({selected.modules} modules, "
            f"{selected.p_dc_w / 1000:.2f} kWp)"
        )
    else:
        metrics = selected.metrics
        headline = (
            f"SELECTED: {selected.config.inverter_id} {selected.config.label} "
            f"({metrics.p_dc_w / 1000:.2f} kWp, DC/AC {metrics.dc_ac_ratio:.2f})"
        )
    return SizingOutput(
        ok=True,
        summary=(
            f"{headline}; {len(result.candidates)} candidate(s), {len(result.rejected)} rejected."
        ),
        selected=brief,
        spec=selected.to_dict(include_spec=True)["spec"],
        alternatives=[_alternative(c) for c in result.candidates[1 : 1 + MAX_ALTERNATIVES]],
        rejected=_grouped_rejections(result),
        assumptions=assumptions,
        next_step=(
            "Show the user the selection and its warnings. If they accept it, call "
            "validate_pv_design with spec, then generate_single_line_diagram."
        ),
    )


def sizing_text(output: SizingOutput) -> str:
    """Compact text of the result for the model: verdict, selection, alternatives, rejections."""
    lines = [output.summary]
    if output.selected is not None:
        selected = output.selected
        if selected.get("explanation_es"):
            lines.append(f"  why (tell the user): {selected['explanation_es']}")
        lines += [f"  warning {i['rule_id']}: {i['message_es']}" for i in selected["issues"]]
        if selected.get("kind") == "microinverter":
            lines += [
                f"  {b['branch']}: {b['units']} unit(s), trunk {b['trunk_size']}, breaker "
                f"{b['ocpd']} {b['ocpd_a']:g} A, home run {b['home_run_size']}, "
                f"ΔV {b['drop_pct']:g} %"
                for b in selected["branches"]
            ]
            if selected.get("ac_combiner"):
                lines.append("  AC combiner box CCA-1 with main breaker ITM-1")
            return "\n".join([*lines, *_tail(output)])
        bos = selected["bos"]
        dc_ocpd = bos["dc_ocpd"]
        string_ocpd = (
            f"{'gPV fuse' if dc_ocpd['device'] == 'fuse' else 'DC breaker'} "
            f"{dc_ocpd['device_id']} {dc_ocpd['rating_a']:g} A"
            if dc_ocpd["required"]
            else dc_ocpd["reason_es"]
        )
        lines.append(f"  string OCPD: {string_ocpd}")
        lines.append(f"  inverter-output breaker: {bos['ac_ocpd_a']:g} A")
        lines += [
            f"  {c['circuit_id']}: {c['size']}, I max {c['i_max_a']:g} A, ΔV {c['vd_pct']:g} %"
            for c in bos["conductors"]
        ]
    return "\n".join([*lines, *_tail(output)])


def _tail(output: SizingOutput) -> list[str]:
    """Alternatives, rejections, assumptions and the next step."""
    lines: list[str] = []
    if output.alternatives:
        lines.append("ALTERNATIVES:")
        lines += [
            f"  #{a['rank']} {a['inverter']} {a['config']}: {a['kwp']:g} kWp, DC/AC {a['dc_ac']}"
            + (f", warnings {', '.join(a['warnings'])}" if a["warnings"] else "")
            for a in output.alternatives
        ]
    if output.rejected:
        lines.append("REJECTED (by rule):")
        lines += [f"  {r['rule_id']} x{r['count']}: {r['example_es']}" for r in output.rejected]
    lines.append("ASSUMPTIONS: " + " | ".join(output.assumptions))
    lines.append(output.next_step)
    return lines
