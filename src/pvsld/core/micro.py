"""Microinverter rules on plain data: module on each input, service voltage and AC branches.

The rules work on :class:`MicroLimits`, built from a catalogue record
(:func:`pvsld.sizing.micro.limits_of`) or from the ``micro`` block of a specification inverter
(:func:`limits_from_spec`), so the spec validation, the editor and the sizing use the same checks.
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

from collections.abc import Mapping
from dataclasses import dataclass, field

from pvsld.core import calc
from pvsld.core.calc import VocMethod
from pvsld.core.model import Inverter, Module
from pvsld.core.severity import Severity
from pvsld.core.tables import NomTables


def _n(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}"


def _g(value: float) -> str:
    return f"{value:g}"


@dataclass(frozen=True)
class MicroFinding:
    """One result of a microinverter rule."""

    rule_id: str
    severity: Severity
    subject: str
    message_es: str


@dataclass(frozen=True)
class MicroLimits:
    """The microinverter data the rules read (one model)."""

    name: str
    max_input_voltage_v: float
    mppt_min_v: float
    max_input_current_a: float
    max_input_isc_a: float
    recommended_module_power_wp: tuple[float, float]
    ac_voltage_range_v: tuple[float, float]
    max_ac_output_current_a: float
    trunk_max_ocpd_a: Mapping[str, float] = field(default_factory=dict)
    """Trunk cable sizes the manufacturer allows and the largest breaker on each."""
    max_units_per_trunk: Mapping[str, int] = field(default_factory=dict)
    max_drop_cable_length_m: Mapping[str, float] = field(default_factory=dict)


def limits_from_spec(inverter: Inverter) -> MicroLimits:
    """The limits of a specification inverter of type ``micro`` (it must carry ``micro``)."""
    data = inverter.micro
    if data is None:
        raise ValueError(f"inverter {inverter.id} has no micro data")
    mppt = inverter.mppt[0]
    return MicroLimits(
        name=f"{inverter.manufacturer} {inverter.model}",
        max_input_voltage_v=inverter.vdc_max_v,
        mppt_min_v=mppt.vmin_v,
        max_input_current_a=mppt.imax_a,
        max_input_isc_a=mppt.isc_max_a,
        recommended_module_power_wp=data.recommended_module_power_wp,
        ac_voltage_range_v=data.ac_voltage_range_v,
        max_ac_output_current_a=inverter.iac_max_a,
        trunk_max_ocpd_a=dict(data.trunk_max_ocpd_a),
        max_units_per_trunk=dict(data.max_units_per_trunk),
        max_drop_cable_length_m=dict(data.max_drop_cable_length_m),
    )


# --- Module on each input ------------------------------------------------------------------------


def module_input_findings(
    micro: MicroLimits,
    module: Module,
    *,
    t_min_c: float,
    t_amb_max_c: float,
    voc_method: VocMethod,
    cell_temp_rise_c: float = calc.DELTA_T_CELL_C,
) -> list[MicroFinding]:
    """MIC-001 to MIC-005 for one module model on the microinverter's inputs.

    ``module.isc_a`` is the design short-circuit current (BNPI for a bifacial module).
    """
    who = micro.name
    found: list[MicroFinding] = []
    voc_cold = calc.voc_max_module_v(module, t_min_c, voc_method)
    if voc_cold > micro.max_input_voltage_v:
        found.append(
            MicroFinding(
                "MIC-001",
                Severity.ERROR,
                who,
                f"El Voc del módulo a {_g(t_min_c)} °C es {_n(voc_cold, 2)} V y la entrada del "
                f"microinversor {who} admite como máximo {_g(micro.max_input_voltage_v)} V.",
            )
        )
    if module.isc_a > micro.max_input_isc_a:
        source = "BNPI (bifacial)" if module.bifacial else "STC"
        found.append(
            MicroFinding(
                "MIC-002",
                Severity.ERROR,
                who,
                f"El Isc del módulo ({_n(module.isc_a, 2)} A {source}) excede la corriente de "
                f"cortocircuito máxima de la entrada del microinversor {who} "
                f"({_g(micro.max_input_isc_a)} A).",
            )
        )
    if module.imp_a > micro.max_input_current_a:
        found.append(
            MicroFinding(
                "MIC-003",
                Severity.INFO,
                who,
                f"El Imp del módulo ({_n(module.imp_a, 2)} A) supera la corriente máxima de "
                f"entrada del microinversor {who} ({_g(micro.max_input_current_a)} A); recortará "
                "algo de potencia en las horas de más sol.",
            )
        )
    low, high = micro.recommended_module_power_wp
    if module.pmax_w < low:
        found.append(
            MicroFinding(
                "MIC-004",
                Severity.INFO,
                who,
                f"El módulo de {_g(module.pmax_w)} W queda abajo de la potencia recomendada del "
                f"microinversor {who} ({_g(low)} a {_g(high)} W); el equipo quedará subutilizado.",
            )
        )
    elif module.pmax_w > high:
        found.append(
            MicroFinding(
                "MIC-004",
                Severity.WARNING,
                who,
                f"El módulo de {_g(module.pmax_w)} W pasa la potencia recomendada del "
                f"microinversor {who} ({_g(low)} a {_g(high)} W); recortará potencia.",
            )
        )
    t_cell = calc.t_cell_max_c(t_amb_max_c, cell_temp_rise_c)
    vmp_hot = calc.vmp_module_v(module, t_cell)
    if vmp_hot < micro.mppt_min_v:
        found.append(
            MicroFinding(
                "MIC-005",
                Severity.WARNING,
                who,
                f"El Vmp del módulo con la celda a {_g(t_cell)} °C es {_n(vmp_hot, 2)} V, abajo de "
                f"la ventana MPPT del microinversor {who} ({_g(micro.mppt_min_v)} V).",
            )
        )
    return found


# --- Service voltage -----------------------------------------------------------------------------


def service_voltage_finding(micro: MicroLimits, connection_voltage_v: float) -> MicroFinding | None:
    """MIC-010: the voltage the unit is wired to must be inside its AC operating window."""
    low, high = micro.ac_voltage_range_v
    if low <= connection_voltage_v <= high:
        return None
    return MicroFinding(
        "MIC-010",
        Severity.ERROR,
        micro.name,
        f"El microinversor {micro.name} trabaja entre {_g(low)} y {_g(high)} V de CA y se "
        f"conectaría a {_g(connection_voltage_v)} V; no puede operar así. Conéctelo entre dos "
        "fases de un servicio 2F-3H o 3F-4H (220 V) o elija un microinversor para esa tensión.",
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


def branch_current_a(micro: MicroLimits, units: int) -> float:
    """690-8(a)(3): the sum of the units' continuous output currents."""
    return units * micro.max_ac_output_current_a


