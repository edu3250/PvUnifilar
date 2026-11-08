"""Layout template ``bt_string_residential_v1``: parameter model -> :class:`Diagram`.

All placement is computed here, on a fixed port grid, in sheet millimetres (ADR-0001, decision 2),
so the render backends stay thin. Flow is left to right (vault note "SLD Drafting Conventions"):

    strings S1, S2 -> inverter -> I1 breaker -> PI -> load centre -> I2 breaker -> MF meter -> grid

with the grounding electrode below the power path, the notes at the lower right of the schematic,
four ruled tables in the lower-left band, the symbol legend and the title and revision blocks in
the right band. One inverter and up to two strings, each on its own MPPT, are supported: this is
the single template of the S1 spike. A spec outside it raises :class:`LayoutError` with the reason
instead of drawing something wrong.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from pvsld.core import calc, layers
from pvsld.core.calc import Derived
from pvsld.core.diagram import (
    A3,
    Connection,
    Diagram,
    LineItem,
    Point,
    PolylineItem,
    PortRef,
    SymbolInstance,
    SymbolSample,
    Table,
    TextItem,
    Viewport,
    rnd,
    text_width_mm,
)
from pvsld.core.model import (
    NOM_EDITION,
    RULEPACK_ID,
    SCHEMA_VERSION,
    Circuit,
    DcDisconnect,
    Inverter,
    MainBreaker,
    Meter,
    Ocpd,
    Panel,
    PointOfConnection,
    PvSystemSpec,
    Raceway,
    address_text,
)
from pvsld.core.tables import NomTables, get_tables
from pvsld.symbols import LIBRARY_VERSION, get_symbol
from pvsld.symbols.cfe.definitions import (
    TITLE_BLOCK_FIELDS,
    TITLE_BLOCK_HEIGHT_MM,
    TITLE_BLOCK_WIDTH_MM,
    dc_disconnect_name,
    pv_string_name,
)

TEMPLATE = "bt_string_residential_v1"
GRID_COMP_ID = "GRID-1"
GROUND_COMP_ID = "PT-1"
TITLE_BLOCK_COMP_ID = "TTLB-1"
NO_VALUE = "—"
MEXICAN_GRID_FREQUENCY_HZ = 60

# --- Sheet regions (sheet millimetres, origin bottom-left of the A3 sheet) ---------------------
INVERTER_XY = (110.0, 220.0)
STRING_X = 20.0
STRING_DY = 20.0
STRING_BEND_MM = 7.5
ITM1_X = 216.0
PI_X = 244.0
PANEL_X = 266.0
ITMP_X = 322.0
METER_X = 350.0
GRID_X = 374.0
GROUND_XY = (206.0, 172.0)
TITLE_BLOCK_XY = (225.0, 10.0)
REVISION_X = 225.0
LEGEND_X, LEGEND_Y_TOP = 225.0, 140.0
TABLES_X, TABLES_Y_TOP, TABLES_GAP = 15.0, 140.0, 5.0
NOTES_X, NOTES_Y_TOP = 290.0, 187.0
NOTES_MAX_WIDTH_MM = 112.0
MIN_REVISION_ROWS, MAX_REVISION_ROWS = 3, 5
CALLOUT_LINE_MM = 3.5
SPD_DROP_MM = 3.5
"""How far below the string conductor the DC SPD hangs (times the SPD scale), without a box."""
SWITCH_OUTSIDE_MM = 2.0
"""A disconnect outside the box (integrated in the inverter) stands this much right of it."""
SPD_BEFORE_SWITCH_MM = 18.0
"""Without a box the SPD hangs this far left of the disconnect, on the upper string."""
JUNCTION_SCALE = 0.5
"""Scale of ``PVSLD_JUNCTION`` (a 2 mm dot) where an SPD tap leaves a string conductor."""


@dataclass(frozen=True)
class Schematic:
    """The power path of a spec: components, conductors and their texts."""

    instances: tuple[SymbolInstance, ...]
    connections: tuple[Connection, ...]
    texts: tuple[TextItem, ...]
    i1: Ocpd
    i2: MainBreaker
    polylines: tuple[PolylineItem, ...] = ()
    junctions: tuple[SymbolSample, ...] = ()
    """``PVSLD_JUNCTION`` dots where the SPD taps leave the string conductors."""


@dataclass(frozen=True)
class Placement:
    """Where the schematic sits on the sheet (sheet millimetres) and how circuits are labelled.

    ``callouts`` draws the four-line conductor callout over each circuit (template v1); without it
    the caller labels the circuits itself (the sheet template numbers them).
    """

    inverter: tuple[float, float]
    string_x: float
    string_dy: float
    string_bend_mm: float
    itm1_x: float
    pi_x: float
    panel_x: float
    itmp_x: float
    meter_x: float
    grid_x: float
    ground: tuple[float, float]
    gec_dc_text: tuple[float, float]
    gec_ac_text: tuple[float, float]
    callouts: bool = True
    pi_description: str = "Punto de interconexión"
    """Description under the PI marker; a compact sheet leaves it empty (the legend names it)."""
    dc_box_x: float | None = None
    """Left side of the DC protection box between the strings and the inverter; ``None`` draws
    the strings straight into the inverter (template v1)."""
    dc_disconnect_x: float | None = None
    """Left end of the DC disconnect when the sheet has no protection box (an integrated or a
    single external disconnect, drawn on each string conductor before the inverter)."""
    dc_scale: float = 1.0
    """Scale of the DC protection devices (0.6 fits them in the protection box)."""
    dc_spd_scale: float = 1.0
    """Scale of the DC SPD drawn without a box (smaller, between the strings)."""
    full_strings: bool = False
    """Draw every module of each string (``PVSLD_PV_STRING_<n>M_UP|DN``), level with its MPPT
    input; otherwise the two-module convention ``PVSLD_PV_STRING``."""


RESIDENTIAL = Placement(
    inverter=INVERTER_XY,
    string_x=STRING_X,
    string_dy=STRING_DY,
    string_bend_mm=STRING_BEND_MM,
    itm1_x=ITM1_X,
    pi_x=PI_X,
    panel_x=PANEL_X,
    itmp_x=ITMP_X,
    meter_x=METER_X,
    grid_x=GRID_X,
    ground=GROUND_XY,
    gec_dc_text=(140.0, 174.0),
    gec_ac_text=(226.0, 174.0),
)


class LayoutError(ValueError):
    """The spec cannot be drawn by this template; the message says why."""


# --- Formatting (NOM-008: decimal point, space before the unit) -----------------------------------


def _n(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}"


def _g(value: float) -> str:
    return f"{value:g}"


# --- Component builders ---------------------------------------------------------------------------


def _attributes(
    symbol_name: str, comp_id: str, values: dict[str, str]
) -> tuple[tuple[str, str], ...]:
    """Complete, ordered attribute values of a component (every tag of the symbol is filled)."""
    symbol = get_symbol(symbol_name)
    values = {
        "COMP_ID": comp_id,
        "IEC_REF": symbol.iec_ref,
        "NMX_REF": symbol.nmx_ref,
        "SOURCE_STANDARD": symbol.source,
        **values,
    }
    missing = [tag for tag in symbol.tags if tag not in values]
    extra = [tag for tag in values if tag not in symbol.tags]
    if missing or extra:
        raise LayoutError(f"{comp_id}: attribute tags mismatch (missing {missing}, extra {extra})")
    return tuple((tag, values[tag]) for tag in symbol.tags)


def _instance(
    symbol_name: str,
    comp_id: str,
    x: float,
    y: float,
    values: dict[str, str],
    *,
    space: str = "model",
    scale: float = 1.0,
    layer: str | None = None,
) -> SymbolInstance:
    return SymbolInstance(
        comp_id=comp_id,
        symbol=symbol_name,
        x=rnd(x),
        y=rnd(y),
        layer=layer or get_symbol(symbol_name).layer,
        attributes=_attributes(symbol_name, comp_id, values),
        space="paper" if space == "paper" else "model",
        scale=scale,
    )


def _string_instances(
    spec: PvSystemSpec, derived: Derived, inverter: Inverter, placement: Placement
) -> list[SymbolInstance]:
    modules = {m.id: m for m in spec.modules}
    out = []
    for index, (string, values) in enumerate(zip(spec.strings, derived.strings, strict=True)):
        module = modules[string.module]
        kwp = _n(values.p_stc_w / 1000, 2)
        symbol = "PVSLD_PV_STRING"
        port_y = get_symbol("PVSLD_INV").port(string.mppt).y
        if placement.full_strings:  # level with its MPPT input; the rows stack away from it
            y = placement.inverter[1] + port_y
            symbol = pv_string_name(string.n_series, "up" if port_y >= 0 else "down")
        elif len(spec.strings) == 1:  # level with its MPPT input: a straight conductor
            y = placement.inverter[1] + port_y
        else:
            y = placement.inverter[1] + (
                placement.string_dy if index == 0 else -placement.string_dy
            )
        out.append(
            _instance(
                symbol,
                string.id,
                placement.string_x,
                y,
                {
                    "TAG": string.id,
                    "DESC": f"{string.n_series} × {_g(module.pmax_w)} W = {kwp} kWp",
                    "MODEL": module.model,
                    "MFR": module.manufacturer,
                    "MPPT": string.mppt,
                    "PMAX_W": _g(module.pmax_w),
                    "N_SERIES": str(string.n_series),
                    "KWP": _n(values.p_stc_w / 1000, 2),
                    "VOC_MAX_V": _n(values.voc_max_string_v),
                    "VMP_V": _n(values.vmp_stc_string_v),
                    "ISC_A": _n(module.isc_a, 2),
                    "IMP_A": _n(module.imp_a, 2),
                },
            )
        )
    return out


def _inverter_instance(inverter: Inverter, placement: Placement) -> SymbolInstance:
    return _instance(
        "PVSLD_INV",
        inverter.id,
        *placement.inverter,
        {
            "TAG": inverter.id,
            "DESC": f"Inversor de red {_n(inverter.pac_w / 1000)} kW, {_g(inverter.vac_v)} V",
            "MFR": inverter.manufacturer,
            "MODEL": inverter.model,
            "PAC_W": _g(inverter.pac_w),
            "VAC_V": _g(inverter.vac_v),
            "PHASES": str(inverter.phases),
            "IAC_MAX_A": _g(inverter.iac_max_a),
            "VDC_MAX_V": _g(inverter.vdc_max_v),
            "MPPT_N": str(len(inverter.mppt)),
            "OCPD_MAX_A": _g(inverter.ocpd_max_a),
            "ISOLATION": inverter.isolation,
            "DC_SWITCH": "Integrado" if inverter.dc_switch_integrated else "No",
            "DC_SPD": inverter.dc_spd_integrated or "No",
        },
    )


def _breaker_instance(
    comp_id: str,
    x: float,
    y: float,
    *,
    role: str,
    poles: int,
    rating_a: float,
    voltage_v: float | None,
    kaic_ka: float | None,
    backfed: bool,
) -> SymbolInstance:
    voltage = _g(voltage_v) if voltage_v is not None else NO_VALUE
    desc = f"{poles}P {_g(rating_a)} A" + (f" {voltage} V" if voltage_v is not None else "")
    return _instance(
        "PVSLD_CB",
        comp_id,
        x,
        y,
        {
            "TAG": comp_id,
            "ROLE": role,
            "DESC": desc,
            "POLES": str(poles),
            "RATING_A": _g(rating_a),
            "VOLT_V": voltage,
            "KAIC_KA": _g(kaic_ka) if kaic_ka is not None else NO_VALUE,
            "BACKFED": "SI" if backfed else "NO",
        },
    )


def _key_devices(spec: PvSystemSpec) -> tuple[Ocpd, MainBreaker, Meter, Panel]:
    """Locate I1, I2, the fiscal meter and the panel of the point of connection."""
    i1 = next((o for o in spec.ac_bos.ocpds if o.role == "I1"), None)
    i2 = next((b for b in spec.ac_bos.main_breakers if b.role == "I2"), None)
    meter = next((m for m in spec.ac_bos.meters if m.role == "MF"), None)
    panel = next(p for p in spec.ac_bos.panels if p.id == spec.ac_bos.point_of_connection.panel)
    if i1 is None:
        raise LayoutError(f"template {TEMPLATE} needs an I1 breaker; validate the spec first")
    if i2 is None:
        raise LayoutError(f"template {TEMPLATE} needs an I2 main breaker; validate the spec first")
    if meter is None:
        raise LayoutError(f"template {TEMPLATE} needs an MF meter; validate the spec first")
    return i1, i2, meter, panel


# --- Routing -----------------------------------------------------------------------------------


def _hvh(a: Point, b: Point, *, bend_x: float | None = None) -> tuple[Point, ...]:
    """Horizontal, vertical, horizontal route (straight when the ends are level).

    The vertical leg is at ``bend_x``, by default half-way between the ends.
    """
    if a.y == b.y:
        return (a, b)
    mid = rnd((a.x + b.x) / 2) if bend_x is None else rnd(bend_x)
    return (a, Point(mid, a.y), Point(mid, b.y), b)


def _vh(a: Point, b: Point) -> tuple[Point, ...]:
    """Vertical then horizontal route."""
    if a.x == b.x or a.y == b.y:
        return (a, b)
    return (a, Point(a.x, b.y), b)


def _raceway(spec: PvSystemSpec, circuit: Circuit) -> Raceway:
    if circuit.raceway.ref is not None:
        return next(c for c in spec.circuits if c.id == circuit.raceway.ref).raceway
    return circuit.raceway


def _raceway_text(raceway: Raceway, tables: NomTables | None = None) -> str:
    """Raceway type and metric designation, with the trade size in inches for EMT (Table 4)."""
    if raceway.type is None:
        return NO_VALUE
    if raceway.trade_size_mm is None:
        return raceway.type
    size = f" {_g(raceway.trade_size_mm)} mm"
    if tables is not None and raceway.type.upper() == "EMT":
        trade = next((r[1] for r in tables.emt if r[0] == raceway.trade_size_mm), None)
        size += f' ({trade}")' if trade else ""
    return f"{raceway.type}{size}"


def _conductor_text(circuit: Circuit) -> str:
    base = f"{circuit.conductors.qty}-{circuit.conductors.size} {circuit.conductors.material} "
    base += circuit.conductors.insulation
    if circuit.neutral is not None:
        base += f" + N {circuit.neutral.size}"
    return base


def _callout(
    spec: PvSystemSpec, circuit: Circuit, vd_pct: float | None, start: Point
) -> list[TextItem]:
    """Conductor callout above the first segment of a wire (four lines, top line first)."""
    egc = f"1-{circuit.egc.size} {circuit.egc.type} (p.t.)"
    drop = f"; ΔV {_n(vd_pct, 2)} %" if vd_pct is not None else ""
    lines = [
        circuit.id,
        _conductor_text(circuit),
        f"+ {egc}",
        f"{_raceway_text(_raceway(spec, circuit))}; {_g(circuit.length_m)} m{drop}",
    ]
    x = rnd(start.x + 2)
    return [
        TextItem(
            layers.TAGS, x, rnd(start.y + 2 + (len(lines) - 1 - i) * CALLOUT_LINE_MM), 2.5, text
        )
        for i, text in enumerate(lines)
    ]


CELL_PADDING_MM = 1.5
LEFT_BAND_WIDTH_MM = 205.0
RIGHT_BAND_WIDTH_MM = 180.0


def _fit_widths(
    title: str,
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    text_height: float = 2.5,
    minimum: float = 10.0,
) -> tuple[float, ...]:
    """Column widths (0.5 mm steps) that hold the widest cell of each column and the title."""
    widths = []
    for column in range(len(header)):
        cells = [header[column], *(row[column] for row in rows)]
        widest = max(text_width_mm(cell, text_height) for cell in cells)
        widths.append(max(minimum, math.ceil((widest + 2 * CELL_PADDING_MM) * 2) / 2))
    needed = text_width_mm(title, text_height) + 2 * CELL_PADDING_MM
    if sum(widths) < needed:
        widths[-1] += math.ceil((needed - sum(widths)) * 2) / 2
    return tuple(widths)


def _check_fit(table: Table, max_width: float) -> Table:
    """Return ``table`` if its text fits its cells and the band, else raise :class:`LayoutError`."""
    for width, text in table.cells():
        if text_width_mm(text, table.text_height) + 2 * CELL_PADDING_MM > width:
            raise LayoutError(f"{table.id}: {text!r} does not fit its {width:g} mm column")
    if table.width > max_width:
        raise LayoutError(
            f"{table.id}: needs {table.width:g} mm but the band holds {max_width:g} mm; "
            "shorten the text or use a larger sheet"
        )
    return table


_BREAKS = re.compile(r"[^ ;\-]+[ ;\-]*")


def _chunks(text: str, limit_mm: float, text_height: float) -> list[str]:
    """Split ``text`` after spaces, semicolons and hyphens into pieces that fit ``limit_mm``."""
    lines: list[str] = []
    current = ""
    for token in _BREAKS.findall(text):
        if current and text_width_mm((current + token).rstrip(), text_height) > limit_mm:
            lines.append(current.rstrip())
            current = ""
        current += token
    lines.append(current.rstrip())
    return lines


def _wrap_rows(
    table_id: str,
    title: str,
    header: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    max_width: float,
    text_height: float = 2.5,
) -> tuple[tuple[str, ...], ...]:
    """Wrap the widest column onto continuation rows when the table is wider than its band.

    The widest column gets the width that makes the table fit; a cell longer than that continues in
    extra rows (other cells blank). Short tables come back unchanged, so their drawing is too.

    Raises:
        LayoutError: a single word of the widest column is wider than the room left for it.
    """
    widths = _fit_widths(title, header, rows)
    excess = sum(widths) - max_width
    if excess <= 0:
        return rows
    column = max(range(len(widths)), key=lambda i: widths[i])
    limit = widths[column] - excess - 2 * CELL_PADDING_MM
    wrapped: list[tuple[str, ...]] = []
    for row in rows:
        pieces = _chunks(row[column], limit, text_height)
        for piece in pieces:
            if text_width_mm(piece, text_height) > limit:
                raise LayoutError(
                    f"{table_id}: {piece!r} (column {header[column]!r}) cannot be wrapped into "
                    f"{limit:.1f} mm; shorten the text or use a larger sheet"
                )
        wrapped.append((*row[:column], pieces[0], *row[column + 1 :]))
        wrapped += [
            tuple(piece if i == column else "" for i in range(len(row))) for piece in pieces[1:]
        ]
    return tuple(wrapped)


def _auto(
    *,
    id: str,
    layer: str,
    x: float,
    y_top: float,
    title: str,
    header: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    row_height: float = 5.0,
    max_width: float | None = LEFT_BAND_WIDTH_MM,
) -> Table:
    """A table whose column widths are fitted to its content (wrapped to ``max_width``)."""
    if max_width is not None:
        rows = _wrap_rows(id, title, header, rows, max_width)
    return Table(
        id=id,
        layer=layer,
        x=x,
        y_top=y_top,
        col_widths=_fit_widths(title, header, rows),
        title=title,
        header=header,
        rows=rows,
        row_height=row_height,
    )


# --- Tables -------------------------------------------------------------------------------------


def _string_table(spec: PvSystemSpec, derived: Derived) -> Table:
    t_min = spec.project.site.t_min_c
    rows = [
        (
            v.string_id,
            f"{v.inverter_id}.{v.mppt_id}",
            str(v.n_series),
            next(m.model for m in spec.modules if m.id == v.module_id),
            _n(v.voc_max_string_v),
            _n(v.vmp_stc_string_v),
            _n(v.vmp_hot_string_v),
            _n(v.isc_a, 2),
            _n(v.imp_a, 2),
            _n(v.p_stc_w / 1000, 2),
        )
        for v in derived.strings
    ]
    rows.append(
        (
            "Total",
            "",
            str(sum(v.n_series for v in derived.strings)),
            "",
            "",
            "",
            "",
            "",
            "",
            _n(derived.kwp_total, 2),
        )
    )
    return _auto(
        id="TBL-STRINGS",
        layer=layers.TABLES,
        x=TABLES_X,
        y_top=TABLES_Y_TOP,
        title=f"TABLA DE RAMAS (Voc máx a T mín = {_g(t_min)} °C)",
        header=(
            "Rama",
            "MPPT",
            "Módulos",
            "Modelo",
            "Voc máx (V)",
            "Vmp STC (V)",
            "Vmp cal. (V)",
            "Isc (A)",
            "Imp (A)",
            "P (kWp)",
        ),
        rows=tuple(rows),
    )


def _equipment_table(spec: PvSystemSpec) -> Table:
    rows = []
    for m in spec.modules:
        beta = f"; β Voc {_g(m.beta_voc_pct_c)} %/°C" if m.beta_voc_pct_c is not None else ""
        rows.append(
            (
                "Módulo",
                f"{m.manufacturer} {m.model}",
                f"{_g(m.pmax_w)} W; Voc {_n(m.voc_v, 2)} V; Isc {_n(m.isc_a, 2)} A; "
                f"Vmp {_n(m.vmp_v, 2)} V; Imp {_n(m.imp_a, 2)} A{beta}",
            )
        )
    for inv in spec.inverters:
        vmin = min(x.vmin_v for x in inv.mppt)
        vmax = max(x.vmax_v for x in inv.mppt)
        rows.append(
            (
                "Inversor",
                f"{inv.manufacturer} {inv.model}",
                f"{_n(inv.pac_w / 1000)} kW; {_g(inv.vac_v)} V; {_g(inv.iac_max_a)} A; "
                f"Vcd máx {_g(inv.vdc_max_v)} V; MPPT {_g(vmin)}-{_g(vmax)} V; "
                f"{len(inv.mppt)} MPPT",
            )
        )
    return _auto(
        id="TBL-EQUIPMENT",
        layer=layers.TABLES,
        x=TABLES_X,
        y_top=0,
        title="DATOS DE EQUIPOS",
        header=("Equipo", "Fabricante y modelo", "Datos principales (STC y nominales)"),
        rows=tuple(rows),
    )


def _conductor_table(spec: PvSystemSpec, derived: Derived, tables: NomTables) -> Table:
    by_circuit = {c.circuit_id: c for c in derived.circuits}
    rows = []
    for circuit in spec.circuits:
        values = by_circuit[circuit.id]
        rows.append(
            (
                circuit.id,
                _n(values.i_max_a) if values.i_max_a is not None else NO_VALUE,
                _conductor_text(circuit),
                _g(tables.awg_mm2[circuit.conductors.size]),
                f"{circuit.egc.size} {circuit.egc.type}",
                _raceway_text(_raceway(spec, circuit)),  # no inches: v1's band is full
                _g(circuit.length_m),
                _n(values.ampacity_corrected_a)
                if values.ampacity_corrected_a is not None
                else NO_VALUE,
                _n(values.vd_pct, 2) if values.vd_pct is not None else NO_VALUE,
            )
        )
    return _auto(
        id="TBL-CONDUCTORS",
        layer=layers.TABLES,
        x=TABLES_X,
        y_top=0,
        title="CÉDULA DE CONDUCTORES",
        header=(
            "Circuito",
            "Imáx (A)",
            "Conductor",
            "mm²",
            "T (p.t.)",
            "Tubo",
            "L (m)",
            "Ampac. (A)",
            "ΔV (%)",
        ),
        rows=tuple(rows),
    )


_ROLE_TEXT = {
    "I1": "I1: Interruptor de la central",
    "I2": "I2: Interruptor principal del servicio",
    "other": "Interruptor termomagnético",
}


def _protection_table(spec: PvSystemSpec) -> Table:
    rows: list[tuple[str, ...]] = []
    for o in spec.ac_bos.ocpds:
        rows.append(
            (
                o.id,
                _ROLE_TEXT[o.role],
                str(o.poles),
                f"{_g(o.rating_a)} A",
                f"{_g(o.voltage_v)} V",
                f"{_g(o.kaic_ka)} kA",
                o.at or NO_VALUE,
            )
        )
    for b in spec.ac_bos.main_breakers:
        panel = next((p.id for p in spec.ac_bos.panels if p.main_ocpd == b.id), NO_VALUE)
        rows.append(
            (
                b.id,
                _ROLE_TEXT[b.role],
                str(b.poles),
                f"{_g(b.rating_a)} A",
                NO_VALUE,
                f"{_g(b.kaic_ka)} kA" if b.kaic_ka is not None else NO_VALUE,
                panel,
            )
        )
    for a in spec.ac_bos.disconnects:
        rows.append(
            (
                a.id,
                _ROLE_TEXT[a.role].replace("Interruptor", "Desconectador"),
                str(a.poles),
                f"{_g(a.rating_a)} A",
                NO_VALUE,
                NO_VALUE,
                NO_VALUE,
            )
        )
    string_functions = {"DCB-": "ITM de CD (cadena)", "FUS-": "Fusible gPV (cadena)"}
    boxed = bool(_string_devices(spec))
    for d in spec.dc_bos.disconnects:
        where = f"{d.integrated_in} (integrado)" if d.integrated_in else NO_VALUE
        if boxed and not d.integrated_in:
            where = "Caja CD"
        rows.append(
            (
                d.id,
                string_functions.get(d.id[:4], "Desconectador de CD"),
                str(d.poles),
                f"{_g(d.ie_a)} A",
                f"{_g(d.ue_v)} V",
                NO_VALUE,
                where,
            )
        )
    for side, spds in (("CD", spec.dc_bos.spds), ("CA", spec.ac_bos.spds)):
        for s in spds:
            volts = s.ucpv_v if s.ucpv_v is not None else s.uc_v
            rows.append(
                (
                    s.id,
                    f"DPS de {side} tipo {s.spd_type}",
                    NO_VALUE,
                    f"In {_g(s.in_ka)} kA",
                    f"{_g(volts)} V" if volts is not None else NO_VALUE,
                    NO_VALUE,
                    "Caja CD" if boxed and side == "CD" else s.at,  # drawn inside the box
                )
            )
    return _auto(
        id="TBL-PROTECTIONS",
        layer=layers.TABLES,
        x=TABLES_X,
        y_top=0,
        title="CUADRO DE PROTECCIONES",
        header=("Equipo", "Función", "Polos", "Capacidad", "Tensión", "kAIC", "Ubicación"),
        rows=tuple(rows),
    )


def _short_source(source: str) -> str:
    """The source of a symbol as the legend prints it: the standard and the clause or appendix."""
    if source.startswith("pvsld"):
        return "pvsld (sin símbolo oficial)"
    short = source.replace("Apéndices", "Ap.").replace("Apéndice", "Ap.")
    return short.split(" (")[0].split(";")[0]


def _legend_table(instances: Sequence[SymbolInstance]) -> Table:
    rows = []
    seen: set[str] = set()
    for item in instances:
        if item.symbol in seen or item.space == "paper":
            continue
        seen.add(item.symbol)
        symbol = get_symbol(item.symbol)
        rows.append((symbol.name, symbol.description_es, _short_source(symbol.source)))
    return _auto(
        id="TBL-LEGEND",
        layer=layers.NOTES,
        x=LEGEND_X,
        y_top=LEGEND_Y_TOP,
        title="CUADRO DE SIMBOLOGÍA (CFE G0100-04; NMX-J-136-ANCE-2019)",
        header=("Bloque", "Descripción", "Fuente"),
        rows=tuple(rows),
        max_width=RIGHT_BAND_WIDTH_MM,
        row_height=4.2,
    )


def _revision_table(spec: PvSystemSpec) -> Table:
    revisions = spec.title_block.revisions
    if len(revisions) > MAX_REVISION_ROWS:
        raise LayoutError(f"template {TEMPLATE} shows at most {MAX_REVISION_ROWS} revisions")
    rows = [
        (r.rev, r.date.isoformat(), r.description, r.by, r.approved_by or NO_VALUE)
        for r in revisions
    ]
    rows += [("", "", "", "", "")] * (MIN_REVISION_ROWS - len(rows))
    height = 5.0 * (2 + len(rows))
    return Table(
        id="TBL-REVISIONS",
        layer=layers.REVISIONS,
        x=REVISION_X,
        y_top=rnd(TITLE_BLOCK_XY[1] + TITLE_BLOCK_HEIGHT_MM + height),
        col_widths=(15, 30, 100, 20, 20),
        title="REGISTRO DE REVISIONES",
        header=("REV", "FECHA", "DESCRIPCIÓN", "POR", "APROBÓ"),
        rows=tuple(rows),
        space="paper",
    )


def _stack(tables: Sequence[Table]) -> list[Table]:
    """Place tables one under the other in the lower-left band, top to bottom."""
    placed: list[Table] = []
    y_top = TABLES_Y_TOP
    for table in tables:
        _check_fit(table, LEFT_BAND_WIDTH_MM)
        placed.append(
            Table(
                id=table.id,
                layer=table.layer,
                x=table.x,
                y_top=rnd(y_top),
                col_widths=table.col_widths,
                title=table.title,
                header=table.header,
                rows=table.rows,
                row_height=table.row_height,
                text_height=table.text_height,
                space=table.space,
            )
        )
        y_top -= table.height + TABLES_GAP
    if y_top + TABLES_GAP < A3.border.y0 + 2:
        raise LayoutError("the tables do not fit in the lower-left band of the sheet")
    return placed


# --- Notes --------------------------------------------------------------------------------------


def _notes(spec: PvSystemSpec, derived: Derived) -> list[str]:
    site = spec.project.site
    method = {
        "coefficient": "con el coeficiente β Voc del fabricante",
        "table_690_7": "con la Tabla 690-7",
        "iec_default_1_2": "con el factor 1.2 de IEC 60364-7-712",
    }[spec.standards.voc_method]
    grounding = spec.grounding
    dc = "sin aterrizar (690-35)" if grounding.dc_system == "ungrounded_690_35" else "aterrizado"
    return [
        f"1. Tensión máxima calculada {method} a T mín = {_g(site.t_min_c)} °C "
        f"({site.t_min_source}); {spec.standards.nom_edition}, 690-7.",
        f"2. Vmp en caliente con celdas a {_g(derived.t_cell_max_c)} °C (T amb máx "
        f"{_g(site.t_amb_max_c)} °C + {_g(calc.DELTA_T_CELL_C)} °C); se usa γ Pmax como "
        f"aproximación de γ Vmp.",
        f"3. Sistema de CD {dc}; unión CD-CA: {grounding.dc_ac_bond_method}; resistencia de "
        f"diseño del electrodo ≤ {_g(grounding.design_resistance_ohm)} Ω.",
        "4. Unidades conforme a NOM-008-SE-2021. Diagrama sin escala.",
        f"5. Reglas {spec.standards.rulepack}. Borrador generado automáticamente: debe ser "
        f"revisado y firmado por el responsable (cédula profesional) antes de presentarse.",
    ]


def _wrap(text: str, max_width: float, height: float) -> list[str]:
    """Greedy word wrap on the estimated rendered width."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and text_width_mm(candidate, height) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _note_items(notes: Sequence[str]) -> list[TextItem]:
    items = [TextItem(layers.NOTES, NOTES_X, NOTES_Y_TOP, 3.5, "NOTAS")]
    y = NOTES_Y_TOP - 5.0
    for note in notes:
        for number, line in enumerate(_wrap(note, NOTES_MAX_WIDTH_MM, 2.5)):
            indent = "" if number == 0 else "   "
            items.append(TextItem(layers.NOTES, NOTES_X, rnd(y), 2.5, indent + line))
            y -= 3.5
        y -= 0.6
    return items


