"""Microinverter rules: module on each input, service voltage and AC branches (Stage 3.9.2).

Owner decisions of 2026-11-07, checked against NOM-001-SEDE-2012:

* **Module on each input.** One module per input, so the 690-8(a)(1) source-circuit current of an
  input is that module's. The input limits are equipment limits of the microinverter:

  ======== ================================================================== ===========
  MIC-001  Voc(T_min) of the module <= maximum DC input voltage (690-7)        error
  MIC-002  Isc of the module <= maximum input short-circuit current            error
           (the module's own Isc, BNPI for a bifacial module, without the
           1.25 factor: owner decision)
  MIC-003  Imp of the module above the maximum input current: clipping         info
  MIC-004  module power outside the recommended range: below = info,           info/warning
           above = warning (the micro clips)
  MIC-005  hot Vmp of the module below the MPPT window                         warning
  ======== ================================================================== ===========

* **Service voltage** (MIC-010). The microinverter connects at the service voltage between the
  conductors it is wired to (220 V between two phases of a 2F-3H or 3F-4H service, 127 V phase to
  neutral). That voltage must fall inside its AC operating window, or it cannot work: error. This
  is an equipment limit from the datasheet, not a NOM article.

* **AC branch** (MIC-011 to MIC-013). The units of a branch share one AC trunk cable and one
  breaker. The circuit current is the sum of the units' continuous output currents
  (690-8(a)(3)); the breaker is the next standard rating at or above 1.25 times that
  (690-8(b)(1), 240-6(a)), and it may not exceed the 240-4(d) limit of the trunk cable size
  (14 AWG 15 A, 12 AWG 20 A, 10 AWG 30 A). The manufacturer's table (units per trunk cable and
  largest breaker) is an extra cap applied after the NOM.

* **Voltage drop** (VD-002, warning as for any AC circuit). The trunk is a daisy chain: the run
  from the branch breaker to the first unit carries every unit's current, and each segment between
  units carries the current of the units after it. The cable from a unit's AC sub connector has a
  manufacturer length limit by size (MIC-014, error: the installation manual says it must be
  strictly adhered to).
"""

from __future__ import annotations

from dataclasses import dataclass

from pvsld.catalogue import Microinverter
from pvsld.catalogue import PVModule as CatalogueModule
from pvsld.core import calc
from pvsld.core.calc import VocMethod
from pvsld.core.model import Module
from pvsld.core.severity import Severity
from pvsld.core.tables import NomTables
from pvsld.sizing.adapters import design_isc_a
from pvsld.sizing.models import Issue


def _n(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}"


def _g(value: float) -> str:
    return f"{value:g}"


# --- Module on each input ------------------------------------------------------------------------


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
    """MIC-001 to MIC-005 for one module model on the microinverter's inputs."""
    who = micro.component_id
    issues: list[Issue] = []
    voc_cold = calc.voc_max_module_v(spec_module, t_min_c, voc_method)
    if voc_cold > micro.max_input_voltage_v:
        issues.append(
            Issue(
                "MIC-001",
                Severity.ERROR,
                f"El Voc del módulo a {_g(t_min_c)} °C es {_n(voc_cold, 2)} V y la entrada del "
                f"microinversor {who} admite como máximo {_g(micro.max_input_voltage_v)} V.",
                subject=who,
            )
        )
    isc = design_isc_a(module)
    if isc > micro.max_short_circuit_current_per_mppt_a:
        source = "BNPI (bifacial)" if module.bnpi is not None else "STC"
        issues.append(
            Issue(
                "MIC-002",
                Severity.ERROR,
                f"El Isc del módulo ({_n(isc, 2)} A {source}) excede la corriente de cortocircuito "
                f"máxima de la entrada del microinversor {who} "
                f"({_g(micro.max_short_circuit_current_per_mppt_a)} A).",
                subject=who,
            )
        )
    if module.imp_a > micro.max_input_current_per_mppt_a:
        issues.append(
            Issue(
                "MIC-003",
                Severity.INFO,
                f"El Imp del módulo ({_n(module.imp_a, 2)} A) supera la corriente máxima de "
                f"entrada del microinversor {who} ({_g(micro.max_input_current_per_mppt_a)} A); "
                "recortará algo de potencia en las horas de más sol.",
                subject=who,
            )
        )
    low, high = micro.recommended_module_power_wp
    if module.pmax_w < low:
        issues.append(
            Issue(
                "MIC-004",
                Severity.INFO,
                f"El módulo de {_g(module.pmax_w)} W queda abajo de la potencia recomendada del "
                f"microinversor {who} ({_g(low)} a {_g(high)} W); el equipo quedará subutilizado.",
                subject=who,
            )
        )
    elif module.pmax_w > high:
        issues.append(
            Issue(
                "MIC-004",
                Severity.WARNING,
                f"El módulo de {_g(module.pmax_w)} W pasa la potencia recomendada del "
                f"microinversor {who} ({_g(low)} a {_g(high)} W); recortará potencia.",
                subject=who,
            )
        )
    t_cell = calc.t_cell_max_c(t_amb_max_c, cell_temp_rise_c)
    vmp_hot = calc.vmp_module_v(spec_module, t_cell)
    if vmp_hot < micro.mppt_voltage_range_v[0]:
        issues.append(
            Issue(
                "MIC-005",
                Severity.WARNING,
                f"El Vmp del módulo con la celda a {_g(t_cell)} °C es {_n(vmp_hot, 2)} V, abajo de "
                f"la ventana MPPT del microinversor {who} "
                f"({_g(micro.mppt_voltage_range_v[0])} V).",
                subject=who,
            )
        )
    return issues


