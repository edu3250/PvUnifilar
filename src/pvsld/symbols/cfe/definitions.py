"""The canonical definition of the CFE symbol library (ADR-0005, Stage 4.2).

This module is the single source of truth that ``pvsld symbols build`` turns into
``symbols/pvsld-symbols.dxf``. Read it next to the legend sheet: each :class:`SymbolSpec`
carries its Spanish designation, the source it is drawn from and a reviewer note.

How the shapes were obtained. The symbols of CFE G0100-04 Appendix C (13) and the usage of
Appendix D (figures D1 and D2: earth, conductor-count ticks, polarity, junction dots) were redrawn
by hand as vector geometry (lines, arcs, circles, polylines, text) from high-resolution crops of
the specification. No raster image and no text of the specification is embedded; only the symbol
names are used. Dimensions are in millimetres at 1:1 and the proportions follow the CFE figures.

Conventions (ADR-0003):

* Block names ``PVSLD_<FUNCTION>``. Every entity names a layer of the house standard, never ``0``.
* The base point ``(0, 0)`` is the upstream connection or the left-middle of the symbol; vertical
  devices hang below it. Power flows left to right. Ports sit on the 2.5 mm grid.
* Visible attributes ``TAG`` and ``DESC`` (Spanish), hidden ``COMP_ID``, ``IEC_REF``, ``NMX_REF``
  and ``SOURCE_STANDARD`` (ADR-0005), then function-specific attributes with the unit in the tag.
  The attribute tags and port ids of the nine blocks of the code-defined catalogue (Phase 2) are
  kept so that Stage 4.3 can switch the generator over with minimal change; the inverter attribute
  ``CERT`` is dropped (certifications are out of scope, owner decision).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import replace

from pvsld.core import layers
from pvsld.symbols.cfe.model import (
    NMX_PENDING,
    NO_IEC_REF,
    SOURCE_CFE_C,
    SOURCE_CFE_D,
    SOURCE_OWN,
    TAG_HEIGHT_MM,
    TEXT_HEIGHT_MM,
    Arc,
    AttDef,
    Circle,
    Dot,
    Fill,
    Label,
    Line,
    Polyline,
    Port,
    Primitive,
    SymbolSpec,
    bounds_of,
    rotate90,
)

_EQ_DC = layers.DC_EQUIPMENT
_EQ_AC = layers.AC_EQUIPMENT
_EQ_UT = layers.UTILITY_EQUIPMENT
_ENCL = layers.ENCLOSURES
_LABL = layers.LABELS
F_GEN = "Generación y almacenamiento"
F_CONV = "Conversión y transformación"
F_PROT = "Protección"
F_SWITCH = "Seccionamiento y maniobra"
F_MEAS = "Medición y monitoreo"
F_GRID = "Red, cargas y tableros"
F_EARTH = "Puesta a tierra"
F_MV = "Media tensión"
F_ANNOT = "Conductores, límites y anotación"
FAMILIES: tuple[str, ...] = (
    F_GEN,
    F_CONV,
    F_PROT,
    F_SWITCH,
    F_MEAS,
    F_GRID,
    F_EARTH,
    F_MV,
    F_ANNOT,
)
_DOTTED = "DOTTED"
_DASHED_SMALL = "DASHED_S"
_DASHDOT2 = "DASHDOT2"
_DASHDOT_SMALL = "DASHDOT_S"
_SOLID = "CONTINUOUS"


def _r(value: float) -> float:
    """Round to 0.1 micrometre so that the file bytes do not depend on libm rounding."""
    return round(value, 4)


# --- Attribute helpers ---------------------------------------------------------------------------


def _common(
    *,
    description_es: str,
    source: str,
    iec_ref: str,
    nmx_ref: str = NMX_PENDING,
    tag_xy: tuple[float, float] = (0, 0),
    desc_xy: tuple[float, float] = (0, 0),
    visible: bool = True,
    layer: str = layers.TAGS,
) -> tuple[AttDef, ...]:
    """TAG and DESC (visible unless the symbol is an annotation) plus the hidden identity set."""
    return (
        AttDef("TAG", "Identificador", *tag_xy, height=TAG_HEIGHT_MM, visible=visible, layer=layer),
        AttDef(
            "DESC",
            "Descripción",
            *desc_xy,
            visible=visible,
            default=description_es,
            layer=layer,
        ),
        AttDef("COMP_ID", "Identificador en el modelo", 0, 0, visible=False, layer=layer),
        AttDef(
            "IEC_REF", "Referencia IEC 60617", 0, 0, visible=False, default=iec_ref, layer=layer
        ),
        AttDef(
            "NMX_REF",
            "Referencia NMX-J-136-ANCE",
            0,
            0,
            visible=False,
            default=nmx_ref,
            layer=layer,
        ),
        AttDef(
            "SOURCE_STANDARD",
            "Norma de origen del símbolo",
            0,
            0,
            visible=False,
            default=source,
            layer=layer,
        ),
    )


def _hidden(*specs: tuple[str, str]) -> tuple[AttDef, ...]:
    return tuple(AttDef(tag, prompt, 0, 0, visible=False) for tag, prompt in specs)


def _spec(
    name: str,
    description_es: str,
    source: str,
    layer: str,
    geometry: tuple[Primitive, ...],
    ports: tuple[Port, ...],
    *,
    iec_ref: str = NO_IEC_REF,
    nmx_ref: str = NMX_PENDING,
    tag_xy: tuple[float, float] | None = None,
    desc_xy: tuple[float, float] | None = None,
    visible: bool = True,
    extra: tuple[AttDef, ...] = (),
    note: str = "",
    family: str = "",
) -> SymbolSpec:
    if nmx_ref == NMX_PENDING and source.startswith("NMX-J-136-ANCE-2019"):
        nmx_ref = source
    if visible:
        x0, y0, _x1, y1 = bounds_of(geometry)
        tag_xy = tag_xy or (x0, y1 + 2.5)
        desc_xy = desc_xy or (x0, y0 - 4.5)
    attdefs = (
        *_common(
            description_es=description_es,
            source=source,
            iec_ref=iec_ref,
            nmx_ref=nmx_ref,
            tag_xy=tag_xy or (0, 0),
            desc_xy=desc_xy or (0, 0),
            visible=visible,
        ),
        *extra,
    )
    return SymbolSpec(
        name=name,
        description_es=description_es,
        source=source,
        iec_ref=iec_ref,
        nmx_ref=nmx_ref,
        layer=layer,
        geometry=geometry,
        attdefs=attdefs,
        ports=ports,
        note=note,
        family=family,
    )


# --- Geometry helpers ----------------------------------------------------------------------------


def _sine(
    cx: float, cy: float, width: float, amplitude: float, periods: float = 1.0, steps: int = 24
) -> tuple[tuple[float, float], ...]:
    """The CFE tilde: starts on the axis, rises to a crest, falls to a trough (``periods`` long)."""
    return tuple(
        (
            _r(cx - width / 2 + width * i / steps),
            _r(cy + amplitude * math.sin(2 * math.pi * periods * i / steps)),
        )
        for i in range(steps + 1)
    )


def _rect(x0: float, y0: float, x1: float, y1: float) -> tuple[tuple[float, float], ...]:
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _bump(
    chord_x: float, cy: float, half: float, sagitta: float, layer: str, *, right: bool
) -> Arc:
    """A shallow winding arc of chord ``2 * half`` that bulges to the right or the left."""
    radius = (half**2 + sagitta**2) / (2 * sagitta)
    spread = math.degrees(math.asin(half / radius))
    offset = radius - sagitta
    if right:
        return Arc(_r(chord_x - offset), cy, _r(radius), _r(-spread), _r(spread), layer)
    return Arc(_r(chord_x + offset), cy, _r(radius), _r(180 - spread), _r(180 + spread), layer)


# --- CFE G0100-04, Appendix C ---------------------------------------------------------------------


def _cfe_breaker(x0: float, y0: float, layer: str) -> tuple[Primitive, ...]:
    """The CFE thermomagnetic breaker, 20 mm long, its left end at ``(x0, y0)``."""
    return (
        Line(x0, y0, x0 + 3.5, y0, layer),
        Arc(x0 + 6, y0 - 0.4455, 3.4455, 29.5, 150.5, layer),
        Line(x0 + 8, y0, x0 + 11, y0, layer),
        Circle(x0 + 12, y0, 1, layer),
        Line(x0 + 13, y0, x0 + 15, y0, layer),
        Polyline(
            ((x0 + 15, y0), (x0 + 16.5, y0 + 1.5), (x0 + 18.5, y0 - 1.5), (x0 + 20, y0)), layer
        ),
    )


def _cfe_varistor(layer: str) -> tuple[Primitive, ...]:
    """The CFE varistor hanging 20 mm below the origin."""
    return (
        Line(0, 0, 0, -5, layer),
        Polyline(_rect(-2.5, -15, 2.5, -5), layer, closed=True),
        Line(0, -15, 0, -20, layer),
        Polyline(((-4, -15), (-4, -11.5), (4, -8.5), (4, -5)), layer),
    )


def _cfe_manual_switch(layer: str) -> tuple[Primitive, ...]:
    """The CFE manual switch, 25 mm long, left lead at the origin."""
    return (
        Line(0, 0, 5, 0, layer),
        Line(5, 0, 12.5, 7.5, layer),
        Line(12.5, 0, 25, 0, layer),
        Line(8.75, 3.75, 5, 8.75, layer, linetype=_DOTTED),
        Line(8.75, 3.75, 8.75, -6.25, layer, linetype=_DOTTED),
        Line(5, 8.75, 8.75, 8, layer),
        Line(5, 8.75, 5.25, 4.75, layer),
    )


PV_MODULE = _spec(
    "PVSLD_PV_MODULE",
    "Módulo fotovoltaico",
    SOURCE_CFE_C,
    _EQ_DC,
    (
        Polyline(_rect(0, -10, 10, 10), _EQ_DC, closed=True),
        Polyline(((0, 10), (5, 2.5), (10, 10)), _EQ_DC),
    ),
    (Port("POS", 5, 10, "up", "DC"), Port("NEG", 5, -10, "down", "DC")),
    iec_ref="IEC 60617 S00908",
    tag_xy=(12.5, 5),
    desc_xy=(12.5, 0.5),
    extra=(
        AttDef("MODEL", "Modelo del módulo", 12.5, -4),
        *_hidden(
            ("MFR", "Fabricante del módulo"),
            ("PMAX_W", "Potencia del módulo (W)"),
            ("VOC_V", "Voc en STC (V)"),
            ("VMP_V", "Vmp en STC (V)"),
            ("ISC_A", "Isc del módulo (A)"),
            ("IMP_A", "Imp del módulo (A)"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.265 (IEC S00908)",
    family=F_GEN,
    note="Rectangle 1:2 with a V from the upper corners to 37 % of the height, as in the crop.",
)

SPD = _spec(
    "PVSLD_SPD",
    "Varistor (dispositivo de protección contra sobretensiones)",
    SOURCE_CFE_C,
    _EQ_DC,
    _cfe_varistor(_EQ_DC),
    (Port("L", 0, 0, "up", "DC"), Port("PE", 0, -20, "down", "PE")),
    tag_xy=(7, -6),
    desc_xy=(7, -10),
    extra=(
        AttDef("SPEC", "Datos del varistor", 7, -14),
        *_hidden(
            ("SPD_TYPE", "Tipo (1, 2, 3)"),
            ("UC_V", "Tensión máxima de operación continua (V)"),
            ("UP_KV", "Nivel de protección (kV)"),
            ("IN_KA", "Corriente nominal de descarga (kA)"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.308 (SPD, forma distinta)",
    family=F_PROT,
    note=(
        "Vertical varistor: narrow rectangle with the bent diagonal stroke. The line port is "
        "DC because the block lives on the DC equipment layer; an AC SPD reuses it."
    ),
)

BREAKER = _spec(
    "PVSLD_CB",
    "Interruptor termomagnético (ITM)",
    SOURCE_CFE_C,
    _EQ_AC,
    _cfe_breaker(0, 0, _EQ_AC),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 20, 0, "right", "AC")),
    iec_ref="IEC 60617 S00287",
    tag_xy=(0, 9.5),
    desc_xy=(0, -6),
    extra=(
        AttDef("ROLE", "Función (I1, I2)", 0, 5.5),
        *_hidden(
            ("POLES", "Polos"),
            ("RATING_A", "Corriente nominal (A)"),
            ("VOLT_V", "Tensión (V)"),
            ("KAIC_KA", "Capacidad interruptiva (kA)"),
            ("BACKFED", "Alimentado en sentido inverso"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.15 (forma distinta)",
    family=F_PROT,
    note="Magnetic hump over the open contact, winding loop and thermal zigzag, left to right.",
)

INVERTER = _spec(
    "PVSLD_INV",
    "Inversor de red de cadena",
    SOURCE_CFE_C,
    _EQ_AC,
    (
        Polyline(_rect(0, -15, 30, 15), _EQ_AC, closed=True),
        Line(0, -15, 30, 15, _EQ_AC),
        Line(5, 9.5, 11, 9.5, _EQ_AC),
        Line(5, 7.5, 11, 7.5, _EQ_AC),
        Polyline(_sine(23, -8, 6, 1.5), _EQ_AC),
        Label(1, 11.5, TEXT_HEIGHT_MM, "A"),
        Label(1, -13.5, TEXT_HEIGHT_MM, "B"),
    ),
    (
        Port("A", 0, 10, "left", "DC"),
        Port("B", 0, -10, "left", "DC", required=False),
        Port("AC", 30, 0, "right", "AC"),
        Port("PE", 15, -15, "down", "PE"),
    ),
    iec_ref="IEC 60617 S00896",
    tag_xy=(0, 29),
    desc_xy=(0, 25),
    extra=(
        AttDef("MFR", "Fabricante", 0, 21),
        AttDef("MODEL", "Modelo", 0, 17),
        *_hidden(
            ("PAC_W", "Potencia CA nominal (W)"),
            ("VAC_V", "Tensión CA nominal (V)"),
            ("PHASES", "Conductores energizados"),
            ("IAC_MAX_A", "Corriente CA máxima (A)"),
            ("VDC_MAX_V", "Tensión CD máxima (V)"),
            ("MPPT_N", "Número de MPPT"),
            ("OCPD_MAX_A", "Protección máxima CA (A)"),
            ("ISOLATION", "Aislamiento"),
            ("DC_SWITCH", "Desconectador CD integrado"),
            ("DC_SPD", "DPS CD integrado"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.357",
    family=F_CONV,
    note=(
        "Box with the diagonal from lower left to upper right, '=' upper left, '~' lower right. "
        "Square, 30 x 30 mm since 0.7.0 (owner request: smaller); the former 'CD'/'CA' captions "
        "are replaced by the symbols."
    ),
)

MAX_INVERTER_MPPT = 4
TOP_MPPT_IDS = ("C", "D")
"""Inputs on the top side of the inverter (owner decision 2026-11-14): A and B stay left."""


def inverter_name(n_mppt: int) -> str:
    """Block of a string inverter with ``n_mppt`` MPPT inputs: ``PVSLD_INV`` for one or two,
    ``PVSLD_INV_3MPPT`` / ``PVSLD_INV_4MPPT`` with C (and D) on top."""
    return "PVSLD_INV" if n_mppt <= 2 else f"PVSLD_INV_{n_mppt}MPPT"


def inverter_mppt(n_mppt: int) -> SymbolSpec:
    """The string inverter with 3 or 4 MPPT inputs: A and B on the left as in ``PVSLD_INV``, C
    (and D) on the top side, symmetric about the centre like the earth below (owner decision
    2026-11-14). The texts move to the upper right so the top inputs do not cross them.

    Raises:
        ValueError: ``n_mppt`` is not 3 or 4.
    """
    if n_mppt not in (3, 4):
        raise ValueError(f"the generated inverter has 3 or 4 MPPT inputs, not {n_mppt}")
    tops = (15.0,) if n_mppt == 3 else (10.0, 20.0)
    labels = tuple(
        Label(_r(x - 1.0), 11.5, TEXT_HEIGHT_MM, TOP_MPPT_IDS[i]) for i, x in enumerate(tops)
    )
    return _spec(
        inverter_name(n_mppt),
        f"Inversor de red de cadena de {n_mppt} MPPT",
        SOURCE_CFE_C,
        _EQ_AC,
        (*INVERTER.geometry, *labels),
        (
            Port("A", 0, 10, "left", "DC"),
            Port("B", 0, -10, "left", "DC", required=False),
            *(Port(TOP_MPPT_IDS[i], x, 15, "up", "DC", required=False) for i, x in enumerate(tops)),
            Port("AC", 30, 0, "right", "AC"),
            Port("PE", 15, -15, "down", "PE"),
        ),
        iec_ref="IEC 60617 S00896",
        tag_xy=(32, 29),
        desc_xy=(32, 25),
        extra=(
            AttDef("MFR", "Fabricante", 32, 21),
            AttDef("MODEL", "Modelo", 32, 17),
            *_hidden(
                ("PAC_W", "Potencia CA nominal (W)"),
                ("VAC_V", "Tensión CA nominal (V)"),
                ("PHASES", "Conductores energizados"),
                ("IAC_MAX_A", "Corriente CA máxima (A)"),
                ("VDC_MAX_V", "Tensión CD máxima (V)"),
                ("MPPT_N", "Número de MPPT"),
                ("OCPD_MAX_A", "Protección máxima CA (A)"),
                ("ISOLATION", "Aislamiento"),
                ("DC_SWITCH", "Desconectador CD integrado"),
                ("DC_SPD", "DPS CD integrado"),
            ),
        ),
        nmx_ref="NMX-J-136-ANCE-2019 4.2.357",
        family=F_CONV,
        note=(
            "PVSLD_INV with the third and fourth MPPT inputs on the top side (C, D), symmetric "
            "about the centre; the texts sit at the upper right. Generated on demand."
        ),
    )


METER = _spec(
    "PVSLD_METER",
    "Medidor de energía bidireccional",
    SOURCE_CFE_C,
    _EQ_UT,
    (
        Polyline(_rect(0, -10, 15, 10), _EQ_UT, closed=True),
        Line(0, 0, 15, 0, _EQ_UT),
        Line(3, 5, 12, 5, _EQ_UT),
        Polyline(((4.5, 6.2), (3, 5), (4.5, 3.8)), _EQ_UT),
        Polyline(((10.5, 6.2), (12, 5), (10.5, 3.8)), _EQ_UT),
        Label(4.6, -7, 3.2, "kWh"),
    ),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 15, 0, "right", "AC")),
    iec_ref="IEC 60617 S00937",
    tag_xy=(0, 13),
    desc_xy=(0, -14),
    extra=_hidden(
        ("METER_TYPE", "Tipo (MF, MCE)"),
        ("BIDIRECTIONAL", "Bidireccional"),
        ("OWNER", "Propietario"),
        ("METER_NO", "Número de medidor"),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.126 (forma distinta)",
    family=F_MEAS,
    note="Two-cell box: double-headed arrow above, 'kWh' below. Ports at the divider line.",
)

GRID = _spec(
    "PVSLD_GRID",
    "Red eléctrica de distribución",
    SOURCE_CFE_C,
    _EQ_UT,
    (Circle(10, 0, 10, _EQ_UT), Polyline(_sine(10, 0, 9, 4, periods=1.05), _EQ_UT)),
    (Port("IN", 0, 0, "left", "AC"),),
    tag_xy=(0, 12.5),
    desc_xy=(0, -15),
    extra=(
        AttDef("SPEC", "Tensión y sistema", 0, -19),
        *_hidden(
            ("VOLT_V", "Tensión nominal (V)"),
            ("SYSTEM", "Sistema"),
            ("FREQ_HZ", "Frecuencia (Hz)"),
            ("OWNER", "Suministrador"),
            ("ISC_KA", "Corriente de falla disponible (kA)"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.314",
    family=F_GRID,
    note="Circle with a sine crest and trough. It replaces the former 'RED' box.",
)

ENCLOSURE = _spec(
    "PVSLD_ENCLOSURE",
    "Gabinete o estructura metálica",
    SOURCE_CFE_C,
    _ENCL,
    (Polyline(_rect(0, -15, 40, 15), _ENCL, closed=True, linetype=_DASHDOT_SMALL),),
    (),
    tag_xy=(0, 18),
    desc_xy=(0, -19),
    extra=_hidden(("ENCL_TYPE", "Tipo de gabinete")),
    family=F_ANNOT,
    note=(
        "Dash-dot rectangle (layer E-ANNO-ENCL, finer DASHDOT_S pattern so that a small symbol "
        "shows several dashes per side). Nominal 40 x 30 mm; scale the INSERT to enclose a part "
        "of the diagram. Ports: none, conductors cross the line."
    ),
)

DIODE = _spec(
    "PVSLD_DIODE",
    "Diodo de paso",
    SOURCE_CFE_C,
    _EQ_DC,
    (
        Line(0, 0, 0, -5, _EQ_DC),
        Line(-4, -5, 4, -5, _EQ_DC),
        Polyline(((0, -5), (-3.5, -12.5), (3.5, -12.5)), _EQ_DC, closed=True),
        Line(0, -12.5, 0, -17.5, _EQ_DC),
    ),
    (Port("K", 0, 0, "up", "DC"), Port("A", 0, -17.5, "down", "DC")),
    tag_xy=(6, -6),
    desc_xy=(6, -10),
    extra=_hidden(("RATING_A", "Corriente nominal (A)"), ("VRRM_V", "Tensión inversa (V)")),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.96",
    family=F_GEN,
    note="Cathode bar on top, triangle pointing up (current flows from anode to cathode).",
)


def _light_load() -> tuple[Primitive, ...]:
    rays: list[Primitive] = []
    for sign_x in (-1, 1):
        for sign_y in (-1, 1):
            c = math.cos(math.radians(45))
            rays.append(
                Line(
                    _r(5 + sign_x * 5 * c),
                    _r(sign_y * 5 * c),
                    _r(5 + sign_x * 7.5 * c),
                    _r(sign_y * 7.5 * c),
                    _EQ_AC,
                )
            )
    return (Circle(5, 0, 5, _EQ_AC), *rays)


def _receptacle_load() -> tuple[Primitive, ...]:
    c = math.cos(math.radians(45))
    chords: list[Primitive] = []
    for offset in (-1.1, 1.1):
        half = math.sqrt(25 - offset**2)
        # unit direction (c, c); unit normal (-c, c)
        mx, my = 5 - offset * c, offset * c
        chords.append(
            Line(
                _r(mx - half * c),
                _r(my - half * c),
                _r(mx + half * c),
                _r(my + half * c),
                _EQ_AC,
            )
        )
    return (Circle(5, 0, 5, _EQ_AC), *chords)


LOAD_LIGHT = _spec(
    "PVSLD_LOAD_LIGHT",
    "Cargas de iluminación",
    SOURCE_CFE_C,
    _EQ_AC,
    _light_load(),
    (Port("IN", 0, 0, "left", "AC"),),
    tag_xy=(0, 10),
    desc_xy=(0, -11),
    extra=_hidden(("LOAD_W", "Carga (W)"), ("VOLT_V", "Tensión (V)"), ("CIRCUIT", "Circuito")),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.53",
    family=F_GRID,
    note="Circle with four diagonal rays outside the circle.",
)

LOAD_RECEPT = _spec(
    "PVSLD_LOAD_RECEPT",
    "Carga de contactos",
    SOURCE_CFE_C,
    _EQ_AC,
    _receptacle_load(),
    (Port("IN", 0, 0, "left", "AC"),),
    tag_xy=(0, 8),
    desc_xy=(0, -9),
    extra=_hidden(("LOAD_W", "Carga (W)"), ("VOLT_V", "Tensión (V)"), ("CIRCUIT", "Circuito")),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.28 (dos trazos)",
    family=F_GRID,
    note="Circle crossed by a diagonal band (two parallel chords at 45 degrees).",
)

CURRENT_SENSOR = _spec(
    "PVSLD_CT",
    "Sensor de corriente",
    SOURCE_CFE_C,
    _EQ_UT,
    (
        Line(0, 0, 15, 0, _EQ_UT),
        Line(0, -10, 15, -10, _EQ_UT),
        Line(4.5, -12, 4.5, 2, _EQ_UT),
        Line(10.5, -12, 10.5, 2, _EQ_UT),
        Arc(7.5, 2, 3, 0, 180, _EQ_UT),
        Arc(7.5, -12, 3, 180, 360, _EQ_UT),
    ),
    (
        Port("IN", 0, 0, "left", "AC"),
        Port("OUT", 15, 0, "right", "AC"),
        Port("IN2", 0, -10, "left", "AC", required=False),
        Port("OUT2", 15, -10, "right", "AC", required=False),
    ),
    tag_xy=(0, 8),
    desc_xy=(0, -19),
    extra=(
        AttDef("SPEC", "Relación de transformación", 0, -23),
        *_hidden(("RATIO", "Relación (A/A)"), ("BURDEN_VA", "Carga (VA)"), ("CLASS", "Clase")),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.124 (forma distinta)",
    family=F_MEAS,
    note=(
        "Stadium (rounded rectangle) crossed by two conductors, as in the crop. The second "
        "conductor (IN2/OUT2) is optional. Port kind is AC; D1 uses it on the DC side."
    ),
)

SWITCH = _spec(
    "PVSLD_SWITCH",
    "Interruptor manual",
    SOURCE_CFE_C,
    _EQ_AC,
    _cfe_manual_switch(_EQ_AC),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 25, 0, "right", "AC")),
    tag_xy=(15, 5),
    desc_xy=(0, -10),
    extra=(
        AttDef("ROLE", "Función", 15, 9),
        *_hidden(
            ("POLES", "Polos"),
            ("RATING_A", "Corriente nominal (A)"),
            ("VOLT_V", "Tensión (V)"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.94 y 4.2.121",
    family=F_SWITCH,
    note="Open blade at 45 degrees, dotted operating line and the manual-actuation arrow.",
)

ISOLATION_TRANSFORMER = _spec(
    "PVSLD_XFMR_ISO",
    "Transformador de aislamiento",
    SOURCE_CFE_C,
    _EQ_AC,
    (
        Line(0, 0, 7.5, 0, _EQ_AC),
        Line(0, -20, 7.5, -20, _EQ_AC),
        Line(17.5, 0, 25, 0, _EQ_AC),
        Line(17.5, -20, 25, -20, _EQ_AC),
        *(_bump(7.5, cy, 2.5, 2.0, _EQ_AC, right=True) for cy in (-2.5, -7.5, -12.5, -17.5)),
        *(_bump(17.5, cy, 2.5, 2.0, _EQ_AC, right=False) for cy in (-2.5, -7.5, -12.5, -17.5)),
        Line(11.25, 0, 11.25, -20, _EQ_AC),
        Line(13.75, 0, 13.75, -20, _EQ_AC),
    ),
    (
        Port("IN", 0, 0, "left", "AC"),
        Port("OUT", 25, 0, "right", "AC"),
        Port("IN2", 0, -20, "left", "AC", required=False),
        Port("OUT2", 25, -20, "right", "AC", required=False),
    ),
    tag_xy=(0, 4),
    desc_xy=(0, -24),
    extra=(
        AttDef("SPEC", "Potencia y relación", 0, -28),
        *_hidden(
            ("KVA", "Potencia (kVA)"),
            ("VOLT_PRI_V", "Tensión primaria (V)"),
            ("VOLT_SEC_V", "Tensión secundaria (V)"),
            ("FREQ_HZ", "Frecuencia (Hz)"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.69 (sin núcleo)",
    family=F_CONV,
    note="Two windings of four shallow bumps facing each other, two core lines between them.",
)

# --- CFE G0100-04, Appendix D (usage in figures D1 and D2) ---------------------------------------

GROUND = _spec(
    "PVSLD_GND",
    "Electrodo de puesta a tierra",
    SOURCE_CFE_D,
    layers.GROUNDING,
    (
        Line(0, 0, 0, -5, layers.GROUNDING, linetype=_SOLID),
        Line(-6, -5, 6, -5, layers.GROUNDING, linetype=_SOLID),
        Line(-4, -7.5, 4, -7.5, layers.GROUNDING, linetype=_SOLID),
        Line(-2, -10, 2, -10, layers.GROUNDING, linetype=_SOLID),
    ),
    (Port("PE", 0, 0, "up", "PE"),),
    iec_ref="IEC 60617 S00200",
    tag_xy=(9, -6),
    desc_xy=(9, -10.5),
    extra=(
        AttDef("SPEC", "Electrodo y resistencia", 9, -14.5),
        *_hidden(
            ("ELECTRODE", "Tipo de electrodo"),
            ("R_OHM", "Resistencia de diseño (Ω)"),
            ("GEC_SIZE", "Calibre del conductor al electrodo"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.13 (IEC S00200)",
    family=F_EARTH,
    note=(
        "Stem and three bars that shorten, as drawn under the inverter and the arrays in D1. "
        "Strokes are continuous (the layer linetype is dashed) so the bars read as a symbol."
    ),
)

CONDUCTOR_MARK = _spec(
    "PVSLD_COND_MARK",
    "Marca de número de conductores",
    SOURCE_CFE_D,
    _LABL,
    (Line(-2.5, -2.5, 0, 2.5, _LABL), Line(0, -2.5, 2.5, 2.5, _LABL)),
    (),
    visible=False,
    extra=(AttDef("N_COND", "Número de conductores", -1.5, 4),),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.131 (IEC S00002)",
    family=F_ANNOT,
    note=(
        "Two slanted ticks '//' centred on the insertion point, laid over a conductor; "
        "N_COND is the printed count."
    ),
)

POLARITY_POSITIVE = _spec(
    "PVSLD_POL_POS",
    "Polaridad positiva (+)",
    SOURCE_CFE_D,
    _LABL,
    (Line(-2, 0, 2, 0, _LABL), Line(0, -2, 0, 2, _LABL)),
    (),
    visible=False,
    nmx_ref="NMX-J-136-ANCE-2019 4.2.320",
    family=F_ANNOT,
    note="Plus sign centred on the insertion point, next to the DC terminal of the inverter.",
)

POLARITY_NEGATIVE = _spec(
    "PVSLD_POL_NEG",
    "Polaridad negativa (-)",
    SOURCE_CFE_D,
    _LABL,
    (Line(-2, 0, 2, 0, _LABL),),
    (),
    visible=False,
    nmx_ref="NMX-J-136-ANCE-2019 4.2.321",
    family=F_ANNOT,
    note="Minus sign centred on the insertion point.",
)

JUNCTION = _spec(
    "PVSLD_JUNCTION",
    "Unión de conductores (punto)",
    SOURCE_CFE_D,
    layers.DC_CONDUCTORS,
    (Dot(0, 0, 1, layers.DC_CONDUCTORS),),
    (),
    visible=False,
    nmx_ref="NMX-J-136-ANCE-2019 4.2.11",
    family=F_ANNOT,
    note="Filled dot of 2 mm where conductors are joined, centred on the insertion point.",
)

# --- Symbols the generator needs and CFE does not define -------------------------------------

PV_STRING = _spec(
    "PVSLD_PV_STRING",
    "Rama de módulos fotovoltaicos en serie",
    "CFE G0100-04 Apéndices C y D",
    _EQ_DC,
    (
        Polyline(_rect(0, -10, 10, 10), _EQ_DC, closed=True),
        Polyline(((0, 10), (5, 2.5), (10, 10)), _EQ_DC),
        Polyline(_rect(20, -10, 30, 10), _EQ_DC, closed=True),
        Polyline(((20, 10), (25, 2.5), (30, 10)), _EQ_DC),
        Dot(12.5, 0, 0.6, _EQ_DC),
        Dot(15, 0, 0.6, _EQ_DC),
        Dot(17.5, 0, 0.6, _EQ_DC),
    ),
    (Port("OUT", 30, 0, "right", "DC"),),
    iec_ref="IEC 60617 S00908",
    tag_xy=(0, 12.5),
    desc_xy=(0, -14.5),
    extra=(
        AttDef("MODEL", "Modelo del módulo", 0, -18.5),
        *_hidden(
            ("MFR", "Fabricante del módulo"),
            ("MPPT", "Entrada MPPT del inversor"),
            ("PMAX_W", "Potencia del módulo (W)"),
            ("N_SERIES", "Módulos en serie"),
            ("KWP", "Potencia de la rama (kWp)"),
            ("VOC_MAX_V", "Voc máxima a T mín (V)"),
            ("VMP_V", "Vmp en STC de la rama (V)"),
            ("ISC_A", "Isc del módulo (A)"),
            ("IMP_A", "Imp del módulo (A)"),
        ),
    ),
    family=F_GEN,
    note=(
        "Built from the CFE module symbol: first and last module of the series, three dots for "
        "the modules in between. The single output port keeps its Phase 2 id and side."
    ),
)

POINT_OF_INTERCONNECTION = _spec(
    "PVSLD_PI",
    "Punto de interconexión (PI) con la red de distribución",
    SOURCE_OWN,
    _EQ_UT,
    (Circle(5, 0, 5, _EQ_UT), Circle(5, 0, 1.5, _EQ_UT)),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 10, 0, "right", "AC")),
    tag_xy=(0, 7.5),
    desc_xy=(-20, -10.5),
    extra=_hidden(
        ("PI_TYPE", "Tipo de conexión"),
        ("PANEL", "Tablero de conexión"),
        ("BREAKER", "Interruptor de conexión"),
    ),
    family=F_GRID,
    note="CFE marks no interconnection point; kept from Phase 2 (ring with an inner circle).",
)

LOAD_CENTER = _spec(
    "PVSLD_PANEL",
    "Centro de carga o tablero con barra",
    SOURCE_CFE_D,
    _EQ_AC,
    (
        Polyline(_rect(0, -17.5, 30, 17.5), _ENCL, closed=True),
        Line(0, 0, 30, 0, _EQ_AC),
        Line(15, -10, 15, 12.5, layers.AC_CONDUCTORS, lineweight=70),
        Line(7.5, -14, 22.5, -14, layers.GROUNDING, linetype=_SOLID),
        Line(15, -17.5, 15, -14, layers.GROUNDING, linetype=_SOLID),
    ),
    (
        Port("LEFT", 0, 0, "left", "AC"),
        Port("RIGHT", 30, 0, "right", "AC"),
        Port("PE", 15, -17.5, "down", "PE"),
    ),
    iec_ref="IEC 60617 S00062",
    tag_xy=(0, 30),
    desc_xy=(0, 25.5),
    extra=(
        AttDef("SPEC", "Datos de la barra", 0, 21),
        *_hidden(
            ("NAME", "Nombre del tablero"),
            ("BUS_A", "Barra (A)"),
            ("MAIN_ID", "Interruptor principal"),
            ("MAIN_A", "Principal (A)"),
            ("SYSTEM", "Sistema"),
            ("KAIC_KA", "Capacidad interruptiva (kA)"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.90 (plano)",
    family=F_GRID,
    note=(
        "'Centro de carga' of figures D1 and D2: the CFE enclosure (dash-dot) around a bus and "
        "a ground bar; 30 x 35 mm since 0.7.0 (owner request: smaller)."
    ),
)

# Title block (pie de plano): 185 x 60 mm, six rows of ten millimetres. Each field is
# (tag, caption, x0, x1, row from the top, value height). Same fields as the Phase 2 catalogue.
TITLE_BLOCK_WIDTH_MM = 185.0
TITLE_BLOCK_HEIGHT_MM = 60.0
TITLE_BLOCK_ROW_MM = 10.0
TITLE_BLOCK_FIELDS: tuple[tuple[str, str, float, float, int, float], ...] = (
    ("PROYECTO", "PROYECTO", 0, 185, 0, 3.5),
    ("CLIENTE", "CLIENTE", 0, 80, 1, 2.5),
    ("UBICACION", "UBICACIÓN", 80, 185, 1, 2.5),
    ("RPU", "RPU", 0, 40, 2, 2.5),
    ("NUM_SERVICIO", "NÚM. DE SERVICIO", 40, 90, 2, 2.5),
    ("NUM_MEDIDOR", "NÚM. DE MEDIDOR", 90, 135, 2, 2.5),
    ("TARIFA", "TARIFA", 135, 155, 2, 2.5),
    ("TENSION_SUMINISTRO", "TENSIÓN", 155, 185, 2, 2.5),
    ("CAPACIDAD", "CAPACIDAD", 0, 46, 3, 2.5),
    ("RESPONSABLE", "RESPONSABLE", 46, 104, 3, 2.5),
    ("CEDULA", "CÉDULA PROF.", 104, 138, 3, 2.5),
    ("UVIE", "UVIE", 138, 163, 3, 2.5),
    ("FECHA", "FECHA", 163, 185, 3, 2.5),
    ("EMPRESA", "EMPRESA", 0, 70, 4, 2.5),
    ("DIBUJO", "DIBUJÓ", 70, 92, 4, 2.5),
    ("REVISO", "REVISÓ", 92, 114, 4, 2.5),
    ("APROBO", "APROBÓ", 114, 136, 4, 2.5),
    ("NORMA", "NORMA", 136, 185, 4, 2.5),
    ("TITULO", "TÍTULO DEL PLANO", 0, 85, 5, 3.5),
    ("PLANO_NO", "PLANO NÚM.", 85, 113, 5, 3.5),
    ("HOJA", "HOJA", 113, 135, 5, 2.5),
    ("REV", "REV.", 135, 150, 5, 3.5),
    ("ESCALA", "ESCALA", 150, 185, 5, 2.5),
)


def _title_block() -> SymbolSpec:
    frame = layers.TITLE_BLOCK
    width, height, row_mm = TITLE_BLOCK_WIDTH_MM, TITLE_BLOCK_HEIGHT_MM, TITLE_BLOCK_ROW_MM
    items: list[Primitive] = [
        Polyline(((0, 0), (width, 0), (width, height), (0, height)), frame, True)
    ]
    for row in range(1, int(height / row_mm)):
        y = height - row * row_mm
        items.append(Line(0, y, width, y, frame, lineweight=25))
    dividers: set[tuple[float, int]] = set()
    for _tag, caption, x0, _x1, row, _height in TITLE_BLOCK_FIELDS:
        y_top = height - row * row_mm
        items.append(Label(x0 + 1.5, y_top - 3.8, TEXT_HEIGHT_MM, caption, frame))
        if x0 > 0 and (x0, row) not in dividers:
            dividers.add((x0, row))
            items.append(Line(x0, y_top, x0, y_top - row_mm, frame, lineweight=25))
    fields = tuple(
        AttDef(
            tag,
            caption.title(),
            x0 + 1.5,
            height - row * row_mm - 8.8,
            value_height,
            layer=frame,
        )
        for tag, caption, x0, _x1, row, value_height in TITLE_BLOCK_FIELDS
    )
    description = "Cuadro de datos del plano (pie de plano)"
    nmx_ref = "NMX-J-136-ANCE Sección 5"
    attdefs = (
        *fields,
        *_common(
            description_es=description,
            source=SOURCE_OWN,
            iec_ref=NO_IEC_REF,
            nmx_ref=nmx_ref,
            visible=False,
            layer=frame,
        ),
    )
    return SymbolSpec(
        name="PVSLD_TTLB",
        description_es=description,
        source=SOURCE_OWN,
        iec_ref=NO_IEC_REF,
        nmx_ref=nmx_ref,
        layer=frame,
        geometry=tuple(items),
        attdefs=attdefs,
        ports=(),
        note="Mexican title block with 23 attribute fields; the layout is the Phase 2 one.",
        family=F_ANNOT,
    )


TITLE_BLOCK = _title_block()

# --- Round 2: the components the generator will need next (map of 2026-10-08) ---------------------
#
# Source priority: CFE G0100-04, then NMX-J-136-ANCE-2019, then IEC 60617 (through the Peruvian DGE
# norm and the owner's UNE-EN 60617 sheet). Devices drawn vertically in their source are rotated so
# that power flows to the right; the origin is then the upstream connection. Shunt devices
# (arrester,
# earth) keep hanging below the origin.

_DC_COND = layers.DC_CONDUCTORS
_AC_COND = layers.AC_CONDUCTORS
_UT = layers.UTILITY_EQUIPMENT
_COMM = layers.COMMUNICATIONS
_NMX = "NMX-J-136-ANCE-2019"
_COMPOSITION = "pvsld (composición)"


def _nmx(ref: str) -> str:
    return f"{_NMX} {ref}"


def _dashed(x1: float, y1: float, x2: float, y2: float, layer: str) -> Line:
    return Line(x1, y1, x2, y2, layer, linetype=_DASHED_SMALL)


def _ellipse(cx: float, cy: float, rx: float, ry: float, layer: str) -> Polyline:
    points = tuple(
        (
            _r(cx + rx * math.cos(2 * math.pi * i / 24)),
            _r(cy + ry * math.sin(2 * math.pi * i / 24)),
        )
        for i in range(24)
    )
    return Polyline(points, layer, closed=True)


def _blade_with_fuse(layer: str, *, bar: bool) -> tuple[Primitive, ...]:
    """Vertical fuse-switch: fixed contact on top, the blade is a fuse tilted to the left."""
    pivot, tip = (0.0, -17.5), (-5.5, -5.0)
    dx, dy = tip[0] - pivot[0], tip[1] - pivot[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length  # along the blade
    nx, ny = -uy, ux  # across the blade
    p0, p1, half = 0.3 * length, 0.72 * length, 1.25
    body = tuple(
        (_r(pivot[0] + ux * t + nx * w), _r(pivot[1] + uy * t + ny * w))
        for t, w in ((p0, half), (p1, half), (p1, -half), (p0, -half))
    )
    items: list[Primitive] = [
        Line(0, 0, 0, -5, layer),
        Line(pivot[0], pivot[1], tip[0], tip[1], layer),
        Polyline(body, layer, closed=True),
        Line(0, -17.5, 0, -25, layer),
    ]
    if bar:
        items.append(Line(-2.5, -5, 2.5, -5, layer))
    return rotate90(items)


def _converter_box(layer: str, caption: str, *, inverter: bool) -> tuple[Primitive, ...]:
    """A 15 mm converter square: diagonal and the sign of the input and output current."""
    items: list[Primitive] = [
        Polyline(_rect(0, -7.5, 15, 7.5), layer, closed=True),
        Line(0, -7.5, 15, 7.5, layer),
        Line(2, 4.5, 6.5, 4.5, layer),
        Line(2, 3, 6.5, 3, layer),
    ]
    if inverter:
        items.append(Polyline(_sine(11, -3.8, 4.5, 1.2, steps=12), layer))
    else:  # DC sign: a solid and a dashed line
        items += [
            Line(8.5, -3, 13, -3, layer),
            Line(8.5, -4.5, 10.2, -4.5, layer),
            Line(11.3, -4.5, 13, -4.5, layer),
        ]
    items.append(Label(2.5, -12.5, TEXT_HEIGHT_MM, caption))
    return tuple(items)


def _labelled_box(
    layer: str, lines: tuple[str, ...], width: float, height: float
) -> tuple[Primitive, ...]:
    """The CFE labelled block (figure D1): a rectangle with the name written inside."""
    items: list[Primitive] = [Polyline(_rect(0, -height, width, 0), layer, closed=True)]
    top = -(height - TEXT_HEIGHT_MM * len(lines) - 1.5 * (len(lines) - 1)) / 2 - TEXT_HEIGHT_MM
    for index, text in enumerate(lines):
        items.append(Label(2.5, _r(top - index * 4), TEXT_HEIGHT_MM, text))
    return tuple(items)


_SIGNAL_PORTS = (
    Port("SENS", 0, -7.5, "left", "SIG", required=False),
    Port("TRIP", 30, -7.5, "right", "SIG", required=False),
)

# --- Generation and storage -----------------------------------------------------------------------

MAX_COMBINER_STRINGS = 24
_COMBINER_PITCH = 15.0


def combiner_name(n_strings: int) -> str:
    """Block name of the combiner box for ``n_strings`` strings, e.g. ``PVSLD_COMBINER_4S``."""
    return f"PVSLD_COMBINER_{n_strings}S"


def combiner_box(n_strings: int, *, name: str | None = None) -> SymbolSpec:
    """The combiner box for ``n_strings`` strings: one CFE breaker per string, a bus and an SPD.

    Inputs ``IN1``..``INn`` sit 15 mm apart (``IN1`` on top), symmetric about the output ``OUT``
    at ``y = 0``; the varistor hangs from the bus to ``PE``. The library keeps the two-string form
    as ``PVSLD_COMBINER`` for the legend; the generator asks for the exact count through
    :func:`pvsld.symbols.cfe.loader.ensure_combiner`, which defines ``PVSLD_COMBINER_<n>S``.

    Raises:
        ValueError: ``n_strings`` is outside ``1..MAX_COMBINER_STRINGS``.
    """
    if not 1 <= n_strings <= MAX_COMBINER_STRINGS:
        raise ValueError(
            f"a combiner box takes 1 to {MAX_COMBINER_STRINGS} strings, not {n_strings}"
        )
    rows = [_r(_COMBINER_PITCH * ((n_strings - 1) / 2 - i)) for i in range(n_strings)]
    top, bottom = rows[0] + 7.5, min(rows[-1] - 7.5, -25.0)
    geometry: list[Primitive] = [
        Polyline(_rect(0, bottom, 60, top), _ENCL, closed=True, linetype="DASHED")
    ]
    for y in rows:
        geometry += [
            Line(0, y, 5, y, _EQ_DC),
            *_cfe_breaker(5, y, _EQ_DC),
            Line(25, y, 35, y, _EQ_DC),
        ]
    if n_strings > 1:
        geometry.append(Line(35, rows[0], 35, rows[-1], _EQ_DC))
        geometry += [Dot(35, y, 0.8, _DC_COND) for y in sorted({*rows, 0.0}, reverse=True)]
    geometry += [
        Line(35, 0, 60, 0, _EQ_DC),
        Dot(45, 0, 0.8, _DC_COND),
        Line(45, 0, 45, -5, _EQ_DC),
        Polyline(_rect(42.5, -15, 47.5, -5), _EQ_DC, closed=True),
        Polyline(((41, -15), (41, -11.5), (49, -8.5), (49, -5)), _EQ_DC),
        Line(45, -15, 45, bottom, _EQ_DC),
    ]
    return _spec(
        name or combiner_name(n_strings),
        "Caja combinadora de ramas (combiner)",
        "pvsld (composición) según CFE G0100-04 Apéndice D, figs. D1 y D2",
        layers.DC_EQUIPMENT,
        tuple(geometry),
        (
            *(Port(f"IN{i + 1}", 0, y, "left", "DC") for i, y in enumerate(rows)),
            Port("OUT", 60, 0, "right", "DC"),
            Port("PE", 45, bottom, "down", "PE"),
        ),
        extra=(
            AttDef("N_STRINGS", "Ramas conectadas", 0, 0, visible=False, default=str(n_strings)),
            *_hidden(
                ("RATING_A", "Corriente nominal de cada interruptor (A)"),
                ("VOLT_V", "Tensión máxima CD (V)"),
                ("SPD_TYPE", "Tipo de DPS"),
            ),
        ),
        family=F_GEN,
        note=(
            "Composition from the figure D1 combiner: a dashed enclosure with one CFE breaker per "
            "string, a bus with junction dots and a varistor to earth. Single-polarity "
            "(single-line) form. The number of strings is a parameter (combiner_box(n), 1 to "
            f"{MAX_COMBINER_STRINGS}); this two-string block is the legend representative and "
            "the generator uses PVSLD_COMBINER_<n>S."
        ),
    )


COMBINER_BOX = combiner_box(2, name="PVSLD_COMBINER")


# --- Microinverter with its inputs and the AC junction box (Stage 3.9.4) ----------------------

MAX_MICRO_INPUTS = 8
MICRO_INPUT_IDS = "ABCDEFGH"
_MICRO_PITCH = 7.5


def microinverter_name(n_inputs: int) -> str:
    """Block name of a microinverter with ``n_inputs`` DC inputs, e.g. ``PVSLD_MICROINV_4IN``."""
    return f"PVSLD_MICROINV_{n_inputs}IN"


def microinverter(n_inputs: int) -> SymbolSpec:
    """A microinverter with one DC input per module (``A``, ``B``...) on top and its AC output
    below, to tap the AC trunk (owner decision 2026-11-07, after the Enphase IQ8 diagrams).

    The CFE inverter square (diagonal, DC sign, sine) widens with the inputs, 7.5 mm apart; the
    library keeps the one-input ``PVSLD_MICROINV`` for the legend and the generator asks for
    ``PVSLD_MICROINV_<n>IN``.

    Raises:
        ValueError: ``n_inputs`` is outside ``1..MAX_MICRO_INPUTS``.
    """
    if not 1 <= n_inputs <= MAX_MICRO_INPUTS:
        raise ValueError(f"a microinverter takes 1 to {MAX_MICRO_INPUTS} inputs, not {n_inputs}")
    width = max(15.0, _MICRO_PITCH * n_inputs)
    xs = [_r(width / 2 + _MICRO_PITCH * (i - (n_inputs - 1) / 2)) for i in range(n_inputs)]
    geometry: tuple[Primitive, ...] = (
        Polyline(_rect(0, -7.5, width, 7.5), _EQ_AC, closed=True),
        Line(0, -7.5, width, 7.5, _EQ_AC),
        Line(2, 4.5, 6.5, 4.5, _EQ_AC),
        Line(2, 3, 6.5, 3, _EQ_AC),
        Polyline(_sine(_r(width - 4), -3.8, 4.5, 1.2, steps=12), _EQ_AC),
        Label(_r(width + 2), -1.25, TEXT_HEIGHT_MM, "uINV"),
    )
    return _spec(
        microinverter_name(n_inputs),
        f"Microinversor de {n_inputs} entrada{'s' if n_inputs > 1 else ''}",
        "pvsld (composición) sobre CFE G0100-04 Apéndice C (inversor)",
        _EQ_AC,
        geometry,
        (
            # A free input (fewer modules than inputs) is not a dangling port.
            *(
                Port(MICRO_INPUT_IDS[i], x, 7.5, "up", "DC", required=False)
                for i, x in enumerate(xs)
            ),
            Port("AC", _r(width / 2), -7.5, "down", "AC"),
        ),
        tag_xy=(_r(width + 2), 5),
        desc_xy=(_r(width + 2), -6),
        extra=(
            AttDef("N_INPUTS", "Entradas de CD", 0, 0, visible=False, default=str(n_inputs)),
            *_hidden(
                ("MFR", "Fabricante"),
                ("MODEL", "Modelo"),
                ("PAC_W", "Potencia CA nominal (W)"),
                ("VAC_V", "Tensión CA nominal (V)"),
                ("BRANCH", "Rama de CA"),
            ),
        ),
        family=F_CONV,
        note=(
            "No official symbol. The CFE inverter square widened to hold one DC input per module "
            "on top (A, B... 7.5 mm apart) and the AC output below; the generator defines it per "
            "input count."
        ),
    )


AC_JUNCTION_BOX = _spec(
    "PVSLD_AC_JBOX",
    "Caja de conexiones de CA (fin del cable troncal)",
    "pvsld (composición) sobre CFE G0100-04 Apéndice D, fig. D1 (bloque rotulado)",
    _EQ_AC,
    (
        Polyline(_rect(0, -3, 8, 3), _EQ_AC, closed=True),
        Label(1.5, -1.25, TEXT_HEIGHT_MM, "JB"),
    ),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 8, 0, "right", "AC")),
    family=F_ANNOT,
    note=(
        "Labelled box where the microinverters' AC trunk cable ends and the home run to the "
        "branch breaker starts (Enphase diagrams). Generated on demand, not in the legend."
    ),
)


# --- PV string with every module (owner decision 2026-10-07) ----------------------------------

MODULES_PER_ROW = 6
MAX_STRING_MODULES = 30
_MOD_W, _MOD_H, _MOD_PITCH, _ROW_PITCH = 5.0, 10.0, 7.5, 15.0


def pv_string_name(n_modules: int, extend: str = "up") -> str:
    """Block name of a string of ``n_modules`` drawn in full, e.g. ``PVSLD_PV_STRING_7M_UP``."""
    return f"PVSLD_PV_STRING_{n_modules}M_{'UP' if extend == 'up' else 'DN'}"


def pv_string(n_modules: int, extend: str = "up") -> SymbolSpec:
    """A PV string with every module drawn: CFE modules in series, six to a row.

    The output ``OUT`` is the end of the last row, on the base point's row (``y = 0``); the other
    rows stack away from it, upwards (``extend="up"``) or downwards (``"down"``), and the tag,
    description and model sit beyond them. Rows are joined by a return conductor along the left.
    Two strings of one inverter use one of each, so both run straight into their MPPT inputs.

    Raises:
        ValueError: ``n_modules`` is outside ``1..MAX_STRING_MODULES`` or ``extend`` is unknown.
    """
    if not 1 <= n_modules <= MAX_STRING_MODULES:
        raise ValueError(f"a string takes 1 to {MAX_STRING_MODULES} modules, not {n_modules}")
    if extend not in ("up", "down"):
        raise ValueError(f"extend must be 'up' or 'down', not {extend!r}")
    counts = [MODULES_PER_ROW] * (n_modules // MODULES_PER_ROW)
    if n_modules % MODULES_PER_ROW:
        counts.append(n_modules % MODULES_PER_ROW)
    rows = len(counts)
    sign = 1 if extend == "up" else -1
    ys = [_r(sign * (rows - 1 - r) * _ROW_PITCH) for r in range(rows)]
    # A short last row is right-aligned, so the output is always at the right edge.
    starts = [0.0] * rows
    if rows > 1:
        starts[-1] = (MODULES_PER_ROW - counts[-1]) * _MOD_PITCH
    geometry: list[Primitive] = []
    for row, (count, y, x0) in enumerate(zip(counts, ys, starts, strict=True)):
        for i in range(count):
            x = x0 + i * _MOD_PITCH
            top = y + _MOD_H / 2
            geometry += [
                Polyline(_rect(x, y - _MOD_H / 2, x + _MOD_W, top), _EQ_DC, closed=True),
                Polyline(((x, top), (x + _MOD_W / 2, _r(y + 1.25)), (x + _MOD_W, top)), _EQ_DC),
            ]
            if i < count - 1:
                geometry.append(Line(x + _MOD_W, y, x + _MOD_PITCH, y, _DC_COND))
        if row < rows - 1:
            x_end = x0 + (count - 1) * _MOD_PITCH + _MOD_W
            y_next, x_next = ys[row + 1], starts[row + 1]
            middle = _r((y + y_next) / 2)
            geometry.append(
                Polyline(
                    (
                        (x_end, y),
                        (x_end + 1.25, y),
                        (x_end + 1.25, middle),
                        (x_next - 1.25, middle),
                        (x_next - 1.25, y_next),
                        (x_next, y_next),
                    ),
                    _DC_COND,
                )
            )
    x_out = starts[-1] + (counts[-1] - 1) * _MOD_PITCH + _MOD_W
    far = max(ys) + _MOD_H / 2 if extend == "up" else min(ys) - _MOD_H / 2
    if extend == "up":
        tag_xy, desc_xy, model_y = (0.0, far + 10.5), (0.0, far + 6.5), far + 2.5
    else:
        tag_xy, desc_xy, model_y = (0.0, far - 6.0), (0.0, far - 10.5), far - 14.5
    return _spec(
        pv_string_name(n_modules, extend),
        "Rama de módulos fotovoltaicos en serie",
        "CFE G0100-04 Apéndices C y D",
        _EQ_DC,
        tuple(geometry),
        (Port("OUT", x_out, 0, "right", "DC"),),
        iec_ref="IEC 60617 S00908",
        tag_xy=tag_xy,
        desc_xy=desc_xy,
        extra=(
            AttDef("MODEL", "Modelo del módulo", 0, _r(model_y)),
            *_hidden(
                ("MFR", "Fabricante del módulo"),
                ("MPPT", "Entrada MPPT del inversor"),
                ("PMAX_W", "Potencia del módulo (W)"),
                ("N_SERIES", "Módulos en serie"),
                ("KWP", "Potencia de la rama (kWp)"),
                ("VOC_MAX_V", "Voc máxima a T mín (V)"),
                ("VMP_V", "Vmp en STC de la rama (V)"),
                ("ISC_A", "Isc del módulo (A)"),
                ("IMP_A", "Imp del módulo (A)"),
            ),
        ),
        family=F_GEN,
        note=(
            "Every module of the string drawn as the CFE module (5 x 10 mm), six to a row, rows "
            "joined in series; the generator defines it per module count."
        ),
    )


BATTERY = _spec(
    "PVSLD_BATTERY",
    "Batería o banco de baterías",
    _nmx("4.2.280 (batería de celdas, forma 1)"),
    layers.BATTERY_EQUIPMENT,
    (
        Line(0, 0, 5, 0, layers.BATTERY_EQUIPMENT),
        *(
            Line(
                5 + 2.5 * i,
                -(6.25 if i % 2 else 3.5),
                5 + 2.5 * i,
                6.25 if i % 2 else 3.5,
                layers.BATTERY_EQUIPMENT,
                lineweight=50,
            )
            for i in range(8)
        ),
        Line(22.5, 0, 27.5, 0, layers.BATTERY_EQUIPMENT),
        Line(23.5, 9.5, 26.5, 9.5, layers.BATTERY_EQUIPMENT),
        Line(25, 8, 25, 11, layers.BATTERY_EQUIPMENT),
    ),
    (Port("NEG", 0, 0, "left", "DC"), Port("POS", 27.5, 0, "right", "DC")),
    nmx_ref=_nmx("4.2.280 (IEC S01365)"),
    extra=_hidden(
        ("CHEMISTRY", "Tecnología"),
        ("CAPACITY_KWH", "Capacidad (kWh)"),
        ("VOLT_V", "Tensión nominal (V)"),
        ("CELLS", "Celdas o módulos en serie"),
    ),
    family=F_GEN,
    note=(
        "Four cells: alternating short and long plates; the long plate is positive, marked "
        "with a plus sign as NMX recommends."
    ),
)

# --- Conversion and transformation ----------------------------------------------------------------

HYBRID_INVERTER = _spec(
    "PVSLD_INV_HYBRID",
    "Inversor híbrido (con puerto de batería)",
    "pvsld (composición) sobre CFE G0100-04 Apéndice C (inversor); ref. DGE 06-63-06",
    _EQ_AC,
    (
        Polyline(_rect(0, -22.5, 50, 22.5), _EQ_AC, closed=True),
        Line(0, -22.5, 50, 22.5, _EQ_AC),
        Line(8, 15, 18, 15, _EQ_AC),
        Line(8, 12, 18, 12, _EQ_AC),
        Polyline(_sine(38, -13, 10, 2.5), _EQ_AC),
        Label(1.5, 11.5, TEXT_HEIGHT_MM, "FV"),
        Label(1.5, -14, TEXT_HEIGHT_MM, "BAT"),
    ),
    (
        Port("A", 0, 10, "left", "DC"),
        Port("BAT", 0, -10, "left", "DC"),
        Port("AC", 50, 0, "right", "AC"),
        Port("PE", 25, -22.5, "down", "PE"),
    ),
    tag_xy=(0, 36.5),
    desc_xy=(0, 32.5),
    extra=(
        AttDef("MFR", "Fabricante", 0, 28.5),
        AttDef("MODEL", "Modelo", 0, 24.5),
        *_hidden(
            ("PAC_W", "Potencia CA nominal (W)"),
            ("VAC_V", "Tensión CA nominal (V)"),
            ("PHASES", "Conductores energizados"),
            ("IAC_MAX_A", "Corriente CA máxima (A)"),
            ("VDC_MAX_V", "Tensión CD máxima (V)"),
            ("MPPT_N", "Número de MPPT"),
            ("BAT_VOLT_V", "Tensión de batería (V)"),
            ("BAT_PCHARGE_W", "Potencia de carga (W)"),
            ("OCPD_MAX_A", "Protección máxima CA (A)"),
            ("ISOLATION", "Aislamiento"),
        ),
    ),
    family=F_CONV,
    note=(
        "No official symbol exists. The CFE inverter square with the second DC terminal used as "
        "the battery port (BAT). DGE 06-63-06 draws a bidirectional converter instead."
    ),
)

MICROINVERTER = _spec(
    "PVSLD_MICROINV",
    "Microinversor (por módulo)",
    "pvsld (composición) sobre CFE G0100-04 Apéndice C (inversor)",
    _EQ_AC,
    _converter_box(_EQ_AC, "uINV", inverter=True),
    (Port("DC", 0, 0, "left", "DC"), Port("AC", 15, 0, "right", "AC")),
    extra=_hidden(
        ("MFR", "Fabricante"),
        ("MODEL", "Modelo"),
        ("PAC_W", "Potencia CA nominal (W)"),
        ("VAC_V", "Tensión CA nominal (V)"),
        ("MODULES_N", "Módulos conectados"),
    ),
    family=F_CONV,
    note="No official symbol. The CFE inverter square at 15 mm with the caption uINV.",
)

OPTIMIZER = _spec(
    "PVSLD_OPTIMIZER",
    "Optimizador de potencia (convertidor CD/CD)",
    "pvsld (composición); convertidor CD/CD de DGE (basada en IEC 60617) 06-63-02",
    _EQ_DC,
    _converter_box(_EQ_DC, "OPT", inverter=False),
    (Port("IN", 0, 0, "left", "DC"), Port("OUT", 15, 0, "right", "DC")),
    extra=_hidden(
        ("MFR", "Fabricante"),
        ("MODEL", "Modelo"),
        ("PMAX_W", "Potencia (W)"),
        ("VOUT_MAX_V", "Tensión de salida máxima (V)"),
    ),
    family=F_CONV,
    note=(
        "No official symbol. DC/DC converter (square, diagonal, DC "
        "sign on both sides), caption OPT."
    ),
)

CHARGE_CONTROLLER = _spec(
    "PVSLD_CHARGE_CTRL",
    "Controlador de carga (MPPT)",
    "pvsld (composición); convertidor CD/CD de DGE (basada en IEC 60617) 06-63-02",
    _EQ_DC,
    _converter_box(_EQ_DC, "MPPT", inverter=False),
    (Port("PV", 0, 0, "left", "DC"), Port("BAT", 15, 0, "right", "DC")),
    extra=_hidden(
        ("MFR", "Fabricante"),
        ("MODEL", "Modelo"),
        ("IOUT_A", "Corriente de carga (A)"),
        ("VOC_MAX_V", "Voc máxima de entrada (V)"),
        ("BAT_VOLT_V", "Tensión de batería (V)"),
    ),
    family=F_CONV,
    note="No official symbol. DC/DC converter square with the caption MPPT; battery on the output.",
)

# --- Protection -----------------------------------------------------------------------------------


def _iec_breaker() -> tuple[Primitive, ...]:
    """Vertical IEC/UNE thermomagnetic breaker (owner's sheet, page 4), turned to run rightwards."""
    return rotate90(
        (
            Line(0, 0, 0, -2.75, _EQ_AC),
            Circle(0, -3.33, 0.58, _EQ_AC),
            Line(0, -8.67, -2.7, -3.33, _EQ_AC),
            Line(0, -8.67, 0, -11.55, _EQ_AC),
            Polyline(_rect(-2.7, -22.75, 2.75, -11.55), _EQ_AC, closed=True),
            Line(-2.7, -17.1, 2.75, -17.1, _EQ_AC),
            Polyline(
                ((0, -11.55), (0, -13.1), (-1.7, -13.1), (-1.7, -15.55), (0, -15.55), (0, -17.1)),
                _EQ_AC,
            ),
            Label(-1.4, -20.9, 2.2, "I>"),
            Line(0, -22.75, 0, -25, _EQ_AC),
            Line(-8.6, -4.9, -8.6, -7.1, _EQ_AC),
            Line(-8.6, -6, -6.35, -6, _EQ_AC),
            Polyline(_rect(-6.35, -7.1, -4.1, -4.9), _EQ_AC, closed=True),
            Line(-6.35, -6, -4.1, -6, _EQ_AC),
            Line(-5.225, -4.9, -5.225, -7.1, _EQ_AC),
            _dashed(-4.1, -6, -1.35, -6, _EQ_AC),
            _dashed(-5.2, -7.1, -5.2, -19.9, _EQ_AC),
            _dashed(-5.2, -14.3, -2.7, -14.3, _EQ_AC),
            _dashed(-5.2, -19.9, -2.7, -19.9, _EQ_AC),
        )
    )


BREAKER_DC = _spec(
    "PVSLD_CB_DC",
    "Interruptor termomagnético de CD (ITM CD)",
    "CFE G0100-04 Apéndice C (interruptor termomagnético), en un circuito de CD",
    _EQ_DC,
    _cfe_breaker(0, 0, _EQ_DC),
    (Port("IN", 0, 0, "left", "DC"), Port("OUT", 20, 0, "right", "DC")),
    iec_ref="IEC 60617 S00287",
    tag_xy=(0, 9.5),
    desc_xy=(0, -6),
    extra=(
        AttDef("ROLE", "Función", 0, 5.5),
        *_hidden(
            ("POLES", "Polos"),
            ("RATING_A", "Corriente nominal (A)"),
            ("VOLT_V", "Tensión CD (V)"),
            ("KAIC_KA", "Capacidad interruptiva (kA)"),
        ),
    ),
    nmx_ref="NMX-J-136-ANCE-2019 4.2.15 (forma distinta)",
    family=F_PROT,
    note=(
        "The CFE thermomagnetic breaker on the DC equipment layer with DC ports: the string "
        "breaker of the DC protection box (owner decision 2026-10-08)."
    ),
)


BREAKER_IEC = _spec(
    "PVSLD_CB_IEC",
    "Interruptor termomagnético (forma IEC/UNE)",
    "UNE-EN 60617 (hoja del propietario, p. 4); cf. NMX-J-136-ANCE-2019 4.2.169 (IEC S00287)",
    _EQ_AC,
    _iec_breaker(),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 25, 0, "right", "AC")),
    iec_ref="IEC 60617 S00287",
    nmx_ref=_nmx("4.2.15 y 4.2.169"),
    extra=(
        AttDef("ROLE", "Función (I1, I2)", 0, 9.5),
        *_hidden(
            ("POLES", "Polos"),
            ("RATING_A", "Corriente nominal (A)"),
            ("VOLT_V", "Tensión (V)"),
            ("KAIC_KA", "Capacidad interruptiva (kA)"),
            ("CURVE", "Curva de disparo"),
            ("BACKFED", "Alimentado en sentido inverso"),
        ),
    ),
    family=F_PROT,
    note=(
        "Single-line form of the owner's sheet: open contact, thermal step and magnetic I> cell, "
        "operator mark and dashed links. Alternative to PVSLD_CB (CFE), which the generator "
        "uses (owner decision 2026-10-08); same attributes and port ids."
    ),
)