# --- Title block --------------------------------------------------------------------------------


def _check_title_block(values: dict[str, str]) -> dict[str, str]:
    """Raise :class:`LayoutError` if a title block value would overflow its cell."""
    for tag, _caption, x0, x1, _row, height in TITLE_BLOCK_FIELDS:
        needed = text_width_mm(values[tag], height) + 2 * CELL_PADDING_MM
        if needed > x1 - x0:
            raise LayoutError(
                f"title block field {tag}: {values[tag]!r} needs {needed:.0f} mm "
                f"but the cell holds {x1 - x0:g} mm; shorten the text"
            )
    return values


def _title_block(spec: PvSystemSpec, derived: Derived) -> SymbolInstance:
    tb = spec.title_block
    site = spec.project.site
    address = site.address
    utility = spec.utility
    return _instance(
        "PVSLD_TTLB",
        TITLE_BLOCK_COMP_ID,
        *TITLE_BLOCK_XY,
        _check_title_block(
            {
                "PROYECTO": spec.project.name,
                "CLIENTE": spec.project.client.name or NO_VALUE,
                "UBICACION": address_text(address) or NO_VALUE,
                "RPU": utility.rpu or NO_VALUE,
                "NUM_SERVICIO": utility.service_number or NO_VALUE,
                "NUM_MEDIDOR": utility.meter_number or NO_VALUE,
                "TARIFA": utility.tariff,
                "TENSION_SUMINISTRO": f"{_g(utility.nominal_voltage_v)} V {utility.system}",
                "CAPACIDAD": f"{_n(derived.kwp_total, 2)} kWp / {_n(derived.kwac_total, 2)} kWac",
                "RESPONSABLE": tb.responsible.name or NO_VALUE,
                "CEDULA": tb.responsible.cedula_profesional or NO_VALUE,
                "UVIE": tb.uvie or "NO APLICA",
                "FECHA": tb.date.isoformat(),
                "EMPRESA": tb.responsible.company or NO_VALUE,
                "DIBUJO": tb.drawn_by,
                "REVISO": tb.checked_by or NO_VALUE,
                "APROBO": tb.approved_by or NO_VALUE,
                "NORMA": spec.standards.nom_edition,
                "TITULO": "Diagrama unifilar fotovoltaico",
                "PLANO_NO": tb.drawing_no,
                "HOJA": tb.sheet,
                "REV": tb.revision,
                "ESCALA": "SIN ESCALA",
                "TAG": TITLE_BLOCK_COMP_ID,
                "DESC": "Cuadro de datos del plano",
            }
        ),
        space="paper",
    )