# --- Service voltage -----------------------------------------------------------------------------


def service_voltage_issue(micro: Microinverter, connection_voltage_v: float) -> Issue | None:
    """MIC-010: the voltage the unit is wired to must be inside its AC operating window."""
    low, high = micro.ac_voltage_range_v
    if low <= connection_voltage_v <= high:
        return None
    return Issue(
        "MIC-010",
        Severity.ERROR,
        f"El microinversor {micro.component_id} trabaja entre {_g(low)} y {_g(high)} V de CA y se "
        f"conectaría a {_g(connection_voltage_v)} V; no puede operar así. Conéctelo entre dos "
        "fases de un servicio 2F-3H o 3F-4H (220 V) o elija un microinversor para esa tensión.",
        subject=micro.component_id,
    )


# --- AC branch -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class BranchLimit:
    """How many units of one model an AC branch on one trunk cable size may carry."""

    trunk_size: str
    nom_units: int
    manufacturer_units: int | None
    ocpd_limit_a: float

    @property
    def units(self) -> int:
        """The allowed units: the NOM limit, capped by the manufacturer's table."""
        if self.manufacturer_units is None:
            return self.nom_units
        return min(self.nom_units, self.manufacturer_units)


def branch_current_a(micro: Microinverter, units: int) -> float:
    """690-8(a)(3): the sum of the units' continuous output currents."""
    return units * micro.max_ac_output_current_a


def branch_ocpd_a(micro: Microinverter, units: int, tables: NomTables) -> float:
    """Next standard rating >= 1.25 x the branch current (690-8(b)(1), 240-6(a))."""
    return calc.next_standard_ocpd_a(calc.ocpd_min_a(branch_current_a(micro, units)), tables)


def _trunk_ocpd_limit_a(micro: Microinverter, trunk_size: str, tables: NomTables) -> float:
    """Largest branch breaker on ``trunk_size``: 240-4(d) (or the 75 degC ampacity), then the
    manufacturer's maximum when it lists the size."""
    small = tables.small_conductor_ocpd_limit_a.get(trunk_size)
    limit = (
        small
        if small is not None
        else calc.max_ocpd_for_ampacity_a(calc.ampacity_a(trunk_size, 75, tables), tables)
    )
    cable = micro.trunk_cable(trunk_size)
    return min(limit, cable.max_ocpd_a) if cable is not None else limit


def branch_limit(micro: Microinverter, trunk_size: str, tables: NomTables) -> BranchLimit:
    """Units per branch on ``trunk_size`` by the NOM and by the manufacturer's table."""
    limit = _trunk_ocpd_limit_a(micro, trunk_size, tables)
    units = 0
    while branch_ocpd_a(micro, units + 1, tables) <= limit:
        units += 1
    return BranchLimit(
        trunk_size=trunk_size,
        nom_units=units,
        manufacturer_units=micro.max_units_per_trunk.get(trunk_size),
        ocpd_limit_a=limit,
    )