def _rcd() -> tuple[Primitive, ...]:
    layer = _EQ_AC
    return rotate90(
        (
            Line(0, 0, 0, -1.5, layer),
            Circle(0, -2.17, 0.67, layer),
            Line(0, -8.83, -3.27, -2.17, layer),
            Line(0, -8.83, 0, -20, layer),
            Line(-10.4, -12.2, 3.15, -12.2, layer),
            Arc(3.15, -14.95, 2.75, 270, 90, layer),
            Line(3.15, -17.7, -10.4, -17.7, layer),
            Line(-10.4, -17.7, -10.4, -12.2, layer),
            _ellipse(-4.45, -14.95, 1.4, 2.7, layer),
            Polyline(_rect(-13.1, -16.3, -7.8, -13.5), layer, closed=True),
            Line(-10.4, -4.2, -10.4, -6.8, layer),
            Line(-10.4, -5.5, -7.77, -5.5, layer),
            Polyline(_rect(-7.77, -6.83, -5.1, -4.17), layer, closed=True),
            Line(-7.77, -5.5, -5.1, -5.5, layer),
            Line(-6.43, -4.17, -6.43, -6.83, layer),
            _dashed(-5.1, -5.5, -1.63, -5.5, layer),
            _dashed(-6.43, -6.83, -6.43, -15, layer),
            Line(-6.43, -15, -7.8, -15, layer),
        )
    )


