"""Deterministic engineering calculations (ADR-0001, layer L2).

Everything here is a pure function of the parameter model and of the NOM tables of
:mod:`pvsld.core.tables`. Only ``+ - * /``, comparisons and ``sqrt`` are used on floats, so a result
is bit-identical on every platform and drawings built from it can be golden-tested byte for byte.

Formulas and worked examples come from the vault notes "Temperature-Corrected Voc",
"PV String Sizing" and "PV Conductor Sizing and Voltage Drop"; the tests reproduce them.

Assumptions flagged on the sheet:

* ``T_cell,max = T_amb,max + 35 degC`` (rooftop mounting, ``DELTA_T_CELL_C``).
* The module data model has no V_mp temperature coefficient, so the Pmax coefficient is used as a
  conservative proxy for it.
* Voltage drop uses the resistance only (cos phi close to 1), as in the vault's worked example.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pvsld.core.model import Circuit, Module, Ocpd, PvSystemSpec, StringSpec
from pvsld.core.tables import NomTables, get_tables

VocMethod = Literal["coefficient", "table_690_7", "iec_default_1_2"]

DELTA_T_CELL_C = 35.0
"""Cell temperature rise over the ambient maximum (rooftop array, vault worked example)."""
ISC_SAFETY_FACTOR = 1.25
"""NOM 690-8(a)(1): maximum current is 125 % of the short-circuit current."""
DWELLING_VOLTAGE_LIMIT_V = 600.0
"""NOM 690-7(c): PV system voltage in one- and two-family dwellings."""
IEC_DEFAULT_VOC_FACTOR = 1.2


# --- Temperature-corrected Voc ---------------------------------------------------------------


def voc_factor_table_690_7(t_min_c: float, tables: NomTables | None = None) -> float:
    """Correction factor of NOM Table 690-7 for the lowest ambient ``t_min_c`` (rounded colder)."""
    tables = tables or get_tables("NOM-001-SEDE-2012")
    if t_min_c >= 25:
        return 1.0
    if t_min_c < -40:
        raise ValueError(f"Table 690-7 stops at -40 degC, got {t_min_c:g}")
    floor = math.floor(t_min_c)
    for lowest_in_band, factor in tables.voc_table_690_7:
        if floor >= lowest_in_band:
            return factor
    raise ValueError(f"no Table 690-7 band for {t_min_c:g} degC")  # pragma: no cover


def voc_max_module_v(module: Module, t_min_c: float, method: VocMethod) -> float:
    """Open-circuit voltage of one module at the lowest ambient temperature."""
    if method == "coefficient":
        if module.beta_voc_pct_c is None:
            raise ValueError(
                f"module {module.id}: beta_voc_pct_c is required by the coefficient method"
            )
        return module.voc_v * (1 + module.beta_voc_pct_c / 100 * (t_min_c - 25))
    if method == "table_690_7":
        return module.voc_v * voc_factor_table_690_7(t_min_c)
    return module.voc_v * IEC_DEFAULT_VOC_FACTOR


def voc_max_string_v(module: Module, n_series: int, t_min_c: float, method: VocMethod) -> float:
    """Maximum system voltage of a string of ``n_series`` modules (NOM 690-7)."""
    return n_series * voc_max_module_v(module, t_min_c, method)


# --- String sizing ---------------------------------------------------------------------------


def t_cell_max_c(t_amb_max_c: float, delta_t_c: float = DELTA_T_CELL_C) -> float:
    """Highest cell temperature: ambient maximum plus the mounting rise."""
    return t_amb_max_c + delta_t_c


def vmp_module_v(module: Module, t_cell_c: float) -> float:
    """Module V_mp at cell temperature ``t_cell_c`` (Pmax coefficient as a V_mp proxy)."""
    return module.vmp_v * (1 + module.gamma_pmax_pct_c / 100 * (t_cell_c - 25))


def vmp_hot_string_v(
    module: Module, n_series: int, t_amb_max_c: float, delta_t_c: float = DELTA_T_CELL_C
) -> float:
    """String V_mp at the highest cell temperature (STR-001 lower MPPT bound)."""
    return n_series * vmp_module_v(module, t_cell_max_c(t_amb_max_c, delta_t_c))


def vmp_cold_string_v(module: Module, n_series: int, t_min_c: float) -> float:
    """String V_mp at the lowest ambient temperature (STR-002 upper MPPT bound)."""
    return n_series * vmp_module_v(module, t_min_c)


@dataclass(frozen=True)
class StringLimits:
    """Allowed number of modules in series for one string (vault: PV String Sizing)."""

    n_max: int
    n_min_operating: int
    n_min_full_power: int


def string_limits(spec: PvSystemSpec, string: StringSpec) -> StringLimits:
    """Module-count window of ``string`` on its inverter MPPT."""
    module = next(m for m in spec.modules if m.id == string.module)
    inverter = next(i for i in spec.inverters if i.id == string.inverter)
    mppt = next(m for m in inverter.mppt if m.id == string.mppt)
    site = spec.project.site
    voc_module = voc_max_module_v(module, site.t_min_c, spec.standards.voc_method)
    ceiling = min(inverter.vdc_max_v, float(module.max_system_voltage_v))
    if site.occupancy.startswith("vivienda"):
        ceiling = min(ceiling, DWELLING_VOLTAGE_LIMIT_V)
    vmp_hot = vmp_module_v(module, t_cell_max_c(site.t_amb_max_c))
    return StringLimits(
        n_max=math.floor(ceiling / voc_module),
        n_min_operating=math.ceil(mppt.vmin_v / vmp_hot),
        n_min_full_power=math.ceil(mppt.vmin_full_power_v / vmp_hot),
    )


def isc_input_a(isc_a: float, n_parallel: int) -> float:
    """Short-circuit current at an MPPT input with ``n_parallel`` strings (NOM 690-8(a)(1))."""
    return n_parallel * ISC_SAFETY_FACTOR * isc_a


# --- Overcurrent protection and conductors ---------------------------------------------------


def ocpd_min_a(i_max_a: float) -> float:
    """Minimum OCPD rating: 125 % of the maximum circuit current (NOM 690-8(b)(1))."""
    return ISC_SAFETY_FACTOR * i_max_a


def next_standard_ocpd_a(amps: float, tables: NomTables | None = None) -> float:
    """Smallest standard rating that is at least ``amps`` (NOM 240-6(a))."""
    tables = tables or get_tables("NOM-001-SEDE-2012")
    for rating in tables.standard_ocpd_a:
        if rating >= amps:
            return rating
    raise ValueError(f"{amps:g} A exceeds the largest standard OCPD rating")


def max_ocpd_for_ampacity_a(ampacity_a: float, tables: NomTables | None = None) -> float:
    """Largest OCPD that protects ``ampacity_a`` (NOM 240-4(b): next standard size up to 800 A)."""
    tables = tables or get_tables("NOM-001-SEDE-2012")
    if ampacity_a <= 800:
        return next_standard_ocpd_a(ampacity_a, tables)
    return max(r for r in tables.standard_ocpd_a if r <= ampacity_a)


def ampacity_a(size: str, column_c: Literal[60, 75, 90], tables: NomTables | None = None) -> float:
    """Copper ampacity of ``size`` in the 60, 75 or 90 degC column (Table 310-15(b)(16))."""
    tables = tables or get_tables("NOM-001-SEDE-2012")
    try:
        columns = tables.ampacity_cu[size]
    except KeyError:
        raise KeyError(f"no ampacity for conductor size {size!r}") from None
    return columns[(60, 75, 90).index(column_c)]


def temperature_correction(ambient_c: float, tables: NomTables | None = None) -> float:
    """90 degC column correction factor; 0 above the last band (no usable ampacity)."""
    tables = tables or get_tables("NOM-001-SEDE-2012")
    for highest_in_band, factor in tables.temp_correction_90c:
        if ambient_c <= highest_in_band:
            return factor
    return 0.0


def rooftop_adder_c(clearance_mm: float, tables: NomTables | None = None) -> float:
    """Temperature adder for a raceway ``clearance_mm`` above a sunlit roof."""
    tables = tables or get_tables("NOM-001-SEDE-2012")
    for largest_clearance, adder in tables.rooftop_adder_c:
        if clearance_mm <= largest_clearance:
            return adder
    return 0.0


def bundling_factor(current_carrying: int, tables: NomTables | None = None) -> float:
    """Adjustment factor for ``current_carrying`` conductors in one raceway."""
    tables = tables or get_tables("NOM-001-SEDE-2012")
    for largest_count, factor in tables.bundling:
        if current_carrying <= largest_count:
            return factor
    return 0.35


def conductor_area_mm2(diameter_mm: float) -> float:
    """Cross-section (mm2) of a round conductor of overall ``diameter_mm``, insulation included."""
    return math.pi / 4 * diameter_mm**2


def fill_limit_pct(conductors: int, tables: NomTables | None = None) -> float:
    """Largest share of the conduit area the conductors may take (Chapter 10, Table 1)."""
    one, two, more = (tables or get_tables("NOM-001-SEDE-2012")).conduit_fill_pct
    return one if conductors == 1 else two if conductors == 2 else more


def smallest_emt(
    areas_mm2: list[float], tables: NomTables | None = None
) -> tuple[int, str, float] | None:
    """The smallest EMT (Table 4) the conductors fit in: (designation mm, trade size, fill %).

    ``None`` when not even the largest EMT of the table holds them.
    """
    tables = tables or get_tables("NOM-001-SEDE-2012")
    limit = fill_limit_pct(len(areas_mm2), tables)
    total = sum(areas_mm2)
    for designation, trade, _diameter, area in tables.emt:
        if total / area * 100 <= limit:
            return designation, trade, total / area * 100
    return None


@dataclass(frozen=True)
class RacewayFill:
    """The conductors one raceway holds: the circuits that share it and their total area."""

    circuit_ids: tuple[str, ...]
    conductors: int
    area_mm2: float
    missing: tuple[str, ...]
    """Conductors whose dimensions are unknown (left out of ``area_mm2``)."""


def insulated_area_mm2(
    size: str, insulation: str, outer_diameter_mm: float | None, tables: NomTables
) -> float | None:
    """Area of an insulated conductor: the datasheet diameter, else Table 5 for TW/THW/THHW."""
    if outer_diameter_mm is not None:
        return conductor_area_mm2(outer_diameter_mm)
    if any(kind in insulation.upper() for kind in ("THHW", "THW", "TW")):
        diameter = tables.thhw_diameter_mm.get(size)
        return conductor_area_mm2(diameter) if diameter is not None else None
    return None


def egc_area_mm2(size: str, egc_type: str, tables: NomTables) -> float | None:
    """Area of an equipment grounding conductor: Table 8 when bare, else as insulated."""
    if egc_type.casefold() in ("desnudo", "bare", "desnuda"):
        return tables.bare_stranded_area_mm2.get(size)
    return insulated_area_mm2(size, egc_type, None, tables)


def raceway_fill(spec: PvSystemSpec, owner: Circuit, tables: NomTables) -> RacewayFill:
    """Conductors in the raceway of ``owner`` and of every circuit that shares it (``ref``).

    One equipment grounding conductor serves the raceway, the largest of its circuits (NOM
    250-122(c)); the phase conductors and neutrals of every circuit count.
    """
    members = [owner, *(c for c in spec.circuits if c.raceway.ref == owner.id)]
    areas: list[float] = []
    missing: list[str] = []
    for circuit in members:
        wires = circuit.conductors
        area = insulated_area_mm2(wires.size, wires.insulation, wires.outer_diameter_mm, tables)
        count = wires.qty + (1 if circuit.neutral is not None else 0)
        if area is None:
            missing.append(f"{circuit.id}: {wires.size} {wires.insulation}")
        else:
            areas += [area] * count
    egcs = [(egc_area_mm2(c.egc.size, c.egc.type, tables), c) for c in members]
    known = [area for area, _c in egcs if area is not None]
    if known:
        areas.append(max(known))
    else:
        missing += [f"{c.id}: tierra {c.egc.size} {c.egc.type}" for _a, c in egcs]
    return RacewayFill(
        tuple(c.id for c in members),
        len(areas) + sum(1 for _ in missing),
        sum(areas),
        tuple(missing),
    )


def voltage_drop_dc_pct(
    size: str, length_m: float, current_a: float, voltage_v: float, tables: NomTables
) -> float:
    """DC two-wire voltage drop in percent of ``voltage_v`` (resistance only)."""
    resistance = tables.resistance_dc_ohm_km[size] / 1000
    return 100 * (2 * length_m * current_a * resistance) / voltage_v


def voltage_drop_ac_pct(
    size: str,
    length_m: float,
    current_a: float,
    voltage_v: float,
    phases: int,
    tables: NomTables,
) -> float:
    """AC voltage drop in percent: two-wire for 1 or 2 energized conductors, three-phase for 3."""
    resistance = tables.resistance_ac_pvc_ohm_km[size] / 1000
    factor = math.sqrt(3) if phases == 3 else 2.0
    return 100 * (factor * length_m * current_a * resistance) / voltage_v


# --- Derived values of a whole system --------------------------------------------------------


@dataclass(frozen=True)
class StringValues:
    """Derived electrical values of one string (feeds VOLT/STR rules and the string table)."""

    string_id: str
    module_id: str
    inverter_id: str
    mppt_id: str
    n_series: int
    n_parallel: int
    voc_max_module_v: float
    voc_max_string_v: float
    vmp_stc_string_v: float
    vmp_hot_string_v: float
    vmp_cold_string_v: float
    isc_a: float
    imp_a: float
    p_stc_w: float
    isc_input_a: float
    limits: StringLimits


@dataclass(frozen=True)
class CircuitValues:
    """Derived values of one circuit (feeds CON rules and the conductor schedule).

    ``i_max_a``, the ampacity fields and ``vd_pct`` are ``None`` for circuit kinds whose current
    is not computed in this version (combiner outputs, feeders, batteries).
    """

    circuit_id: str
    kind: str
    size: str
    i_max_a: float | None
    ampacity_75_a: float
    ampacity_90_a: float
    t_effective_c: float | None
    k_temp: float | None
    k_fill: float | None
    ampacity_corrected_a: float | None
    ocpd_id: str | None
    ocpd_rating_a: float | None
    ocpd_min_a: float | None
    vd_pct: float | None


@dataclass(frozen=True)
class Derived:
    """Everything the rules and the diagram builder need that is not an input."""

    kwp_total: float
    kwac_total: float
    dc_ac_ratio: float
    t_cell_max_c: float
    strings: tuple[StringValues, ...]
    circuits: tuple[CircuitValues, ...]


def _base_id(endpoint: str) -> str:
    return endpoint.split(".", maxsplit=1)[0]


def protecting_ocpd(spec: PvSystemSpec, circuit: Circuit) -> Ocpd | None:
    """The OCPD a circuit is wired to (either endpoint), if any."""
    by_id = {o.id: o for o in spec.ac_bos.ocpds}
    for endpoint in (circuit.to, circuit.from_):
        found = by_id.get(_base_id(endpoint))
        if found is not None:
            return found
    return None


def _string_values(spec: PvSystemSpec) -> tuple[StringValues, ...]:
    site = spec.project.site
    method = spec.standards.voc_method
    modules = {m.id: m for m in spec.modules}
    parallel: dict[tuple[str, str], int] = {}
    for string in spec.strings:
        key = (string.inverter, string.mppt)
        parallel[key] = parallel.get(key, 0) + 1

    values = []
    for string in spec.strings:
        module = modules[string.module]
        n_parallel = parallel[(string.inverter, string.mppt)]
        values.append(
            StringValues(
                string_id=string.id,
                module_id=module.id,
                inverter_id=string.inverter,
                mppt_id=string.mppt,
                n_series=string.n_series,
                n_parallel=n_parallel,
                voc_max_module_v=voc_max_module_v(module, site.t_min_c, method),
                voc_max_string_v=voc_max_string_v(module, string.n_series, site.t_min_c, method),
                vmp_stc_string_v=string.n_series * module.vmp_v,
                vmp_hot_string_v=vmp_hot_string_v(module, string.n_series, site.t_amb_max_c),
                vmp_cold_string_v=vmp_cold_string_v(module, string.n_series, site.t_min_c),
                isc_a=module.isc_a,
                imp_a=module.imp_a,
                p_stc_w=string.n_series * module.pmax_w,
                isc_input_a=isc_input_a(module.isc_a, n_parallel),
                limits=string_limits(spec, string),
            )
        )
    return tuple(values)


def _circuit_values(
    spec: PvSystemSpec, strings: tuple[StringValues, ...], tables: NomTables
) -> tuple[CircuitValues, ...]:
    by_circuit = {c.id: c for c in spec.circuits}
    string_values = {s.string_id: s for s in strings}
    inverters = {i.id: i for i in spec.inverters}
    site = spec.project.site
    values = []
    for circuit in spec.circuits:
        size = circuit.conductors.size
        source = string_values.get(_base_id(circuit.from_))
        inverter = inverters.get(_base_id(circuit.from_))

        i_max: float | None = None
        vd_pct: float | None = None
        if circuit.kind == "pv_source" and source is not None:
            i_max = ISC_SAFETY_FACTOR * source.isc_a
            vd_pct = voltage_drop_dc_pct(
                size, circuit.length_m, source.imp_a, source.vmp_stc_string_v, tables
            )
        elif circuit.kind == "inverter_output" and inverter is not None:
            i_max = inverter.iac_max_a
            vd_pct = voltage_drop_ac_pct(
                size, circuit.length_m, i_max, inverter.vac_v, inverter.phases, tables
            )

        raceway = circuit.raceway
        if raceway.ref is not None:
            raceway = by_circuit[raceway.ref].raceway
        ccc = raceway.ccc_count if raceway.ccc_count is not None else circuit.conductors.qty
        ambient = circuit.ambient_c if circuit.ambient_c is not None else site.t_amb_max_c

        t_effective: float | None = None
        k_temp: float | None = None
        k_fill: float | None = None
        corrected: float | None = None
        a90 = ampacity_a(size, 90, tables)
        if i_max is not None:
            clearance = raceway.rooftop_clearance_mm
            adder = rooftop_adder_c(clearance, tables) if clearance is not None else 0.0
            t_effective = ambient + adder
            k_temp = temperature_correction(t_effective, tables)
            k_fill = bundling_factor(ccc, tables)
            corrected = a90 * k_temp * k_fill

        ocpd = protecting_ocpd(spec, circuit)
        values.append(
            CircuitValues(
                circuit_id=circuit.id,
                kind=circuit.kind,
                size=size,
                i_max_a=i_max,
                ampacity_75_a=ampacity_a(size, 75, tables),
                ampacity_90_a=a90,
                t_effective_c=t_effective,
                k_temp=k_temp,
                k_fill=k_fill,
                ampacity_corrected_a=corrected,
                ocpd_id=ocpd.id if ocpd else None,
                ocpd_rating_a=ocpd.rating_a if ocpd else None,
                ocpd_min_a=next_standard_ocpd_a(ocpd_min_a(i_max), tables)
                if i_max is not None
                else None,
                vd_pct=vd_pct,
            )
        )
    return tuple(values)


def derive_strings(spec: PvSystemSpec) -> Derived:
    """The string part of :func:`derive` alone, for a draft whose AC side is not there yet.

    Reads only ``project.site``, ``standards``, ``modules``, ``inverters`` and ``strings``;
    ``circuits`` stays empty, so only rules that read the strings may run on the result.
    """
    strings = _string_values(spec)
    kwp = sum(s.p_stc_w for s in strings) / 1000
    kwac = sum(i.pac_w * i.qty for i in spec.inverters) / 1000
    return Derived(
        kwp_total=kwp,
        kwac_total=kwac,
        dc_ac_ratio=kwp / kwac,
        t_cell_max_c=t_cell_max_c(spec.project.site.t_amb_max_c),
        strings=strings,
        circuits=(),
    )


def derive(spec: PvSystemSpec) -> Derived:
    """Compute every derived value of ``spec`` (see the vault note PV SLD Parameter Model)."""
    tables = get_tables(spec.standards.nom_edition)
    strings = _string_values(spec)
    kwp = sum(s.p_stc_w for s in strings) / 1000
    kwac = sum(i.pac_w * i.qty for i in spec.inverters) / 1000
    return Derived(
        kwp_total=kwp,
        kwac_total=kwac,
        dc_ac_ratio=kwp / kwac,
        t_cell_max_c=t_cell_max_c(spec.project.site.t_amb_max_c),
        strings=strings,
        circuits=_circuit_values(spec, strings, tables),
    )
