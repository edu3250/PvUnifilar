"""The schematic of several string inverters on the A3 sheet (Stage 3.12.3, owner 2026-11-13).

Each inverter is a row: its strings, its DC protection box and the inverter, drawn exactly as the
one-inverter sheet draws them (:func:`pvsld.core.layout.build_schematic` on the inverter's part of
the specification) and then scaled into the row. The inverter outputs ``C-INV<n>`` run to a
vertical bus and the AC junction box (``CJ-CA1``), and one run ``C-INVT`` reaches the shared
breaker ``ITM-1``; the service side (point of connection, load centre, service main, meter, grid)
is the one-inverter sheet's. Every earth (inverter, DC SPD) drops to its row's earth rail and to
one earth bus down to the electrode.

The sheet draws at most :data:`MAX_INVERTERS` inverters with up to two strings each (owner
approval 2026-11-13); a larger design is calculated but its drawing is refused with a clear
message.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pvsld.core import layers
from pvsld.core.calc import Derived, derive
from pvsld.core.diagram import (
    Connection,
    Point,
    PolylineItem,
    PortRef,
    SymbolInstance,
    SymbolSample,
    TextItem,
    rnd,
)
from pvsld.core.layout import (
    GRID_COMP_ID,
    GROUND_COMP_ID,
    JUNCTION_SCALE,
    LayoutError,
    Placement,
    Schematic,
    _instance,
    build_schematic,
    ground_instance,
)
from pvsld.core.model import PvSystemSpec, parse_spec
from pvsld.symbols import get_symbol

MAX_INVERTERS = 3
MAX_STRINGS_PER_INVERTER = 2
ROWS: dict[int, tuple[float, ...]] = {2: (222.0, 168.0), 3: (240.0, 195.0, 150.0)}
"""Height of each inverter row (the AC row of the service is at 195)."""
SCALE: dict[int, float] = {2: 0.72, 3: 0.6}
"""How much smaller a row is than the one-inverter drawing."""
LEFT_X = 16.0
"""The rows keep the strings' left end of the one-inverter sheet."""
BUS_X = 140.0
"""The vertical AC bus where the inverter outputs meet, left of the junction box."""
JBOX_X = 143.0
EARTH_X = 119.0
"""The vertical earth bus, between the inverters and their circuit markers."""
GROUND_XY = (70.0, 120.0)
"""The electrode, under the rows and left of the protection schedule."""
PANEL_EARTH_Y = 158.0
"""Where the load centre's earth turns to the earth bus; lower than the top of the protection
schedule (the sheet passes it)."""
JUNCTION_ID = "CJ-CA1"
"""The junction box the sizing engine names (a drawing may name its own)."""
SERVICE_IDS = frozenset({GRID_COMP_ID, GROUND_COMP_ID})


def check_drawable(spec: PvSystemSpec) -> None:
    """The limits of the sheet (owner approval 2026-11-13)."""
    inverters = spec.string_inverters()
    too_many = [
        i.id
        for i in inverters
        if sum(1 for s in spec.strings if s.inverter == i.id) > MAX_STRINGS_PER_INVERTER
    ]
    if len(inverters) > MAX_INVERTERS or too_many:
        raise LayoutError(
            f"El plano dibuja hasta {MAX_INVERTERS} inversores con {MAX_STRINGS_PER_INVERTER} "
            f"cadenas cada uno; el diseño tiene {len(inverters)} inversores"
            + (f" ({', '.join(too_many)} con más cadenas)" if too_many else "")
            + ". El diseño sí se valida y se calcula."
        )
    if spec.ac_bos.point_of_connection.panel is None:
        raise LayoutError(
            "El plano A3 todavía no dibuja un diseño sin centro de carga (llega en la siguiente "
            "versión). El diseño sí se valida y se calcula."
        )


def _part(spec: PvSystemSpec, number: int, inverter_id: str, itm_id: str) -> PvSystemSpec:
    """The specification of one inverter: its strings, DC box and output run to the breaker."""
    data: dict[str, Any] = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    own = [s for s in data["strings"] if s["inverter"] == inverter_id]
    ids = {s["id"] for s in own}
    data["inverters"] = [i for i in data["inverters"] if i["id"] == inverter_id]
    data["strings"] = own
    data["modules"] = [m for m in data["modules"] if m["id"] in {s["module"] for s in own}]
    mine = {f"DCD-CD{number}", f"DPS-CD{number}"} | {
        f"{prefix}{s}" for s in ids for prefix in ("FUS-", "DCB-", "DCD-", "DPS-")
    }
    dc = data["dc_bos"]
    dc["disconnects"] = [
        d for d in dc["disconnects"] if d.get("integrated_in") == inverter_id or d["id"] in mine
    ]
    dc["spds"] = [s for s in dc["spds"] if s["id"] in mine]
    circuits = []
    for circuit in data["circuits"]:
        source = circuit["from"].split(".", 1)[0]
        if circuit["kind"] == "pv_source" and source in ids:
            circuits.append(circuit)
        elif circuit["kind"] == "inverter_output" and source == inverter_id:
            circuits.append({**circuit, "to": itm_id})
    data["circuits"] = circuits
    data["ac_bos"]["junction_boxes"] = []
    return parse_spec(data)