RESIDUAL_CURRENT_DEVICE = _spec(
    "PVSLD_RCD",
    "Interruptor diferencial (protección de corriente residual)",
    "UNE-EN 60617 (hoja del propietario, p. 4, interruptor "
    "diferencial); sin símbolo en NMX-J-136-ANCE-2019",
    _EQ_AC,
    _rcd(),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 20, 0, "right", "AC")),
    extra=(
        AttDef("ROLE", "Función", 0, 9.5),
        *_hidden(
            ("POLES", "Polos"),
            ("RATING_A", "Corriente nominal (A)"),
            ("VOLT_V", "Tensión (V)"),
            ("IDN_MA", "Sensibilidad (mA)"),
            ("RCD_TYPE", "Tipo (AC, A, B)"),
        ),
    ),
    family=F_PROT,
    note=(
        "Single-line form of the owner's sheet: open contact with its operator mark, the "
        "conductor through a toroid and the trip coil, dashed link to the operator. NMX has "
        "no symbol; its Table 2 only lists device numbers."
    ),
)


def _fuse(layer: str) -> tuple[Primitive, ...]:
    """NMX 4.2.21: a rectangle with an end cap line at each end, 22.5 mm with its leads."""
    return (
        Line(0, 0, 5, 0, layer),
        Polyline(_rect(5, -3.75, 17.5, 3.75), layer, closed=True),
        Line(7.5, -3.75, 7.5, 3.75, layer),
        Line(15, -3.75, 15, 3.75, layer),
        Line(17.5, 0, 22.5, 0, layer),
    )


