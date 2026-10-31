"""Balance of system v1: string overcurrent protection, inverter-output breaker and conductors.

Sources (vault notes "PV Overcurrent Protection and Disconnects" and "PV Conductor Sizing and
Voltage Drop", NOM-001-SEDE-2012; tables in :mod:`pvsld.core.tables`):

* **String OCPD needed?** NOM 690-9(a): none when the other sources cannot push more than the
  module's maximum series fuse through a string, practical test ``(N_p - 1) x 1.25 x Isc >
  max_series_fuse``. With one or two strings per MPPT input that never happens for a compliant
  module, so no string breaker is selected ("none for <= 2 strings per input"). The designer can
  ask for one per string anyway (``dc_ocpd: always``).
* **String breaker.** ``rating >= 1.25 x 1.25 x Isc`` (690-8(b)(1) on the 690-8(a)(1) current),
  ``rating <= max series fuse`` of the module, a rated voltage at or above Voc(T_min) of the string
  (the smallest offered one is chosen) and a breaking capacity from
  ``DcBreaker.breaking_capacity_ka(poles, voltage)``. The smallest adequate rating wins.
* **String fuse (gPV).** The default device of the DC protection box (owner decision 2026-10-08),
  in a two-pole fuse-disconnector: the same current window as the breaker (``>= 1.56 x Isc``, which
  is also the manufacturer's rule for ganged holders, ``<= max series fuse``), a rated voltage at or
  above Voc(T_min) and an interrupting rating above the fault current. The smallest wins.
* **Inverter-output OCPD.** The next standard rating (240-6(a)) >= 1.25 x the inverter's maximum
  AC current (690-8(b)(1)).
* **Conductors** (copper, 75 and 90 degC columns of Table 310-15(b)(16); the tables of
  ``pvsld.core.tables`` carry the "verify before real use" warning of that module). The smallest
  size with: ampacity at 75 degC >= 1.25 x I_max (CON-001), ampacity at 90 degC after temperature
  and bundling corrections >= I_max (CON-002), protected by its OCPD per 240-4(b)/(d) (CON-003,
  AC only), and a voltage drop within the project limit (VD-001/VD-002).

* **Raceways** (Stage 3.4b, owner choices 2026-10-09: EMT; PV cable on the DC side, THHW-LS on
  the AC side). The DC raceway holds both conductors of every string, the AC raceway the phases
  and the neutral; one bare grounding conductor each (250-122(c)). Areas: the cable datasheet
  diameter (PV cable, Chapter 10 Table 1 note 5), Table 5 for THHW (THHW-LS has no row of its own)
  and Table 8 for the bare conductor. The smallest EMT of Table 4 within the Table 1 fill wins.

* **Switchgear from the catalogue** (owner 2026-10-09): ITM-1 is the next AC breaker rating up
  with an interrupting rating at least the service's fault current (110-9); ITM-P keeps the
  service's rating and takes its device and kAIC; the box disconnect is a PV switch-disconnector
  with two poles per string (four in series for one string) rated at Voc(T_min) for the string's
  maximum current. With nothing fitting, the earlier assumptions stay and a warning says so.

The grounding conductor keeps the circuit conductor's size (owner decision 2026-10-09, no Table
250-122 sizing) and the inverter's maximum output OCPD is not checked (OCP-005 dropped).

TODO: external DC disconnects for inverters without an integrated switch when there is no
protection box (DIS-001), aluminium conductors and multiple inverters.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pvsld.catalogue import AcBreaker, Cable, DcBreaker, DcFuse, DcSwitch
from pvsld.catalogue import PVModule as CatalogueModule
from pvsld.core import calc
from pvsld.core.severity import Severity
from pvsld.core.tables import NomTables
from pvsld.sizing.models import (
    ConductorChoice,
    DcOcpdChoice,
    DeviceChoice,
    Issue,
    RacewayChoice,
)

STRING_BREAKER_POLES = 2
"""A string is a two-wire circuit: both conductors are opened (ungrounded array, NOM 690-35)."""
STRING_FUSE_POLES = 2
"""One gPV fuse per conductor of the string, in a two-pole fuse-disconnector."""
LARGEST_SIZE = "4/0 AWG"

TODO_STAGE_3_4B = (
    "EGC del mismo calibre que el conductor del circuito (decisión del propietario).",
    "Desconectador de CD externo si el inversor no lo integra y no hay caja de protecciones "
    "(DIS-001, pendiente).",
)


def _n(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}"


def _g(value: float) -> str:
    return f"{value:g}"


# --- String overcurrent protection -------------------------------------------------------------


def string_ocpd_required(
    module: CatalogueModule, isc_a: float, strings_per_input: int
) -> tuple[bool, str]:
    """NOM 690-9(a) practical test: ``(N_p - 1) x 1.25 x Isc > max_series_fuse`` -> OCPD needed."""
    backfeed_a = (strings_per_input - 1) * calc.ISC_SAFETY_FACTOR * isc_a
    limit_a = module.max_series_fuse_a
    if backfeed_a > limit_a:
        return True, (
            f"({strings_per_input} - 1) × 1.25 × {_n(isc_a, 2)} A = {_n(backfeed_a)} A supera el "
            f"fusible máximo del módulo ({_g(limit_a)} A): se requiere protección por rama "
            f"(NOM 690-9(a))."
        )
    if strings_per_input == 1:
        return False, (
            "No se requiere: una sola rama por entrada de MPPT, sin fuentes en paralelo "
            "(NOM 690-9(a))."
        )
    return False, (
        f"No se requiere: ({strings_per_input} - 1) × 1.25 × {_n(isc_a, 2)} A = "
        f"{_n(backfeed_a)} A no supera el fusible máximo del módulo ({_g(limit_a)} A) "
        f"(NOM 690-9(a); con 1 o 2 ramas por entrada no se requiere)."
    )


@dataclass(frozen=True)
class StringNeed:
    """What the protection of one string must meet (the criteria of the module docstring)."""

    minimum_a: float
    """1.25 x 1.25 x Isc (690-8(b)(1) on the 690-8(a)(1) current)."""
    maximum_a: float
    """The module's maximum series fuse."""
    voc_cold_v: float
    """Voc of the string at the lowest temperature: the least rated voltage."""
    fault_a: float
    """Current the protection may have to interrupt: every string of the input."""