# --- The template -------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Stop:
    """A device a string runs through, and its ports in and out."""

    comp_id: str
    in_port: str = "IN"
    out_port: str = "OUT"


@dataclass(frozen=True)
class _DcChain:
    """Devices of the DC side of one string, in order from the string to the inverter."""

    devices: tuple[_Stop, ...]
    circuit_run: int = 0
    """The run that carries the string circuit and its marker (after the field connector)."""


@dataclass(frozen=True)
class _DcDevices:
    """The DC protection devices of the sheet template and how they connect."""

    instances: tuple[SymbolInstance, ...] = ()
    chains: dict[str, _DcChain] = field(default_factory=dict)
    spd: SymbolInstance | None = None
    spd_taps: tuple[tuple[PortRef, str, float], ...] = ()
    """Each SPD feed: the device port it leaves, the SPD port it reaches, the x of its rise."""
    junctions: tuple[SymbolSample, ...] = ()
    outline: tuple[PolylineItem, TextItem] | None = None
    string_spds: tuple[tuple[str, PortRef, str, float], ...] = ()
    """Per-string box: each SPD id, the device port its tap leaves, its port, the rise x."""


STRING_DEVICE_SYMBOLS = {"DCB-": "PVSLD_CB_DC", "FUS-": "PVSLD_FUSE_DISC_DC"}
"""Id prefix of the sizing engine's string protection -> block: DC breaker, gPV fuse holder."""
DC_BOX_CAPTIONS = ("CAJA DE PROTECCIONES CD", "CAJA DE PROT. CD", "PROTECCIONES CD")
DC_BOX_CAPTION_MM = 1.5
DC_BOX_INSET_MM = 1.5
"""Gap between the box's entry terminal and the string protection."""
CONNECTOR_GAP_MM = 1.0
"""The field connector (MC4 type) sits this far right of the string's last module."""
SPD_TAP_MM = 2.5
"""The upper string's tap to the SPD drops this far right of the string protection."""
SPD_BOX_DROP_MM = 4.0
"""The SPD module's inputs sit this far below the lower string conductor."""
SWITCH_GAP_MM = 3.0
"""Gap between the lower tap to the SPD and the box disconnect."""
STRING_SPD_STEP_MM = 3.0
"""Per-string box: each string's SPD hangs this much right of the previous one, so the upper
SPD's earth lead passes clear of the lower SPD."""