def _fuse_spec(name: str, description_es: str, layer: str, kind: str, classes: str) -> SymbolSpec:
    return _spec(
        name,
        description_es,
        _nmx("4.2.21 (FUS, forma nacional 1)"),
        layer,
        _fuse(layer),
        (Port("IN", 0, 0, "left", kind), Port("OUT", 22.5, 0, "right", kind)),
        nmx_ref=_nmx("4.2.21 (alt. 4.2.174, IEC S00362)"),
        extra=_hidden(
            ("RATING_A", "Corriente nominal (A)"),
            ("VOLT_V", "Tensión (V)"),
            ("FUSE_CLASS", f"Clase ({classes})"),
            ("KAIC_KA", "Capacidad interruptiva (kA)"),
        ),
        family=F_PROT,
        note=(
            "Rectangle with an end cap line at each end; the leads stop at the body. Turned to "
            f"run rightwards. Port kind {kind}; "
            + ("string fuses (gPV)." if kind == "DC" else "same shape as PVSLD_FUSE, AC ports.")
        ),
    )


FUSE = _fuse_spec("PVSLD_FUSE", "Fusible (CD)", _EQ_DC, "DC", "gPV, gG")
FUSE_AC = _fuse_spec("PVSLD_FUSE_AC", "Fusible (CA)", _EQ_AC, "AC", "gG, gL")