def string_need(
    *, max_series_fuse_a: float, isc_a: float, strings_per_input: int, voc_cold_string_v: float
) -> StringNeed:
    """The :class:`StringNeed` of a string of ``isc_a`` with ``strings_per_input`` on its input."""
    return StringNeed(
        minimum_a=calc.ocpd_min_a(calc.ISC_SAFETY_FACTOR * isc_a),
        maximum_a=max_series_fuse_a,
        voc_cold_v=voc_cold_string_v,
        fault_a=strings_per_input * isc_a,
    )


def string_rating_problem(rating_a: float, need: StringNeed) -> str | None:
    """Why ``rating_a`` cannot protect the string (NOM 690-8(b)(1), 690-9), or ``None``."""
    if rating_a < need.minimum_a:
        return f"{_g(rating_a)} A < {_n(need.minimum_a)} A (1.25 × 1.25 × Isc)"
    if rating_a > need.maximum_a:
        return f"{_g(rating_a)} A > {_g(need.maximum_a)} A (fusible máximo)"
    return None


def fuse_problem(fuse: DcFuse, need: StringNeed) -> str | None:
    """Why the catalogue gPV ``fuse`` cannot protect the string, or ``None`` when it can."""
    if (why := string_rating_problem(fuse.rated_current_a, need)) is not None:
        return why
    if fuse.rated_voltage_v < need.voc_cold_v:
        return f"{_g(fuse.rated_voltage_v)} V < {_n(need.voc_cold_v)} V"
    if fuse.breaking_capacity_ka * 1000 < need.fault_a:
        return f"capacidad interruptiva {_g(fuse.breaking_capacity_ka)} kA < {_n(need.fault_a)} A"
    return None