def _string_devices(spec: PvSystemSpec) -> list[DcDisconnect]:
    """The string protections of the box (DCB-S1... or FUS-S1...), one per string, or none."""
    devices = sorted(
        (
            d
            for d in spec.dc_bos.disconnects
            if not d.integrated_in and d.id[:4] in STRING_DEVICE_SYMBOLS
        ),
        key=lambda d: d.id,
    )
    return devices if len(devices) == len(spec.strings) else []


def _dc_devices(
    spec: PvSystemSpec, inverter: Inverter, placement: Placement, string_out: dict[str, Point]
) -> _DcDevices:
    """The DC side between the strings and the inverter (sheet template).

    With one string protection per string the owner's reference box is drawn (2026-10-08); else
    an integrated or single external disconnect stands just before the inverter and the SPD hangs
    from the upper string (:func:`_dc_devices_without_box`).
    """
    if placement.dc_box_x is None or placement.dc_disconnect_x is None:
        return _DcDevices()
    devices = _string_devices(spec)
    if devices:
        return _protection_box(spec, inverter, placement, devices, string_out)
    return _dc_devices_without_box(spec, inverter, placement)


def _protection_box(
    spec: PvSystemSpec,
    inverter: Inverter,
    placement: Placement,
    devices: Sequence[DcDisconnect],
    string_out: dict[str, Point],
) -> _DcDevices:
    """The DC protection box of the owner's reference, with the library 0.8.0 blocks.

    Each string enters through its field connector and the box terminal, runs through its
    protection (gPV fuse-disconnector by default, or DC breaker), feeds the SPD module below the
    strings, then the one ganged disconnect of the box and the exit terminal. The upper string's
    tap crosses the lower string without a junction dot, as in the reference.
    """
    assert placement.dc_box_x is not None
    assert placement.dc_disconnect_x is not None
    k = placement.dc_scale
    x0 = placement.dc_box_x
    strings = list(spec.strings)
    inv_ports = get_symbol("PVSLD_INV")
    row = {s.id: placement.inverter[1] + inv_ports.port(s.mppt).y for s in strings}
    order = sorted(strings, key=lambda s: -row[s.id])  # upper string first
    dc = spec.dc_bos
    switch = next((d for d in dc.disconnects if not d.integrated_in and d not in devices), None)
    integrated = next((d for d in dc.disconnects if d.integrated_in == inverter.id), None)
    terminal_r = get_symbol("PVSLD_TERMINAL").bounds()[2] / 2 * k
    instances: list[SymbolInstance] = []
    stops: dict[str, list[_Stop]] = {s.id: [] for s in strings}
    by_string = dict(zip((s.id for s in strings), devices, strict=True))
    for string in strings:
        y, device = row[string.id], by_string[string.id]
        connector, entry = f"CX-{string.id}", f"XE-{string.id}"
        instances += [
            _instance(
                "PVSLD_PV_CONNECTOR",
                connector,
                string_out[string.id].x + CONNECTOR_GAP_MM,
                y,
                {"TAG": "", "DESC": "", "RATING_A": NO_VALUE, "VOLT_V": NO_VALUE},
                scale=k,
            ),
            _instance(
                "PVSLD_TERMINAL",
                entry,
                x0 - terminal_r,
                y,
                {"TAG": "", "DESC": "", "TERMINAL_NO": ""},
                scale=k,
                layer=layers.DC_CONDUCTORS,  # BYBLOCK colour: the terminal shows in DC blue
            ),
            _string_device(device, x0 + DC_BOX_INSET_MM, y, k),
        ]
        stops[string.id] += [_Stop(connector), _Stop(entry, "L", "R"), _Stop(device.id)]
    device_out = max(
        i.port_xy("OUT").x for i in instances if i.symbol in STRING_DEVICE_SYMBOLS.values()
    )
    spd_instance = None
    taps: list[tuple[PortRef, str, float]] = []
    junctions: list[SymbolSample] = []
    x_after = device_out
    per_string = any(d.id.startswith("DCD-S") for d in dc.disconnects)
    string_spds: list[tuple[str, PortRef, str, float]] = []
    if per_string:
        # Each string has its own SPD and two-pole switch; only the earth is shared (2026-11-04).
        spds = {spd.id.removeprefix("DPS-"): spd for spd in dc.spds if spd.id.startswith("DPS-S")}
        switches = {
            d.id.removeprefix("DCD-"): d for d in dc.disconnects if d.id.startswith("DCD-S")
        }
        for index, string in enumerate(order):
            spd = spds.get(string.id)
            if spd is None:
                continue
            volts = spd.ucpv_v if spd.ucpv_v is not None else spd.uc_v
            item = _instance(
                "PVSLD_SPD_DC_BOX",
                spd.id,
                device_out + SPD_TAP_MM + index * STRING_SPD_STEP_MM,
                row[string.id],
                {
                    "TAG": spd.id,
                    "DESC": "",
                    "SPEC": "",
                    "SPD_TYPE": spd.spd_type,
                    "UC_V": _g(volts) if volts else NO_VALUE,
                    "UP_KV": NO_VALUE,
                    "IN_KA": _g(spd.in_ka),
                },
                scale=k,
            )
            instances.append(item)
            rise = item.port_xy("L1").x
            string_spds.append((spd.id, PortRef(by_string[string.id].id, "OUT"), "L1", rise))
            junctions.append(
                SymbolSample(
                    "PVSLD_JUNCTION",
                    rnd(rise),
                    rnd(row[string.id]),
                    JUNCTION_SCALE,
                    get_symbol("PVSLD_JUNCTION").layer,
                    "model",
                )
            )
            x_after = max(x_after, rise)
        for string in order:
            switch_part = switches.get(string.id)
            if switch_part is None:
                continue
            instances.append(
                _instance(
                    dc_disconnect_name(1),
                    switch_part.id,
                    x_after + SWITCH_GAP_MM,
                    row[string.id],
                    {
                        "TAG": switch_part.id,
                        "DESC": "",
                        "N_STRINGS": "1",
                        "ROLE": "DCD",
                        "POLES": str(switch_part.poles),
                        "RATING_A": _g(switch_part.ie_a),
                        "VOLT_V": _g(switch_part.ue_v),
                        "LOAD_BREAK": "SI",
                    },
                )
            )
            stops[string.id].append(_Stop(switch_part.id, "IN1", "OUT1"))
        x_after = max(
            (i.port_xy("OUT1").x for i in instances if i.comp_id.startswith("DCD-S")),
            default=x_after,
        )
        # The box's own switches replace the inverter's integrated one in the drawing.
        switch = next(iter(switches.values()), None)
    elif dc.spds:
        spd = dc.spds[0]
        lower_y = min(row.values())
        x_spd = device_out + SPD_TAP_MM
        volts = spd.ucpv_v if spd.ucpv_v is not None else spd.uc_v
        spd_instance = _instance(
            "PVSLD_SPD_DC_BOX",
            spd.id,
            x_spd,
            lower_y - SPD_BOX_DROP_MM,
            {
                "TAG": spd.id,
                "DESC": f"DPS CD {spd.spd_type}",
                "SPEC": "",  # Uc and In are in the protection schedule
                "SPD_TYPE": spd.spd_type,
                "UC_V": _g(volts) if volts else NO_VALUE,
                "UP_KV": NO_VALUE,
                "IN_KA": _g(spd.in_ka),
            },
            scale=k,
        )
        for string, port in zip(order, ("L1", "L2"), strict=False):
            rise = spd_instance.port_xy(port).x
            taps.append((PortRef(by_string[string.id].id, "OUT"), port, rise))
            junctions.append(
                SymbolSample(
                    "PVSLD_JUNCTION",
                    rnd(rise),
                    rnd(row[string.id]),
                    JUNCTION_SCALE,
                    get_symbol("PVSLD_JUNCTION").layer,
                    "model",
                )
            )
        x_after = max(spd_instance.port_xy(port).x for _tap, port, _x in taps)
    if switch is not None and not per_string:
        name = dc_disconnect_name(len(strings))
        top = row[order[0].id]
        instances.append(
            _instance(
                name,
                switch.id,
                x_after + SWITCH_GAP_MM,
                top,
                {
                    "TAG": switch.id,
                    "DESC": "",  # the protection schedule says what it is
                    "N_STRINGS": str(len(strings)),
                    "ROLE": "DCD",
                    "POLES": str(switch.poles),
                    "RATING_A": _g(switch.ie_a),
                    "VOLT_V": _g(switch.ue_v),
                    "LOAD_BREAK": "SI",
                },
            )
        )
        for index, string in enumerate(order, start=1):
            stops[string.id].append(_Stop(switch.id, f"IN{index}", f"OUT{index}"))
        x_after = instances[-1].port_xy("OUT1").x
    boxed = [i for i in instances if not i.comp_id.startswith("CX-")]
    if spd_instance is not None:
        boxed.append(spd_instance)
    parts = [box for item in boxed for _name, box in item.boxes()]
    x1 = rnd(max(x_after, *(b.x1 for b in parts)) + 1.0 + terminal_r)
    for string in strings:
        exit_id = f"XS-{string.id}"
        instances.append(
            _instance(
                "PVSLD_TERMINAL",
                exit_id,
                x1 - terminal_r,
                row[string.id],
                {"TAG": "", "DESC": "", "TERMINAL_NO": ""},
                scale=k,
                layer=layers.DC_CONDUCTORS,  # BYBLOCK colour: the terminal shows in DC blue
            )
        )
        stops[string.id].append(_Stop(exit_id, "L", "R"))
    if switch is None and integrated is not None:
        for string in strings:
            comp_id = integrated.id if len(strings) == 1 else f"{integrated.id}/{string.id}"
            instances.append(
                _disconnect_instance(
                    integrated,
                    comp_id,
                    x1 + SWITCH_OUTSIDE_MM,
                    row[string.id],
                    k,
                    tag=string is order[0],
                )
            )
            stops[string.id].append(_Stop(comp_id))
    y1 = rnd(max(b.y1 for b in parts) + 1.0)
    y0 = rnd(min(b.y0 for b in parts) - 1.0)
    outline = PolylineItem(
        layers.ENCLOSURES,
        (Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1)),
        closed=True,
    )
    text = next(
        (
            c
            for c in DC_BOX_CAPTIONS
            if x0 + text_width_mm(c, DC_BOX_CAPTION_MM) < placement.inverter[0] - 1
        ),
        DC_BOX_CAPTIONS[-1],
    )
    caption = TextItem(layers.ENCLOSURES, x0, rnd(y1 + 1.5), DC_BOX_CAPTION_MM, text)
    chains = {s.id: _DcChain(tuple(stops[s.id]), circuit_run=1) for s in strings}
    return _DcDevices(
        tuple(instances),
        chains,
        spd_instance,
        tuple(taps),
        tuple(junctions),
        (outline, caption),
        tuple(string_spds),
    )