SPD_AC = _spec(
    "PVSLD_SPD_AC",
    "Varistor en CA (dispositivo de protección contra sobretensiones)",
    "CFE G0100-04 Apéndice C (varistor), usado en CA en Apéndice D",
    _EQ_AC,
    _cfe_varistor(_EQ_AC),
    (Port("L", 0, 0, "up", "AC"), Port("PE", 0, -20, "down", "PE")),
    nmx_ref=_nmx("4.2.308 (SPD, forma distinta)"),
    tag_xy=(7, -6),
    desc_xy=(7, -10),
    extra=(
        AttDef("SPEC", "Datos del varistor", 7, -14),
        *_hidden(
            ("SPD_TYPE", "Tipo (1, 2, 3)"),
            ("UC_V", "Tensión máxima de operación continua (V)"),
            ("UP_KV", "Nivel de protección (kV)"),
            ("IN_KA", "Corriente nominal de descarga (kA)"),
        ),
    ),
    family=F_PROT,
    note=(
        "The CFE varistor on the AC equipment layer with an AC line port "
        "(figures D1/D2 draw it on both sides)."
    ),
)

GROUND_FAULT_DETECTOR = _spec(
    "PVSLD_GFDI",
    "Detector de falla a tierra",
    "CFE G0100-04 Apéndice D, fig. D1 (detector de falla a tierra)",
    _EQ_DC,
    _labelled_box(_EQ_DC, ("Detector", "de falla a", "tierra"), 30, 15),
    _SIGNAL_PORTS,
    extra=_hidden(("TRIP_MA", "Umbral de disparo (mA)"), ("GFDI_TYPE", "Tipo")),
    family=F_PROT,
    note=(
        "The labelled block of figure D1. SENS takes the dashed link from the current sensor, "
        "TRIP leaves for the DC switch. Signal ports (kind SIG)."
    ),
)