def breaker_fit(breaker: DcBreaker, need: StringNeed) -> tuple[str | None, float, float]:
    """``(why, ue_v, icu_ka)`` of a two-pole catalogue ``breaker`` on the string.

    ``why`` is ``None`` when the breaker can protect it; ``ue_v`` is then the smallest rated
    voltage at or above Voc(T_min) and ``icu_ka`` its breaking capacity there.
    """
    if (why := string_rating_problem(breaker.rated_current_a, need)) is not None:
        return why, 0.0, 0.0
    voltages = [v for v in breaker.ue_options_v(STRING_BREAKER_POLES) if v >= need.voc_cold_v]
    if not voltages:
        return (
            f"sin tensión nominal >= {_n(need.voc_cold_v)} V en {STRING_BREAKER_POLES}P",
            0.0,
            0.0,
        )
    icu_ka = breaker.breaking_capacity_ka(STRING_BREAKER_POLES, need.voc_cold_v)
    if icu_ka is None or icu_ka * 1000 < need.fault_a:
        shown = "sin dato" if icu_ka is None else f"{_g(icu_ka)} kA"
        return f"capacidad de ruptura {shown} < {_n(need.fault_a)} A de falla", 0.0, 0.0
    return None, min(voltages), icu_ka


def _need_of(
    module: CatalogueModule, isc_a: float, strings_per_input: int, voc_cold_string_v: float
) -> StringNeed:
    return string_need(
        max_series_fuse_a=module.max_series_fuse_a,
        isc_a=isc_a,
        strings_per_input=strings_per_input,
        voc_cold_string_v=voc_cold_string_v,
    )


def _window_es(need: StringNeed) -> str:
    return f"(>= {_n(need.minimum_a)} A, <= {_g(need.maximum_a)} A, >= {_n(need.voc_cold_v)} V)"


def breaker_for(need: StringNeed, breakers: Sequence[DcBreaker], reason_es: str) -> DcOcpdChoice:
    """The smallest catalogue breaker that meets ``need`` (a required protection).

    ``device_id`` is ``None`` when none does; ``reason_es`` then explains each exclusion.
    """
    qualified: list[tuple[float, float, str, float]] = []
    rejected: list[tuple[str, str]] = []
    for breaker in sorted(breakers, key=lambda b: b.component_id):
        why, ue_v, icu_ka = breaker_fit(breaker, need)
        if why is None:
            qualified.append((breaker.rated_current_a, ue_v, breaker.component_id, icu_ka))
        else:
            rejected.append((breaker.component_id, why))
    if not qualified:
        details = (
            "; ".join(f"{name}: {why}" for name, why in rejected) or "catálogo sin interruptores"
        )
        return DcOcpdChoice(
            required=True,
            reason_es=(
                f"{reason_es} Ningún interruptor de CD del catálogo cumple {_window_es(need)}: "
                f"{details}."
            ),
            minimum_rating_a=need.minimum_a,
            rejected=tuple(rejected),
        )
    rating, ue_v, name, icu_ka = min(qualified, key=lambda q: q[:3])
    return DcOcpdChoice(
        required=True,
        reason_es=reason_es,
        device_id=name,
        rating_a=rating,
        ue_v=ue_v,
        poles=STRING_BREAKER_POLES,
        icu_ka=icu_ka,
        minimum_rating_a=need.minimum_a,
        rejected=tuple(rejected),
    )


def fuse_for(need: StringNeed, fuses: Sequence[DcFuse], reason_es: str) -> DcOcpdChoice:
    """The smallest catalogue gPV fuse that meets ``need`` (a required protection).

    Like :func:`breaker_for`: ``device_id`` ``None`` with every exclusion when none does.
    """
    qualified: list[tuple[float, str, DcFuse]] = []
    rejected: list[tuple[str, str]] = []
    for fuse in sorted(fuses, key=lambda f: f.component_id):
        why = fuse_problem(fuse, need)
        if why is None:
            qualified.append((fuse.rated_current_a, fuse.component_id, fuse))
        else:
            rejected.append((fuse.component_id, why))
    if not qualified:
        details = "; ".join(f"{name}: {why}" for name, why in rejected) or "catálogo sin fusibles"
        return DcOcpdChoice(
            required=True,
            reason_es=(
                f"{reason_es} Ningún fusible gPV del catálogo cumple {_window_es(need)}: {details}."
            ),
            device="fuse",
            minimum_rating_a=need.minimum_a,
            rejected=tuple(rejected),
        )
    rating, name, fuse = min(qualified, key=lambda q: q[:2])
    return DcOcpdChoice(
        required=True,
        reason_es=reason_es,
        device_id=name,
        device="fuse",
        rating_a=rating,
        ue_v=fuse.rated_voltage_v,
        poles=STRING_FUSE_POLES,
        icu_ka=fuse.breaking_capacity_ka,
        minimum_rating_a=need.minimum_a,
        rejected=tuple(rejected),
    )