def _string_device(device: DcDisconnect, x: float, y: float, k: float) -> SymbolInstance:
    """The string protection: a gPV fuse-disconnector (FUS-) or a DC breaker (DCB-)."""
    symbol = STRING_DEVICE_SYMBOLS[device.id[:4]]
    if symbol == "PVSLD_FUSE_DISC_DC":
        values = {
            "TAG": device.id,
            "DESC": f"gPV {_g(device.ie_a)} A",
            "POLES": str(device.poles),
            "RATING_A": NO_VALUE,  # the holder's own rating is chosen with the fuse
            "VOLT_V": _g(device.ue_v),
            "FUSE_A": _g(device.ie_a),
            "FUSE_CLASS": "gPV",
        }
    else:
        values = {
            "TAG": device.id,
            "DESC": f"{device.poles}P {_g(device.ie_a)} A",
            "ROLE": "ITM CD",
            "POLES": str(device.poles),
            "RATING_A": _g(device.ie_a),
            "VOLT_V": _g(device.ue_v),
            "KAIC_KA": NO_VALUE,
        }
    return _instance(symbol, device.id, x, y, values, scale=k)


def _disconnect_instance(
    switch: DcDisconnect, comp_id: str, x: float, y: float, k: float, *, tag: bool = True
) -> SymbolInstance:
    """One pole pair of a disconnect drawn on a string conductor (``PVSLD_DC_DISCONNECT``)."""
    return _instance(
        "PVSLD_DC_DISCONNECT",
        comp_id,
        x,
        y,
        {
            "TAG": switch.id if tag else "",  # one tag for the whole switch, on the upper pole
            "DESC": "",  # the protection schedule says where it is (no room here)
            "ROLE": "DCD",
            "POLES": str(switch.poles),
            "RATING_A": _g(switch.ie_a),
            "VOLT_V": _g(switch.ue_v),
            "LOAD_BREAK": "SI",
        },
        scale=k,
    )