INSULATION_MONITOR = _spec(
    "PVSLD_INS_MONITOR",
    "Monitor de aislamiento",
    "CFE G0100-04 Apéndice D, fig. D2 (monitor de aislamiento)",
    _EQ_DC,
    _labelled_box(_EQ_DC, ("Monitor", "de", "aislamiento"), 30, 15),
    _SIGNAL_PORTS,
    extra=_hidden(("TRIP_KOHM", "Umbral de alarma (kΩ)")),
    family=F_PROT,
    note="The labelled block of figure D2 (floating arrays). Signal ports (kind SIG).",
)

AFCI = _spec(
    "PVSLD_AFCI",
    "Detector de falla por arco (AFCI)",
    "pvsld (composición); bloque rotulado al estilo de CFE G0100-04 Apéndice D",
    _EQ_DC,
    _labelled_box(_EQ_DC, ("AFCI", "Detector de", "falla por arco"), 30, 15),
    _SIGNAL_PORTS,
    extra=_hidden(("AFCI_TYPE", "Tipo (integrado, externo)")),
    family=F_PROT,
    note="No official symbol in CFE, NMX or DGE. A labelled block in the style of figure D1.",
)

PROTECTIVE_RELAY = _spec(
    "PVSLD_RELAY",
    "Relevador de protección",
    "DGE (basada en IEC 60617) 07-73-01; números ANSI de NMX-J-136-ANCE-2019, Tabla 2",
    _EQ_UT,
    (Polyline(_rect(0, -15, 25, 0), _EQ_UT, closed=True),),
    (
        Port("SENS", 0, -7.5, "left", "SIG", required=False),
        Port("TRIP", 25, -7.5, "right", "SIG", required=False),
    ),
    nmx_ref=_nmx("Tabla 2 (números de dispositivo, solo texto)"),
    tag_xy=(0, 3),
    desc_xy=(0, -19),
    extra=(
        AttDef("FUNCTION", "Función medida (U<, U>, f<, f>)", 3, -7, height=4, legend_sample="U<"),
        AttDef("ANSI_NO", "Número de dispositivo ANSI (27, 59, 81)", 3, -12.5, legend_sample="27"),
        *_hidden(("SETTING", "Ajuste"), ("DELAY_S", "Retardo (s)")),
    ),
    family=F_PROT,
    note=(
        "Measuring-relay rectangle; the quantity and condition are the visible attribute FUNCTION "
        "(for example U<), the ANSI number is ANSI_NO. NMX gives numbers only, no drawing."
    ),
)

# --- Switching and control ------------------------------------------------------------------------

DC_DISCONNECT = _spec(
    "PVSLD_DC_DISCONNECT",
    "Desconectador de CD (interruptor manual con carga)",
    "CFE G0100-04 Apéndice C (interruptor manual), usado como "
    "desconectador de CD en Apéndice D, fig. D1",
    _EQ_DC,
    _cfe_manual_switch(_EQ_DC),
    (Port("IN", 0, 0, "left", "DC"), Port("OUT", 25, 0, "right", "DC")),
    nmx_ref=_nmx("4.2.94 y 4.2.119 (con carga)"),
    tag_xy=(0, 10),
    desc_xy=(0, -9.5),
    extra=(
        *_hidden(
            ("ROLE", "Función"),
            ("POLES", "Polos"),
            ("RATING_A", "Corriente nominal (A)"),
            ("VOLT_V", "Tensión (V)"),
            ("LOAD_BREAK", "Con capacidad de corte con carga"),
        ),
    ),
    family=F_SWITCH,
    note=(
        "The CFE manual switch on the DC equipment layer with DC ports (owner decision "
        "2026-10-08); LOAD_BREAK records whether it may open under load."
    ),
)

FUSE_SWITCH = _spec(
    "PVSLD_FUSE_DISC",
    "Fusible-seccionador",
    _nmx("4.2.176 (FUS-SECC, IEC S00369)"),
    _EQ_AC,
    _blade_with_fuse(_EQ_AC, bar=True),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 25, 0, "right", "AC")),
    iec_ref="IEC 60617 S00369",
    nmx_ref=_nmx("4.2.176 (alt. 4.2.177, S00370)"),
    extra=_hidden(
        ("POLES", "Polos"),
        ("RATING_A", "Corriente nominal (A)"),
        ("VOLT_V", "Tensión (V)"),
        ("FUSE_A", "Fusible (A)"),
    ),
    family=F_SWITCH,
    note=(
        "The blade is the fuse, tilted off the fixed contact whose bar marks the isolator. "
        "Turned to run rightwards."
    ),
)

SAFETY_SWITCH = _spec(
    "PVSLD_SAFETY_SWITCH",
    "Interruptor de seguridad con fusibles (CA)",
    _nmx("4.2.128 (IS, forma unifilar)"),
    _EQ_AC,
    (
        Polyline(_rect(3.75, -5, 26.25, 5), _EQ_AC, closed=True),
        Line(0, 0, 7.5, 0, _EQ_AC),
        Dot(7.5, 0, 0.8, _EQ_AC),
        Dot(11.25, 0, 0.8, _EQ_AC),
        Line(11.25, 0, 8.5, 3.25, _EQ_AC),
        Line(11.25, 0, 15, 0, _EQ_AC),
        Arc(16.25, 0, 1.25, 0, 180, _EQ_AC),
        Arc(18.75, 0, 1.25, 180, 360, _EQ_AC),
        Line(20, 0, 30, 0, _EQ_AC),
    ),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 30, 0, "right", "AC")),
    extra=_hidden(
        ("POLES", "Polos"),
        ("RATING_A", "Corriente nominal (A)"),
        ("VOLT_V", "Tensión (V)"),
        ("FUSE_A", "Fusibles (A)"),
    ),
    family=F_SWITCH,
    note=(
        "Box with the visible-blade switch and the fuse in series. The "
        "fuse is drawn as a simple S-wave."
    ),
)

TRANSFER_SWITCH = _spec(
    "PVSLD_TRANSFER_SW",
    "Interruptor de transferencia",
    "DGE (basada en IEC 60617) 07-71-03; dispositivos 43 y 83 de NMX-J-136-ANCE-2019, Tabla 2",
    _EQ_AC,
    (
        Line(-5, 0, -5, -7.5, _EQ_AC),
        Line(-5, -7.5, -2.5, -7.5, _EQ_AC),
        Line(0, 0, 0, -7.5, _EQ_AC),
        Line(0, -15, -3.75, -7.5, _EQ_AC),
        Line(0, -15, 0, -22.5, _EQ_AC),
    ),
    (
        Port("A", -5, 0, "up", "AC"),
        Port("B", 0, 0, "up", "AC"),
        Port("COM", 0, -22.5, "down", "AC"),
    ),
    nmx_ref=_nmx("Tabla 2, dispositivos 43 y 83 (solo texto)"),
    tag_xy=(3, -12),
    desc_xy=(3, -16),
    extra=(
        AttDef("ROLE", "Función (ATS, 43)", 3, -20),
        *_hidden(
            ("POLES", "Polos"), ("RATING_A", "Corriente nominal (A)"), ("VOLT_V", "Tensión (V)")
        ),
    ),
    family=F_SWITCH,
    note=(
        "Break-before-make changeover contact: the common lead COM at the bottom rests on contact "
        "A; contact B is the other source. Vertical, not turned."
    ),
)

CONTACTOR = _spec(
    "PVSLD_CONTACTOR",
    "Contactor",
    _nmx("4.2.168 (contacto, IEC S00284)"),
    _EQ_AC,
    rotate90(
        (
            Line(0, 0, 0, -7.5, _EQ_AC),
            Arc(0, -5.75, 1.75, 90, 270, _EQ_AC),
            Line(0, -15, -5, -7.5, _EQ_AC),
            Line(0, -15, 0, -22.5, _EQ_AC),
        )
    ),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 22.5, 0, "right", "AC")),
    iec_ref="IEC 60617 S00284",
    extra=_hidden(
        ("POLES", "Polos"),
        ("RATING_A", "Corriente nominal (A)"),
        ("VOLT_V", "Tensión (V)"),
        ("COIL_V", "Tensión de bobina (V)"),
    ),
    family=F_SWITCH,
    note=(
        "Make contact with the contactor qualifier (small semicircle on the fixed contact). NMX "
        "titles it only 'Contacto'. Turned to run rightwards."
    ),
)

# --- Measurement and monitoring -------------------------------------------------------------------