def select_string_breaker(
    *,
    module: CatalogueModule,
    isc_a: float,
    strings_per_input: int,
    voc_cold_string_v: float,
    breakers: Sequence[DcBreaker],
    required: bool,
    reason_es: str,
) -> DcOcpdChoice:
    """Choose the smallest catalogue breaker that satisfies the criteria of the module docstring.

    When ``required`` is false nothing is selected. When it is true and no breaker qualifies the
    returned choice has ``device_id`` ``None`` and ``reason_es`` explains each exclusion.
    """
    if not required:
        return DcOcpdChoice(required=False, reason_es=reason_es)
    need = _need_of(module, isc_a, strings_per_input, voc_cold_string_v)
    return breaker_for(need, breakers, reason_es)


def select_string_fuse(
    *,
    module: CatalogueModule,
    isc_a: float,
    strings_per_input: int,
    voc_cold_string_v: float,
    fuses: Sequence[DcFuse],
    required: bool,
    reason_es: str,
) -> DcOcpdChoice:
    """Choose the smallest catalogue gPV fuse that satisfies the criteria of the module docstring.

    Like :func:`select_string_breaker`: nothing when ``required`` is false, ``device_id`` ``None``
    with every exclusion in ``reason_es`` when no fuse qualifies.
    """
    if not required:
        return DcOcpdChoice(required=False, reason_es=reason_es, device="fuse")
    need = _need_of(module, isc_a, strings_per_input, voc_cold_string_v)
    return fuse_for(need, fuses, reason_es)


# --- Raceways (EMT) -----------------------------------------------------------------------------

AC_INSULATION = "THHW-LS"
"""Insulation of the AC circuits (owner choice 2026-10-09); NOM Table 5 gives it as THHW."""
EGC_TYPE = "desnudo"


def pv_cable(size: str, cables: Sequence[Cable]) -> Cable | None:
    """The ``size`` variant of the first PV cable family offering it (by family id)."""
    return next(
        (
            c
            for c in sorted(cables, key=lambda c: (c.family_id, c.component_id))
            if c.application == "pv" and c.size == size
        ),
        None,
    )