def _dc_devices_without_box(
    spec: PvSystemSpec, inverter: Inverter, placement: Placement
) -> _DcDevices:
    """No string protection: the integrated (or single external) disconnect before the inverter,
    and the SPD tapped from the upper string before it."""
    assert placement.dc_box_x is not None
    assert placement.dc_disconnect_x is not None
    dc = spec.dc_bos
    strings = list(spec.strings)
    switch = next((d for d in dc.disconnects if d.integrated_in == inverter.id), None)
    external = [d for d in dc.disconnects if not d.integrated_in]
    if switch is None and len(external) == 1:
        switch = external[0]
    inv_ports = get_symbol("PVSLD_INV")
    row = {s.id: placement.inverter[1] + inv_ports.port(s.mppt).y for s in strings}
    order = sorted(strings, key=lambda s: -row[s.id])
    instances: list[SymbolInstance] = []
    chains: dict[str, _DcChain] = {}
    for string in strings:
        if switch is None:
            chains[string.id] = _DcChain(())
            continue
        comp_id = switch.id if len(strings) == 1 else f"{switch.id}/{string.id}"
        instances.append(
            _disconnect_instance(
                switch,
                comp_id,
                placement.dc_disconnect_x + SWITCH_OUTSIDE_MM,
                row[string.id],
                placement.dc_scale,
                tag=string is order[0],
            )
        )
        chains[string.id] = _DcChain((_Stop(comp_id),))
    if not dc.spds or not order or not chains[order[0].id].devices:
        return _DcDevices(tuple(instances), chains)
    spd = dc.spds[0]
    tap = chains[order[0].id].devices[0].comp_id
    point = next(i for i in instances if i.comp_id == tap).port_xy("IN")
    k = placement.dc_spd_scale
    # Before the disconnect (the SPD must not hang by the inverter), on the string conductor.
    spd_x = placement.dc_disconnect_x - SPD_BEFORE_SWITCH_MM
    volts = spd.ucpv_v if spd.ucpv_v is not None else spd.uc_v
    spd_instance = _instance(
        "PVSLD_SPD",
        spd.id,
        spd_x,
        point.y - SPD_DROP_MM * k,
        {
            "TAG": spd.id,
            "DESC": f"DPS CD {spd.spd_type}",
            "SPEC": "",  # Uc and In are in the protection schedule
            "SPD_TYPE": spd.spd_type,
            "UC_V": _g(volts) if volts else NO_VALUE,
            "UP_KV": NO_VALUE,
            "IN_KA": _g(spd.in_ka),
        },
        scale=k,
    )
    junction = SymbolSample(
        "PVSLD_JUNCTION",
        rnd(spd_x),
        rnd(point.y),
        JUNCTION_SCALE,
        get_symbol("PVSLD_JUNCTION").layer,
        "model",
    )
    return _DcDevices(
        tuple(instances), chains, spd_instance, ((PortRef(tap, "IN"), "L", spd_x),), (junction,)
    )


