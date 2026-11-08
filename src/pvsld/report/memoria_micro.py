"""Calculation report rows of a microinverter design (schema 0.2.0, Stage 3.10.3).

Same :class:`~pvsld.report.memoria.Memoria` rows as the string design (inputs with their source,
calculations with the Excel formula, the value, the limit, the check and the reference), so both
writers render it unchanged. The calculations follow :mod:`pvsld.core.micro`: the module on each
input (MIC-001..005), each AC branch (current, breaker MIC-011, trunk limit 240-4(d), the
manufacturer's cap MIC-012, home-run conductor, daisy-chain voltage drop VD-002), the AC combiner
(MIC-020), the 120 % busbar rule (PCC-002) and the interrupting ratings (110-9).

The daisy-chain drop has a closed form: the home run carries ``n x I`` and the trunk segments
``(n-1) x I ... 1 x I``, whose sum is ``n (n-1) / 2 x I``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pvsld.core import micro as micro_rules
from pvsld.core.calc import Derived
from pvsld.core.model import PvSystemSpec
from pvsld.core.rules import Finding, connection_voltage_v
from pvsld.core.tables import get_tables
from pvsld.report.bom import bill_of_materials, device_name


def build_micro_memoria(
    spec: PvSystemSpec,
    derived: Derived,
    findings: Sequence[Finding] = (),
    *,
    explanation_es: str = "",
    assumptions: Sequence[str] = (),
) -> Any:
    from pvsld.report.memoria import TITLE, Calc, Input, Memoria, _g, general_rows

    tables = get_tables(spec.standards.nom_edition)
    module = spec.modules[0]
    micro = next(i for i in spec.inverters if i.type == "micro")
    assert micro.micro is not None
    limits = micro_rules.limits_from_spec(micro)
    site = spec.project.site
    units = sum(len(b.microinverters) for b in spec.ac_branches)
    n_modules = sum(len(u.inputs) for b in spec.ac_branches for u in b.microinverters)
    m_name = f"{module.manufacturer} {module.model}"
    i_name = f"{micro.manufacturer} {micro.model}"
    mppt = micro.mppt[0]
    low, high = micro.micro.recommended_module_power_wp
    window_low, window_high = micro.micro.ac_voltage_range_v

    mem = Memoria(
        title=TITLE,
        subtitle=(
            f"Sistema fotovoltaico interconectado de {derived.kwp_total:.2f} kWp — {n_modules} "
            f"módulos {m_name} + {units} microinversores {i_name}"
        ),
        general=general_rows(spec),
        equipment=[
            (
                f"Módulo FV — {m_name}",
                [
                    ("Pmax", f"{_g(module.pmax_w)} W"),
                    ("Voc / Isc", f"{_g(module.voc_v)} V / {_g(module.isc_a)} A"),
                    ("Vmp / Imp", f"{_g(module.vmp_v)} V / {_g(module.imp_a)} A"),
                    ("β Voc", f"{_g(module.beta_voc_pct_c)} %/°C"),
                ],
            ),
            (
                f"Microinversor — {i_name}",
                [
                    ("Pac", f"{_g(micro.pac_w)} W, {_g(window_low)}–{_g(window_high)} V CA"),
                    ("Entradas", f"{len(micro.mppt)}, un módulo por entrada"),
                    ("Vcd máx.", f"{_g(micro.vdc_max_v)} V"),
                    ("I máx. / Isc máx.", f"{_g(mppt.imax_a)} A / {_g(mppt.isc_max_a)} A"),
                    ("Módulo recomendado", f"{_g(low)} a {_g(high)} W"),
                    ("Iac máx.", f"{_g(micro.iac_max_a)} A"),
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

    mfr, ifr = f"Hoja técnica {module.manufacturer}", f"Hoja técnica {micro.manufacturer}"
    put("pmax", "Módulo FV", "Potencia máxima (STC)", module.pmax_w, "W", mfr)
    put("voc", "Módulo FV", "Voc", module.voc_v, "V", mfr)
    put("isc", "Módulo FV", "Isc de diseño (BNPI si es bifacial)", module.isc_a, "A", mfr)
    put("vmp", "Módulo FV", "Vmp", module.vmp_v, "V", mfr)
    put("imp", "Módulo FV", "Imp", module.imp_a, "A", mfr)
    put("beta", "Módulo FV", "Coef. temperatura Voc (β)", module.beta_voc_pct_c or 0.0, "%/°C", mfr)
    put("gamma", "Módulo FV", "Coef. temperatura Pmax (γ)", module.gamma_pmax_pct_c, "%/°C", mfr)
    put("pac", "Microinversor", "Potencia nominal CA por equipo", micro.pac_w, "W", ifr)
    put("vdcmax", "Microinversor", "Tensión máxima CD por entrada", micro.vdc_max_v, "V", ifr)
    put("mpptmin", "Microinversor", "MPPT mínima", mppt.vmin_v, "V", ifr)
    put("imax", "Microinversor", "Corriente máx. por entrada", mppt.imax_a, "A", ifr)
    put("iscmax", "Microinversor", "Isc máx. por entrada", mppt.isc_max_a, "A", ifr)
    put("iac", "Microinversor", "Corriente máxima CA por equipo", micro.iac_max_a, "A", ifr)
    put("vaclow", "Microinversor", "Tensión CA mínima de operación", window_low, "V", ifr)
    put("vachigh", "Microinversor", "Tensión CA máxima de operación", window_high, "V", ifr)
    put("plow", "Microinversor", "Potencia de módulo recomendada mínima", low, "W", ifr)
    put("phigh", "Microinversor", "Potencia de módulo recomendada máxima", high, "W", ifr)
    put("nmod", "Arreglo", "Módulos", n_modules, "—", "Diseño")
    put("nmic", "Arreglo", "Microinversores", units, "—", "Diseño")
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
    voltage = connection_voltage_v(spec, micro)
    put(
        "vserv",
        "Servicio",
        "Tensión a la que se conecta el microinversor",
        voltage,
        "V",
        "Servicio",
    )

    s1, s2 = "2. Arreglo", "3. Entrada del microinversor (un módulo por entrada)"
    calc(
        "pstc",
        s1,
        "Potencia del arreglo (STC)",
        "{nmod}*{pmax}",
        n_modules * module.pmax_w,
        "W",
        shown=f"{n_modules} × {_g(module.pmax_w)} W",
    )
    calc(
        "dcac",
        s1,
        "Relación CD/CA",
        "[pstc]/({nmic}*{pac})",
        v["pstc"] / (units * micro.pac_w),
        "",
        shown=f"{_g(v['pstc'])} W / ({units} × {_g(micro.pac_w)} W)",
        cite="Política del proyecto",
    )
    calc(
        "vservchk",
        s1,
        "Tensión del servicio en la ventana de CA",
        "{vserv}",
        voltage,
        "V",
        limit=f"{_g(window_low)} a {_g(window_high)} V",
        check=("between", "vaclow", "vachigh"),
        cite="Ficha técnica, MIC-010",
    )
    calc(
        "vocmod",
        s2,
        "Voc máx. del módulo en frío",
        "{voc}*(1+{beta}/100*({tmin}-25))",
        module.voc_v * (1 + v["beta"] / 100 * (site.t_min_c - 25)),
        "V",
        shown=f"{_g(module.voc_v)} × [1 + ({_g(v['beta'])}/100)({_g(site.t_min_c)} − 25)]",
        limit=f"≤ {_g(micro.vdc_max_v)} V (entrada)",
        check=("<=", "vdcmax"),
        cite="NOM 690-7(a), MIC-001",
    )
    calc(
        "iscchk",
        s2,
        "Isc del módulo en la entrada (sin factor 1.25)",
        "{isc}",
        module.isc_a,
        "A",
        limit=f"≤ {_g(mppt.isc_max_a)} A (entrada)",
        check=("<=", "iscmax"),
        cite="Ficha técnica, MIC-002",
    )
    calc(
        "impchk",
        s2,
        "Imp del módulo frente a la corriente de entrada",
        "{imp}",
        module.imp_a,
        "A",
        limit=f"recorta arriba de {_g(mppt.imax_a)} A (informativo)",
        cite="MIC-003",
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
    calc(
        "vmphot",
        s2,
        "Vmp del módulo en caliente",
        "{vmp}*(1+{gamma}/100*([tcell]-25))",
        module.vmp_v * (1 + module.gamma_pmax_pct_c / 100 * (derived.t_cell_max_c - 25)),
        "V",
        limit=f"≥ {_g(mppt.vmin_v)} V (MPPT)",
        check=(">=", "mpptmin"),
        cite="MIC-005",
    )
    calc(
        "pmaxchk",
        s2,
        "Potencia del módulo frente a la recomendada",
        "{pmax}",
        module.pmax_w,
        "W",
        limit=f"{_g(low)} a {_g(high)} W recomendado (informativo)",
        cite="MIC-004",
    )

    values = {c.circuit_id: c for c in derived.circuits}
    circuits = {c.from_.split(".", 1)[0]: c for c in spec.circuits}
    ocpds = {o.id: o for o in spec.ac_bos.ocpds}
    put(
        "fault",
        "Protecciones",
        "Corriente de falla disponible",
        spec.utility.available_fault_current_ka,
        "kA",
        "Dato del servicio",
    )
    total = 0.0
    for number, branch in enumerate(spec.ac_branches, start=3):
        bid = branch.id
        n = len(branch.microinverters)
        total += n * micro.iac_max_a
        section = f"{number + 1}. Rama {bid}"
        ocpd = ocpds[branch.ocpd]
        circuit = circuits.get(bid)
        cv = values.get(circuit.id) if circuit else None
        limit = micro_rules.branch_limit(limits, branch.trunk_size, tables)
        put(f"n{bid}", f"Rama {bid}", "Microinversores en la rama", n, "—", "Diseño")
        put(
            f"itm{bid}",
            f"Rama {bid}",
            f"{ocpd.id} ({device_name(ocpd.model, 'ITM')})",
            ocpd.rating_a,
            "A",
            "Catálogo",
        )
        put(
            f"lim{bid}",
            f"Rama {bid}",
            f"ITM máximo del troncal {branch.trunk_size}",
            limit.ocpd_limit_a,
            "A",
            "NOM 240-4(d) y fabricante",
        )
        calc(
            f"i{bid}",
            section,
            "Corriente de la rama",
            f"{{n{bid}}}*{{iac}}",
            n * micro.iac_max_a,
            "A",
            shown=f"{n} × {_g(micro.iac_max_a)} A",
            cite="NOM 690-8(a)(3)",
        )
        calc(
            f"min{bid}",
            section,
            "Protección mínima de la rama",
            f"1.25*[i{bid}]",
            1.25 * v[f"i{bid}"],
            "A",
            shown=f"1.25 × {v[f'i{bid}']:.2f} A",
            limit=f"≤ {_g(ocpd.rating_a)} A ({ocpd.id})",
            check=("<=", f"itm{bid}"),
            cite="NOM 690-8(b)(1), 240-6, MIC-011",
        )
        calc(
            f"trunk{bid}",
            section,
            f"{ocpd.id} frente al troncal {branch.trunk_size}",
            f"{{itm{bid}}}",
            ocpd.rating_a,
            "A",
            limit=f"≤ {_g(limit.ocpd_limit_a)} A",
            check=("<=", f"lim{bid}"),
            cite="NOM 240-4(d), MIC-011",
        )
        if limit.manufacturer_units is not None:
            calc(
                f"cap{bid}",
                section,
                "Microinversores por troncal (tope del fabricante)",
                f"{{n{bid}}}",
                n,
                "—",
                limit=f"≤ {limit.manufacturer_units}",
                check=("<=", float(limit.manufacturer_units)),
                cite="Manual de instalación, MIC-012",
            )
        if circuit is None or cv is None:
            continue
        size = circuit.conductors.size
        put(
            f"len{bid}", f"Rama {bid}", "Longitud al ITM (tramo)", circuit.length_m, "m", "Proyecto"
        )
        put(
            f"sp{bid}",
            f"Rama {bid}",
            "Separación entre microinversores",
            branch.spacing_m,
            "m",
            "Proyecto",
        )
        put(
            f"r{bid}",
            f"Rama {bid}",
            f"Resistencia {size} (CA)",
            tables.resistance_ac_pvc_ohm_km[size],
            "Ω/km",
            "NOM Cap. 10, Tabla 9",
        )
        put(
            f"rt{bid}",
            f"Rama {bid}",
            f"Resistencia del troncal {branch.trunk_size} (CA)",
            tables.resistance_ac_pvc_ohm_km[branch.trunk_size],
            "Ω/km",
            "NOM Cap. 10, Tabla 9",
        )
        put(
            f"a75{bid}",
            f"Rama {bid}",
            f"Ampacidad {size} a 75 °C",
            cv.ampacity_75_a,
            "A",
            "NOM 310-15(b)(16)",
        )
        calc(
            f"req{bid}",
            section,
            f"Ampacidad requerida a 75 °C ({circuit.id})",
            f"1.25*[i{bid}]",
            1.25 * v[f"i{bid}"],
            "A",
            limit=f"≤ {_g(cv.ampacity_75_a)} A ({size})",
            check=("<=", f"a75{bid}"),
            cite="CON-001",
        )
        pairs = n * (n - 1) / 2
        drop = (
            2
            * (
                circuit.length_m * n * micro.iac_max_a * v[f"r{bid}"]
                + branch.spacing_m * pairs * micro.iac_max_a * v[f"rt{bid}"]
            )
            / 1000
            / voltage
            * 100
        )
        calc(
            f"vd{bid}",
            section,
            "Caída de tensión hasta el último microinversor",
            f"2*({{len{bid}}}*{{n{bid}}}*{{iac}}*{{r{bid}}}+{{sp{bid}}}*{{n{bid}}}*({{n{bid}}}-1)/2"
            f"*{{iac}}*{{rt{bid}}})/1000/{{vserv}}*100",
            drop,
            "%",
            shown=f"2 × ({_g(circuit.length_m)} m × {n} × I × r + {_g(branch.spacing_m)} m × "
            f"{_g(pairs)} × I × r troncal) / {_g(voltage)} V",
            limit=f"≤ {_g(spec.standards.vd_limits_pct.ac)} %",
            check=("<=", spec.standards.vd_limits_pct.ac),
            cite="VD-002",
        )
        calc(
            f"kaic{bid}",
            section,
            f"Capacidad interruptiva de {ocpd.id}",
            f"{ocpd.kaic_ka}",
            ocpd.kaic_ka,
            "kA",
            limit=f"≥ {_g(v['fault'])} kA",
            check=(">=", "fault"),
            cite="NOM 110-9",
        )

    tail = len(spec.ac_branches) + 4
    combiner = spec.ac_combiner
    if combiner is not None:
        section = f"{tail}. Caja combinadora {combiner.id}"
        tail += 1
        main = ocpds[combiner.main_ocpd]
        put(
            "itmmain",
            "Caja combinadora",
            f"{main.id} ({device_name(main.model, 'ITM')})",
            main.rating_a,
            "A",
            "Catálogo",
        )
        put("itot", "Caja combinadora", "Corriente de todas las ramas", total, "A", "Cálculo")
        calc(
            "minmain",
            section,
            "Protección mínima del ITM general",
            "1.25*{itot}",
            1.25 * total,
            "A",
            limit=f"≤ {_g(main.rating_a)} A ({main.id})",
            check=("<=", "itmmain"),
            cite="NOM 690-8(b)(1), MIC-020",
        )
    panel = spec.ac_bos.panels[0] if spec.ac_bos.panels else None
    main_breaker = spec.ac_bos.main_breakers[0] if spec.ac_bos.main_breakers else None
    if panel is not None and main_breaker is not None:
        backfed = [o for o in spec.ac_bos.ocpds if o.backfed and o.at == panel.id]
        put(
            "itmp",
            "Protecciones",
            f"{main_breaker.id} (principal)",
            main_breaker.rating_a,
            "A",
            "Servicio",
        )
        put("bus", "Protecciones", f"Barra de {panel.id}", panel.bus_a, "A", "Proyecto")
        sources = sum(o.rating_a for o in backfed)
        put(
            "sources",
            "Protecciones",
            "Suma de ITM de la generación en la barra",
            sources,
            "A",
            "Diseño",
        )
        calc(
            "rule120",
            f"{tail}. Barra del centro de carga",
            "Regla del 120 % de la barra",
            "{sources}+{itmp}",
            sources + main_breaker.rating_a,
            "A",
            shown=" + ".join(f"{_g(o.rating_a)} A" for o in backfed)
            + f" + {_g(main_breaker.rating_a)} A",
            limit=f"≤ {_g(1.2 * panel.bus_a)} A (1.2 × {_g(panel.bus_a)} A)",
            check=("<=", 1.2 * panel.bus_a),
            cite="NOM 705-12(d)(2), PCC-002",
        )
    return mem


__all__ = ["build_micro_memoria"]