def branch_ocpd_a(micro: MicroLimits, units: int, tables: NomTables) -> float:
    """Next standard rating >= 1.25 x the branch current (690-8(b)(1), 240-6(a))."""
    return calc.next_standard_ocpd_a(calc.ocpd_min_a(branch_current_a(micro, units)), tables)


def _trunk_ocpd_limit_a(micro: MicroLimits, trunk_size: str, tables: NomTables) -> float:
    """Largest branch breaker on ``trunk_size``: 240-4(d) (or the 75 degC ampacity), then the
    manufacturer's maximum when it lists the size."""
    small = tables.small_conductor_ocpd_limit_a.get(trunk_size)
    limit = (
        small
        if small is not None
        else calc.max_ocpd_for_ampacity_a(calc.ampacity_a(trunk_size, 75, tables), tables)
    )
    maker = micro.trunk_max_ocpd_a.get(trunk_size)
    return min(limit, maker) if maker is not None else limit


def branch_limit(micro: MicroLimits, trunk_size: str, tables: NomTables) -> BranchLimit:
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


def branch_findings(
    micro: MicroLimits,
    *,
    branch_id: str,
    units: int,
    trunk_size: str,
    ocpd_a: float | None,
    tables: NomTables,
) -> list[MicroFinding]:
    """MIC-011 to MIC-013 for one AC branch (``ocpd_a`` None = the engine picks the breaker)."""
    found: list[MicroFinding] = []
    if micro.trunk_max_ocpd_a and trunk_size not in micro.trunk_max_ocpd_a:
        allowed = ", ".join(micro.trunk_max_ocpd_a)
        found.append(
            MicroFinding(
                "MIC-013",
                Severity.ERROR,
                branch_id,
                f"La rama {branch_id} usa cable troncal {trunk_size}, que el fabricante del "
                f"microinversor {micro.name} no contempla (permitidos: {allowed}).",
            )
        )
    limit = branch_limit(micro, trunk_size, tables)
    current = branch_current_a(micro, units)
    needed = branch_ocpd_a(micro, units, tables)
    if units > limit.nom_units:
        found.append(
            MicroFinding(
                "MIC-011",
                Severity.ERROR,
                branch_id,
                f"La rama {branch_id} tiene {units} microinversores ({_n(current, 2)} A); su ITM "
                f"tendría que ser de {_g(needed)} A (1.25 × {_n(current, 2)} A, NOM 690-8(b)) y el "
                f"cable {trunk_size} admite como máximo {_g(limit.ocpd_limit_a)} A "
                f"(240-4(d) y fabricante). Máximo {limit.nom_units} por rama con {trunk_size}.",
            )
        )
    elif limit.manufacturer_units is not None and units > limit.manufacturer_units:
        found.append(
            MicroFinding(
                "MIC-012",
                Severity.ERROR,
                branch_id,
                f"La rama {branch_id} tiene {units} microinversores y el fabricante permite "
                f"{limit.manufacturer_units} por cable troncal {trunk_size}.",
            )
        )
    if ocpd_a is not None:
        if ocpd_a < calc.ocpd_min_a(current):
            found.append(
                MicroFinding(
                    "MIC-011",
                    Severity.ERROR,
                    branch_id,
                    f"El ITM de {_g(ocpd_a)} A de la rama {branch_id} es menor que 1.25 × "
                    f"{_n(current, 2)} A = {_n(calc.ocpd_min_a(current), 2)} A (NOM 690-8(b)); "
                    f"use {_g(needed)} A.",
                )
            )
        elif ocpd_a > limit.ocpd_limit_a:
            found.append(
                MicroFinding(
                    "MIC-011",
                    Severity.ERROR,
                    branch_id,
                    f"El ITM de {_g(ocpd_a)} A de la rama {branch_id} excede lo que protege el "
                    f"cable troncal {trunk_size} ({_g(limit.ocpd_limit_a)} A, 240-4(d) y "
                    "fabricante).",
                )
            )
    return found