def build_schematic(spec: PvSystemSpec, derived: Derived, placement: Placement) -> Schematic:
    """Components and conductors of ``spec`` placed by ``placement`` (shared by every template).

    Raises:
        LayoutError: more than one inverter or two strings, an unknown MPPT or a missing I1, I2
            or MF device.
    """
    if len(spec.inverters) != 1:
        raise LayoutError(
            f"template {TEMPLATE} draws exactly one inverter, got {len(spec.inverters)}"
        )
    inverter = spec.inverters[0]
    if len(spec.strings) > 2:
        raise LayoutError(f"template {TEMPLATE} draws at most two strings, got {len(spec.strings)}")
    allowed = {p.id for p in get_symbol("PVSLD_INV").ports if p.kind == "DC"}
    for string in spec.strings:
        if string.mppt not in allowed:
            raise LayoutError(f"string {string.id}: MPPT {string.mppt!r} has no port in the symbol")
    i1, i2, meter, panel = _key_devices(spec)

    # Components, in drawing order.
    instances: list[SymbolInstance] = [*_string_instances(spec, derived, inverter, placement)]
    instances.append(_inverter_instance(inverter, placement))
    instances.append(
        _breaker_instance(
            i1.id,
            placement.itm1_x,
            placement.inverter[1],
            role="I1",
            poles=i1.poles,
            rating_a=i1.rating_a,
            voltage_v=i1.voltage_v,
            kaic_ka=i1.kaic_ka,
            backfed=i1.backfed,
        )
    )
    poc = spec.ac_bos.point_of_connection
    instances.append(
        _instance(
            "PVSLD_PI",
            poc.id,
            placement.pi_x,
            placement.inverter[1],
            {
                "TAG": poc.id,
                "DESC": placement.pi_description,
                "PI_TYPE": poc.type,
                "PANEL": poc.panel,
                "BREAKER": poc.breaker,
            },
        )
    )
    instances += service_instances(spec, placement, i2, meter, panel)
    instances.append(ground_instance(spec, placement.ground))
    string_out = {
        i.comp_id: i.port_xy("OUT") for i in instances if i.comp_id in {s.id for s in spec.strings}
    }
    return _finish_schematic(
        spec, derived, placement, inverter, instances, string_out, i1, i2, meter, panel, poc
    )


def service_instances(
    spec: PvSystemSpec, placement: Placement, i2: MainBreaker, meter: Meter, panel: Panel
) -> list[SymbolInstance]:
    """The load centre, the service main, the fiscal meter and the grid, on the AC row."""
    instances: list[SymbolInstance] = []
    instances.append(
        _instance(
            "PVSLD_PANEL",
            panel.id,
            placement.panel_x,
            placement.inverter[1],
            {
                "TAG": panel.id,
                "DESC": panel.name,
                "SPEC": f"Barra {_g(panel.bus_a)} A; {panel.system}; {_g(panel.kaic_ka)} kA",
                "NAME": panel.name,
                "BUS_A": _g(panel.bus_a),
                "MAIN_ID": panel.main_ocpd,
                "MAIN_A": _g(i2.rating_a),
                "SYSTEM": panel.system,
                "KAIC_KA": _g(panel.kaic_ka),
            },
        )
    )
    instances.append(
        _breaker_instance(
            i2.id,
            placement.itmp_x,
            placement.inverter[1],
            role="I2",
            poles=i2.poles,
            rating_a=i2.rating_a,
            voltage_v=None,
            kaic_ka=None,
            backfed=False,
        )
    )
    instances.append(
        _instance(
            "PVSLD_METER",
            meter.id,
            placement.meter_x,
            placement.inverter[1],
            {
                "TAG": meter.id,
                "DESC": "Medidor MF",
                "METER_TYPE": "MF",
                "BIDIRECTIONAL": "SI" if meter.bidirectional else "NO",
                "OWNER": meter.owner,
                "METER_NO": meter.meter_no or "",
            },
        )
    )
    utility = spec.utility
    instances.append(
        _instance(
            "PVSLD_GRID",
            GRID_COMP_ID,
            placement.grid_x,
            placement.inverter[1],
            {
                "TAG": "RED-1",
                "DESC": f"Red {utility.supplier}",
                "SPEC": f"{_g(utility.nominal_voltage_v)} V {utility.system} "
                f"{MEXICAN_GRID_FREQUENCY_HZ} Hz",
                "VOLT_V": _g(utility.nominal_voltage_v),
                "SYSTEM": utility.system,
                "FREQ_HZ": str(MEXICAN_GRID_FREQUENCY_HZ),
                "OWNER": utility.supplier,
                "ISC_KA": _g(utility.available_fault_current_ka),
            },
        )
    )
    return instances


def ground_instance(spec: PvSystemSpec, xy: tuple[float, float]) -> SymbolInstance:
    """The ground electrode with its type, length and design resistance."""
    electrode = spec.grounding.electrodes[0]
    return _instance(
        "PVSLD_GND",
        GROUND_COMP_ID,
        *xy,
        {
            "TAG": GROUND_COMP_ID,
            "DESC": "Electrodo de puesta a tierra",
            "SPEC": f"{electrode.type.capitalize()} {_n(electrode.length_m, 2)} m; "
            f"≤ {_g(spec.grounding.design_resistance_ohm)} Ω",
            "ELECTRODE": electrode.type,
            "R_OHM": _g(spec.grounding.design_resistance_ohm),
            "GEC_SIZE": spec.grounding.gec_dc,
        },
    )