@dataclasses.dataclass(frozen=True)
class _Move:
    """Scale about the one-inverter sheet's origin and move into a row."""

    scale: float
    dx: float
    dy: float

    def x(self, x: float) -> float:
        return rnd(LEFT_X + (x - LEFT_X) * self.scale + self.dx)

    def y(self, y: float) -> float:
        return rnd(y * self.scale + self.dy)

    def point(self, p: Point) -> Point:
        return Point(self.x(p.x), self.y(p.y))


def _row_move(placement: Placement, scale: float, row_y: float) -> _Move:
    # the inverter's AC row of the one-inverter drawing lands on row_y
    return _Move(scale, 0.0, row_y - placement.inverter[1] * scale)


def build_multi_schematic(
    spec: PvSystemSpec,
    derived: Derived,
    placement: Placement,
    *,
    panel_earth_y: float = PANEL_EARTH_Y,
) -> Schematic:
    """Components and conductors of several string inverters (see the module docstring).

    Raises:
        LayoutError: the design is larger than the sheet, or a key device is missing.
    """
    check_drawable(spec)
    inverters = spec.string_inverters()
    itm_id = spec.inverter_breaker_id()
    count = len(inverters)
    rows, scale = ROWS[count], SCALE[count]
    y_ac = placement.inverter[1]

    instances: list[SymbolInstance] = []
    connections: list[Connection] = []
    texts: list[TextItem] = []
    polylines: list[PolylineItem] = []
    junctions: list[SymbolSample] = []
    earths: list[tuple[str, Point, float]] = []  # (component, its PE, its row's rail height)
    service: Schematic | None = None

    for number, (inverter, row_y) in enumerate(zip(inverters, rows, strict=True), start=1):
        part = _part(spec, number, inverter.id, itm_id)
        drawn = build_schematic(part, derive(part), placement)
        if service is None:
            service = drawn
        move = _row_move(placement, scale, row_y)
        keep = {s.id for s in part.strings} | {inverter.id}
        keep |= {d.id for d in part.dc_bos.disconnects} | {s.id for s in part.dc_bos.spds}
        dc_ids = {i.comp_id for i in drawn.instances} - {
            drawn.i1.id,
            drawn.i2.id,
            part.ac_bos.point_of_connection.id,
            *(p.id for p in part.ac_bos.panels),
            *(m.id for m in part.ac_bos.meters),
            *SERVICE_IDS,
        }
        row_items = [i for i in drawn.instances if i.comp_id in dc_ids or i.comp_id in keep]
        moved = {
            i.comp_id: dataclasses.replace(
                i, x=move.x(i.x), y=move.y(i.y), scale=rnd(i.scale * scale)
            )
            for i in row_items
        }
        instances += moved.values()
        for conn in drawn.connections:
            if conn.kind != "pv_source":
                continue
            connections.append(
                dataclasses.replace(conn, points=tuple(move.point(p) for p in conn.points))
            )
        for sample in drawn.junctions:
            junctions.append(
                dataclasses.replace(
                    sample, x=move.x(sample.x), y=move.y(sample.y), scale=sample.scale * scale
                )
            )
        for line in drawn.polylines:
            polylines.append(
                dataclasses.replace(line, points=tuple(move.point(p) for p in line.points))
            )
        for text in drawn.texts:
            if text.text.startswith("GEC"):
                continue
            texts.append(
                dataclasses.replace(
                    text, x=move.x(text.x), y=move.y(text.y), height=rnd(text.height * scale)
                )
            )
        lowest = min(
            item.y + min(b.y0 - item.y for _n, b in item.boxes()) if item.boxes() else item.y
            for item in moved.values()
            if item.comp_id not in {s.id for s in part.strings}
        )
        rail_y = rnd(lowest - 1.5)
        for comp_id, item in moved.items():
            if "PE" in {p.id for p in get_symbol(item.symbol).ports}:
                earths.append((comp_id, item.port_xy("PE"), rail_y))

    assert service is not None
    by_id: dict[str, SymbolInstance] = {i.comp_id: i for i in instances}
    for item in service.instances:
        if item.comp_id in {
            service.i1.id,
            service.i2.id,
            spec.ac_bos.point_of_connection.id,
            *(p.id for p in spec.ac_bos.panels),
            *(m.id for m in spec.ac_bos.meters),
            GRID_COMP_ID,
        }:
            instances.append(item)
            by_id[item.comp_id] = item
    ground = ground_instance(spec, GROUND_XY)
    instances.append(ground)
    by_id[GROUND_COMP_ID] = ground

    # The AC bus, the junction box (when the design has one) and the shared breaker.
    junction_id = spec.ac_bos.junction_boxes[0].id if spec.ac_bos.junction_boxes else None
    i1_in = by_id[service.i1.id].port_xy("IN")
    target, target_port, target_xy = service.i1.id, "IN", i1_in
    if junction_id is not None:
        jbox = _instance(
            "PVSLD_AC_JBOX",
            junction_id,
            JBOX_X,
            y_ac,
            {"TAG": "", "DESC": junction_id},
            scale=0.8,
        )
        instances.append(jbox)
        by_id[junction_id] = jbox
        target, target_port, target_xy = junction_id, "IN", jbox.port_xy("IN")
    circuits = {c.from_.split(".", 1)[0]: c for c in spec.circuits}
    for inverter in inverters:
        ac = by_id[inverter.id].port_xy("AC")
        circuit = circuits.get(inverter.id)
        points = (ac, Point(BUS_X, ac.y), Point(BUS_X, target_xy.y), target_xy)
        if abs(ac.y - target_xy.y) < 1e-6:
            points = (ac, target_xy)
        connections.append(
            Connection(
                circuit.id if circuit else f"AC-{inverter.id}",
                "inverter_output",
                layers.AC_CONDUCTORS,
                PortRef(inverter.id, "AC"),
                PortRef(target, target_port),
                points,
                circuit.id if circuit else None,
            )
        )
    junctions.append(
        SymbolSample(
            "PVSLD_JUNCTION",
            rnd(BUS_X),
            rnd(target_xy.y),
            JUNCTION_SCALE,
            get_symbol("PVSLD_JUNCTION").layer,
            "model",
        )
    )
    if junction_id is not None:
        shared = circuits.get(junction_id)
        connections.append(
            Connection(
                shared.id if shared else "AC-SHARED",
                "inverter_output",
                layers.AC_CONDUCTORS,
                PortRef(junction_id, "OUT"),
                PortRef(service.i1.id, "IN"),
                (by_id[junction_id].port_xy("OUT"), i1_in),
                shared.id if shared else None,
            )
        )
    for conn in service.connections:
        if conn.kind == "ac_network":
            connections.append(conn)
    panel = spec.ac_bos.point_of_connection.panel

    # Earths: each to its row's rail, the rails to the earth bus, the bus to the electrode.
    ground_pe = ground.port_xy("PE")
    for comp_id, pe, rail_y in earths:
        connections.append(
            Connection(
                f"GND-{comp_id}",
                "grounding",
                layers.GROUNDING,
                PortRef(comp_id, "PE"),
                PortRef(GROUND_COMP_ID, "PE"),
                (
                    pe,
                    Point(pe.x, rail_y),
                    Point(EARTH_X, rail_y),
                    Point(EARTH_X, ground_pe.y),
                    ground_pe,
                ),
            )
        )
    if panel is not None:
        connections.append(
            Connection(
                f"GND-{panel}",
                "grounding",
                layers.GROUNDING,
                PortRef(panel, "PE"),
                PortRef(GROUND_COMP_ID, "PE"),
                (
                    by_id[panel].port_xy("PE"),
                    Point(by_id[panel].port_xy("PE").x, panel_earth_y),
                    Point(EARTH_X, panel_earth_y),
                    Point(EARTH_X, ground_pe.y),
                    ground_pe,
                ),
            )
        )
    texts.append(
        TextItem(
            layers.TAGS,
            rnd(EARTH_X + 2.0),
            rnd(GROUND_XY[1] + 20.0),
            2.5,
            f"GEC CD: {spec.grounding.gec_dc} Cu desnudo",
        )
    )
    texts.append(
        TextItem(
            layers.TAGS,
            rnd(BUS_X + 8.0),
            rnd(panel_earth_y + 1.5),
            2.5,
            f"GEC CA: {spec.grounding.gec_ac} Cu desnudo",
        )
    )
    return Schematic(
        tuple(instances),
        tuple(connections),
        tuple(texts),
        service.i1,
        service.i2,
        tuple(polylines),
        tuple(junctions),
    )


__all__ = ["MAX_INVERTERS", "build_multi_schematic", "check_drawable"]
