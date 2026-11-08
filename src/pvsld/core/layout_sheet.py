"""Layout template ``a3_plantilla_v1``: the schematic on the owner's A3 sheet template.

The sheet itself (frame, boxes, fixed texts, styles) comes from a :class:`SheetTemplate`; this
module places the schematic in the free area, numbers the circuits with the template's markers
(1: PV strings, 2: inverter output) and writes every field from the design: heading, system
capacity, service, calculation summary, notes, circuit boxes, module and inverter data, the
symbology of the blocks drawn and the drawing number and date.

Personal data (location, address, owner, RPU, responsible engineer, company) is written only when
the spec gives it (Stage 3.6.2, professional mode); what is missing stays a blank line to fill by
hand, and the title block reads "COMPAÑÍA INSTALADORA" without a company. A given value is cut
with "…" to the length of the blank line it replaces, so the user's data never breaks the sheet.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

from pvsld.core import calc, layers
from pvsld.core.calc import Derived
from pvsld.core.diagram import (
    A3,
    CircleItem,
    Diagram,
    Point,
    SymbolSample,
    Table,
    TextItem,
    Viewport,
    rnd,
    text_width_mm,
)
from pvsld.core.layout import (
    NO_VALUE,
    LayoutError,
    Placement,
    _conductor_text,
    _fit_widths,
    _g,
    _n,
    _protection_table,
    _raceway,
    _raceway_text,
    _wrap,
    build_schematic,
)
from pvsld.core.model import (
    NOM_EDITION,
    RULEPACK_ID,
    SCHEMA_VERSION,
    Circuit,
    Inverter,
    Ocpd,
    PvSystemSpec,
)
from pvsld.core.sheet import SheetTemplate
from pvsld.core.tables import get_tables
from pvsld.symbols import LIBRARY_VERSION, get_symbol

TEMPLATE = "a3_plantilla_v1"
BLANK = "_" * 27
BLANK_SHORT = "_" * 14
COMPANY = "COMPAÑÍA INSTALADORA"
OPEN_SANS_WIDTH = 1.0
"""Width of the sheet font over the Arial metrics of :func:`text_width_mm` (the template's Open
Sans is mapped to Arial, see ``FONT_MAP``)."""

PLACEMENT = Placement(
    inverter=(121.0, 195.0),
    string_x=16.0,
    string_dy=20.0,
    string_bend_mm=7.5,
    itm1_x=160.0,
    pi_x=185.0,
    panel_x=200.0,
    itmp_x=235.0,
    meter_x=259.0,
    grid_x=279.0,
    ground=(160.0, 150.0),
    gec_dc_text=(94.0, 146.0),
    gec_ac_text=(220.0, 152.0),
    callouts=False,
    pi_description="",
    dc_box_x=74.0,
    dc_disconnect_x=99.0,
    dc_scale=0.6,
    dc_spd_scale=0.5,
    full_strings=True,
)
PROTECTIONS_CORNER = (309.0, 102.0)
"""Lower-right corner of the protection schedule, in the free lower right of the schematic area."""
TABLE_TEXT = (1.4, 3.4, "OpenSans", "OpenSansCondensed-Bold")
"""Text height, row height, cell style and title style of the schedule (the template's boxes)."""
TABLE_TITLE_HEIGHT = 2.2
"""Height of the box titles of the template."""
GROUND_LABEL_DROP_MM = 18.5
"""From the electrode's port down to the end of its label (tag and two lines), plus a 2 mm gap: the
earthing bus is lifted until the label clears a tall protection schedule."""

# Width available to the value of each field group (sheet millimetres).
FIELD_WIDTHS: dict[str, float] = {
    "title": 250.0,
    "subtitle": 250.0,
    "location": 68.0,
    "address": 90.0,
    "installer": 72.0,
    "owner": 72.0,
    "capacity": 68.0,
    "memory": 121.0,
    "notes": 96.0,
    "circuit1": 82.0,
    "circuit2": 78.0,
    "module": 42.0,
    "inverter": 42.0,
    "symbology": 98.0,
    "company": 160.0,
    "project": 160.0,
    "design": 70.0,
    "review": 70.0,
    "drawing": 70.0,
    "date": 32.0,
}
MONTHS = ("ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC")
REGIMES = {
    "medicion_neta": "Medición neta",
    "facturacion_neta": "Facturación neta",
    "venta_total": "Venta total",
}
INVERTER_TYPES = {
    "string": "String",
    "central": "Central",
    "micro": "Microinversor",
    "hybrid": "Híbrido",
    "optimizer_string": "String con optimizadores",
}
PHASES = {1: "1F (L-N)", 2: "1F (L-L), 2 hilos", 3: "3F"}


def _thousands(value: float) -> str:
    return f"{value:,.0f}"


def _kwp(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _date(day: object) -> str:
    return f"{day.day:02d}-{MONTHS[day.month - 1]}-{day.year}"  # type: ignore[attr-defined]


class _Fields:
    """Collects field values and checks that each fits the width of its box."""

    def __init__(self, template: SheetTemplate) -> None:
        self.template = template
        self.items: list[TextItem] = []

    def set(self, name: str, text: str) -> None:
        item = self.template.field(name, text)
        group = name.split(".")[0]
        limit = FIELD_WIDTHS.get(group)
        width = text_width_mm(text, item.height) * OPEN_SANS_WIDTH
        if limit is not None and width > limit:
            raise LayoutError(
                f"sheet field {name}: {text!r} needs {width:.0f} mm but the box holds "
                f"{limit:g} mm; shorten the text"
            )
        self.items.append(item)

    def fit(self, name: str, value: str | None, blank: str, prefix: str = "") -> None:
        """``prefix + value`` cut to the width of ``prefix + blank``, or the blank line itself."""
        if not value:
            self.set(name, prefix + blank)
            return
        height = self.template.field(name, blank).height
        limit = text_width_mm(prefix + blank, height)
        text = prefix + value
        while len(text) > len(prefix) + 1 and text_width_mm(text, height) > limit:
            text = text[:-2] + "…"
        self.set(name, text)

    def lines(self, prefix: str, texts: Sequence[str]) -> None:
        anchors = sorted(
            (name for name in self.template.fields if name.startswith(f"{prefix}.")),
            key=lambda name: int(name.rsplit(".", 1)[1]) if name.rsplit(".", 1)[1].isdigit() else 0,
        )
        numbered = [name for name in anchors if name.rsplit(".", 1)[1].isdigit()]
        if len(texts) > len(numbered):
            raise LayoutError(
                f"sheet box {prefix}: {len(texts)} lines but the template has {len(numbered)}"
            )
        for name, text in zip(numbered, [*texts, *[""] * len(numbered)], strict=False):
            self.set(name, text)


# --- Field contents ------------------------------------------------------------------------------


def _strings_summary(spec: PvSystemSpec) -> tuple[int, str]:
    n_modules = sum(s.n_series for s in spec.strings)
    series = sorted({s.n_series for s in spec.strings})
    count = len(spec.strings)
    if len(series) == 1:
        text = f"{count} cadena{'s' if count > 1 else ''} de {series[0]} módulos en serie"
    else:
        text = f"{count} cadenas de {', '.join(map(str, series))} módulos en serie"
    return n_modules, text


def _degrees(value: float | str | None) -> str | None:
    """A number in decimal degrees; text the user wrote stays as written."""
    if value is None or isinstance(value, str):
        return value
    return f"{value:.5f}°"


def _service(spec: PvSystemSpec) -> str:
    utility = spec.utility
    volts = utility.nominal_voltage_v
    if utility.system in ("2F-3H", "3F-4H"):
        voltage = f"{volts}/{round(volts / math.sqrt(3))} V"
    else:
        voltage = f"{volts} V"
    return f"{utility.voltage_level} {utility.system} {voltage}  -  {REGIMES[utility.regime]}"


def _memory(spec: PvSystemSpec, derived: Derived, i1: Ocpd) -> list[str]:
    site = spec.project.site
    inverter = spec.inverters[0]
    first = derived.strings[0]
    mppts = {m.id: m for m in inverter.mppt}
    mppt = mppts[first.mppt_id]
    strings_per_mppt = max(
        sum(1 for s in spec.strings if s.mppt == m) for m in {s.mppt for s in spec.strings}
    )
    worst_voc = max(s.voc_max_string_v for s in derived.strings)
    lines = [
        f"CD:  Voc máx = {first.n_series} x {_n(first.voc_max_module_v, 2)} = "
        f"{_n(worst_voc)} V a {_g(site.t_min_c)} °C <= {_g(inverter.vdc_max_v)} V del inversor  OK",
        f"      Vmp = {_n(first.vmp_hot_string_v)} V (celda a {_g(derived.t_cell_max_c)} °C) a "
        f"{_n(first.vmp_cold_string_v)} V (frío), dentro de MPPT {_g(mppt.vmin_v)}-"
        f"{_g(mppt.vmax_v)} V  OK",
    ]
    isc_design = 1.25 * first.isc_a
    protections = {"DCB-": "ITM CD", "FUS-": "Fusible gPV"}
    string_devices = [d for d in spec.dc_bos.disconnects if d.id[:4] in protections]
    if string_devices:
        device = string_devices[0]
        fuse = (
            f"{protections[device.id[:4]]} {_g(device.ie_a)} A por cadena en la caja de "
            "protecciones CD"
        )
    elif strings_per_mppt == 1:
        fuse = "fusible de cadena no requerido (una cadena por MPPT, 690-9(a))"
    else:
        fuse = f"{strings_per_mppt} cadenas por MPPT"
    isc_max = _g(mppt.isc_max_a)
    lines.append(
        f"      Isc x 1.25 = {_n(isc_design, 2)} A <= Icc máx {isc_max} A/MPPT  OK ; {fuse}"
    )
    imp = first.imp_a
    if imp > mppt.imax_a:
        lines.append(
            f"      Imp {_n(imp, 2)} A > {_g(mppt.imax_a)} A/MPPT: el inversor limita la corriente "
            "(recorte en pico, STR-005)."
        )
    else:
        lines.append(f"      Imp {_n(imp, 2)} A <= {_g(mppt.imax_a)} A/MPPT  OK")
    out = next((c for c in spec.circuits if c.kind == "inverter_output"), None)
    values = {c.circuit_id: c for c in derived.circuits}
    if out is not None and out.id in values:
        v = values[out.id]
        i_out = inverter.iac_max_a
        lines.append(
            f"CA:  Isal máx {_g(i_out)} A x 1.25 = {_n(1.25 * i_out, 2)} A "
            f"-> ITM {i1.poles}P x {_g(i1.rating_a)} A (690-8) ; conductor {out.conductors.size} "
            f"{_n(v.ampacity_corrected_a or v.ampacity_75_a)} A  OK"
        )
    poc = spec.ac_bos.point_of_connection
    panel = next(p for p in spec.ac_bos.panels if p.id == poc.panel)
    if poc.type == "load_side":
        ratings = {o.id: o.rating_a for o in spec.ac_bos.ocpds}
        ratings.update({b.id: b.rating_a for b in spec.ac_bos.main_breakers})
        sources = [panel.main_ocpd] + [
            o.id for o in spec.ac_bos.ocpds if o.backfed and o.at == panel.id
        ]
        total = sum(ratings[name] for name in dict.fromkeys(sources))
        limit = 1.2 * panel.bus_a
        verdict = "OK" if total <= limit else "NO CUMPLE"
        summands = " + ".join(f"{_g(ratings[name])} A" for name in dict.fromkeys(sources))
        lines.append(
            f"      Regla 120 % (705-12(d)(2)): {_g(panel.bus_a)} A x 1.20 = {_g(limit)} A >= "
            f"{summands} = {_g(total)} A  {verdict} ; "
            f"CD/CA = {_thousands(derived.kwp_total * 1000)} / "
            f"{_thousands(derived.kwac_total * 1000)} = {_n(derived.dc_ac_ratio, 2)}"
        )
    grounding = spec.grounding
    electrode = grounding.electrodes[0]
    egc = sorted({c.egc.size for c in spec.circuits})
    lines.append(
        f"T:   Tierra de equipo {', '.join(egc)} ; GEC CD {grounding.gec_dc} / CA "
        f"{grounding.gec_ac} (250-66) ; electrodo {electrode.type} {_g(electrode.length_m)} m "
        f"<= {_g(grounding.design_resistance_ohm)} Ω"
    )
    return lines


def _notes(spec: PvSystemSpec, derived: Derived) -> list[str]:
    poc = spec.ac_bos.point_of_connection
    second = (
        "2. ITM FV en el extremo de la barra opuesto al interruptor principal (conexión del lado "
        "de la carga)."
        if poc.type == "load_side"
        else "2. Conexión del lado de suministro (705-12(a))."
    )
    return [
        f"1. Cumple {spec.standards.nom_edition}, Art. 690 y 705; simbología CFE G0100-04 y "
        "NMX-J-136-ANCE.",
        second,
        "3. Rotular: 'SISTEMA FV - FUENTE DE ENERGÍA ADICIONAL' en medidor, CC e inversor.",
        f"4. Borrador ({spec.standards.rulepack}): verificar barras e ITM principal en sitio; "
        "firma del responsable.",
    ]


def _devices(names: Sequence[str]) -> str:
    return f"; {', '.join(names)}" if names else ""


def _circuit_dc(spec: PvSystemSpec, derived: Derived) -> tuple[str, str, list[str]]:
    _n_modules, summary = _strings_summary(spec)
    circuit = next((c for c in spec.circuits if c.kind == "pv_source"), None)
    title = (
        "CADENA FV -> INVERSOR (CD)" if len(spec.strings) == 1 else "CADENAS FV -> INVERSOR (CD)"
    )
    first = derived.strings[0]
    lines: list[str] = []
    if circuit is not None:
        values = {c.circuit_id: c for c in derived.circuits}.get(circuit.id)
        lines += _conductor_lines(spec, circuit)
        voc, vmp = _n(first.voc_max_string_v), _n(first.vmp_stc_string_v)
        lines.append(f"Voc = {voc} V a T mín   Vmp = {vmp} V (STC)")
        if values is not None and values.i_max_a is not None:
            lines.append(
                f"Imp = {_n(first.imp_a, 2)} A   Isc = {_n(first.isc_a, 2)} A   Idis = "
                f"{_n(values.i_max_a)} A   Amp = {_n(values.ampacity_corrected_a or 0)} A"
                + (f"   ΔV {_n(values.vd_pct, 2)} %" if values.vd_pct is not None else "")
            )
    return title, summary, lines


def _circuit_ac(spec: PvSystemSpec, derived: Derived, i1: Ocpd) -> tuple[str, str, list[str]]:
    inverter = spec.inverters[0]
    circuit = next((c for c in spec.circuits if c.kind == "inverter_output"), None)
    title = "INVERSOR -> ITM FV, CENTRO DE CARGA (CA)"
    subtitle = f"{_g(inverter.vac_v)} Vca, {PHASES[inverter.phases]} + tierra, 60 Hz"
    subtitle += _devices([f"{s.id} {s.spd_type} en {s.at}" for s in spec.ac_bos.spds])
    lines: list[str] = []
    if circuit is not None:
        values = {c.circuit_id: c for c in derived.circuits}.get(circuit.id)
        lines += _conductor_lines(spec, circuit)
        if values is not None and values.i_max_a is not None:
            lines.append(
                f"Imáx = {_g(inverter.iac_max_a)} A   Idis = {_n(values.i_max_a, 2)} A -> ITM "
                f"{i1.poles}x{_g(i1.rating_a)} A"
            )
            lines.append(
                f"Amp {circuit.conductors.size} = {_n(values.ampacity_corrected_a or 0)} A >= "
                f"{_g(i1.rating_a)} A"
                + (
                    f"   ΔV = {_n(values.vd_pct, 2)} % (<= {_g(spec.standards.vd_limits_pct.ac)} %)"
                    if values.vd_pct is not None
                    else ""
                )
            )
    return title, subtitle, lines


def _conductor_lines(spec: PvSystemSpec, circuit: Circuit) -> list[str]:
    return [
        _conductor_text(circuit),
        f"1 x {circuit.egc.size} {circuit.egc.type} (puesta a tierra)",
        f"{_raceway_text(_raceway(spec, circuit), get_tables(spec.standards.nom_edition))}   "
        f"L = {_g(circuit.length_m)} m",
    ]


def _module_lines(spec: PvSystemSpec) -> list[str]:
    module = spec.modules[0]
    beta = f"{_g(module.beta_voc_pct_c)} %/°C" if module.beta_voc_pct_c is not None else NO_VALUE
    return [
        module.manufacturer.upper(),
        module.model,
        "Bifacial" if module.bifacial else "Monofacial",
        f"{_g(module.pmax_w)} Wp      Vmp: {_n(module.vmp_v, 2)} V",
        f"{_n(module.imp_a, 2)} A     Voc: {_n(module.voc_v, 2)} V",
        f"{_n(module.isc_a, 2)} A     Vsist: {_thousands(module.max_system_voltage_v)} V",
        f"{beta}  Fus. máx: {_g(module.max_series_fuse_a)} A",
        NO_VALUE,
        " / ".join(c.split(" (")[0] for c in module.certifications) or NO_VALUE,
    ]


def _inverter_lines(spec: PvSystemSpec) -> list[str]:
    inverter = spec.inverters[0]
    mppt = inverter.mppt[0]
    protections = ["Anti-isla"]
    if inverter.gfp:
        protections.append("GFP")
    if inverter.afci:
        protections.append("AFCI")
    if inverter.dc_spd_integrated:
        protections.append("SPD CD")
    kind = INVERTER_TYPES[inverter.type]
    return [
        inverter.manufacturer.upper(),
        inverter.model,
        f"{kind}, {PHASES[inverter.phases]}",
        f"{_thousands(inverter.pac_w)} W",
        f"{_g(inverter.vac_v)} Vca 60 Hz  Imáx: {_g(inverter.iac_max_a)} A",
        f"{_g(inverter.vdc_max_v)} V   MPPT: {_g(mppt.vmin_v)}-{_g(mppt.vmax_v)} V",
        f"{_g(mppt.imax_a)} A   Icc máx: {_g(mppt.isc_max_a)} A",
        ", ".join(protections),
        NO_VALUE,
    ]


def _symbology(
    symbols: Sequence[str], box: tuple[float, float, float, float]
) -> tuple[list[SymbolSample], list[TextItem]]:
    """Each block drawn, scaled into a cell of the symbology box, with its designation."""
    x0, y0, x1, y1 = box
    columns = 2
    rows = max(1, math.ceil(len(symbols) / columns))
    cell_w = (x1 - x0) / columns
    cell_h = min(11.0, (y1 - y0 - 2) / rows)
    icon_w, icon_h = 14.0, cell_h - 3.0
    style = "OpenSans"
    samples: list[SymbolSample] = []
    texts: list[TextItem] = []
    for index, name in enumerate(symbols):
        spec = get_symbol(name)
        bx0, by0, bx1, by1 = spec.bounds()
        scale = min(icon_w / (bx1 - bx0), icon_h / (by1 - by0), 1.0)
        column, row = index % columns, index // columns
        cx = x0 + column * cell_w + 1.0 + icon_w / 2
        cy = y1 - 1.0 - row * cell_h - cell_h / 2
        samples.append(
            SymbolSample(
                name,
                rnd(cx - (bx0 + bx1) / 2 * scale),
                rnd(cy - (by0 + by1) / 2 * scale),
                rnd(scale),
                spec.layer,
            )
        )
        text_x = x0 + column * cell_w + icon_w + 3.0
        wrapped = _wrap(spec.description_es, cell_w - icon_w - 4.0, 1.4 * OPEN_SANS_WIDTH)[:2]
        top = cy + (len(wrapped) - 1) * 1.1 - 0.7
        for line_no, line in enumerate(wrapped):
            texts.append(
                TextItem(
                    layers.LABELS,
                    rnd(text_x),
                    rnd(top - line_no * 2.2),
                    1.4,
                    line,
                    "paper",
                    style=style,
                )
            )
    return samples, texts


# --- Microinverter designs (Stage 3.10.4) --------------------------------------------------------


def _module_count(spec: PvSystemSpec) -> int:
    micro = sum(len(u.inputs) for b in spec.ac_branches for u in b.microinverters)
    return sum(s.n_series for s in spec.strings) + micro


def _micro(spec: PvSystemSpec) -> Inverter:
    return next(i for i in spec.inverters if i.type == "micro")


def _micro_memory(spec: PvSystemSpec, derived: Derived) -> list[str]:
    site = spec.project.site
    micro = _micro(spec)
    module = spec.modules[0]
    mppt = micro.mppt[0]
    voc = calc.voc_max_module_v(module, site.t_min_c, spec.standards.voc_method)
    vmp_hot = calc.vmp_module_v(module, derived.t_cell_max_c)
    data = micro.micro
    low, high = data.recommended_module_power_wp if data else (0.0, 0.0)
    lines = [
        f"CD:  Voc módulo = {_n(voc, 2)} V a {_g(site.t_min_c)} °C <= {_g(micro.vdc_max_v)} V ; "
        f"Isc {_n(module.isc_a, 2)} A <= {_g(mppt.isc_max_a)} A por entrada (sin 1.25)  OK",
        f"      Vmp = {_n(vmp_hot)} V (celda a {_g(derived.t_cell_max_c)} °C), MPPT "
        f"{_g(mppt.vmin_v)}-{_g(mppt.vmax_v)} V ; módulo {_g(module.pmax_w)} W, recomendado "
        f"{_g(low)}-{_g(high)} W",
    ]
    ocpds = {o.id: o for o in spec.ac_bos.ocpds}
    parts = []
    for branch in spec.ac_branches:
        n = len(branch.microinverters)
        current = n * micro.iac_max_a
        parts.append(
            f"{branch.id} {n}x{_g(micro.iac_max_a)} A x 1.25 = {_n(1.25 * current, 1)} A -> "
            f"{_g(ocpds[branch.ocpd].rating_a)} A ({branch.trunk_size})"
        )
    for index in range(0, len(parts), 2):
        prefix = "CA:  " if index == 0 else "      "
        lines.append(prefix + " ; ".join(parts[index : index + 2]) + "  (690-8, 240-4(d))")
    combiner = spec.ac_combiner
    if combiner is not None:
        total = sum(len(b.microinverters) for b in spec.ac_branches) * micro.iac_max_a
        main = ocpds[combiner.main_ocpd]
        lines.append(
            f"      {combiner.id}: {_n(total, 2)} A x 1.25 = {_n(1.25 * total, 1)} A -> "
            f"{main.id} {main.poles}P x {_g(main.rating_a)} A  OK"
        )
    poc = spec.ac_bos.point_of_connection
    panel = next(p for p in spec.ac_bos.panels if p.id == poc.panel)
    ratings = {o.id: o.rating_a for o in spec.ac_bos.ocpds}
    ratings.update({b.id: b.rating_a for b in spec.ac_bos.main_breakers})
    sources = [panel.main_ocpd] + [
        o.id for o in spec.ac_bos.ocpds if o.backfed and o.at == panel.id
    ]
    total_a = sum(ratings[name] for name in dict.fromkeys(sources))
    limit = 1.2 * panel.bus_a
    lines.append(
        f"      Regla 120 %: {_g(panel.bus_a)} A x 1.20 = {_g(limit)} A >= {_g(total_a)} A  "
        f"{'OK' if total_a <= limit else 'NO CUMPLE'} ; CD/CA = {_n(derived.dc_ac_ratio, 2)}"
    )
    grounding = spec.grounding
    electrode = grounding.electrodes[0]
    egc = sorted({c.egc.size for c in spec.circuits})
    lines.append(
        f"T:   Tierra de equipo {', '.join(egc)} ; GEC CA {grounding.gec_ac} (250-66) ; "
        f"electrodo {electrode.type} {_g(electrode.length_m)} m <= "
        f"{_g(grounding.design_resistance_ohm)} Ω"
    )
    return lines


def _micro_circuit1(spec: PvSystemSpec, derived: Derived) -> tuple[str, str, list[str]]:
    micro = _micro(spec)
    values = {c.circuit_id: c for c in derived.circuits}
    circuits = {c.from_.split(".", 1)[0]: c for c in spec.circuits}
    ocpds = {o.id: o for o in spec.ac_bos.ocpds}
    units = sum(len(b.microinverters) for b in spec.ac_branches)
    title = "RAMAS DE MICROINVERSORES -> ITM DE RAMA (CA)"
    subtitle = (
        f"{units} microinversores en {len(spec.ac_branches)} rama(s), "
        f"{_g(micro.vac_v)} Vca {PHASES[micro.phases]} + tierra"
    )
    first = circuits.get(spec.ac_branches[0].id)
    lines = _conductor_lines(spec, first)[:2] if first is not None else []
    for branch in spec.ac_branches:
        circuit = circuits.get(branch.id)
        v = values.get(circuit.id) if circuit else None
        ocpd = ocpds[branch.ocpd]
        text = (
            f"{branch.id}: {len(branch.microinverters)} micro(s), troncal {branch.trunk_size}, "
            f"{ocpd.id} {ocpd.poles}x{_g(ocpd.rating_a)} A"
        )
        if circuit is not None and v is not None:
            text += f", {circuit.conductors.size} L={_g(circuit.length_m)} m"
            if v.vd_pct is not None:
                text += f", ΔV {_n(v.vd_pct, 2)} %"
        lines.append(text)
    return title, subtitle, lines


def _micro_circuit2(spec: PvSystemSpec, derived: Derived) -> tuple[str, str, list[str]]:
    micro = _micro(spec)
    combiner = spec.ac_combiner
    spds = _devices([f"{s.id} {s.spd_type} en {s.at}" for s in spec.ac_bos.spds])
    subtitle = f"{_g(micro.vac_v)} Vca, {PHASES[micro.phases]} + tierra, 60 Hz{spds}"
    if combiner is None:
        ocpds = {o.id: o for o in spec.ac_bos.ocpds}
        breakers = ", ".join(f"{b.ocpd} {_g(ocpds[b.ocpd].rating_a)} A" for b in spec.ac_branches)
        panel = spec.ac_bos.point_of_connection.panel
        return (
            "ITM DE RAMA EN EL CENTRO DE CARGA (CA)",
            subtitle,
            [
                f"ITM de rama en {panel}: {breakers}",
                "Conexión del lado de la carga, extremo opuesto al principal",
            ],
        )
    circuit = next((c for c in spec.circuits if c.from_ == combiner.id), None)
    main = next(o for o in spec.ac_bos.ocpds if o.id == combiner.main_ocpd)
    lines: list[str] = []
    if circuit is not None:
        v = {c.circuit_id: c for c in derived.circuits}.get(circuit.id)
        lines += _conductor_lines(spec, circuit)
        if v is not None and v.i_max_a is not None:
            lines.append(
                f"Idis = {_n(v.i_max_a, 2)} A -> {main.id} {main.poles}x{_g(main.rating_a)} A"
                + (f"   ΔV = {_n(v.vd_pct, 2)} %" if v.vd_pct is not None else "")
            )
    return "CAJA COMBINADORA -> ITM FV, CENTRO DE CARGA (CA)", subtitle, lines


def _micro_inverter_lines(spec: PvSystemSpec) -> list[str]:
    micro = _micro(spec)
    mppt = micro.mppt[0]
    return [
        micro.manufacturer.upper(),
        micro.model,
        f"Microinversor, {PHASES[micro.phases]}",
        f"{micro.qty} x {_thousands(micro.pac_w)} W = {_thousands(micro.qty * micro.pac_w)} W",
        f"{_g(micro.vac_v)} Vca 60 Hz  Imáx: {_g(micro.iac_max_a)} A c/u",
        f"{_g(micro.vdc_max_v)} V   MPPT: {_g(mppt.vmin_v)}-{_g(mppt.vmax_v)} V",
        f"{_g(mppt.imax_a)} A   Icc máx: {_g(mppt.isc_max_a)} A por entrada",
        f"{len(micro.mppt)} entrada(s), un módulo por entrada",
        NO_VALUE,
    ]


def _clear_of_schedule(placement: Placement, schedule: Table) -> Placement:
    """Lift the earthing bus and its two GEC labels when the schedule reaches the electrode's label.

    The schedule grows upwards from a fixed corner, one row per protection device; with two string
    fuses it has eight rows and its top reached the last line of the label.
    """
    lift = schedule.y_top + GROUND_LABEL_DROP_MM - placement.ground[1]
    if lift <= 0:
        return placement

    def up(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0], rnd(point[1] + lift))

    return replace(
        placement,
        ground=up(placement.ground),
        gec_dc_text=up(placement.gec_dc_text),
        gec_ac_text=up(placement.gec_ac_text),
    )


def _protections(spec: PvSystemSpec) -> Table:
    """The protection schedule of v1 in the style of the template's boxes."""
    rows = _protection_table(spec).rows
    title = "CUADRO DE PROTECCIONES"
    header = ("Equipo", "Función", "Polos", "Capacidad", "Tensión", "kAIC", "Ubicación")
    text_height, row_height, style, title_style = TABLE_TEXT
    widths = list(_fit_widths(title, header, rows, text_height=text_height, minimum=7.0))
    title_width = text_width_mm(title, TABLE_TITLE_HEIGHT) + 4.0
    if sum(widths) < title_width:
        widths[-1] += title_width - sum(widths)
    x1, y0 = PROTECTIONS_CORNER
    height = row_height * (2 + len(rows))
    return Table(
        id="TBL-PROTECTIONS",
        layer=layers.TABLES,
        x=rnd(x1 - sum(widths)),
        y_top=rnd(y0 + height),
        col_widths=tuple(widths),
        title=title,
        header=header,
        rows=rows,
        row_height=row_height,
        text_height=text_height,
        style=style,
        title_style=title_style,
        title_center=True,
        title_height=TABLE_TITLE_HEIGHT,
    )


# --- The template -------------------------------------------------------------------------------


def build_sheet_diagram(
    spec: PvSystemSpec, derived: Derived | None, template: SheetTemplate
) -> Diagram:
    """Lay out ``spec`` on the owner's sheet ``template``.

    Raises:
        LayoutError: the spec is outside the schematic of the template, or a value does not fit
            its box.
    """
    from pvsld.sheets import a3_plantilla_v1 as definition  # the anchors of this template

    if spec.layout.template != TEMPLATE:
        raise LayoutError(f"unsupported layout template {spec.layout.template!r}")
    derived = derived or calc.derive(spec)
    schedule = _protections(spec)
    micro = bool(spec.ac_branches)
    if micro:
        from pvsld.core.layout_micro import build_micro_schematic

        ground_y = max(PLACEMENT.ground[1], schedule.y_top + GROUND_LABEL_DROP_MM)
        schematic = build_micro_schematic(spec, derived, PLACEMENT, ground_y=ground_y)
    else:
        schematic = build_schematic(spec, derived, _clear_of_schedule(PLACEMENT, schedule))
    i1 = schematic.i1

    # Circuit markers: 1 on each PV string conductor, 2 on the inverter output.
    radius = definition.MARKER_RADIUS_MM
    height, style = definition.MARKER_TEXT
    circles: list[CircleItem] = []
    marker_texts: list[TextItem] = []
    for conn in schematic.connections:
        if micro:  # 1 on each branch home run, 2 on the combiner output
            number = "2" if conn.circuit_id == "C-CCA" else "1"
        else:
            number = {"pv_source": "1", "inverter_output": "2"}.get(conn.kind)
        if number is None or conn.circuit_id is None:  # one marker per circuit, on its first run
            continue
        a, b = conn.points[0], conn.points[1]
        if micro and number == "2":  # the combiner output is the last run, from the bus
            a, b = conn.points[-2], conn.points[-1]
        centre = Point(rnd((a.x + b.x) / 2), rnd(a.y + radius + 1.8))
        circles.append(CircleItem(layers.TAGS, centre.x, centre.y, radius))
        marker_texts.append(
            TextItem(layers.TAGS, centre.x, centre.y, height, number, style=style, align="center")
        )

    fields = _Fields(template)
    n_modules = _module_count(spec)
    module = spec.modules[0]
    inverter = _micro(spec) if micro else spec.inverters[0]
    utility = spec.utility
    tb = spec.title_block
    level = "BAJA" if utility.voltage_level == "BT" else "MEDIA"
    fields.set(
        "title", f"SISTEMA FOTOVOLTAICO INTERCONECTADO A LA RED DE {_kwp(derived.kwp_total)} kWp"
    )
    fields.set(
        "subtitle",
        f"DIAGRAMA UNIFILAR  -  GENERACIÓN DISTRIBUIDA EN {level} TENSIÓN "
        f"({REGIMES[utility.regime].upper()})  -  SIMBOLOGÍA CFE G0100-04 / NMX-J-136-ANCE",
    )
    # Personal data: what the spec gives, else blank lines to fill by hand.
    site, client, responsible = spec.project.site, spec.project.client, tb.responsible
    address = site.address
    fields.fit("location.lat", _degrees(site.lat), BLANK[:22])
    fields.fit("location.lon", _degrees(site.lon), BLANK[:22])
    fields.set("location.altitude", BLANK[:22])
    fields.set("location.datum", "WGS84")
    street = " ".join(part for part in (address.street, address.number) if part)
    town = ", ".join(part for part in (address.municipio, address.estado) if part)
    fields.fit("address.1", street, BLANK, "CALLE: ")
    fields.fit("address.2", address.colonia, BLANK[:25], "COLONIA: ")
    fields.fit("address.3", town, BLANK + "______")
    fields.set("address.4", f"C.P.: {address.cp or '______'}   TARIFA: {utility.tariff}")
    fields.fit("installer.name", responsible.name, BLANK)
    fields.set("installer.email", BLANK)
    fields.set("installer.contact", BLANK)
    fields.fit("installer.cedula", responsible.cedula_profesional, BLANK)
    fields.fit("owner.name", client.name, BLANK)
    fields.fit("owner.rpu", utility.rpu, BLANK)
    fields.set("owner.service", _service(spec))
    fields.set("capacity.pdc", f"{_thousands(derived.kwp_total * 1000)} Wp")
    fields.set("capacity.pac", f"{_thousands(inverter.pac_w * inverter.qty)} W")
    fields.set("capacity.ratio", _n(derived.dc_ac_ratio, 2))
    fields.set("capacity.energy", "______ kWh/año")

    fields.lines("memory", _micro_memory(spec, derived) if micro else _memory(spec, derived, i1))
    fields.lines("notes", _notes(spec, derived))
    boxes = (
        (_micro_circuit1(spec, derived), _micro_circuit2(spec, derived))
        if micro
        else (_circuit_dc(spec, derived), _circuit_ac(spec, derived, i1))
    )
    for prefix, (title, subtitle, lines) in zip(("circuit1", "circuit2"), boxes, strict=True):
        fields.set(f"{prefix}.title", title)
        fields.set(f"{prefix}.subtitle", subtitle)
        fields.lines(prefix, lines)
    fields.lines("module", _module_lines(spec))
    fields.lines("inverter", _micro_inverter_lines(spec) if micro else _inverter_lines(spec))
    fields.set("symbology.title", "SIMBOLOGÍA (CFE G0100-04 / NMX-J-136-ANCE)")

    company = responsible.company.upper() if responsible.company else None
    fields.fit("company.name", company, COMPANY + "_" * 20 if company else COMPANY)
    fields.set("company.city", "")
    fields.set(
        "project",
        f"PROYECTO: SFVI DE {_kwp(derived.kwp_total)} kWp  -  {n_modules} x "
        f"{module.manufacturer.split()[0].upper()} {_g(module.pmax_w)} Wp + "
        + (f"{inverter.qty} x " if micro else "")
        + f"{inverter.manufacturer.upper()} {inverter.model}",
    )
    contact = " / ".join(part for part in (client.phone, client.email) if part)
    fields.fit("design.name", responsible.name, BLANK)
    fields.fit("design.cedula", responsible.cedula_profesional, BLANK_SHORT)
    fields.fit("review.name", tb.checked_by, BLANK)
    fields.set("review.cedula", BLANK_SHORT)
    fields.fit("owner.title_name", client.name, BLANK)
    fields.fit("owner.contact", contact, BLANK_SHORT)
    fields.set("drawing", f"{tb.drawing_no}   HOJA {tb.sheet.upper()}   REV. {tb.revision}")
    fields.set("date", _date(tb.date))

    # A full string is shown in the symbology as one module.
    drawn = list(
        dict.fromkeys(
            "PVSLD_PV_MODULE" if i.symbol.startswith("PVSLD_PV_STRING") else i.symbol
            for i in schematic.instances
        )
    )
    samples, symbology_texts = _symbology(drawn, definition.SYMBOLOGY_BOX)

    border = A3.border
    width = border.x1 - border.x0
    height_mm = border.y1 - border.y0
    viewport = Viewport(
        center=Point(rnd(border.x0 + width / 2), rnd(border.y0 + height_mm / 2)),
        size=(rnd(width), rnd(height_mm)),
        view_center=Point(rnd(border.x0 + width / 2), rnd(border.y0 + height_mm / 2)),
        view_height=rnd(height_mm),
    )
    metadata = (
        ("PROJECT_ID", spec.project.id),
        ("SCHEMA_VERSION", SCHEMA_VERSION),
        ("RULEPACK", RULEPACK_ID),
        ("NOM_EDITION", NOM_EDITION),
        ("LAYOUT_TEMPLATE", TEMPLATE),
        ("SHEET_TEMPLATE", template.name),
        ("SYMBOL_LIBRARY_VERSION", LIBRARY_VERSION),
    )
    return Diagram(
        sheet=A3,
        template=TEMPLATE,
        metadata=metadata,
        instances=schematic.instances,
        connections=schematic.connections,
        texts=(
            *schematic.texts,
            *marker_texts,
            *template.texts,
            *fields.items,
            *symbology_texts,
        ),
        lines=template.lines,
        polylines=(*template.polylines, *schematic.polylines),
        tables=(schedule,),
        viewport=viewport,
        circles=(*template.circles, *circles),
        samples=(*samples, *schematic.junctions),
        text_styles=template.text_styles,
        sheet_in_model=True,  # everything editable in one place (owner request)
    )
