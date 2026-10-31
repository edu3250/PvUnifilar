"""Versioned rule pack ``mx-gd-2026.10`` (S1 subset of the vault note "PV SLD Validation Rules").

Each rule has a stable id, a severity, its basis and citations, the Mexican checklist ids it
implements (MX-xx, vault note "Mexican SLD Submission Requirements") and a Spanish message that
names the component, the computed value and the way out, so Claude can explain a failure and
correct the spec. Rule data (titles, citations, MX ids) is separate from the check functions and is
published through :func:`rulepack_catalogue` for the future ``pvsld://rulepack/mx-gd-2026.10``
MCP resource.

Implemented in this version: VOLT-001, STR-001, STR-004, STR-007, CON-003, CON-006, PCC-002,
MET-001, DIS-003 and DIS-004. The remaining rules of the vault pack (98 in total) arrive with
later stages.

Scope notes, where the vault definition needs data that schema 0.1.0 does not carry:

* MET-001 checks the count, the role and the bidirectional flag of the fiscal meter. The meter
  sits on the utility side of I2 by construction of the layout template.
* STR-007 compares the array STC power with ``inverters[].pdc_max_w`` (the datasheet "recommended
  maximum PV power"; the optimizer-only value only when the design declares optimizers on every
  module) and applies the DC/AC ratio policy of :mod:`pvsld.core.policy` (defaults: warning above
  1.35, error above 1.50, both owner-configurable).
* CON-006 checks EMT raceways only (the Table 4 rows loaded so far); conductor areas come from the
  datasheet diameter in the spec, else Table 5 (TW/THW/THHW), and bare grounding conductors from
  Table 8. One grounding conductor per shared raceway, the largest (250-122(c)).
* DIS-003 checks the ``manual``, ``lockable`` and ``visible_break`` flags only when the spec
  declares them (``null`` means "not declared" and is not an error).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from pvsld.core import calc
from pvsld.core.calc import Derived
from pvsld.core.model import PvSystemSpec
from pvsld.core.policy import DEFAULT_DC_AC_POLICY, DcAcPolicy
from pvsld.core.severity import Severity
from pvsld.core.tables import NomTables, get_tables


@dataclass(frozen=True)
class Cite:
    """A regulatory citation: document and clause."""

    doc: str
    clause: str

    def __str__(self) -> str:
        return f"{self.doc} {self.clause}"


@dataclass(frozen=True)
class Finding:
    """One result of a rule: what failed, where, and how to read it."""

    rule_id: str
    severity: Severity
    message_es: str
    mx_ids: tuple[str, ...]
    subject: str
    cites: tuple[Cite, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "subject": self.subject,
            "message_es": self.message_es,
            "mx_ids": list(self.mx_ids),
            "cites": [str(c) for c in self.cites],
        }


@dataclass(frozen=True)
class RuleContext:
    """Everything a rule may read."""

    spec: PvSystemSpec
    derived: Derived
    tables: NomTables
    policy: DcAcPolicy = DEFAULT_DC_AC_POLICY


@dataclass(frozen=True)
class RuleDef:
    """Declarative metadata of a rule plus its check function."""

    id: str
    title_es: str
    severity: str  # "E", "W" or "E/W" (the check picks one per finding)
    basis: tuple[str, ...]
    cites: tuple[Cite, ...]
    mx_ids: tuple[str, ...]
    check: Callable[[RuleContext], Iterator[tuple[Severity, str, str]]]
    """Yields ``(severity, subject, message_es)`` for each violation."""
    sections: tuple[str, ...] = ()
    """Spec sections the check reads, for draft evaluation (:mod:`pvsld.core.draft`): the rule can
    run on a draft once these are valid. Empty means it needs the whole specification."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title_es": self.title_es,
            "severity": self.severity,
            "basis": list(self.basis),
            "cites": [{"doc": c.doc, "clause": c.clause} for c in self.cites],
            "mx_ids": list(self.mx_ids),
        }


def _n(value: float, decimals: int = 1) -> str:
    """Format a computed number with a decimal point (NOM-008)."""
    return f"{value:.{decimals}f}"


def _g(value: float) -> str:
    """Format a declared number without trailing zeros (``600.0`` -> ``600``)."""
    return f"{value:g}"


# --- VOLT-001 ----------------------------------------------------------------------------------