# --- Voltage drop --------------------------------------------------------------------------------


def trunk_drop_pct(
    micro: MicroLimits,
    *,
    units: int,
    trunk_size: str,
    spacing_m: float,
    home_run_m: float,
    voltage_v: float,
    tables: NomTables,
    home_run_size: str | None = None,
) -> float:
    """Voltage drop (%) from the branch breaker to the last unit of a daisy-chained trunk.

    ``home_run_m`` carries every unit's current; the segment after unit ``k`` (of ``spacing_m``)
    carries the current of the ``units - k`` units beyond it. Two energized conductors. The home
    run is ``home_run_size`` (default: the trunk size).
    """
    return calc.voltage_drop_trunk_pct(
        home_run_size or trunk_size,
        trunk_size,
        home_run_m=home_run_m,
        spacing_m=spacing_m,
        units=units,
        unit_current_a=micro.max_ac_output_current_a,
        voltage_v=voltage_v,
        tables=tables,
    )


def trunk_drop_finding(
    *, branch_id: str, drop_pct: float, limit_pct: float, trunk_size: str
) -> MicroFinding | None:
    """VD-002 warning when the branch drop exceeds the project's AC limit."""
    if drop_pct <= limit_pct:
        return None
    return MicroFinding(
        "VD-002",
        Severity.WARNING,
        branch_id,
        f"La caída de tensión de la rama {branch_id} ({_n(drop_pct, 2)} %) excede el límite de "
        f"{_n(limit_pct, 2)} % con cable {trunk_size}; acorte el recorrido o use un calibre mayor.",
    )


def drop_cable_finding(micro: MicroLimits, *, size: str, length_m: float) -> MicroFinding | None:
    """MIC-014: the cable from a unit's AC sub connector within the manufacturer's length."""
    longest = micro.max_drop_cable_length_m.get(size)
    if longest is None:
        if not micro.max_drop_cable_length_m:
            return None
        allowed = ", ".join(micro.max_drop_cable_length_m)
        return MicroFinding(
            "MIC-014",
            Severity.ERROR,
            micro.name,
            f"El fabricante del microinversor {micro.name} no da longitud para cable {size} "
            f"desde el conector de CA (calibres con dato: {allowed}).",
        )
    if length_m <= longest:
        return None
    return MicroFinding(
        "MIC-014",
        Severity.ERROR,
        micro.name,
        f"El cable {size} desde el conector de CA del microinversor {micro.name} mide "
        f"{_g(length_m)} m y el fabricante permite como máximo {_g(longest)} m.",
    )
