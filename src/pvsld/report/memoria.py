"""Calculation report (memoria de cálculo) as an Excel workbook with live formulas and as a PDF.

:func:`build_memoria` turns a validated spec and its derived values into rows: inputs (with their
source), calculations (the Excel formula over the inputs, the Python value, the limit, the check
and the NOM reference), the bill of materials and the rule findings. Both writers render the same
rows, so the workbook and the PDF always agree; the workbook recalculates when the user edits an
input (owner approval of the prototype, 2026-10-09).

The calculations follow the sizing engine and the rule pack (VOLT-001, STR-001/004/005, 690-8/9,
CON-001/002/003, VD-001/002, 110-9, PCC-002, CON-006). Rows whose data the spec does not have (no
string protection, a raceway other than EMT) are left out.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pvsld.core.calc import Derived
from pvsld.core.model import Circuit, PvSystemSpec, address_text
from pvsld.core.rules import Finding
from pvsld.core.tables import get_tables
from pvsld.report.bom import (
    BOX_SWITCH,
    STRING_BREAKER,
    STRING_FUSE,
    BomLine,
    bill_of_materials,
    device_name,
)

TITLE = "MEMORIA DE CÁLCULO"
DRAFT_NOTE = (
    "Borrador generado por pvsld. Debe revisarlo y firmarlo el ingeniero responsable. "
    "Normas: NOM-001-SEDE-2012 (Art. 690, 705; Cap. 10); CFE G0100-04."
)


@dataclass(frozen=True)
class Input:
    key: str
    group: str
    label: str
    value: float
    unit: str
    source: str


@dataclass(frozen=True)
class Calc:
    """One calculated row. ``formula`` uses ``{input}`` and ``[calc]`` placeholders."""

    key: str
    section: str
    label: str
    formula: str
    value: float
    unit: str
    shown: str = ""
    limit: str = ""
    check: tuple[Any, ...] | None = None
    cite: str = ""

    def passes(self, values: dict[str, float]) -> bool | None:
        if self.check is None:
            return None
        op, *refs = self.check
        lim = [values[r] if isinstance(r, str) else r for r in refs]
        if op == "<=":
            return self.value <= lim[0] + 1e-9
        if op == ">=":
            return self.value >= lim[0] - 1e-9
        return lim[0] - 1e-9 <= self.value <= lim[1] + 1e-9


@dataclass
class Memoria:
    """Everything both writers render."""

    title: str
    subtitle: str
    general: list[tuple[str, str]]
    equipment: list[tuple[str, list[tuple[str, str]]]]
    inputs: list[Input] = field(default_factory=list)
    calcs: list[Calc] = field(default_factory=list)
    bom: list[BomLine] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    explanation_es: str = ""
    assumptions: list[str] = field(default_factory=list)

    @property
    def values(self) -> dict[str, float]:
        return {**{i.key: i.value for i in self.inputs}, **{c.key: c.value for c in self.calcs}}

    @property
    def ok(self) -> bool:
        values = self.values
        return all(c.passes(values) is not False for c in self.calcs)


def _g(value: float) -> str:
    return f"{value:g}"


def _location(spec: PvSystemSpec) -> str:
    """Address and coordinates as given; blank when the user gave none (personal data)."""
    site = spec.project.site
    parts = [address_text(site.address)]
    coordinates = [
        c if isinstance(c, str) else f"{c:.5f}" for c in (site.lat, site.lon) if c is not None
    ]
    parts.append(", ".join(coordinates))
    return " — ".join(part for part in parts if part)


def _owner(spec: PvSystemSpec) -> str:
    client, utility = spec.project.client, spec.utility
    rpu = f"RPU {utility.rpu}" if utility.rpu else ""
    return ", ".join(part for part in (client.name, rpu) if part)


def _responsible(spec: PvSystemSpec) -> str:
    who = spec.title_block.responsible
    cedula = f"cédula {who.cedula_profesional}" if who.cedula_profesional else ""
    return ", ".join(part for part in (who.name, cedula, who.company) if part)


def _raceway_owner(spec: PvSystemSpec, circuit: Circuit) -> Circuit:
    if circuit.raceway.ref is None:
        return circuit
    return next(c for c in spec.circuits if c.id == circuit.raceway.ref)


def general_rows(spec: PvSystemSpec) -> list[tuple[str, str]]:
    """Project, standard, location, service, owner, scheme, responsible and date."""
    return [
        ("Proyecto", spec.project.name),
        ("Norma", spec.standards.nom_edition),
        ("Ubicación", _location(spec)),
        (
            "Servicio",
            f"{spec.utility.supplier} {spec.utility.system} "
            f"{_g(spec.utility.nominal_voltage_v)} V, tarifa {spec.utility.tariff}",
        ),
        ("Propietario", _owner(spec)),
        ("Esquema", "Medición neta, conexión del lado de la carga"),
        ("Responsable", _responsible(spec)),
        ("Fecha", spec.title_block.date.strftime("%d/%m/%Y")),
    ]


def build_memoria(
    spec: PvSystemSpec,
    derived: Derived,
    findings: Sequence[Finding] = (),
    *,
    explanation_es: str = "",
    assumptions: Sequence[str] = (),
) -> Memoria:
    """The rows of the calculation report for ``spec`` (one inverter, its first string type).

    A microinverter design (``ac_branches``) takes the rows of
    :func:`pvsld.report.memoria_micro.build_micro_memoria`.
    """
    if spec.ac_branches:
        from pvsld.report.memoria_micro import build_micro_memoria

        return build_micro_memoria(
            spec, derived, findings, explanation_es=explanation_es, assumptions=assumptions
        )
    tables = get_tables(spec.standards.nom_edition)
    module, inv, string = spec.modules[0], spec.inverters[0], spec.strings[0]
    sv = derived.strings[0]
    mppt = next(m for m in inv.mppt if m.id == string.mppt)
    site = spec.project.site
    dc_c = next(c for c in spec.circuits if c.kind == "pv_source")
    ac_c = next(c for c in spec.circuits if c.kind == "inverter_output")
    values_by_id = {v.circuit_id: v for v in derived.circuits}
    dc_v, ac_v = values_by_id[dc_c.id], values_by_id[ac_c.id]
    ns, np_ = sv.n_series, sv.n_parallel
    n_strings = len(spec.strings)
    kwp = derived.kwp_total
    m_name = f"{module.manufacturer} {module.model}"
    i_name = f"{inv.manufacturer} {inv.model}"

    mem = Memoria(
        title=TITLE,
        subtitle=(
            f"Sistema fotovoltaico interconectado de {kwp:.2f} kWp — "
            f"{sum(s.n_series for s in spec.strings)} módulos {m_name} + {i_name}"
        ),
        general=general_rows(spec),
        equipment=[
            (
                f"Módulo FV — {m_name}",
                [
                    ("Pmax", f"{_g(module.pmax_w)} W"),
                    ("Voc / Isc", f"{_g(module.voc_v)} V / {_g(module.isc_a)} A"),
                    ("Vmp / Imp", f"{_g(module.vmp_v)} V / {_g(module.imp_a)} A"),
                    (
                        "β Voc / γ Pmax",
                        f"{_g(module.beta_voc_pct_c)} / {_g(module.gamma_pmax_pct_c)} %/°C",
                    ),
                    ("Fusible máx.", f"{_g(module.max_series_fuse_a)} A"),
                ],
            ),
            (
                f"Inversor — {i_name}",
                [
                    ("Pac", f"{_g(inv.pac_w)} W, {_g(inv.vac_v)} V"),
                    ("Vcd máx.", f"{_g(inv.vdc_max_v)} V"),
                    ("MPPT", f"{_g(mppt.vmin_v)}–{_g(mppt.vmax_v)} V, {len(inv.mppt)} entradas"),
                    ("I máx. / Isc máx.", f"{_g(mppt.imax_a)} A / {_g(mppt.isc_max_a)} A por MPPT"),
                    ("Iac máx.", f"{_g(inv.iac_max_a)} A"),
                ],
            ),
        ],
        bom=bill_of_materials(spec, derived),
        findings=list(findings),
        explanation_es=explanation_es,
        assumptions=list(assumptions),
    )
    v: dict[str, float] = {}

    def put(key: str, group: str, label: str, value: float, unit: str, source: str) -> None:
        mem.inputs.append(Input(key, group, label, value, unit, source))
        v[key] = value

    def calc(
        key: str, section: str, label: str, formula: str, value: float, unit: str, **kw: Any
    ) -> None:
        mem.calcs.append(Calc(key, section, label, formula, value, unit, **kw))
        v[key] = value

    mfr, ifr = f"Hoja técnica {module.manufacturer}", f"Hoja técnica {inv.manufacturer}"
    put("pmax", "Módulo FV", "Potencia máxima (STC)", module.pmax_w, "W", mfr)
    put("voc", "Módulo FV", "Voc", module.voc_v, "V", mfr)
    put("isc", "Módulo FV", "Isc", module.isc_a, "A", mfr)
    put("vmp", "Módulo FV", "Vmp", module.vmp_v, "V", mfr)
    put("imp", "Módulo FV", "Imp", module.imp_a, "A", mfr)
    put("beta", "Módulo FV", "Coef. temperatura Voc (β)", module.beta_voc_pct_c, "%/°C", mfr)
    put("gamma", "Módulo FV", "Coef. temperatura Pmax (γ)", module.gamma_pmax_pct_c, "%/°C", mfr)
    put("fusemax", "Módulo FV", "Fusible máximo en serie", module.max_series_fuse_a, "A", mfr)
    put("pac", "Inversor", "Potencia nominal CA", inv.pac_w * inv.qty, "W", ifr)
    put("vdcmax", "Inversor", "Tensión máxima CD", inv.vdc_max_v, "V", ifr)
    put("mpptmin", "Inversor", "MPPT mínima", mppt.vmin_v, "V", ifr)
    put("mpptmax", "Inversor", "MPPT máxima", mppt.vmax_v, "V", ifr)
    put("mpptimax", "Inversor", "Corriente máx. por MPPT", mppt.imax_a, "A", ifr)
    put("mpptisc", "Inversor", "Isc máx. por MPPT", mppt.isc_max_a, "A", ifr)
    put("iac", "Inversor", "Corriente máxima CA", inv.iac_max_a, "A", ifr)
    put("vac", "Inversor", "Tensión CA", inv.vac_v, "V", ifr)
    put("ns", "Arreglo", "Módulos en serie por cadena", ns, "—", "Diseño")
    put("np", "Arreglo", "Cadenas en paralelo por MPPT", np_, "—", "Diseño")
    put("nstr", "Arreglo", "Cadenas en total", n_strings, "—", "Diseño")
    put("tmin", "Sitio", "Temperatura mínima", site.t_min_c, "°C", "Valor de diseño")
    put("tmax", "Sitio", "Temperatura ambiente máxima", site.t_amb_max_c, "°C", "Valor de diseño")
    put(
        "dtcell",
        "Sitio",
        "Elevación de temperatura de celda",
        derived.t_cell_max_c - site.t_amb_max_c,
        "°C",
        "Supuesto de diseño",
    )

    dc_amb = dc_c.ambient_c if dc_c.ambient_c is not None else site.t_amb_max_c
    put(
        "lenDC",
        "Circuito CD",
        "Longitud cadena → inversor",
        dc_c.length_m,
        "m",
        "Dato del proyecto",
    )
    put(
        "ambDC",
        "Circuito CD",
        "Temperatura ambiente del circuito",
        dc_amb,
        "°C",
        "Dato del proyecto",
    )
    put(
        "adder",
        "Circuito CD",
        "Incremento por tubo al sol",
        (dc_v.t_effective_c or dc_amb) - dc_amb,
        "°C",
        "NOM 310-15(b)(3)(c)",
    )
    put(
        "rDC",
        "Circuito CD",
        f"Resistencia {dc_c.conductors.size} (CD)",
        tables.resistance_dc_ohm_km[dc_c.conductors.size],
        "Ω/km",
        "NOM Cap. 10, Tabla 8",
    )
    put(
        "a75DC",
        "Circuito CD",
        f"Ampacidad {dc_c.conductors.size} a 75 °C",
        dc_v.ampacity_75_a,
        "A",
        "NOM 310-15(b)(16)",
    )
    put(
        "a90DC",
        "Circuito CD",
        f"Ampacidad {dc_c.conductors.size} a 90 °C",
        dc_v.ampacity_90_a,
        "A",
        "NOM 310-15(b)(16)",
    )
    put(
        "kDC",
        "Circuito CD",
        "Factor por temperatura",
        dc_v.k_temp or 1.0,
        "—",
        "NOM 310-15(b)(2)(a)",
    )
    put(
        "fDC",
        "Circuito CD",
        "Factor por agrupamiento",
        dc_v.k_fill or 1.0,
        "—",
        "NOM 310-15(b)(3)(a)",
    )
    put(
        "lenAC", "Circuito CA", "Longitud inversor → ITM-1", ac_c.length_m, "m", "Dato del proyecto"
    )
    put(
        "rAC",
        "Circuito CA",
        f"Resistencia {ac_c.conductors.size} (CA)",
        tables.resistance_ac_pvc_ohm_km[ac_c.conductors.size],
        "Ω/km",
        "NOM Cap. 10, Tabla 9",
    )
    put(
        "a75AC",
        "Circuito CA",
        f"Ampacidad {ac_c.conductors.size} a 75 °C",
        ac_v.ampacity_75_a,
        "A",
        "NOM 310-15(b)(16)",
    )
    put(
        "a90AC",
        "Circuito CA",
        f"Ampacidad {ac_c.conductors.size} a 90 °C",
        ac_v.ampacity_90_a,
        "A",
        "NOM 310-15(b)(16)",
    )
    put(
        "kAC",
        "Circuito CA",
        "Factor por temperatura",
        ac_v.k_temp or 1.0,
        "—",
        "NOM 310-15(b)(2)(a)",
    )
    put(
        "fAC",
        "Circuito CA",
        "Factor por agrupamiento",
        ac_v.k_fill or 1.0,
        "—",
        "NOM 310-15(b)(3)(a)",
    )

    s1, s2, s3, s4, s5, s6, s7, s8 = (
        "2. Arreglo",
        "3. Tensiones",
        "4. Corrientes",
        "5. Protección de cadena",
        "6. Conductor CD",
        "7. Conductor y protección CA",
        "8. Canalización",
        "",
    )
    del s8
    calc(
        "pstc",
        s1,
        "Potencia del arreglo (STC)",
        "{nstr}*{ns}*{pmax}",
        n_strings * ns * module.pmax_w,
        "W",
        shown=f"{n_strings} × {ns} × {_g(module.pmax_w)} W",
    )
    calc(
        "dcac",
        s1,
        "Relación CD/CA",
        "[pstc]/{pac}",
        v["pstc"] / v["pac"],
        "",
        shown=f"{_g(v['pstc'])} W / {_g(v['pac'])} W",
        limit="1.10 – 1.25 recomendado",
        cite="Política del proyecto",
    )
    calc(
        "vocmod",
        s2,
        "Voc máx. del módulo en frío",
        "{voc}*(1+{beta}/100*({tmin}-25))",
        module.voc_v * (1 + module.beta_voc_pct_c / 100 * (site.t_min_c - 25)),
        "V",
        shown=f"{_g(module.voc_v)} × [1 + ({_g(module.beta_voc_pct_c)}/100)({_g(site.t_min_c)} − 25)]",
        cite="NOM 690-7(a)",
    )
    calc(
        "vocstr",
        s2,
        "Voc máx. de la cadena",
        "{ns}*[vocmod]",
        ns * v["vocmod"],
        "V",
        shown=f"{ns} × {v['vocmod']:.2f} V",
        limit=f"≤ {_g(inv.vdc_max_v)} V (inversor)",
        check=("<=", "vdcmax"),
        cite="NOM 690-7(a), VOLT-001",
    )
    calc(
        "tcell",
        s2,
        "Temperatura de celda máxima",
        "{tmax}+{dtcell}",
        derived.t_cell_max_c,
        "°C",
        shown=f"{_g(site.t_amb_max_c)} + {_g(v['dtcell'])}",
    )
    gamma = module.gamma_pmax_pct_c
    calc(
        "vmphot",
        s2,
        "Vmp de la cadena en caliente",
        "{ns}*{vmp}*(1+{gamma}/100*([tcell]-25))",
        ns * module.vmp_v * (1 + gamma / 100 * (derived.t_cell_max_c - 25)),
        "V",
        shown=f"{ns} × {_g(module.vmp_v)} × [1 + ({_g(gamma)}/100)({_g(derived.t_cell_max_c)} − 25)]",
        limit=f"≥ {_g(mppt.vmin_v)} V (MPPT)",
        check=(">=", "mpptmin"),
        cite="STR-001",
    )
    calc(
        "vmpcold",
        s2,
        "Vmp de la cadena en frío",
        "{ns}*{vmp}*(1+{gamma}/100*({tmin}-25))",
        ns * module.vmp_v * (1 + gamma / 100 * (site.t_min_c - 25)),
        "V",
        shown=f"{ns} × {_g(module.vmp_v)} × [1 + ({_g(gamma)}/100)({_g(site.t_min_c)} − 25)]",
        limit=f"≤ {_g(mppt.vmax_v)} V (MPPT)",
        check=("<=", "mpptmax"),
        cite="STR-002",
    )
    calc(
        "iscin",
        s3,
        "Isc de diseño en la entrada MPPT",
        "1.25*{isc}*{np}",
        1.25 * module.isc_a * np_,
        "A",
        shown=f"1.25 × {_g(module.isc_a)} A × {np_}",
        limit=f"≤ {_g(mppt.isc_max_a)} A",
        check=("<=", "mpptisc"),
        cite="NOM 690-8(a)(1), STR-004",
    )
    calc(
        "impin",
        s3,
        "Imp en la entrada MPPT",
        "{imp}*{np}",
        module.imp_a * np_,
        "A",
        shown=f"{_g(module.imp_a)} A × {np_}",
        limit=f"≤ {_g(mppt.imax_a)} A",
        check=("<=", "mpptimax"),
        cite="STR-005 (recorte si no cumple)",
    )

    dc = spec.dc_bos
    fuses = [d for d in dc.disconnects if d.id.startswith(STRING_FUSE)]
    breakers = [d for d in dc.disconnects if d.id.startswith(STRING_BREAKER)]
    switches = [d for d in dc.disconnects if d.id.startswith(BOX_SWITCH)]
    protection = (fuses or breakers or [None])[0]
    if protection is not None:
        kind = "Fusible gPV" if fuses else "ITM de CD"
        name = device_name(protection.model, kind)
        put("protA", "Protecciones", f"{kind} ({name})", protection.ie_a, "A", "Catálogo")
        put(
            "protV",
            "Protecciones",
            f"Tensión de la protección ({kind})",
            protection.ue_v,
            "V",
            "Catálogo",
        )
        calc(
            "protmin",
            s4,
            "Corriente mínima de la protección",
            "1.25*1.25*{isc}",
            1.5625 * module.isc_a,
            "A",
            shown=f"1.25 × 1.25 × {_g(module.isc_a)} A",
            cite="NOM 690-8(b)(1), 690-9(b)",
        )
        calc(
            "protchk",
            s4,
            f"{kind} seleccionado",
            "{protA}",
            protection.ie_a,
            "A",
            shown=name,
            limit=f"≥ {v['protmin']:.1f} A y ≤ {_g(module.max_series_fuse_a)} A",
            check=("between", "protmin", "fusemax"),
            cite="NOM 690-9",
        )
        calc(
            "protvchk",
            s4,
            f"Tensión de la protección ({kind})",
            "{protV}",
            protection.ue_v,
            "V",
            shown=f"{_g(protection.ue_v)} V cd",
            limit=f"≥ {v['vocstr']:.1f} V",
            check=(">=", "vocstr"),
            cite="NOM 690-9(d)",
        )
    for sw in switches:
        name = device_name(sw.model, "Seccionador")
        put("swA", "Protecciones", f"Seccionador {sw.id} ({name})", sw.ie_a, "A", "Catálogo")
        put("swV", "Protecciones", f"Tensión del seccionador {sw.id}", sw.ue_v, "V", "Catálogo")
        calc(
            "swchk",
            s4,
            f"Seccionador {sw.id}, corriente",
            "{swA}",
            sw.ie_a,
            "A",
            shown=f"{name}, {sw.poles} polos",
            limit=f"≥ {v['iscin']:.1f} A",
            check=(">=", "iscin"),
            cite="NOM 690-15, 690-17",
        )
        calc(
            "swvchk",
            s4,
            f"Seccionador {sw.id}, tensión",
            "{swV}",
            sw.ue_v,
            "V",
            shown=f"Escalón ≤ {_g(sw.ue_v)} V",
            limit=f"≥ {v['vocstr']:.1f} V",
            check=(">=", "vocstr"),
            cite="NOM 690-17",
        )
        break

    size_dc = dc_c.conductors.size
    calc(
        "imaxdc",
        s5,
        "Corriente máxima del circuito",
        "1.25*{isc}*{np}",
        1.25 * module.isc_a * np_,
        "A",
        shown=f"1.25 × {_g(module.isc_a)} A",
        cite="NOM 690-8(a)(1)",
    )
    calc(
        "req75dc",
        s5,
        "Ampacidad requerida a 75 °C",
        "1.25*[imaxdc]",
        1.25 * v["imaxdc"],
        "A",
        shown=f"1.25 × {v['imaxdc']:.2f} A",
        limit=f"≤ {_g(dc_v.ampacity_75_a)} A ({size_dc})",
        check=("<=", "a75DC"),
        cite="NOM 690-8(b)(1), CON-001",
    )
    calc(
        "teffdc",
        s5,
        "Temperatura efectiva",
        "{ambDC}+{adder}",
        dc_amb + v["adder"],
        "°C",
        shown=f"{_g(dc_amb)} + {_g(v['adder'])}",
        cite="NOM 310-15(b)(3)(c)",
    )
    calc(
        "ampdc",
        s5,
        "Ampacidad corregida a 90 °C",
        "{a90DC}*{kDC}*{fDC}",
        dc_v.ampacity_90_a * v["kDC"] * v["fDC"],
        "A",
        shown=f"{_g(dc_v.ampacity_90_a)} A × {_g(v['kDC'])} × {_g(v['fDC'])}",
        limit=f"≥ {v['imaxdc']:.2f} A",
        check=(">=", "imaxdc"),
        cite="NOM 310-15, CON-002",
    )
    calc(
        "vddc",
        s5,
        "Caída de tensión",
        "2*{lenDC}/1000*{rDC}*{imp}/({ns}*{vmp})*100",
        2 * dc_c.length_m / 1000 * v["rDC"] * module.imp_a / (ns * module.vmp_v) * 100,
        "%",
        shown=f"2 × {_g(dc_c.length_m)} m × {_g(v['rDC'])} Ω/km × {_g(module.imp_a)} A / "
        f"({ns} × {_g(module.vmp_v)} V)",
        limit=f"≤ {_g(spec.standards.vd_limits_pct.dc)} %",
        check=("<=", spec.standards.vd_limits_pct.dc),
        cite="VD-001",
    )

    ac = spec.ac_bos
    itm = next((o for o in ac.ocpds if o.role == "I1"), None)
    main = ac.main_breakers[0] if ac.main_breakers else None
    panel = ac.panels[0] if ac.panels else None
    size_ac = ac_c.conductors.size
    phases = ac_c.conductors.qty
    factor = math.sqrt(3) if phases == 3 else 2.0
    if itm is not None:
        put(
            "itm1",
            "Protecciones",
            f"{itm.id} ({device_name(itm.model, 'ITM')})",
            itm.rating_a,
            "A",
            "Catálogo",
        )
        put(
            "kaic",
            "Protecciones",
            f"Capacidad interruptiva de {itm.id}",
            itm.kaic_ka,
            "kA",
            "Catálogo",
        )
        put(
            "fault",
            "Protecciones",
            "Corriente de falla disponible",
            spec.utility.available_fault_current_ka,
            "kA",
            "Dato del servicio",
        )
        calc(
            "iac125",
            s6,
            "Protección mínima de salida",
            "1.25*{iac}",
            1.25 * inv.iac_max_a,
            "A",
            shown=f"1.25 × {_g(inv.iac_max_a)} A",
            limit=f"≤ {_g(itm.rating_a)} A ({itm.id})",
            check=("<=", "itm1"),
            cite="NOM 690-8(b)(1), 240-6",
        )
    calc(
        "req75ac",
        s6,
        "Ampacidad requerida a 75 °C",
        "1.25*{iac}",
        1.25 * inv.iac_max_a,
        "A",
        shown=f"1.25 × {_g(inv.iac_max_a)} A",
        limit=f"≤ {_g(ac_v.ampacity_75_a)} A ({size_ac})",
        check=("<=", "a75AC"),
        cite="CON-001",
    )
    if itm is not None:
        calc(
            "ampac",
            s6,
            "Ampacidad corregida a 90 °C",
            "{a90AC}*{kAC}*{fAC}",
            ac_v.ampacity_90_a * v["kAC"] * v["fAC"],
            "A",
            shown=f"{_g(ac_v.ampacity_90_a)} A × {_g(v['kAC'])} × {_g(v['fAC'])}",
            limit=f"≥ {_g(itm.rating_a)} A (protegido por {itm.id})",
            check=(">=", "itm1"),
            cite="NOM 240-4, CON-003",
        )
    calc(
        "vdac",
        s6,
        "Caída de tensión",
        f"{factor:.6g}*{{lenAC}}/1000*{{rAC}}*{{iac}}/{{vac}}*100",
        factor * ac_c.length_m / 1000 * v["rAC"] * inv.iac_max_a / inv.vac_v * 100,
        "%",
        shown=f"{'√3' if phases == 3 else '2'} × {_g(ac_c.length_m)} m × {_g(v['rAC'])} Ω/km × "
        f"{_g(inv.iac_max_a)} A / {_g(inv.vac_v)} V",
        limit=f"≤ {_g(spec.standards.vd_limits_pct.ac)} %",
        check=("<=", spec.standards.vd_limits_pct.ac),
        cite="VD-002",
    )
    if itm is not None:
        calc(
            "kaicchk",
            s6,
            f"Capacidad interruptiva de {itm.id}",
            "{kaic}",
            itm.kaic_ka,
            "kA",
            shown=device_name(itm.model, "ITM"),
            limit=f"≥ {_g(v['fault'])} kA (falla disponible)",
            check=(">=", "fault"),
            cite="NOM 110-9",
        )
    if itm is not None and main is not None and panel is not None:
        put(
            "itmp",
            "Protecciones",
            f"{main.id} ({device_name(main.model, 'ITM')})",
            main.rating_a,
            "A",
            "Dato del servicio",
        )
        put("bus", "Protecciones", f"Barra de {panel.id}", panel.bus_a, "A", "Dato del proyecto")
        calc(
            "rule120",
            s6,
            "Regla del 120 % de la barra",
            "{itm1}+{itmp}",
            itm.rating_a + main.rating_a,
            "A",
            shown=f"{_g(itm.rating_a)} A + {_g(main.rating_a)} A",
            limit=f"≤ {_g(1.2 * panel.bus_a)} A (1.2 × {_g(panel.bus_a)} A)",
            check=("<=", 1.2 * panel.bus_a),
            cite="NOM 705-12(d)(2), PCC-002",
        )

    emt = {r[0]: r for r in tables.emt}
    for circuit, key in ((dc_c, "DC"), (ac_c, "AC")):
        owner = _raceway_owner(spec, circuit)
        r = owner.raceway
        if r.type is None or r.type.upper() != "EMT" or r.trade_size_mm not in emt:
            continue
        members = [owner, *(c for c in spec.circuits if c.raceway.ref == owner.id)]
        wires = owner.conductors
        count = sum(c.conductors.qty + (1 if c.neutral else 0) for c in members)
        diameter = wires.outer_diameter_mm or tables.thhw_diameter_mm.get(wires.size)
        bare = tables.bare_stranded_area_mm2.get(owner.egc.size)
        if diameter is None or bare is None:
            continue
        designation, trade, _d, area = emt[r.trade_size_mm]
        put(
            f"od{key}",
            "Canalización",
            f"Diámetro del conductor {wires.size} ({wires.insulation})",
            diameter,
            "mm",
            "Hoja técnica" if wires.outer_diameter_mm else "NOM Cap. 10, Tabla 5",
        )
        put(
            f"bare{key}",
            "Canalización",
            f"Área de la tierra desnuda {owner.egc.size}",
            bare,
            "mm²",
            "NOM Cap. 10, Tabla 8",
        )
        put(
            f"emt{key}",
            "Canalización",
            f"Área EMT {designation} mm",
            area,
            "mm²",
            "NOM Cap. 10, Tabla 4",
        )
        calc(
            f"area{key}",
            s7,
            f"Área ocupada, tubo {'CD' if key == 'DC' else 'CA'}",
            f"{count}*PI()/4*{{od{key}}}^2+{{bare{key}}}",
            count * math.pi / 4 * diameter**2 + bare,
            "mm²",
            shown=f"{count} × π/4 × {_g(diameter)}² + {_g(bare)}",
            cite="NOM Cap. 10, Tablas 4, 5 y 8",
        )
        calc(
            f"fill{key}",
            s7,
            f'Llenado EMT {designation} mm ({trade}")',
            f"[area{key}]/{{emt{key}}}*100",
            v[f"area{key}"] / area * 100,
            "%",
            shown=f"{v[f'area{key}']:.1f} / {_g(area)} mm²",
            limit="≤ 40 % (más de 2 conductores)",
            check=("<=", 40.0),
            cite="NOM Cap. 10, Tabla 1, CON-006",
        )
    return mem


# ======================================== Excel ===========================================


def write_memoria_xlsx(mem: Memoria, path: Path) -> Path:
    """The workbook: Datos (blue inputs), Memoria (live formulas), Materiales, Validación."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    arial = "Arial"
    font = lambda **k: Font(name=arial, **k)  # noqa: E731
    head_fill = PatternFill("solid", fgColor="1F4E78")
    sec_fill = PatternFill("solid", fgColor="DDEBF7")
    ok_fill, bad_fill = (
        PatternFill("solid", fgColor="E2EFDA"),
        PatternFill("solid", fgColor="F8CBAD"),
    )
    thin = Side(style="thin", color="999999")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    values = mem.values

    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True

    def header(ws: Any, row: int, titles: Sequence[str]) -> None:
        for col, text in enumerate(titles, 1):
            cell = ws.cell(row, col, text)
            cell.font, cell.fill, cell.border = font(bold=True, color="FFFFFF"), head_fill, box

    ws = wb.active
    ws.title = "Datos"
    ws["A1"] = f"{mem.title} — DATOS DE ENTRADA"
    ws["A1"].font = font(bold=True, size=14)
    ws["A2"] = (
        "Texto azul: valores de entrada (editables). Cambie un valor y la hoja «Memoria» se recalcula."
    )
    ws["A2"].font = font(italic=True, color="0000FF")
    header(ws, 4, ["Grupo", "Dato", "Valor", "Unidad", "Fuente"])
    addr: dict[str, str] = {}
    for row, item in enumerate(mem.inputs, 5):
        ws.cell(row, 1, item.group).font = font()
        ws.cell(row, 2, item.label).font = font()
        cell = ws.cell(row, 3, item.value)
        cell.font = font(color="0000FF")
        cell.number_format = "0" if float(item.value).is_integer() else "0.00##"
        ws.cell(row, 4, item.unit).font = font()
        ws.cell(row, 5, item.source).font = font()
        for col in range(1, 6):
            ws.cell(row, col).border = box
        addr[item.key] = f"Datos!$C${row}"
    for col, width in zip("ABCDE", (16, 44, 12, 8, 26), strict=True):
        ws.column_dimensions[col].width = width

    wm = wb.create_sheet("Memoria")
    wm["A1"] = mem.title
    wm["A1"].font = font(bold=True, size=14)
    wm["A2"] = mem.subtitle
    wm["A2"].font = font(bold=True)
    wm["A3"] = DRAFT_NOTE
    wm["A3"].font = font(italic=True, size=9)
    header(wm, 5, ["Concepto", "Cálculo", "Valor", "Unidad", "Límite", "Resultado", "Referencia"])
    res: dict[str, str] = {}

    def excel(formula: str) -> str:
        out = formula
        for key, ref in addr.items():
            out = out.replace("{" + key + "}", ref)
        for key, ref in res.items():
            out = out.replace("[" + key + "]", ref)
        return "=" + out

    row, section = 6, None
    for c in mem.calcs:
        if c.section != section:
            section = c.section
            for col in range(1, 8):
                wm.cell(row, col).fill, wm.cell(row, col).border = sec_fill, box
            wm.cell(row, 1, section).font = font(bold=True)
            row += 1
        wm.cell(row, 1, c.label).font = font()
        wm.cell(row, 2, c.shown).font = font(size=9, color="555555")
        cell = wm.cell(row, 3, excel(c.formula))
        cell.font, cell.number_format = font(), "0.00"
        res[c.key] = f"Memoria!$C${row}"
        wm.cell(row, 4, c.unit).font = font()
        wm.cell(row, 5, c.limit).font = font()
        if c.check is not None:
            op, *refs = c.check
            lim = [addr.get(r, res.get(r)) if isinstance(r, str) else repr(r) for r in refs]
            me = f"C{row}"
            if op == "<=":
                cond = f"{me}<={lim[0]}+0.000001"
            elif op == ">=":
                cond = f"{me}>={lim[0]}-0.000001"
            else:
                cond = f"AND({me}>={lim[0]}-0.000001,{me}<={lim[1]}+0.000001)"
            out = wm.cell(row, 6, f'=IF({cond},"CUMPLE","NO CUMPLE")')
            out.font, out.alignment = font(bold=True), Alignment(horizontal="center")
            out.fill = ok_fill if c.passes(values) else bad_fill
        wm.cell(row, 7, c.cite).font = font(size=9)
        for col in range(1, 8):
            wm.cell(row, col).border = box
        row += 1
    if mem.explanation_es:
        row += 1
        wm.cell(row, 1, "Elección del inversor").font = font(bold=True)
        wm.cell(row + 1, 1, mem.explanation_es).font = font(size=9)
    for col, width in zip("ABCDEFG", (40, 54, 11, 7, 30, 13, 30), strict=True):
        wm.column_dimensions[col].width = width
    wm.freeze_panes = "A6"

    wl = wb.create_sheet("Materiales")
    wl["A1"] = "RESUMEN DE MATERIALES DE LA INSTALACIÓN"
    wl["A1"].font = font(bold=True, size=14)
    header(wl, 3, ["Grupo", "Partida", "Descripción", "Cantidad", "Unidad"])
    for row, line in enumerate(mem.bom, 4):
        for col, value in enumerate(
            (line.group, line.item, line.description, line.quantity, line.unit), 1
        ):
            cell = wl.cell(row, col, value)
            cell.font, cell.border = font(), box
    wl.cell(
        len(mem.bom) + 5,
        1,
        "Longitudes de un sentido, sin desperdicio: agregue la holgura de obra.",
    ).font = font(italic=True, size=9)
    for col, width in zip("ABCDE", (24, 34, 56, 10, 8), strict=True):
        wl.column_dimensions[col].width = width

    wv = wb.create_sheet("Validación")
    wv["A1"] = "VALIDACIÓN Y SUPUESTOS"
    wv["A1"].font = font(bold=True, size=14)
    header(wv, 3, ["Regla", "Severidad", "Mensaje"])
    row = 4
    for f in mem.findings:
        for col, value in enumerate((f.rule_id, f.severity.value, f.message_es), 1):
            cell = wv.cell(row, col, value)
            cell.font, cell.border = font(), box
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        row += 1
    errors = sum(1 for f in mem.findings if f.severity.value == "E")
    warnings = sum(1 for f in mem.findings if f.severity.value == "W")
    wv.cell(row, 1, f"{errors} errores, {warnings} advertencias.").font = font(bold=True)
    row += 2
    if mem.assumptions:
        wv.cell(row, 1, "Supuestos").font = font(bold=True)
        row += 1
        for text in mem.assumptions:
            cell = wv.cell(row, 1, "• " + text)
            cell.font, cell.alignment = font(size=9), Alignment(wrap_text=True, vertical="top")
            wv.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
            wv.row_dimensions[row].height = 26
            row += 1
    for col, width in zip("ABC", (12, 10, 110), strict=True):
        wv.column_dimensions[col].width = width

    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = False
    wb.active = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# ========================================= PDF ============================================