METER_M = _spec(
    "PVSLD_METER_M",
    "Medidor de consumo o límite de exportación",
    _nmx("4.2.126 (equipo de medición, M)"),
    _UT,
    (
        Polyline(_rect(0, -7.5, 15, 7.5), _UT, closed=True),
        Label(4.9, -2.4, 6.5, "M"),
    ),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 15, 0, "right", "AC")),
    extra=_hidden(
        ("METER_TYPE", "Tipo"),
        ("FUNCTION", "Función (consumo, límite de exportación)"),
        ("OWNER", "Propietario"),
        ("METER_NO", "Número de medidor"),
    ),
    family=F_MEAS,
    note=(
        "NMX measuring-equipment square with the letter M (owner decision), distinct from the "
        "CFE bidirectional revenue meter."
    ),
)

MONITORING = _spec(
    "PVSLD_MONITOR",
    "Subsistema de control y monitoreo",
    "CFE G0100-04 Apéndice D, figs. D1 y D2 (subsistema de control y monitoreo)",
    _COMM,
    (
        Polyline(_rect(0, -7.5, 60, 7.5), _COMM, closed=True),
        Label(4, -1.2, TEXT_HEIGHT_MM, "Subsistema de control y monitoreo"),
    ),
    (Port("LINK", 0, 0, "left", "SIG", required=False),),
    extra=_hidden(("PROTOCOL", "Protocolo"), ("MODEL", "Modelo")),
    family=F_MEAS,
    note="Wide dashed block (layer E-COMM-COND, dashed) with the name written inside, as in D1/D2.",
)

# --- Earthing -------------------------------------------------------------------------------------

PROTECTIVE_EARTH = _spec(
    "PVSLD_PE",
    "Tierra de protección (conductor de puesta a tierra de equipos)",
    _nmx("4.2.117 (TPT, IEC S00202)"),
    layers.GROUNDING,
    (
        Circle(0, -5, 5, layers.GROUNDING),
        Line(0, 0, 0, -5.5, layers.GROUNDING, linetype=_SOLID),
        Line(-3.7, -5.5, 3.7, -5.5, layers.GROUNDING, linetype=_SOLID),
        Line(-2.6, -7, 2.6, -7, layers.GROUNDING, linetype=_SOLID),
        Line(-1.3, -8.5, 1.3, -8.5, layers.GROUNDING, linetype=_SOLID),
    ),
    (Port("PE", 0, 0, "up", "PE"),),
    iec_ref="IEC 60617 S00202",
    tag_xy=(7, -4),
    desc_xy=(7, -8),
    extra=_hidden(("EGC_SIZE", "Calibre del conductor de puesta a tierra")),
    family=F_EARTH,
    note="Earth symbol inside a circle: protective earth, as opposed to the electrode PVSLD_GND.",
)

GROUND_BUS = _spec(
    "PVSLD_GND_BUS",
    "Barra de puesta a tierra",
    _nmx("4.2.130 (BPT)"),
    layers.GROUNDING,
    (
        Line(0, 0, 30, 0, layers.GROUNDING, lineweight=50, linetype=_SOLID),
        Label(27, 1.5, TAG_HEIGHT_MM, "T"),
    ),
    (Port("L", 0, 0, "left", "PE"), Port("R", 30, 0, "right", "PE", required=False)),
    extra=_hidden(("BUS_A", "Barra (A)"), ("MATERIAL", "Material")),
    family=F_EARTH,
    note="Heavy straight line with the letter T above its right end.",
)

NEUTRAL_BUS = _spec(
    "PVSLD_NEUTRAL_BUS",
    "Barra de neutro",
    _nmx("4.2.129"),
    _AC_COND,
    (
        Line(0, 0, 30, 0, _AC_COND, lineweight=50),
        Label(27, 1.5, TAG_HEIGHT_MM, "N"),
    ),
    (Port("L", 0, 0, "left", "AC"), Port("R", 30, 0, "right", "AC", required=False)),
    extra=_hidden(("BUS_A", "Barra (A)"), ("MATERIAL", "Material")),
    family=F_EARTH,
    note="Same line as the ground bus with the letter N. Kept with the earthing family.",
)

# --- Medium voltage -------------------------------------------------------------------------------

TRANSFORMER_MV = _spec(
    "PVSLD_XFMR",
    "Transformador de potencia MT/BT",
    _nmx("4.2.123 (TR)"),
    _UT,
    (
        Line(0, 0, 7.5, 0, _UT),
        *(_bump(7.5, cy, 2.5, 2.5, _UT, right=True) for cy in (7.5, 2.5, -2.5, -7.5)),
        *(_bump(17.5, cy, 2.5, 2.5, _UT, right=False) for cy in (7.5, 2.5, -2.5, -7.5)),
        Line(17.5, 0, 25, 0, _UT),
    ),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 25, 0, "right", "AC")),
    nmx_ref=_nmx("4.2.123 (alt. 4.2.69, IEC S00841)"),
    tag_xy=(0, 12),
    desc_xy=(0, -14),
    extra=(
        AttDef("SPEC", "Potencia y relación", 0, -18),
        *_hidden(
            ("KVA", "Potencia (kVA)"),
            ("VOLT_PRI_V", "Tensión primaria (V)"),
            ("VOLT_SEC_V", "Tensión secundaria (V)"),
            ("VECTOR_GROUP", "Grupo de conexión"),
            ("IMPEDANCE_PCT", "Impedancia (%)"),
        ),
    ),
    family=F_MV,
    note=(
        "Two facing coils of four bumps without core lines, a lead at "
        "the middle of each (owner decision)."
    ),
)

FUSE_CUTOUT = _spec(
    "PVSLD_CUTOUT",
    "Cortacircuito fusible (CCF)",
    _nmx("4.2.122 (FUS-DES, IEC S00368)"),
    _UT,
    _blade_with_fuse(_UT, bar=False),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 25, 0, "right", "AC")),
    iec_ref="IEC 60617 S00368",
    extra=_hidden(
        ("RATING_A", "Corriente nominal (A)"),
        ("VOLT_KV", "Tensión (kV)"),
        ("FUSE_A", "Fusible (A)"),
        ("KAIC_KA", "Capacidad interruptiva (kA)"),
    ),
    family=F_MV,
    note=(
        "The fuse is the open blade, as in the fuse-switch without the "
        "isolator bar. Turned rightwards."
    ),
)

MV_ARRESTER = _spec(
    "PVSLD_ARRESTER_MV",
    "Apartarrayos de media tensión",
    _nmx("4.2.178 (AR, IEC S00373)"),
    _UT,
    (
        Line(0, 0, 0, -12, _UT),
        Polyline(_rect(-2.5, -20, 2.5, -5), _UT, closed=True),
        Fill(((-1.5, -8), (1.5, -8), (0, -10.5)), _UT),
        Line(0, -20, 0, -25, _UT),
    ),
    (Port("L", 0, 0, "up", "AC"), Port("PE", 0, -25, "down", "PE")),
    iec_ref="IEC 60617 S00373",
    nmx_ref=_nmx("4.2.178 (alt. 4.2.120)"),
    tag_xy=(6, -8),
    desc_xy=(6, -12),
    extra=_hidden(
        ("VOLT_KV", "Tensión nominal (kV)"),
        ("MCOV_KV", "Tensión máxima de operación continua (kV)"),
        ("IN_KA", "Corriente de descarga (kA)"),
    ),
    family=F_MV,
    note=(
        "Slim rectangle on the line with a filled arrowhead; the lower lead "
        "goes to earth. Hangs below the origin."
    ),
)

MV_DISCONNECT = _spec(
    "PVSLD_DISCONNECT_MV",
    "Cuchillas desconectadoras de media tensión",
    _nmx("4.2.118 (DSSC)"),
    _UT,
    (
        Line(0, 0, 7.5, 0, _UT),
        Dot(7.5, 0, 0.8, _UT),
        Dot(15, 0, 0.8, _UT),
        Line(15, 0, 9.5, 5.5, _UT),
        Line(15, 0, 25, 0, _UT),
    ),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 25, 0, "right", "AC")),
    nmx_ref=_nmx("4.2.118 (alt. 4.2.119 con carga, 4.2.170 IEC S00288)"),
    extra=_hidden(
        ("POLES", "Polos"),
        ("RATING_A", "Corriente nominal (A)"),
        ("VOLT_KV", "Tensión (kV)"),
        ("LOAD_BREAK", "Con capacidad de corte con carga"),
    ),
    family=F_MV,
    note=(
        "Safety disconnect without load break: filled dot on each "
        "contact, blade open at 45 degrees."
    ),
)

CT_MV = _spec(
    "PVSLD_CT_MV",
    "Transformador de corriente (TC)",
    _nmx("4.2.124 (TC)"),
    _UT,
    rotate90(
        (
            Line(0, 0, 0, -15, _UT, lineweight=50),
            Polyline(((1.5, -2.5), (-5, -5), (1.5, -7.5), (-5, -10), (1.5, -12.5)), _UT),
        )
    ),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 15, 0, "right", "AC")),
    extra=(
        AttDef("SPEC", "Relación de transformación", 0, -10),
        *_hidden(("RATIO", "Relación (A/A)"), ("BURDEN_VA", "Carga (VA)"), ("CLASS", "Clase")),
    ),
    family=F_MV,
    note=(
        "Heavy conductor crossed by a two-chevron zigzag. Distinct "
        "from the CFE current sensor PVSLD_CT."
    ),
)

VT_MV = _spec(
    "PVSLD_VT_MV",
    "Transformador de potencial (TP)",
    _nmx("4.2.75 (TP)"),
    _UT,
    (
        Line(0, 0, 2.5, 0, _UT, lineweight=50),
        Polyline(((2.5, 4.5), (7.5, 2.25), (2.5, 0), (7.5, -2.25), (2.5, -4.5)), _UT),
        Polyline(((13, 4.5), (8, 2.25), (13, 0), (8, -2.25), (13, -4.5)), _UT),
        Line(13, 0, 15, 0, _UT, lineweight=50),
    ),
    (Port("IN", 0, 0, "left", "AC"), Port("OUT", 15, 0, "right", "AC")),
    extra=(
        AttDef("SPEC", "Relación de transformación", 0, -10),
        *_hidden(("RATIO", "Relación (V/V)"), ("BURDEN_VA", "Carga (VA)"), ("CLASS", "Clase")),
    ),
    family=F_MV,
    note="Two opposed zigzag windings with a lead at the middle of each.",
)

# --- Conductors, boundaries and annotation --------------------------------------------------------

BUSBAR = _spec(
    "PVSLD_BUSBAR",
    "Barra colectora con puntos de unión",
    "CFE G0100-04 Apéndice D, figs. D1 y D2 (barra de ramas)",
    _DC_COND,
    (
        Line(0, 0, 30, 0, _DC_COND, lineweight=70),
        Dot(7.5, 0, 0.8, _DC_COND),
        Dot(15, 0, 0.8, _DC_COND),
        Dot(22.5, 0, 0.8, _DC_COND),
    ),
    (Port("L", 0, 0, "left", "DC"), Port("R", 30, 0, "right", "DC", required=False)),
    extra=_hidden(("RATING_A", "Corriente nominal (A)"), ("VOLT_V", "Tensión (V)")),
    family=F_ANNOT,
    note=(
        "The string bus of the combiner: a heavy line with a filled "
        "dot at each tee. DC layer and ports."
    ),
)

TERMINAL = _spec(
    "PVSLD_TERMINAL",
    "Terminal o borne",
    _nmx("4.2.140 (IEC S00017)"),
    _AC_COND,
    (Circle(1.25, 0, 1.25, _AC_COND, color_by_block=True),),
    (Port("L", 0, 0, "left", "ANY"), Port("R", 2.5, 0, "right", "ANY", required=False)),
    iec_ref="IEC 60617 S00017",
    extra=_hidden(("TERMINAL_NO", "Número de borne")),
    family=F_ANNOT,
    note=(
        "Small open circle on the conductor, coloured BYBLOCK: it takes the colour of the layer "
        "it is inserted on (owner decision 2026-10-08), so it serves AC and DC circuits."
    ),
)

CROSSING = _spec(
    "PVSLD_CROSSING",
    "Cruce de conductores sin conexión",
    _nmx("4.2.12"),
    _AC_COND,
    (Line(0, 0, 10, 0, _AC_COND), Line(5, 5, 5, -5, _AC_COND)),
    (
        Port("W", 0, 0, "left", "ANY"),
        Port("E", 10, 0, "right", "ANY"),
        Port("N", 5, 5, "up", "ANY"),
        Port("S", 5, -5, "down", "ANY"),
    ),
    visible=False,
    family=F_ANNOT,
    note=(
        "Two lines crossing at 90 degrees and no dot; the connection "
        "is the junction dot PVSLD_JUNCTION."
    ),
)

BOUNDARY = _spec(
    "PVSLD_BOUNDARY",
    "Límite de responsabilidad CFE / usuario",
    "pvsld (composición); línea punto-punto-raya con los rótulos CFE y USUARIO",
    _ENCL,
    (
        Line(0, 15, 0, -15, _ENCL, linetype=_DASHDOT2),
        Label(-9.5, 11, 3, "CFE"),
        Label(2, 11, 3, "USUARIO"),
    ),
    (),
    visible=False,
    family=F_ANNOT,
    note=(
        "No boundary symbol exists in CFE, NMX or DGE. A vertical dash-dot-dot line between the "
        "utility side and the user side, as the captions of D1/D2 describe."
    ),
)

# --- DC protection box after the owner's reference box (owner decision 2026-10-08) -----------