def _volt_001(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    site = ctx.spec.project.site
    inverters = {i.id: i for i in ctx.spec.inverters}
    for values in ctx.derived.strings:
        inverter = inverters[values.inverter_id]
        if values.voc_max_string_v > inverter.vdc_max_v:
            yield (
                Severity.ERROR,
                values.string_id,
                f"La rama {values.string_id} alcanza {_n(values.voc_max_string_v)} V a "
                f"{_g(site.t_min_c)} °C; el inversor {inverter.id} admite "
                f"{_g(inverter.vdc_max_v)} V. Reduzca los módulos en serie "
                f"(máximo {values.limits.n_max} módulos).",
            )


# --- STR-001 -----------------------------------------------------------------------------------


def _str_001(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    inverters = {i.id: i for i in ctx.spec.inverters}
    for values in ctx.derived.strings:
        inverter = inverters[values.inverter_id]
        mppt = next(m for m in inverter.mppt if m.id == values.mppt_id)
        hot = values.vmp_hot_string_v
        where = f"{inverter.id}.{mppt.id}"
        if hot < mppt.vmin_v:
            yield (
                Severity.ERROR,
                values.string_id,
                f"La rama {values.string_id} entrega {_n(hot)} V con celdas a "
                f"{_g(ctx.derived.t_cell_max_c)} °C; el MPPT {where} arranca en "
                f"{_g(mppt.vmin_v)} V. Aumente los módulos en serie "
                f"(mínimo {values.limits.n_min_operating} módulos).",
            )
        elif hot < mppt.vmin_full_power_v:
            yield (
                Severity.WARNING,
                values.string_id,
                f"La rama {values.string_id} entrega {_n(hot)} V con celdas a "
                f"{_g(ctx.derived.t_cell_max_c)} °C, por debajo de los "
                f"{_g(mppt.vmin_full_power_v)} V de potencia plena del MPPT {where}. "
                f"Para potencia plena use al menos {values.limits.n_min_full_power} módulos.",
            )


# --- STR-004 -----------------------------------------------------------------------------------


def _str_004(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    inverters = {i.id: i for i in ctx.spec.inverters}
    seen: set[tuple[str, str]] = set()
    for values in ctx.derived.strings:
        key = (values.inverter_id, values.mppt_id)
        if key in seen:
            continue
        seen.add(key)
        mppt = next(m for m in inverters[key[0]].mppt if m.id == key[1])
        if values.isc_input_a > mppt.isc_max_a:
            yield (
                Severity.ERROR,
                f"{key[0]}.{key[1]}",
                f"El MPPT {key[0]}.{key[1]} recibe {values.n_parallel} rama(s) con "
                f"{_n(values.isc_input_a)} A de corriente de cortocircuito (1.25 × Isc por "
                f"rama); el límite del inversor es {_g(mppt.isc_max_a)} A.",
            )


# --- STR-007 -----------------------------------------------------------------------------------


def _str_007(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    """Array power within the inverter limit (E); DC/AC ratio against the policy (E/W/I).

    One finding per inverter at most: the power-limit error already reports the ratio.
    ``pdc_max_w`` is the datasheet "recommended maximum PV power" of one inverter; the
    optimizer-only value belongs there only when the design declares optimizers on every module.
    """
    policy = ctx.policy
    for inverter in ctx.spec.inverters:
        strings = [v for v in ctx.derived.strings if v.inverter_id == inverter.id]
        if not strings:
            continue
        p_dc_w = sum(v.p_stc_w for v in strings)
        limit_w = inverter.pdc_max_w * inverter.qty
        pac_w = inverter.pac_w * inverter.qty
        ratio = p_dc_w / pac_w
        where = (
            f"El arreglo de {sum(v.n_series for v in strings)} módulos del inversor {inverter.id}"
            f" suma {_n(p_dc_w / 1000, 2)} kWp (STC)"
        )
        if p_dc_w > limit_w:
            modules = {v.module_id for v in strings}
            hint = ""
            if len(modules) == 1:
                pmax_w = p_dc_w / sum(v.n_series for v in strings)
                hint = f" (como máximo {int(limit_w // pmax_w)} módulos de {_g(pmax_w)} W)"
            yield (
                Severity.ERROR,
                inverter.id,
                f"{where}; el inversor admite como máximo {_n(limit_w / 1000, 2)} kW de potencia "
                f"FV (relación CD/CA {_n(ratio, 2)} sobre {_n(pac_w / 1000, 2)} kW CA). "
                f"Reduzca los módulos{hint} o elija un inversor de mayor potencia.",
            )
            continue
        severity = policy.classify(ratio)
        if severity is Severity.ERROR:
            yield (
                Severity.ERROR,
                inverter.id,
                f"{where} sobre un inversor de {_n(pac_w / 1000, 2)} kW CA: la relación CD/CA es "
                f"{_n(ratio, 2)}, por encima del máximo de diseño {_n(policy.error_above, 2)}. "
                f"Reduzca los módulos o elija un inversor de mayor potencia.",
            )
        elif severity is Severity.WARNING:
            yield (
                Severity.WARNING,
                inverter.id,
                f"{where} sobre un inversor de {_n(pac_w / 1000, 2)} kW CA: la relación CD/CA es "
                f"{_n(ratio, 2)}, por encima de {_n(policy.warn_above, 2)}. Se admite (no excede "
                f"los {_n(limit_w / 1000, 2)} kW del inversor) pero habrá recorte de potencia.",
            )
        elif severity is Severity.INFO:
            yield (
                Severity.INFO,
                inverter.id,
                f"{where} sobre un inversor de {_n(pac_w / 1000, 2)} kW CA: la relación CD/CA es "
                f"{_n(ratio, 2)}, menor que {_n(policy.info_below, 2)}; el inversor queda "
                f"sobredimensionado respecto del arreglo.",
            )


# --- CON-003 -----------------------------------------------------------------------------------


def _con_003(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    for values in ctx.derived.circuits:
        if values.ocpd_id is None or values.ocpd_rating_a is None:
            continue
        corrected = values.ampacity_corrected_a
        usable = corrected if corrected is not None else values.ampacity_75_a
        ampacity = min(values.ampacity_75_a, usable)
        if ampacity <= 0:
            yield (
                Severity.ERROR,
                values.circuit_id,
                f"El conductor {values.size} del circuito {values.circuit_id} no tiene ampacidad "
                f"a la temperatura de diseño ({_g(values.t_effective_c or 0)} °C).",
            )
            continue
        limit = calc.max_ocpd_for_ampacity_a(ampacity, ctx.tables)
        small_limit = ctx.tables.small_conductor_ocpd_limit_a.get(values.size)
        clause = "NOM 240-4(b)"
        if small_limit is not None and small_limit < limit:
            limit = small_limit
            clause = "NOM 240-4(d)"
        if values.ocpd_rating_a > limit:
            yield (
                Severity.ERROR,
                values.circuit_id,
                f"El conductor {values.size} del circuito {values.circuit_id} (ampacidad "
                f"{_g(ampacity)} A) no queda protegido por {values.ocpd_id} de "
                f"{_g(values.ocpd_rating_a)} A: el máximo permitido es {_g(limit)} A "
                f"({clause}). Aumente el calibre o reduzca la protección.",
            )


# --- CON-006 -----------------------------------------------------------------------------------


def _con_006(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    for owner in ctx.spec.circuits:
        raceway = owner.raceway
        if raceway.ref is not None or raceway.type is None or raceway.trade_size_mm is None:
            continue
        if raceway.type.upper() != "EMT":  # Table 4 is loaded for EMT only
            continue
        row = next((r for r in ctx.tables.emt if r[0] == raceway.trade_size_mm), None)
        if row is None:
            sizes = ", ".join(str(r[0]) for r in ctx.tables.emt)
            yield (
                Severity.ERROR,
                owner.id,
                f"La canalización del circuito {owner.id} (EMT {_g(raceway.trade_size_mm)} mm) "
                f"no es una designación de la Tabla 4 del Capítulo 10 ({sizes} mm).",
            )
            continue
        fill = calc.raceway_fill(ctx.spec, owner, ctx.tables)
        shared = ", ".join(fill.circuit_ids)
        if fill.missing:
            yield (
                Severity.WARNING,
                owner.id,
                f"No se puede verificar el llenado de la canalización de {shared}: falta el "
                f"diámetro exterior de {'; '.join(fill.missing)} (dato de la hoja técnica del "
                "cable, outer_diameter_mm).",
            )
            continue
        designation, trade, _diameter, area = row
        pct = fill.area_mm2 / area * 100
        limit = calc.fill_limit_pct(fill.conductors, ctx.tables)
        if pct > limit:
            fits = calc.smallest_emt(
                [fill.area_mm2 / fill.conductors] * fill.conductors, ctx.tables
            )
            way_out = f'use EMT de {fits[0]} mm ({fits[1]}")' if fits else "divida los circuitos"
            yield (
                Severity.ERROR,
                owner.id,
                f'La canalización EMT {designation} mm ({trade}") de {shared} queda llena al '
                f"{_n(pct)} % con {fill.conductors} conductores ({_n(fill.area_mm2)} mm²): el "
                f"máximo es {_g(limit)} % (NOM Capítulo 10, Tabla 1). Para cumplir, {way_out}.",
            )


# --- PCC-002 -----------------------------------------------------------------------------------


def _pcc_002(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    spec = ctx.spec
    if spec.ac_bos.point_of_connection.type != "load_side":
        return
    ratings = {o.id: o.rating_a for o in spec.ac_bos.ocpds}
    ratings.update({b.id: b.rating_a for b in spec.ac_bos.main_breakers})
    for panel in spec.ac_bos.panels:
        sources = [panel.main_ocpd] + [
            o.id for o in spec.ac_bos.ocpds if o.backfed and o.at == panel.id
        ]
        total = sum(ratings[name] for name in dict.fromkeys(sources))
        limit = 1.2 * panel.bus_a
        if total > limit:
            yield (
                Severity.ERROR,
                panel.id,
                f"La suma de las protecciones que alimentan la barra de {panel.id} "
                f"({_g(total)} A: {', '.join(dict.fromkeys(sources))}) excede el 120 % de la "
                f"barra ({_g(panel.bus_a)} A × 1.2 = {_g(limit)} A).",
            )


# --- MET-001 -----------------------------------------------------------------------------------


def _met_001(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    fiscal = [m for m in ctx.spec.ac_bos.meters if m.role == "MF"]
    if not fiscal:
        yield (
            Severity.ERROR,
            "MF",
            "No se declara el medidor fiscal bidireccional (MF) en el punto de interconexión.",
        )
        return
    if len(fiscal) > 1:
        names = ", ".join(m.id for m in fiscal)
        yield (
            Severity.ERROR,
            "MF",
            f"Se declaran {len(fiscal)} medidores MF ({names}); debe haber exactamente uno.",
        )
    for meter in fiscal:
        if not meter.bidirectional:
            yield (
                Severity.ERROR,
                meter.id,
                f"El medidor {meter.id} (MF) debe ser bidireccional para medir la energía "
                f"entregada y la recibida.",
            )


# --- DIS-003 / DIS-004 -------------------------------------------------------------------------


def _dis_003(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    devices = [*ctx.spec.ac_bos.ocpds, *ctx.spec.ac_bos.disconnects]
    i1 = [d for d in devices if d.role == "I1"]
    if not i1:
        yield (
            Severity.ERROR,
            "I1",
            "No se declara el desconectador I1 a la salida de la central (manual, visible, "
            "bloqueable e identificado).",
        )
    for device in i1:
        flags = {
            "manual": getattr(device, "manual", None),
            "visible": getattr(device, "visible_break", None),
            "bloqueable": getattr(device, "lockable", None),
        }
        failed = [name for name, value in flags.items() if value is False]
        if failed:
            yield (
                Severity.ERROR,
                device.id,
                f"El desconectador I1 {device.id} debe ser manual, visible y bloqueable; "
                f"se declara que no es: {', '.join(failed)}.",
            )


def _dis_004(ctx: RuleContext) -> Iterator[tuple[Severity, str, str]]:
    i2_breakers = [b for b in ctx.spec.ac_bos.main_breakers if b.role == "I2"]
    other_i2 = [d for d in [*ctx.spec.ac_bos.ocpds, *ctx.spec.ac_bos.disconnects] if d.role == "I2"]
    if not i2_breakers and not other_i2:
        yield (
            Severity.ERROR,
            "I2",
            "No se declara el desconectador I2 bidireccional del servicio.",
        )
        return
    if not i2_breakers:
        yield (
            Severity.ERROR,
            other_i2[0].id,
            f"El desconectador I2 {other_i2[0].id} debe declararse en ac_bos.main_breakers con "
            f"bidirectional = true para verificar que es bidireccional.",
        )
    for breaker in i2_breakers:
        if not breaker.bidirectional:
            yield (
                Severity.ERROR,
                breaker.id,
                f"El desconectador I2 {breaker.id} del servicio debe ser bidireccional.",
            )


STRING_SECTIONS = ("project.site", "standards", "modules", "inverters", "strings")
"""What the string and voltage rules read: they run on a draft before the AC side exists."""

RULES: tuple[RuleDef, ...] = (
    RuleDef(
        id="VOLT-001",
        title_es="Tensión máxima de la rama ≤ tensión máxima de entrada CD del inversor",
        severity="E",
        basis=("NOM", "MFR"),
        cites=(Cite("NOM-001-SEDE-2012", "690-7(a)"), Cite("NOM-001-SEDE-2012", "110-3(b)")),
        mx_ids=("MX-C02",),
        check=_volt_001,
        sections=STRING_SECTIONS,
    ),
    RuleDef(
        id="STR-001",
        title_es="Tensión Vmp en caliente dentro de la ventana MPPT",
        severity="E/W",
        basis=("MFR",),
        cites=(Cite("NOM-001-SEDE-2012", "110-3(b)"),),
        mx_ids=("MX-C04",),
        check=_str_001,
        sections=STRING_SECTIONS,
    ),
    RuleDef(
        id="STR-004",
        title_es="Corriente de cortocircuito de entrada dentro del límite del inversor",
        severity="E",
        basis=("NOM", "MFR"),
        cites=(Cite("NOM-001-SEDE-2012", "690-8(a)(1)"),),
        mx_ids=("MX-C05",),
        check=_str_004,
        sections=STRING_SECTIONS,
    ),
    RuleDef(
        id="STR-007",
        title_es="Potencia CD del arreglo dentro del límite del inversor y relación CD/CA",
        severity="E/W/I",
        basis=("MFR", "POL"),
        cites=(Cite("NOM-001-SEDE-2012", "110-3(b)"),),
        mx_ids=(),
        check=_str_007,
        sections=STRING_SECTIONS,
    ),
    RuleDef(
        id="CON-003",
        title_es="Conductor protegido por su dispositivo de sobrecorriente",
        severity="E",
        basis=("NOM",),
        cites=(
            Cite("NOM-001-SEDE-2012", "240-4(b)"),
            Cite("NOM-001-SEDE-2012", "240-4(d)"),
            Cite("NOM-001-SEDE-2012", "690-8(b)(2)"),
        ),
        mx_ids=("MX-C07",),
        check=_con_003,
    ),
    RuleDef(
        id="CON-006",
        title_es="Llenado de la canalización (EMT) dentro del porcentaje permitido",
        severity="E/W",
        basis=("NOM",),
        cites=(
            Cite("NOM-001-SEDE-2012", "358-22"),
            Cite("NOM-001-SEDE-2012", "Capítulo 10, Tablas 1, 4, 5 y 8"),
        ),
        mx_ids=(),
        check=_con_006,
    ),
    RuleDef(
        id="PCC-002",
        title_es="Regla del 120 % de la barra (conexión del lado de la carga)",
        severity="E",
        basis=("NOM",),
        cites=(Cite("NOM-001-SEDE-2012", "705-12(d)(2)"),),
        mx_ids=("MX-E05",),
        check=_pcc_002,
    ),
    RuleDef(
        id="MET-001",
        title_es="Medidor fiscal bidireccional en el punto de interconexión",
        severity="E",
        basis=("CRE/CFE",),
        cites=(
            Cite("CRE RES/142/2017", "Anexo I"),
            Cite("CFE G0100-04", "6.5"),
        ),
        mx_ids=("MX-F01",),
        check=_met_001,
    ),
    RuleDef(
        id="DIS-003",
        title_es="Desconectador I1 a la salida de la central",
        severity="E",
        basis=("CRE/CFE",),
        cites=(
            Cite("Manual de Interconexión", "Anexo 1, 2.1(c)"),
            Cite("CFE G0100-04", "6.9.6.1"),
        ),
        mx_ids=("MX-E01",),
        check=_dis_003,
    ),
    RuleDef(
        id="DIS-004",
        title_es="Desconectador I2 bidireccional del servicio",
        severity="E",
        basis=("CRE/CFE",),
        cites=(
            Cite("Manual de Interconexión", "Anexo 1, 2.1(d)"),
            Cite("CFE G0100-04", "6.9.6.2"),
        ),
        mx_ids=("MX-E02",),
        check=_dis_004,
    ),
)


def rulepack_catalogue() -> list[dict[str, Any]]:
    """Catalogue of the implemented rules as plain data (for the MCP resource and the docs)."""
    return [rule.to_dict() for rule in RULES]


def run_rules(
    spec: PvSystemSpec,
    derived: Derived,
    policy: DcAcPolicy = DEFAULT_DC_AC_POLICY,
    *,
    only: Iterable[str] | None = None,
) -> list[Finding]:
    """Run every rule of the pack (or the ids in ``only``), in pack order; return the findings.

    ``policy`` carries the DC/AC ratio thresholds of STR-007 (project policy, not regulation).
    """
    wanted = None if only is None else set(only)
    ctx = RuleContext(
        spec=spec, derived=derived, tables=get_tables(spec.standards.nom_edition), policy=policy
    )
    findings: list[Finding] = []
    for rule in RULES:
        if wanted is not None and rule.id not in wanted:
            continue
        findings.extend(
            Finding(
                rule_id=rule.id,
                severity=severity,
                message_es=message,
                mx_ids=rule.mx_ids,
                subject=subject,
                cites=rule.cites,
            )
            for severity, subject, message in rule.check(ctx)
        )
    return findings