def size_raceway(
    *,
    circuit_id: str,
    conductors: int,
    conductor_area_mm2: float | None,
    egc_size: str,
    given_trade_size_mm: float | None,
    insulation: str,
    tables: NomTables,
    cable: Cable | None = None,
) -> tuple[RacewayChoice, list[Issue]]:
    """EMT for ``conductors`` insulated conductors plus one bare grounding conductor.

    With ``given_trade_size_mm`` the designer's size is kept and checked; otherwise the smallest EMT
    of Table 4 within the Table 1 fill is chosen.
    """
    egc = tables.bare_stranded_area_mm2.get(egc_size)
    count = conductors + 1
    limit = calc.fill_limit_pct(count, tables)
    base = {
        "circuit_id": circuit_id,
        "type": "EMT",
        "conductors": count,
        "fill_limit_pct": limit,
        "insulation": insulation,
        "cable_id": cable.component_id if cable else None,
        "outer_diameter_mm": cable.outer_diameter_mm if cable else None,
    }
    if conductor_area_mm2 is None or egc is None:
        what = "del cable PV en el catálogo" if conductor_area_mm2 is None else "del conductor"
        return RacewayChoice(
            **base,
            trade_size_mm=given_trade_size_mm,
            trade_size_in=None,
            area_mm2=None,
            fill_pct=None,
        ), [
            Issue(
                "CON-006",
                Severity.WARNING,
                f"Llenado de la canalización de {circuit_id} sin verificar: falta el diámetro "
                f"{what}.",
                subject=circuit_id,
            )
        ]
    area = conductors * conductor_area_mm2 + egc
    rows = {r[0]: r for r in tables.emt}
    if given_trade_size_mm is not None:
        row = rows.get(int(given_trade_size_mm))
        if row is None or row[0] != given_trade_size_mm:
            return RacewayChoice(
                **base,
                trade_size_mm=given_trade_size_mm,
                trade_size_in=None,
                area_mm2=area,
                fill_pct=None,
            ), [
                Issue(
                    "CON-006",
                    Severity.ERROR,
                    f"EMT {_g(given_trade_size_mm)} mm no es una designación de la Tabla 4.",
                    subject=circuit_id,
                )
            ]
        pct = area / row[3] * 100
        choice = RacewayChoice(
            **base, trade_size_mm=row[0], trade_size_in=row[1], area_mm2=area, fill_pct=pct
        )
        if pct > limit:
            return choice, [
                Issue(
                    "CON-006",
                    Severity.ERROR,
                    f"La canalización EMT {row[0]} mm de {circuit_id} queda llena al {_n(pct)} % "
                    f"(máximo {_g(limit)} %, Capítulo 10, Tabla 1).",
                    subject=circuit_id,
                )
            ]
        return choice, []
    for designation, trade, _diameter, total in tables.emt:
        pct = area / total * 100
        if pct <= limit:
            return RacewayChoice(
                **base,
                trade_size_mm=designation,
                trade_size_in=trade,
                area_mm2=area,
                fill_pct=pct,
            ), []
    return RacewayChoice(
        **base, trade_size_mm=None, trade_size_in=None, area_mm2=area, fill_pct=None
    ), [
        Issue(
            "CON-006",
            Severity.ERROR,
            f"Ningún EMT de la Tabla 4 aloja los {count} conductores de {circuit_id} "
            f"({_n(area)} mm²).",
            subject=circuit_id,
        )
    ]


# --- Catalogue switchgear: AC breakers and the box disconnect ------------------------------------


def ac_breaker_problem(
    breaker: AcBreaker,
    *,
    rating_a: float,
    poles: int,
    voltage_v: float,
    fault_ka: float,
    exact: bool = False,
) -> str | None:
    """Why the catalogue AC ``breaker`` does not fit a position, or ``None`` when it does.

    The position needs ``poles`` poles, a rated voltage of at least ``voltage_v``, ``rating_a``
    (exactly with ``exact``, else at least) and an interrupting rating of ``fault_ka`` (NOM 110-9).
    """
    if breaker.poles != poles:
        return f"{breaker.poles} polos; se requieren {poles}"
    if breaker.voltage_v < voltage_v:
        return f"{_g(breaker.voltage_v)} V < {_g(voltage_v)} V"
    if exact and breaker.rated_current_a != rating_a:
        return f"{_g(breaker.rated_current_a)} A; el servicio es de {_g(rating_a)} A"
    if breaker.rated_current_a < rating_a:
        return f"{_g(breaker.rated_current_a)} A < {_g(rating_a)} A"
    if breaker.interrupting_ka < fault_ka:
        return (
            f"interrumpe {_g(breaker.interrupting_ka)} kA, menos que los {_g(fault_ka)} kA de "
            "falla disponibles"
        )
    return None