MAX_DC_DISCONNECT_POLES = 12
DC_DISCONNECT_PITCH = 20.0
"""Distance between the pole pairs of a multi-string DC disconnect: the MPPT input spacing."""
_DCD_K = 0.6
"""The poles are the library's DC disconnect at 60 %, the size it has in the protection box."""


def dc_disconnect_name(n_strings: int) -> str:
    """Block name of the DC disconnect that opens ``n_strings`` strings."""
    return f"PVSLD_DC_DISCONNECT_{n_strings}S"


def dc_disconnect_multi(n_strings: int) -> SymbolSpec:
    """One DC switch-disconnector for ``n_strings`` strings: a pole pair per string, ganged.

    Pole ``i`` runs from ``IN<i>`` to ``OUT<i>``, ``DC_DISCONNECT_PITCH`` below the previous one;
    the operator handle sits on the top pole and the dotted mechanical link runs through every
    blade, as in the reference box.

    Raises:
        ValueError: ``n_strings`` is outside ``1..MAX_DC_DISCONNECT_POLES``.
    """
    if not 1 <= n_strings <= MAX_DC_DISCONNECT_POLES:
        raise ValueError(
            f"a DC disconnect opens 1 to {MAX_DC_DISCONNECT_POLES} strings, not {n_strings}"
        )
    k = _DCD_K
    rows = [_r(-DC_DISCONNECT_PITCH * i) for i in range(n_strings)]
    geometry: list[Primitive] = []
    for y in rows:
        geometry += [
            Line(0, y, 5 * k, y, _EQ_DC),
            Line(5 * k, y, 12.5 * k, _r(y + 7.5 * k), _EQ_DC),
            Line(12.5 * k, y, 25 * k, y, _EQ_DC),
        ]
    top, mid = rows[0], 8.75 * k
    geometry += [
        # Handle on the top pole, then the link through every blade (a single dotted line).
        Line(_r(mid), _r(top + 3.75 * k), 5 * k, _r(top + 8.75 * k), _EQ_DC, linetype=_DOTTED),
        Line(5 * k, _r(top + 8.75 * k), _r(mid), _r(top + 8 * k), _EQ_DC),
        Line(5 * k, _r(top + 8.75 * k), _r(5.25 * k), _r(top + 4.75 * k), _EQ_DC),
    ]
    if n_strings > 1:
        geometry.append(
            Line(
                _r(mid),
                _r(top + 3.75 * k),
                _r(mid),
                _r(rows[-1] + 3.75 * k),
                _EQ_DC,
                linetype=_DOTTED,
            )
        )
    ports = tuple(
        port
        for i, y in enumerate(rows, start=1)
        for port in (
            Port(f"IN{i}", 0, y, "left", "DC"),
            Port(f"OUT{i}", _r(25 * k), y, "right", "DC"),
        )
    )
    spec = _spec(
        dc_disconnect_name(n_strings),
        "Desconectador de CD multipolar",
        f"{_COMPOSITION}: interruptor manual por polo y unión mecánica (IEC 60617)",
        _EQ_DC,
        tuple(geometry),
        ports,
        iec_ref="IEC 60617 (interruptor-seccionador; unión mecánica)",
        nmx_ref=_nmx("4.2.94 y 4.2.119 (con carga)"),
        tag_xy=(0, _r(top + 10 * k)),
        desc_xy=(0, _r(rows[-1] - 6 * k)),
        extra=(
            AttDef("N_STRINGS", "Cadenas", 0, 0, visible=False, default=str(n_strings)),
            *_hidden(
                ("ROLE", "Función"),
                ("POLES", "Polos"),
                ("RATING_A", "Corriente nominal (A)"),
                ("VOLT_V", "Tensión (V)"),
                ("LOAD_BREAK", "Con capacidad de corte con carga"),
            ),
        ),
        family=F_SWITCH,
        note=(
            "One switch for every string of the protection box (owner's reference, 2026-10-08): "
            "the DC disconnect contact at 60 % per string, 20 mm apart, the handle on the top "
            "pole and one dotted mechanical link through all blades. Parametric "
            "(dc_disconnect_multi(n), 1 to 12 strings); the legend shows two."
        ),
    )
    # Drawn at the box size already: its texts match the 60 % breakers next to it.
    return replace(
        spec,
        attdefs=tuple(
            replace(a, height=_r(a.height * k)) if a.visible else a for a in spec.attdefs
        ),
    )


DC_DISCONNECT_2S = dc_disconnect_multi(2)

FUSE_SWITCH_DC = _spec(
    "PVSLD_FUSE_DISC_DC",
    "Fusible-seccionador de CD (portafusible gPV)",
    _nmx("4.2.176 (FUS-SECC, IEC S00369)"),
    _EQ_DC,
    _blade_with_fuse(_EQ_DC, bar=True),
    (Port("IN", 0, 0, "left", "DC"), Port("OUT", 25, 0, "right", "DC")),
    iec_ref="IEC 60617 S00369",
    nmx_ref=_nmx("4.2.176 (alt. 4.2.177, S00370)"),
    extra=_hidden(
        ("POLES", "Polos"),
        ("RATING_A", "Corriente nominal del portafusible (A)"),
        ("VOLT_V", "Tensión (V)"),
        ("FUSE_A", "Fusible (A)"),
        ("FUSE_CLASS", "Clase (gPV)"),
    ),
    family=F_SWITCH,
    note=(
        "PVSLD_FUSE_DISC on the DC equipment layer with DC ports: the string fuse holder of a "
        "protection box, which isolates the string when opened."
    ),
)

SPD_DC_BOX = _spec(
    "PVSLD_SPD_DC_BOX",
    "DPS de CD en módulo (varias entradas)",
    f"{_COMPOSITION}: varistor en envolvente de equipo (IEC 60617)",
    _EQ_DC,
    (
        Line(0, 0, 0, -2.5, _EQ_DC),
        Line(5, 0, 5, -2.5, _EQ_DC),
        Polyline(_rect(-2.5, -12.5, 7.5, -2.5), _EQ_DC, closed=True),
        Polyline(_rect(1.5, -10.5, 3.5, -4.5), _EQ_DC, closed=True),
        Polyline(((0.5, -10.5), (0.5, -8.5), (4.5, -6.5), (4.5, -4.5)), _EQ_DC),
        Line(2.5, -12.5, 2.5, -17.5, _EQ_DC),
    ),
    (
        Port("L1", 0, 0, "up", "DC"),
        Port("L2", 5, 0, "up", "DC", required=False),
        Port("PE", 2.5, -17.5, "down", "PE"),
    ),
    iec_ref="IEC 60617 (objeto; varistor)",
    nmx_ref=_nmx("4.2.308 (SPD, forma distinta)"),
    tag_xy=(9, -6),
    desc_xy=(9, -10),
    extra=(
        AttDef("SPEC", "Datos del DPS", 9, -14),
        *_hidden(
            ("SPD_TYPE", "Tipo (1, 2, 3)"),
            ("UC_V", "Tensión máxima de operación continua (V)"),
            ("UP_KV", "Nivel de protección (kV)"),
            ("IN_KA", "Corriente nominal de descarga (kA)"),
        ),
    ),
    family=F_PROT,
    note=(
        "The SPD module of the owner's reference box: a 10 mm equipment square holding the "
        "varistor, two line inputs on top (one per string in single-line form, L2 optional) "
        "and the earth lead below."
    ),
)

PV_CONNECTOR = _spec(
    "PVSLD_PV_CONNECTOR",
    "Conector de CD enchufable (tipo MC4)",
    "IEC 60617 (clavija y base, contacto macho y hembra)",
    _EQ_DC,
    (
        Line(0, 0, 2.5, 0, _EQ_DC),
        Arc(5, 0, 2.5, 90, 270, _EQ_DC),
        Fill(((4, -0.9), (7.5, -0.9), (7.5, 0.9), (4, 0.9)), _EQ_DC),
        Line(7.5, 0, 10, 0, _EQ_DC),
    ),
    (Port("IN", 0, 0, "left", "DC"), Port("OUT", 10, 0, "right", "DC")),
    iec_ref="IEC 60617 (clavija y base)",
    tag_xy=(0, 3.5),
    desc_xy=(0, -5),
    extra=_hidden(("RATING_A", "Corriente nominal (A)"), ("VOLT_V", "Tensión (V)")),
    family=F_ANNOT,
    note=(
        "Plug and socket: the socket arc on the string side, the filled plug towards the "
        "equipment. Marks the field connectors (MC4 type) where a string enters the box."
    ),
)


# Order of the legend: families as the owner asked (generation, conversion, protection, switching,
# measurement, grid and loads, earthing, medium voltage, annotation); inside each, the CFE symbols
# first (Appendix C in the order of the specification), then NMX, then the compositions.
LIBRARY: tuple[SymbolSpec, ...] = (
    # generation and storage
    PV_MODULE,
    PV_STRING,
    DIODE,
    COMBINER_BOX,
    BATTERY,
    # conversion and transformation
    INVERTER,
    HYBRID_INVERTER,
    MICROINVERTER,
    OPTIMIZER,
    CHARGE_CONTROLLER,
    ISOLATION_TRANSFORMER,
    # protection
    BREAKER,
    BREAKER_DC,
    BREAKER_IEC,
    RESIDUAL_CURRENT_DEVICE,
    FUSE,
    FUSE_AC,
    SPD,
    SPD_DC_BOX,
    SPD_AC,
    GROUND_FAULT_DETECTOR,
    INSULATION_MONITOR,
    AFCI,
    PROTECTIVE_RELAY,
    # switching and control
    SWITCH,
    DC_DISCONNECT,
    DC_DISCONNECT_2S,
    FUSE_SWITCH,
    FUSE_SWITCH_DC,
    SAFETY_SWITCH,
    TRANSFER_SWITCH,
    CONTACTOR,
    # measurement and monitoring
    METER,
    METER_M,
    CURRENT_SENSOR,
    MONITORING,
    # grid, loads and boards
    GRID,
    POINT_OF_INTERCONNECTION,
    LOAD_CENTER,
    LOAD_LIGHT,
    LOAD_RECEPT,
    # earthing
    GROUND,
    PROTECTIVE_EARTH,
    GROUND_BUS,
    NEUTRAL_BUS,
    # medium voltage
    TRANSFORMER_MV,
    FUSE_CUTOUT,
    MV_ARRESTER,
    MV_DISCONNECT,
    CT_MV,
    VT_MV,
    # conductors, boundaries and annotation
    BUSBAR,
    TERMINAL,
    PV_CONNECTOR,
    CROSSING,
    JUNCTION,
    CONDUCTOR_MARK,
    POLARITY_POSITIVE,
    POLARITY_NEGATIVE,
    ENCLOSURE,
    BOUNDARY,
    TITLE_BLOCK,
)

SYMBOLS: Mapping[str, SymbolSpec] = {spec.name: spec for spec in LIBRARY}


_COMBINER_NAME = re.compile(r"PVSLD_COMBINER_([1-9][0-9]?)S")
_PV_STRING_NAME = re.compile(r"PVSLD_PV_STRING_([1-9][0-9]?)M_(UP|DN)")
_DC_DISCONNECT_NAME = re.compile(r"PVSLD_DC_DISCONNECT_([1-9][0-9]?)S")
_MICROINVERTER_NAME = re.compile(r"PVSLD_MICROINV_([1-9])IN")
_INVERTER_MPPT_NAME = re.compile(r"PVSLD_INV_([34])MPPT")
GENERATED: Mapping[str, SymbolSpec] = {AC_JUNCTION_BOX.name: AC_JUNCTION_BOX}
"""Fixed blocks the generator defines on demand that the legend does not list."""


def get_symbol(name: str) -> SymbolSpec:
    """Return the symbol called ``name``.

    Besides the blocks of :data:`LIBRARY`, ``PVSLD_COMBINER_<n>S`` names the combiner box for
    ``n`` strings (:func:`combiner_box`) and ``PVSLD_PV_STRING_<n>M_UP|DN`` a string with its
    ``n`` modules drawn (:func:`pv_string`) and ``PVSLD_DC_DISCONNECT_<n>S`` the DC disconnect
    of ``n`` strings (:func:`dc_disconnect_multi`), ``PVSLD_MICROINV_<n>IN`` a microinverter
    with ``n`` inputs (:func:`microinverter`), ``PVSLD_INV_<n>MPPT`` a string inverter with 3
    or 4 MPPT inputs (:func:`inverter_mppt`) and :data:`GENERATED` the fixed blocks outside the
    legend (``PVSLD_AC_JBOX``); the generator defines them on demand.

    Raises:
        KeyError: the library has no such block.
    """
    try:
        return SYMBOLS[name]
    except KeyError:
        pass
    match = _COMBINER_NAME.fullmatch(name)
    if match and 1 <= int(match.group(1)) <= MAX_COMBINER_STRINGS:
        return combiner_box(int(match.group(1)))
    match = _PV_STRING_NAME.fullmatch(name)
    if match and 1 <= int(match.group(1)) <= MAX_STRING_MODULES:
        return pv_string(int(match.group(1)), "up" if match.group(2) == "UP" else "down")
    match = _DC_DISCONNECT_NAME.fullmatch(name)
    if match and 1 <= int(match.group(1)) <= MAX_DC_DISCONNECT_POLES:
        return dc_disconnect_multi(int(match.group(1)))
    match = _MICROINVERTER_NAME.fullmatch(name)
    if match and 1 <= int(match.group(1)) <= MAX_MICRO_INPUTS:
        return microinverter(int(match.group(1)))
    match = _INVERTER_MPPT_NAME.fullmatch(name)
    if match:
        return inverter_mppt(int(match.group(1)))
    if name in GENERATED:
        return GENERATED[name]
    raise KeyError(f"unknown symbol {name!r}; library has {', '.join(SYMBOLS)}")