def _register_fonts() -> tuple[str, str, str]:
    """Arial on Windows; matplotlib's DejaVu (always installed with pvsld) elsewhere."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        (
            "Arial",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/ariali.ttf",
        ),
    ]
    import matplotlib

    ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    candidates.append(
        (
            "DejaVuSans",
            str(ttf / "DejaVuSans.ttf"),
            str(ttf / "DejaVuSans-Bold.ttf"),
            str(ttf / "DejaVuSans-Oblique.ttf"),
        )
    )
    for name, regular, bold, italic in candidates:
        if all(Path(p).exists() for p in (regular, bold, italic)):
            for suffix, file in (("", regular), ("-Bold", bold), ("-Italic", italic)):
                if f"pvsld-{name}{suffix}" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(f"pvsld-{name}{suffix}", file))
            return f"pvsld-{name}", f"pvsld-{name}-Bold", f"pvsld-{name}-Italic"
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def write_memoria_pdf(mem: Memoria, path: Path) -> Path:
    """Letter-size PDF: general data, equipment, the calculation sections, BOM, validation."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    regular, bold, italic = _register_fonts()
    base = ParagraphStyle("b", fontName=regular, fontSize=8.5, leading=10.5)
    small = ParagraphStyle(
        "s", parent=base, fontSize=7.5, leading=9, textColor=colors.HexColor("#444444")
    )
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=16, leading=20, alignment=TA_CENTER)
    h2 = ParagraphStyle(
        "h2",
        fontName=bold,
        fontSize=10.5,
        leading=13,
        spaceBefore=8,
        spaceAfter=4,
        textColor=colors.HexColor("#1F4E78"),
    )
    sub = ParagraphStyle("sub", parent=base, fontName=bold, fontSize=10, alignment=TA_CENTER)
    note = ParagraphStyle("note", parent=small, fontName=italic, alignment=TA_CENTER)
    head = colors.HexColor("#1F4E78")
    values = mem.values

    def grid(
        rows: list[list[Any]], widths: list[float], spans: Sequence[tuple[Any, Any]] = ()
    ) -> Any:
        table = Table(rows, colWidths=widths, repeatRows=1)
        style = [
            ("FONT", (0, 0), (-1, -1), regular, 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("BACKGROUND", (0, 0), (-1, 0), head),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONT", (0, 0), (-1, 0), bold, 8),
        ]
        style += [("SPAN", a, b) for a, b in spans]
        table.setStyle(TableStyle(style))
        return table

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(regular, 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(
            15 * mm,
            10 * mm,
            "pvsld — memoria de cálculo (borrador para revisión del ingeniero responsable)",
        )
        canvas.drawRightString(letter[0] - 15 * mm, 10 * mm, f"Página {doc.page}")
        canvas.restoreState()

    story: list[Any] = [
        Paragraph(mem.title, h1),
        Paragraph(mem.subtitle, sub),
        Spacer(1, 3),
        Paragraph(DRAFT_NOTE, note),
        Spacer(1, 8),
    ]
    general = [
        [mem.general[i][0], mem.general[i][1], mem.general[i + 1][0], mem.general[i + 1][1]]
        for i in range(0, len(mem.general), 2)
    ]
    t = Table(general, colWidths=[22 * mm, 68 * mm, 18 * mm, 72 * mm])
    t.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), regular, 8),
                ("FONT", (0, 0), (0, -1), bold, 8),
                ("FONT", (2, 0), (2, -1), bold, 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
            ]
        )
    )
    story += [t, Paragraph("1. Equipos principales", h2)]
    (left_title, left), (right_title, right) = mem.equipment
    rows: list[list[Any]] = [[left_title, "", right_title, ""]]
    rows += [[a, b, c, d] for (a, b), (c, d) in zip(left, right, strict=False)]
    story.append(
        grid(rows, [35 * mm, 55 * mm, 35 * mm, 55 * mm], spans=[((0, 0), (1, 0)), ((2, 0), (3, 0))])
    )
    if mem.explanation_es:
        story += [
            Spacer(1, 4),
            Paragraph(f"<b>Elección del inversor.</b> {mem.explanation_es}", base),
        ]

    section, rows = None, []

    def flush() -> None:
        if rows:
            story.append(
                KeepTogether(
                    [
                        Paragraph(section or "", h2),
                        grid(
                            [["Concepto", "Cálculo", "Valor", "Límite", "", "Ref."], *rows],
                            [40 * mm, 54 * mm, 18 * mm, 30 * mm, 18 * mm, 24 * mm],
                        ),
                    ]
                )
            )

    for c in mem.calcs:
        if c.section != section:
            flush()
            section, rows = c.section, []
        ok = c.passes(values)
        mark: Any = ""
        if ok is not None:
            color, word = ("#2E7D32", "CUMPLE") if ok else ("#C62828", "NO CUMPLE")
            mark = Paragraph(f'<font color="{color}"><b>{word}</b></font>', small)
        rows.append(
            [
                Paragraph(c.label, base),
                Paragraph(c.shown, small),
                f"{c.value:.2f} {c.unit}".strip(),
                Paragraph(c.limit, small),
                mark,
                Paragraph(c.cite, small),
            ]
        )
    flush()

    story += [PageBreak(), Paragraph("Resumen de materiales de la instalación", h2)]
    story.append(
        grid(
            [["Grupo", "Partida", "Descripción", "Cant.", "Unidad"]]
            + [
                [
                    Paragraph(b.group, base),
                    Paragraph(b.item, base),
                    Paragraph(b.description, base),
                    f"{b.quantity:g}",
                    b.unit,
                ]
                for b in mem.bom
            ],
            [32 * mm, 40 * mm, 78 * mm, 14 * mm, 14 * mm],
        )
    )
    story.append(
        Paragraph("Longitudes de un sentido, sin desperdicio: agregue la holgura de obra.", small)
    )
    errors = sum(1 for f in mem.findings if f.severity.value == "E")
    warnings = sum(1 for f in mem.findings if f.severity.value == "W")
    story += [
        Paragraph("Validación (reglas mx-gd-2026.10)", h2),
        Paragraph(
            f"{errors} errores, {warnings} advertencias. "
            + " ".join(f"[{f.rule_id}] {f.message_es}" for f in mem.findings),
            base,
        ),
    ]
    if mem.assumptions:
        story.append(Paragraph("Supuestos", h2))
        story += [Paragraph("• " + a, small) for a in mem.assumptions]
    story += [
        Spacer(1, 18),
        Table(
            [["", ""], ["Ing. responsable (nombre, cédula y firma)", "Fecha"]],
            colWidths=[100 * mm, 50 * mm],
            style=TableStyle(
                [
                    ("LINEABOVE", (0, 1), (-1, 1), 0.6, colors.black),
                    ("FONT", (0, 0), (-1, -1), regular, 8),
                    ("TOPPADDING", (0, 0), (-1, 0), 18),
                ]
            ),
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=mem.title,
        author="pvsld",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