def _finish_schematic(
    spec: PvSystemSpec,
    derived: Derived,
    placement: Placement,
    inverter: Inverter,
    instances: list[SymbolInstance],
    string_out: dict[str, Point],
    i1: Ocpd,
    i2: MainBreaker,
    meter: Meter,
    panel: Panel,
    poc: PointOfConnection,
) -> Schematic:
    dc = _dc_devices(spec, inverter, placement, string_out)
    dc_chains = dc.chains
    spd_instance = dc.spd
    instances += dc.instances
    if spd_instance is not None:
        instances.append(spd_instance)
    by_id = {item.comp_id: item for item in instances}

    # Conductors.
    connections: list[Connection] = []
    texts: list[TextItem] = []
    values_by_circuit = {c.circuit_id: c for c in derived.circuits}

    def connect(
        conn_id: str,
        kind: str,
        layer: str,
        start: PortRef,
        end: PortRef,
        *,
        route: str = "hvh",
        bend_x: float | None = None,
        circuit: Circuit | None = None,
    ) -> None:
        a = by_id[start.comp_id].port_xy(start.port)
        b = by_id[end.comp_id].port_xy(end.port)
        points = _hvh(a, b, bend_x=bend_x) if route == "hvh" else _vh(a, b)
        connections.append(
            Connection(conn_id, kind, layer, start, end, points, circuit.id if circuit else None)
        )
        if circuit is not None and placement.callouts:
            texts.extend(_callout(spec, circuit, values_by_circuit[circuit.id].vd_pct, points[0]))

    for string in spec.strings:
        circuit = next(
            (
                c
                for c in spec.circuits
                if c.kind == "pv_source" and c.from_.split(".")[0] == string.id
            ),
            None,
        )
        chain = dc_chains.get(string.id, _DcChain(()))
        stops = [
            PortRef(string.id, "OUT"),
            *(PortRef(d.comp_id, port) for d in chain.devices for port in (d.in_port, d.out_port)),
            PortRef(inverter.id, string.mppt),
        ]
        for segment, (start, end) in enumerate(zip(stops[::2], stops[1::2], strict=True)):
            first = segment == chain.circuit_run
            connect(
                (circuit.id if circuit else f"DC-{string.id}") + ("" if first else f"-{segment}"),
                "pv_source",
                layers.DC_CONDUCTORS,
                start,
                end,
                # Bend next to the inverter, clear of the callout above the string's wire.
                bend_x=placement.inverter[0] - placement.string_bend_mm,
                circuit=circuit if first else None,
            )
    inverter_circuit = next((c for c in spec.circuits if c.kind == "inverter_output"), None)
    connect(
        inverter_circuit.id if inverter_circuit else "AC-INV",
        "inverter_output",
        layers.AC_CONDUCTORS,
        PortRef(inverter.id, "AC"),
        PortRef(i1.id, "IN"),
        circuit=inverter_circuit,
    )
    chain = [
        (PortRef(i1.id, "OUT"), PortRef(poc.id, "IN")),
        (PortRef(poc.id, "OUT"), PortRef(panel.id, "LEFT")),
        (PortRef(panel.id, "RIGHT"), PortRef(i2.id, "IN")),
        (PortRef(i2.id, "OUT"), PortRef(meter.id, "IN")),
        (PortRef(meter.id, "OUT"), PortRef(GRID_COMP_ID, "IN")),
    ]
    for index, (start, end) in enumerate(chain, start=1):
        connect(f"NET-{index:02d}", "ac_network", layers.AC_CONDUCTORS, start, end)
    connect(
        f"GND-{inverter.id}",
        "grounding",
        layers.GROUNDING,
        PortRef(inverter.id, "PE"),
        PortRef(GROUND_COMP_ID, "PE"),
        route="vh",
    )
    connect(
        f"GND-{panel.id}",
        "grounding",
        layers.GROUNDING,
        PortRef(panel.id, "PE"),
        PortRef(GROUND_COMP_ID, "PE"),
        route="vh",
    )
    for spd_id, tap, port, rise_x in dc.string_spds:
        connect(
            f"DC-{spd_id}",
            "pv_source",
            layers.DC_CONDUCTORS,
            tap,
            PortRef(spd_id, port),
            bend_x=rise_x,
        )
        connect(
            f"GND-{spd_id}",
            "grounding",
            layers.GROUNDING,
            PortRef(spd_id, "PE"),
            PortRef(GROUND_COMP_ID, "PE"),
            route="vh",
        )
    if spd_instance is not None:
        for index, (tap, port, rise_x) in enumerate(dc.spd_taps):
            connect(
                f"DC-{spd_instance.comp_id}" + (f"-{index + 1}" if index else ""),
                "pv_source",
                layers.DC_CONDUCTORS,
                tap,
                PortRef(spd_instance.comp_id, port),
                bend_x=rise_x,
            )
        connect(
            f"GND-{spd_instance.comp_id}",
            "grounding",
            layers.GROUNDING,
            PortRef(spd_instance.comp_id, "PE"),
            PortRef(GROUND_COMP_ID, "PE"),
            route="vh",
        )
    gec_dc = f"GEC CD: {spec.grounding.gec_dc} Cu desnudo"
    gec_ac = f"GEC CA: {spec.grounding.gec_ac} Cu desnudo"
    texts.append(TextItem(layers.TAGS, *placement.gec_dc_text, 2.5, gec_dc))
    texts.append(TextItem(layers.TAGS, *placement.gec_ac_text, 2.5, gec_ac))
    polylines: tuple[PolylineItem, ...] = ()
    if dc.outline is not None:
        polylines = (dc.outline[0],)
        texts.append(dc.outline[1])
    return Schematic(
        tuple(instances), tuple(connections), tuple(texts), i1, i2, polylines, dc.junctions
    )


def build_diagram(spec: PvSystemSpec, derived: Derived | None = None) -> Diagram:
    """Lay out ``spec`` on an A3 sheet.

    Raises:
        LayoutError: the spec is outside the template (more than one inverter or two strings, an
            unknown MPPT, no I1/I2/MF device) or does not fit on the sheet.
    """
    if spec.layout.template != TEMPLATE:
        raise LayoutError(f"unsupported layout template {spec.layout.template!r}")
    derived = derived or calc.derive(spec)
    tables_data = get_tables(spec.standards.nom_edition)

    schematic = build_schematic(spec, derived, RESIDENTIAL)
    instances = list(schematic.instances)
    connections = list(schematic.connections)
    texts = list(schematic.texts)

    # Headings.
    texts.append(TextItem(layers.TAGS, 15.0, 277.0, 5.0, "DIAGRAMA UNIFILAR FOTOVOLTAICO"))
    texts.append(TextItem(layers.ENCLOSURES, 20.0, 266.0, 2.5, "CIRCUITOS DE CD"))
    texts.append(TextItem(layers.ENCLOSURES, 168.0, 266.0, 2.5, "CIRCUITOS DE CA"))
    texts += _note_items(_notes(spec, derived))

    # Tables.
    stacked = _stack(
        [
            _string_table(spec, derived),
            _equipment_table(spec),
            _conductor_table(spec, derived, tables_data),
            _protection_table(spec),
        ]
    )
    all_tables = [
        *stacked,
        _check_fit(_legend_table(instances), RIGHT_BAND_WIDTH_MM),
        _check_fit(_revision_table(spec), TITLE_BLOCK_WIDTH_MM),
    ]

    # Sheet furniture (paper space).
    instances.append(_title_block(spec, derived))
    border = A3.border
    sheet_lines = [
        LineItem(layers.TITLE_BLOCK, border.x0, border.y0, border.x1, border.y0, "paper", 70),
        LineItem(layers.TITLE_BLOCK, border.x1, border.y0, border.x1, border.y1, "paper", 70),
        LineItem(layers.TITLE_BLOCK, border.x1, border.y1, border.x0, border.y1, "paper", 70),
        LineItem(layers.TITLE_BLOCK, border.x0, border.y1, border.x0, border.y0, "paper", 70),
    ]
    width = border.x1 - border.x0
    height = border.y1 - border.y0
    viewport = Viewport(
        center=Point(rnd(border.x0 + width / 2), rnd(border.y0 + height / 2)),
        size=(rnd(width), rnd(height)),
        view_center=Point(rnd(border.x0 + width / 2), rnd(border.y0 + height / 2)),
        view_height=rnd(height),
    )
    metadata = (
        ("PROJECT_ID", spec.project.id),
        ("SCHEMA_VERSION", SCHEMA_VERSION),
        ("RULEPACK", RULEPACK_ID),
        ("NOM_EDITION", NOM_EDITION),
        ("LAYOUT_TEMPLATE", TEMPLATE),
        ("SYMBOL_LIBRARY_VERSION", LIBRARY_VERSION),
    )
    return Diagram(
        sheet=A3,
        template=TEMPLATE,
        metadata=metadata,
        instances=tuple(instances),
        connections=tuple(connections),
        texts=tuple(texts),
        lines=tuple(sheet_lines),
        polylines=(),
        tables=tuple(all_tables),
        viewport=viewport,
    )


__all__ = ["TEMPLATE", "LayoutError", "build_diagram"]