def select_ac_breaker(
    *,
    position: str,
    rating_a: float,
    poles: int,
    voltage_v: float,
    fault_ka: float,
    breakers: Sequence[AcBreaker],
    exact: bool = False,
) -> tuple[DeviceChoice | None, list[Issue]]:
    """The catalogue breaker for ``position``: ``rating_a`` (or the next one up unless ``exact``),
    ``poles`` poles, rated for ``voltage_v`` and interrupting at least ``fault_ka`` (NOM 110-9).
    """

    def problem(b: AcBreaker, fault: float) -> str | None:
        return ac_breaker_problem(
            b, rating_a=rating_a, poles=poles, voltage_v=voltage_v, fault_ka=fault, exact=exact
        )

    fitting = sorted(
        (b for b in breakers if problem(b, 0.0) is None),
        key=lambda b: (b.rated_current_a, -b.interrupting_ka, b.component_id),
    )
    strong = [b for b in fitting if problem(b, fault_ka) is None]
    if strong:
        b = strong[0]
        return DeviceChoice(
            position, b.component_id, b.rated_current_a, b.voltage_v, b.interrupting_ka
        ), []
    if fitting:
        b = fitting[0]
        why = (
            f"{b.component_id} interrumpe {_g(b.interrupting_ka)} kA, menos que los "
            f"{_g(fault_ka)} kA de falla disponibles"
        )
    else:
        why = f"ningún interruptor de {poles} polos y {_g(rating_a)} A en el catálogo"
    return None, [
        Issue(
            "OCP-003",
            Severity.WARNING,
            f"{position}: {why}; el kAIC queda igual a la corriente de falla del servicio.",
            subject=position,
        )
    ]


def box_switch_poles_per_string(n_strings: int) -> int:
    """Poles each string goes through: two, or four in series for a single string."""
    return 4 if n_strings == 1 else 2


def box_switch_rating(
    switch: DcSwitch, n_strings: int, voc_cold_string_v: float
) -> tuple[float, float] | None:
    """``(ie_a, voltage step)`` of ``switch`` opening ``n_strings`` strings at Voc(T_min).

    ``None`` when it has too few poles or no rating at that voltage.
    """
    per_string = box_switch_poles_per_string(n_strings)
    if switch.poles < per_string * n_strings:
        return None
    return switch.rating_a(per_string, voc_cold_string_v)


def box_switch_problem(
    switch: DcSwitch, *, n_strings: int, voc_cold_string_v: float, string_i_max_a: float
) -> str | None:
    """Why the catalogue ``switch`` cannot be the box disconnect, or ``None`` when it can."""
    per_string = box_switch_poles_per_string(n_strings)
    if switch.poles < per_string * n_strings:
        return f"{switch.poles} polos; se requieren {per_string * n_strings}"
    rating = box_switch_rating(switch, n_strings, voc_cold_string_v)
    if rating is None:
        return f"sin capacidad a {_n(voc_cold_string_v)} V con {per_string} polos por cadena"
    if rating[0] < string_i_max_a:
        return f"{_g(rating[0])} A < {_n(string_i_max_a)} A a {_g(rating[1])} V"
    return None


def select_box_switch(
    *,
    position: str,
    n_strings: int,
    voc_cold_string_v: float,
    string_i_max_a: float,
    switches: Sequence[DcSwitch],
) -> tuple[DeviceChoice | None, list[Issue]]:
    """The PV switch-disconnector of the box: every string through two poles (four in series for
    a single string), rated at Voc(T_min) for at least the string's maximum current (690-8(a)).
    """
    candidates: list[tuple[float, str, DcSwitch, float]] = []
    for switch in sorted(switches, key=lambda s: s.component_id):
        problem = box_switch_problem(
            switch,
            n_strings=n_strings,
            voc_cold_string_v=voc_cold_string_v,
            string_i_max_a=string_i_max_a,
        )
        rating = box_switch_rating(switch, n_strings, voc_cold_string_v)
        if problem is None and rating is not None:
            candidates.append((rating[0], switch.component_id, switch, rating[1]))
    if not candidates:
        return None, [
            Issue(
                "DIS-001",
                Severity.WARNING,
                f"{position}: ningún seccionador del catálogo abre {n_strings} cadena(s) de "
                f"{_n(string_i_max_a)} A a {_n(voc_cold_string_v)} V; se toman los valores de la "
                "protección de cadena.",
                subject=position,
            )
        ]
    ie_a, name, switch, step_v = min(candidates, key=lambda c: c[:2])
    poles_per_string = box_switch_poles_per_string(n_strings)
    return DeviceChoice(
        position,
        name,
        ie_a,
        step_v,
        note_es=f"{poles_per_string} polos por cadena, {switch.utilization_category}",
        poles=switch.poles,  # the switch uses all of its poles (four in series for one string)
    ), []


# --- Inverter-output OCPD ----------------------------------------------------------------------


