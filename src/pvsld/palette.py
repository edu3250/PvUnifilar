"""Palette descriptors for an interactive editor (ENG-001, Stage 3.8.1).

A palette lists what a user may place on a sheet: the **types** the engine can calculate today,
each with its symbol (full geometry, bounds, ports, attributes), the JSON Schema of the spec
section that holds its editable values, and the catalogue **equipment** that can fill it. A browser
and the DXF backend draw from the same source: the symbol part of a descriptor is a lossless copy
of the :class:`~pvsld.symbols.cfe.model.SymbolSpec` the DXF block is built from
(:func:`symbol_from_descriptor` rebuilds it, and the tests check the round trip).

Rules (owner decisions of 2026-10-30):

* Only types the engine calculates today; the other library symbols join when the engine does.
* Grid, grounding electrode, junction, terminal and PV connector are engine-defined types: their
  provenance is ``pvsld`` and they never carry a manufacturer record.
* The junction dot has no ports in the library; its descriptor adds four optional anchors at the
  centre of each edge of the dot (the DXF block is unchanged).
* Equipment comes from the bundled, reviewed catalogue only; the user's local catalogue is a
  plugin feature, not part of the palette.

The module imports no render, report or server dependency (ENG-014).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pvsld.catalogue.registry import Component, ComponentRegistry
from pvsld.core import model as spec_model
from pvsld.resources import data_path
from pvsld.symbols.cfe import get_symbol
from pvsld.symbols.cfe.model import (
    LIBRARY_VERSION,
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
)

__all__ = [
    "FORMAT",
    "TYPES",
    "EquipmentEntry",
    "Palette",
    "TypeDescriptor",
    "build_palette",
    "palette_json_schema",
    "symbol_from_descriptor",
]

FORMAT = "pvsld.palette/1"
"""Format of the palette document; a breaking change of its shape bumps the number."""

ENGINE_SOURCE = "pvsld"
"""Provenance of the engine-defined types (no manufacturer record)."""

_STRICT = ConfigDict(extra="forbid", frozen=True)


# --- Symbol geometry -----------------------------------------------------------------------------


class LineModel(BaseModel):
    model_config = _STRICT
    kind: Literal["line"] = "line"
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str
    lineweight: int | None = None
    linetype: str | None = None


class PolylineModel(BaseModel):
    model_config = _STRICT
    kind: Literal["polyline"] = "polyline"
    points: tuple[tuple[float, float], ...]
    layer: str
    closed: bool = False
    linetype: str | None = None


class CircleModel(BaseModel):
    model_config = _STRICT
    kind: Literal["circle"] = "circle"
    cx: float
    cy: float
    radius: float
    layer: str
    color_by_block: bool = False


class ArcModel(BaseModel):
    """Counter-clockwise arc from ``start`` to ``end`` degrees."""

    model_config = _STRICT
    kind: Literal["arc"] = "arc"
    cx: float
    cy: float
    radius: float
    start: float
    end: float
    layer: str


class DotModel(BaseModel):
    """A filled disc."""

    model_config = _STRICT
    kind: Literal["dot"] = "dot"
    cx: float
    cy: float
    radius: float
    layer: str


class FillModel(BaseModel):
    """A filled polygon."""

    model_config = _STRICT
    kind: Literal["fill"] = "fill"
    points: tuple[tuple[float, float], ...]
    layer: str


class LabelModel(BaseModel):
    """Fixed text of the symbol; ``(x, y)`` is the left baseline."""

    model_config = _STRICT
    kind: Literal["label"] = "label"
    x: float
    y: float
    height: float
    text: str
    layer: str


PrimitiveModel = Annotated[
    LineModel | PolylineModel | CircleModel | ArcModel | DotModel | FillModel | LabelModel,
    Field(discriminator="kind"),
]

_TO_MODEL: dict[type, type[BaseModel]] = {
    Line: LineModel,
    Polyline: PolylineModel,
    Circle: CircleModel,
    Arc: ArcModel,
    Dot: DotModel,
    Fill: FillModel,
    Label: LabelModel,
}
_FROM_MODEL: dict[type[BaseModel], type] = {value: key for key, value in _TO_MODEL.items()}


class PortModel(BaseModel):
    """A connection point in symbol-local millimetres (Y up)."""

    model_config = _STRICT
    id: str
    x: float
    y: float
    direction: Literal["left", "right", "up", "down"]
    kind: Literal["DC", "AC", "PE", "SIG", "ANY"]
    required: bool
    anchor_only: bool = Field(
        default=False,
        description="Added by the palette for a symbol without library ports; not in the DXF block",
    )


class AttributeModel(BaseModel):
    """An attribute of the block: its tag, prompt and text placement."""

    model_config = _STRICT
    tag: str
    prompt_es: str
    x: float
    y: float
    height: float
    visible: bool
    default: str
    layer: str
    legend_sample: str


class SymbolModel(BaseModel):
    """Everything needed to draw the block, as built into the DXF library."""

    model_config = _STRICT
    block: str
    library_version: str
    description_es: str
    source: str
    iec_ref: str
    nmx_ref: str
    family: str
    layer: str
    note: str
    bounds: tuple[float, float, float, float] = Field(
        description="(xmin, ymin, xmax, ymax) of the geometry, attributes not included"
    )
    geometry: tuple[PrimitiveModel, ...]
    ports: tuple[PortModel, ...]
    attributes: tuple[AttributeModel, ...]


# --- Types and equipment -------------------------------------------------------------------------


class TypeDescriptor(BaseModel):
    """One placeable type: its symbol, the spec section it edits and where its data comes from."""

    model_config = _STRICT
    type_id: str
    label_es: str
    provenance: Literal["catalogue", "pvsld"] = Field(
        description="'catalogue': filled with equipment records; 'pvsld': defined by the engine"
    )
    component_types: tuple[str, ...] = Field(
        description="Catalogue component types that fill this type (empty for engine types)"
    )
    spec_section: str | None = Field(
        description="Where the values live in the parameter specification, e.g. 'strings[]'"
    )
    id_prefix: str | None = Field(
        description="Id prefix the engine uses to tell this type apart inside its spec section "
        "(string protections share dc_bos.disconnects[]: FUS- fuse holder, DCB- DC breaker)"
    )
    parameters_schema: dict[str, Any] = Field(
        description="JSON Schema of the spec section (empty when the type has no editable values)"
    )
    symbol: SymbolModel
    fingerprint: str = Field(description="SHA-256 of this descriptor without the fingerprint")


class EquipmentProvenance(BaseModel):
    """Where a catalogue record comes from and who reviewed it."""

    model_config = _STRICT
    filename: str
    sha256: str
    title: str
    reviewed_by: str | None
    review_date: date | None


class EquipmentEntry(BaseModel):
    """One catalogue reference that can fill a type."""

    model_config = _STRICT
    component_id: str
    type_id: str
    component_type: str
    manufacturer: str
    family_id: str
    provenance: EquipmentProvenance


class Palette(BaseModel):
    """The palette document: types, equipment and the versions they were built from."""

    model_config = _STRICT
    format: Literal["pvsld.palette/1"] = FORMAT
    library_version: str
    schema_version: str = Field(description="Version of the parameter specification")
    types: tuple[TypeDescriptor, ...]
    equipment: tuple[EquipmentEntry, ...]
    fingerprint: str = Field(description="SHA-256 of the document without the fingerprint")

    def type(self, type_id: str) -> TypeDescriptor:
        for item in self.types:
            if item.type_id == type_id:
                return item
        raise KeyError(f"no palette type {type_id!r}")

    def equipment_for(self, type_id: str) -> list[EquipmentEntry]:
        return [item for item in self.equipment if item.type_id == type_id]


# --- Definitions ---------------------------------------------------------------------------------


class _TypeDef:
    def __init__(
        self,
        type_id: str,
        label_es: str,
        block: str,
        *,
        component_types: tuple[str, ...] = (),
        spec_section: str | None = None,
        spec_model_name: str | None = None,
        id_prefix: str | None = None,
    ) -> None:
        self.type_id = type_id
        self.label_es = label_es
        self.block = block
        self.component_types = component_types
        self.spec_section = spec_section
        self.spec_model_name = spec_model_name
        self.id_prefix = id_prefix


TYPES: tuple[_TypeDef, ...] = (
    _TypeDef(
        "pv_string",
        "Rama de módulos fotovoltaicos",
        "PVSLD_PV_STRING",
        component_types=("pv_module",),
        spec_section="strings[]",
        spec_model_name="StringSpec",
    ),
    _TypeDef(
        "inverter",
        "Inversor",
        "PVSLD_INV",
        component_types=("string_inverter", "hybrid_inverter"),
        spec_section="inverters[]",
        spec_model_name="Inverter",
    ),
    _TypeDef(
        "dc_fuse_holder",
        "Fusible-seccionador de CD (gPV)",
        "PVSLD_FUSE_DISC_DC",
        component_types=("dc_fuse",),
        spec_section="dc_bos.disconnects[]",
        spec_model_name="DcDisconnect",
        id_prefix="FUS-",
    ),
    _TypeDef(
        "dc_breaker",
        "Interruptor termomagnético de CD",
        "PVSLD_CB_DC",
        component_types=("dc_breaker",),
        spec_section="dc_bos.disconnects[]",
        spec_model_name="DcDisconnect",
        id_prefix="DCB-",
    ),
    _TypeDef(
        "dc_switch",
        "Desconectador de CD",
        "PVSLD_DC_DISCONNECT",
        component_types=("dc_switch",),
        spec_section="dc_bos.disconnects[]",
        spec_model_name="DcDisconnect",
    ),
    _TypeDef(
        "dc_spd",
        "Protección contra sobretensiones de CD",
        "PVSLD_SPD",
        spec_section="dc_bos.spds[]",
        spec_model_name="Spd",
    ),
    _TypeDef(
        "ac_breaker",
        "Interruptor termomagnético",
        "PVSLD_CB",
        component_types=("ac_breaker",),
        spec_section="ac_bos.ocpds[]",
        spec_model_name="Ocpd",
    ),
    _TypeDef(
        "interconnection_point",
        "Punto de interconexión",
        "PVSLD_PI",
        spec_section="ac_bos.point_of_connection",
        spec_model_name="PointOfConnection",
    ),
    _TypeDef(
        "load_centre",
        "Centro de carga",
        "PVSLD_PANEL",
        spec_section="ac_bos.panels[]",
        spec_model_name="Panel",
    ),
    _TypeDef(
        "meter",
        "Medidor",
        "PVSLD_METER",
        spec_section="ac_bos.meters[]",
        spec_model_name="Meter",
    ),
    _TypeDef(
        "grid",
        "Red de distribución",
        "PVSLD_GRID",
        spec_section="utility",
        spec_model_name="Utility",
    ),
    _TypeDef(
        "ground_electrode",
        "Electrodo de puesta a tierra",
        "PVSLD_GND",
        spec_section="grounding.electrodes[]",
        spec_model_name="Electrode",
    ),
    _TypeDef("junction", "Unión de conductores", "PVSLD_JUNCTION"),
    _TypeDef("terminal", "Terminal o borne", "PVSLD_TERMINAL"),
    _TypeDef("pv_connector", "Conector de CD (tipo MC4)", "PVSLD_PV_CONNECTOR"),
)
"""The types the engine calculates today, in palette order."""

_BY_COMPONENT_TYPE = {ct: t.type_id for t in TYPES for ct in t.component_types}


# --- Conversion ----------------------------------------------------------------------------------


def _primitive_model(item: Primitive) -> BaseModel:
    return _TO_MODEL[type(item)](**asdict(item))


def _primitive(item: BaseModel) -> Primitive:
    values = item.model_dump(exclude={"kind"})
    if "points" in values:
        values["points"] = tuple(tuple(point) for point in values["points"])
    return _FROM_MODEL[type(item)](**values)


def _anchors(spec: SymbolSpec) -> tuple[PortModel, ...]:
    """Anchors at the centre of each edge of a port-less symbol (the junction dot)."""
    xmin, ymin, xmax, ymax = spec.bounds()
    xc, yc = (xmin + xmax) / 2, (ymin + ymax) / 2
    edges = (
        ("W", xmin, yc, "left"),
        ("E", xmax, yc, "right"),
        ("N", xc, ymax, "up"),
        ("S", xc, ymin, "down"),
    )
    return tuple(
        PortModel(
            id=name, x=x, y=y, direction=direction, kind="ANY", required=False, anchor_only=True
        )
        for name, x, y, direction in edges
    )


def _symbol_model(spec: SymbolSpec, *, add_anchors: bool) -> SymbolModel:
    ports = tuple(PortModel(**asdict(port)) for port in spec.ports)
    if add_anchors and not ports:
        ports = _anchors(spec)
    return SymbolModel(
        block=spec.name,
        library_version=LIBRARY_VERSION,
        description_es=spec.description_es,
        source=spec.source,
        iec_ref=spec.iec_ref,
        nmx_ref=spec.nmx_ref,
        family=spec.family,
        layer=spec.layer,
        note=spec.note,
        bounds=spec.bounds(),
        geometry=tuple(_primitive_model(item) for item in spec.geometry),
        ports=ports,
        attributes=tuple(AttributeModel(**asdict(attdef)) for attdef in spec.attdefs),
    )


def symbol_from_descriptor(symbol: SymbolModel) -> SymbolSpec:
    """Rebuild the library symbol from its descriptor (the palette's anchors are dropped)."""
    return SymbolSpec(
        name=symbol.block,
        description_es=symbol.description_es,
        source=symbol.source,
        iec_ref=symbol.iec_ref,
        nmx_ref=symbol.nmx_ref,
        layer=symbol.layer,
        geometry=tuple(_primitive(item) for item in symbol.geometry),
        attdefs=tuple(AttDef(**attribute.model_dump()) for attribute in symbol.attributes),
        ports=tuple(
            Port(**port.model_dump(exclude={"anchor_only"}))
            for port in symbol.ports
            if not port.anchor_only
        ),
        note=symbol.note,
        family=symbol.family,
    )


def _fingerprint(data: dict[str, Any]) -> str:
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _type_descriptor(definition: _TypeDef) -> TypeDescriptor:
    schema: dict[str, Any] = {}
    if definition.spec_model_name is not None:
        schema = getattr(spec_model, definition.spec_model_name).model_json_schema()
    fields: dict[str, Any] = {
        "type_id": definition.type_id,
        "label_es": definition.label_es,
        "provenance": "catalogue" if definition.component_types else ENGINE_SOURCE,
        "component_types": definition.component_types,
        "spec_section": definition.spec_section,
        "id_prefix": definition.id_prefix,
        "parameters_schema": schema,
        "symbol": _symbol_model(get_symbol(definition.block), add_anchors=True),
    }
    draft = TypeDescriptor.model_construct(**fields, fingerprint="")
    return TypeDescriptor(
        **fields, fingerprint=_fingerprint(draft.model_dump(mode="json", exclude={"fingerprint"}))
    )


def _equipment(components: Iterable[Component]) -> tuple[EquipmentEntry, ...]:
    entries = []
    for component in components:
        type_id = _BY_COMPONENT_TYPE.get(component.component_type)
        if type_id is None:  # conductors are wires, not palette items
            continue
        source = component.source
        entries.append(
            EquipmentEntry(
                component_id=component.component_id,
                type_id=type_id,
                component_type=component.component_type,
                manufacturer=component.manufacturer,
                family_id=component.family_id,
                provenance=EquipmentProvenance(
                    filename=source.filename,
                    sha256=source.sha256,
                    title=source.title,
                    reviewed_by=source.reviewed_by,
                    review_date=source.review_date,
                ),
            )
        )
    return tuple(sorted(entries, key=lambda e: (e.type_id, e.component_id)))


def build_palette(registry: ComponentRegistry | None = None) -> Palette:
    """Build the palette from ``registry`` (default: the bundled catalogue, reviewed records)."""
    if registry is None:
        registry = ComponentRegistry.load(data_path("datasheets/records"))
    fields: dict[str, Any] = {
        "library_version": LIBRARY_VERSION,
        "schema_version": spec_model.SCHEMA_VERSION,
        "types": tuple(_type_descriptor(definition) for definition in TYPES),
        "equipment": _equipment(registry),
    }
    draft = Palette.model_construct(format=FORMAT, **fields, fingerprint="")
    return Palette(
        **fields, fingerprint=_fingerprint(draft.model_dump(mode="json", exclude={"fingerprint"}))
    )


def palette_json_schema() -> dict[str, Any]:
    """JSON Schema (2020-12) of the palette document."""
    return Palette.model_json_schema()