def branch_issues(
    micro: Microinverter,
    *,
    branch_id: str,
    units: int,
    trunk_size: str,
    ocpd_a: float | None,
    tables: NomTables,
) -> list[Issue]:
    """MIC-011 to MIC-013 for one AC branch (``ocpd_a`` None = the engine picks the breaker)."""
    issues: list[Issue] = []
    if micro.trunk_cable(trunk_size) is None:
        allowed = ", ".join(cable.size for cable in micro.trunk_cables)
        issues.append(
            Issue(
                "MIC-013",
                Severity.ERROR,
                f"La rama {branch_id} usa cable troncal {trunk_size}, que el fabricante del "
                f"microinversor {micro.component_id} no contempla (permitidos: {allowed}).",
                subject=branch_id,
            )
        )
    limit = branch_limit(micro, trunk_size, tables)
    current = branch_current_a(micro, units)
    needed = branch_ocpd_a(micro, units, tables)
    if units > limit.nom_units:
        issues.append(
            Issue(
                "MIC-011",
                Severity.ERROR,
                f"La rama {branch_id} tiene {units} microinversores ({_n(current, 2)} A); su ITM "
                f"tendría que ser de {_g(needed)} A (1.25 × {_n(current, 2)} A, NOM 690-8(b)) y el "
                f"cable {trunk_size} admite como máximo {_g(limit.ocpd_limit_a)} A "
                f"(240-4(d) y fabricante). Máximo {limit.nom_units} por rama con {trunk_size}.",
                subject=branch_id,
            )
        )
    elif limit.manufacturer_units is not None and units > limit.manufacturer_units:
        issues.append(
            Issue(
                "MIC-012",
                Severity.ERROR,
                f"La rama {branch_id} tiene {units} microinversores y el fabricante permite "
                f"{limit.manufacturer_units} por cable troncal {trunk_size}.",
                subject=branch_id,
            )
        )
    if ocpd_a is not None:
        if ocpd_a < calc.ocpd_min_a(current):
            issues.append(
                Issue(
                    "MIC-011",
                    Severity.ERROR,
                    f"El ITM de {_g(ocpd_a)} A de la rama {branch_id} es menor que 1.25 × "
                    f"{_n(current, 2)} A = {_n(calc.ocpd_min_a(current), 2)} A (NOM 690-8(b)); "
                    f"use {_g(needed)} A.",
                    subject=branch_id,
                )
            )
        elif ocpd_a > limit.ocpd_limit_a:
            issues.append(
                Issue(
                    "MIC-011",
                    Severity.ERROR,
                    f"El ITM de {_g(ocpd_a)} A de la rama {branch_id} excede lo que protege el "
                    f"cable troncal {trunk_size} ({_g(limit.ocpd_limit_a)} A, 240-4(d) y "
                    "fabricante).",
                    subject=branch_id,
                )
            )
    return issues


# --- Voltage drop --------------------------------------------------------------------------------


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
    """Voltage drop (%) from the branch breaker to the last unit of a daisy-chained trunk.

    ``home_run_m`` carries every unit's current; the segment after unit ``k`` (of ``spacing_m``)
    carries the current of the ``units - k`` units beyond it. Two energized conductors.
    """
    current = micro.max_ac_output_current_a
    drop = calc.voltage_drop_ac_pct(trunk_size, home_run_m, units * current, voltage_v, 2, tables)
    for beyond in range(units - 1, 0, -1):
        drop += calc.voltage_drop_ac_pct(
            trunk_size, spacing_m, beyond * current, voltage_v, 2, tables
        )
    return drop


def trunk_drop_issue(
    *, branch_id: str, drop_pct: float, limit_pct: float, trunk_size: str
) -> Issue | None:
    """VD-002 warning when the branch drop exceeds the project's AC limit."""
    if drop_pct <= limit_pct:
        return None
    return Issue(
        "VD-002",
        Severity.WARNING,
        f"La caída de tensión de la rama {branch_id} ({_n(drop_pct, 2)} %) excede el límite de "
        f"{_n(limit_pct, 2)} % con cable {trunk_size}; acorte el recorrido o use un calibre mayor.",
        subject=branch_id,
    )


def drop_cable_issue(micro: Microinverter, *, size: str, length_m: float) -> Issue | None:
    """MIC-014: the cable from a unit's AC sub connector within the manufacturer's length."""
    longest = micro.max_drop_cable_length_m.get(size)
    if longest is None:
        if not micro.max_drop_cable_length_m:
            return None
        allowed = ", ".join(micro.max_drop_cable_length_m)
        return Issue(
            "MIC-014",
            Severity.ERROR,
            f"El fabricante del microinversor {micro.component_id} no da longitud para cable "
            f"{size} desde el conector de CA (calibres con dato: {allowed}).",
            subject=micro.component_id,
        )
    if length_m <= longest:
        return None
    return Issue(
        "MIC-014",
        Severity.ERROR,
        f"El cable {size} desde el conector de CA del microinversor {micro.component_id} mide "
        f"{_g(length_m)} m y el fabricante permite como máximo {_g(longest)} m.",
        subject=micro.component_id,
    )