def ac_ocpd_rating_a(iac_max_a: float, tables: NomTables) -> float:
    """Next standard rating >= 1.25 x the inverter's maximum AC current (690-8(b)(1), 240-6(a))."""
    return calc.next_standard_ocpd_a(calc.ocpd_min_a(iac_max_a), tables)


# --- Conductors ---------------------------------------------------------------------------------


def size_conductor(
    *,
    circuit_id: str,
    kind: str,
    i_max_a: float,
    ocpd_a: float | None,
    current_carrying: int,
    ambient_c: float,
    clearance_mm: float | None,
    length_m: float,
    drop_pct: Callable[[str], float],
    drop_limit_pct: float,
    drop_rule: str,
    tables: NomTables,
    given_size: str | None = None,
) -> tuple[ConductorChoice | None, list[Issue]]:
    """Smallest copper size that satisfies ampacity, protection and voltage drop for one circuit.

    ``drop_pct(size)`` returns the voltage drop in percent for a size. Returns ``(None, [error])``
    when no size up to 4/0 AWG is safe, and a warning (with the largest size) when only the voltage
    drop cannot be met. A ``given_size`` (fixed by the user) is checked instead of chosen: an error
    when it is not safe (naming the smallest safe size), a warning when its drop is too high.
    """
    adder = calc.rooftop_adder_c(clearance_mm, tables) if clearance_mm is not None else 0.0
    t_effective = ambient_c + adder
    k_temp = calc.temperature_correction(t_effective, tables)
    k_fill = calc.bundling_factor(current_carrying, tables)
    safe: list[tuple[str, float, float]] = []
    for size in tables.ampacity_cu:
        a75 = calc.ampacity_a(size, 75, tables)
        a90 = calc.ampacity_a(size, 90, tables)
        corrected = a90 * k_temp * k_fill
        if a75 < calc.ocpd_min_a(i_max_a) or corrected < i_max_a:
            continue
        if ocpd_a is not None:
            limit = calc.max_ocpd_for_ampacity_a(min(a75, corrected), tables)
            small = tables.small_conductor_ocpd_limit_a.get(size)
            if ocpd_a > limit or (small is not None and ocpd_a > small):
                continue
        safe.append((size, a75, corrected))
    if not safe:
        return None, [
            Issue(
                "CON-002",
                Severity.ERROR,
                f"Ningún calibre de cobre hasta {LARGEST_SIZE} soporta {_n(i_max_a)} A en el "
                f"circuito {circuit_id} a {_g(t_effective)} °C con {current_carrying} conductores "
                f"(factores {_n(k_temp, 2)} y {_n(k_fill, 2)}).",
                subject=circuit_id,
            )
        ]
    issues: list[Issue] = []
    if given_size is not None:
        fixed = next((c for c in safe if c[0] == given_size), None)
        if fixed is None:
            return None, [
                Issue(
                    "CON-002",
                    Severity.ERROR,
                    f"El calibre {given_size} fijado para el circuito {circuit_id} no soporta "
                    f"{_n(i_max_a)} A a {_g(t_effective)} °C con su protección; el mínimo es "
                    f"{safe[0][0]}.",
                    subject=circuit_id,
                )
            ]
        safe = [fixed]
    chosen = next((c for c in safe if drop_pct(c[0]) <= drop_limit_pct), None)
    if chosen is None:
        chosen = safe[-1]
        issues.append(
            Issue(
                drop_rule,
                Severity.WARNING,
                f"La caída de tensión del circuito {circuit_id} ({_n(drop_pct(chosen[0]), 2)} %) "
                f"excede el límite de {_n(drop_limit_pct, 2)} % incluso con {chosen[0]} a "
                f"{_g(length_m)} m; acorte el recorrido o suba la tensión.",
                subject=circuit_id,
            )
        )
    size, a75, corrected = chosen
    return (
        ConductorChoice(
            circuit_id=circuit_id,
            kind=kind,
            size=size,
            i_max_a=i_max_a,
            ocpd_a=ocpd_a,
            ampacity_75_a=a75,
            ampacity_corrected_a=corrected,
            vd_pct=drop_pct(size),
            vd_limit_pct=drop_limit_pct,
            length_m=length_m,
        ),
        issues,
    )
