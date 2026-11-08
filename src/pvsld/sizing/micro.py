"""Microinverter rules for catalogue equipment (Stage 3.9.2).

The rules live in :mod:`pvsld.core.micro` and work on plain data; this module turns a catalogue
:class:`~pvsld.catalogue.Microinverter` into those limits and the core findings into sizing
:class:`~pvsld.sizing.models.Issue` objects. See :mod:`pvsld.core.micro` for the rule table.
"""

from __future__ import annotations

from pvsld.catalogue import Microinverter
from pvsld.catalogue import PVModule as CatalogueModule
from pvsld.core import calc
from pvsld.core import micro as core
from pvsld.core.calc import VocMethod
from pvsld.core.micro import BranchLimit, MicroFinding, MicroLimits
from pvsld.core.model import Module
from pvsld.core.tables import NomTables
from pvsld.sizing.models import Issue

__all__ = [
    "BranchLimit",
    "branch_current_a",
    "branch_issues",
    "branch_limit",
    "branch_ocpd_a",
    "drop_cable_issue",
    "limits_of",
    "module_input_issues",
    "service_voltage_issue",
    "trunk_drop_issue",
    "trunk_drop_pct",
]


def limits_of(micro: Microinverter) -> MicroLimits:
    """The rule data of a catalogue microinverter (its id names it in the messages)."""
    return MicroLimits(
        name=micro.component_id,
        max_input_voltage_v=micro.max_input_voltage_v,
        mppt_min_v=micro.mppt_voltage_range_v[0],
        max_input_current_a=micro.max_input_current_per_mppt_a,
        max_input_isc_a=micro.max_short_circuit_current_per_mppt_a,
        recommended_module_power_wp=micro.recommended_module_power_wp,
        ac_voltage_range_v=micro.ac_voltage_range_v,
        max_ac_output_current_a=micro.max_ac_output_current_a,
        trunk_max_ocpd_a={cable.size: cable.max_ocpd_a for cable in micro.trunk_cables},
        max_units_per_trunk=dict(micro.max_units_per_trunk),
        max_drop_cable_length_m=dict(micro.max_drop_cable_length_m),
    )


def _issue(finding: MicroFinding) -> Issue:
    return Issue(finding.rule_id, finding.severity, finding.message_es, subject=finding.subject)


def _issues(findings: list[MicroFinding]) -> list[Issue]:
    return [_issue(finding) for finding in findings]


def module_input_issues(
    micro: Microinverter,
    module: CatalogueModule,
    spec_module: Module,
    *,
    t_min_c: float,
    t_amb_max_c: float,
    voc_method: VocMethod,
    cell_temp_rise_c: float = calc.DELTA_T_CELL_C,
) -> list[Issue]:
    """MIC-001 to MIC-005 (``spec_module`` from :func:`~pvsld.sizing.adapters.to_spec_module`,
    which carries the BNPI Isc of a bifacial ``module``)."""
    del module  # the spec module already carries the design values
    return _issues(
        core.module_input_findings(
            limits_of(micro),
            spec_module,
            t_min_c=t_min_c,
            t_amb_max_c=t_amb_max_c,
            voc_method=voc_method,
            cell_temp_rise_c=cell_temp_rise_c,
        )
    )


def service_voltage_issue(micro: Microinverter, connection_voltage_v: float) -> Issue | None:
    """MIC-010."""
    finding = core.service_voltage_finding(limits_of(micro), connection_voltage_v)
    return _issue(finding) if finding else None


def branch_current_a(micro: Microinverter, units: int) -> float:
    """690-8(a)(3): the sum of the units' continuous output currents."""
    return core.branch_current_a(limits_of(micro), units)


def branch_ocpd_a(micro: Microinverter, units: int, tables: NomTables) -> float:
    """Next standard rating >= 1.25 x the branch current."""
    return core.branch_ocpd_a(limits_of(micro), units, tables)


def branch_limit(micro: Microinverter, trunk_size: str, tables: NomTables) -> BranchLimit:
    """Units per branch on ``trunk_size`` by the NOM and by the manufacturer's table."""
    return core.branch_limit(limits_of(micro), trunk_size, tables)


def branch_issues(
    micro: Microinverter,
    *,
    branch_id: str,
    units: int,
    trunk_size: str,
    ocpd_a: float | None,
    tables: NomTables,
) -> list[Issue]:
    """MIC-011 to MIC-013 for one AC branch."""
    return _issues(
        core.branch_findings(
            limits_of(micro),
            branch_id=branch_id,
            units=units,
            trunk_size=trunk_size,
            ocpd_a=ocpd_a,
            tables=tables,
        )
    )


def trunk_drop_pct(
    micro: Microinverter,
    *,
    units: int,
    trunk_size: str,
    spacing_m: float,
    home_run_m: float,
    voltage_v: float,
    tables: NomTables,
) -> float:
    """Voltage drop (%) from the branch breaker to the last unit of the trunk."""
    return core.trunk_drop_pct(
        limits_of(micro),
        units=units,
        trunk_size=trunk_size,
        spacing_m=spacing_m,
        home_run_m=home_run_m,
        voltage_v=voltage_v,
        tables=tables,
    )


def trunk_drop_issue(
    *, branch_id: str, drop_pct: float, limit_pct: float, trunk_size: str
) -> Issue | None:
    """VD-002 warning when the branch drop exceeds the project's AC limit."""
    finding = core.trunk_drop_finding(
        branch_id=branch_id, drop_pct=drop_pct, limit_pct=limit_pct, trunk_size=trunk_size
    )
    return _issue(finding) if finding else None


def drop_cable_issue(micro: Microinverter, *, size: str, length_m: float) -> Issue | None:
    """MIC-014."""
    finding = core.drop_cable_finding(limits_of(micro), size=size, length_m=length_m)
    return _issue(finding) if finding else None
