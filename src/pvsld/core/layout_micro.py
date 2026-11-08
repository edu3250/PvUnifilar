"""The schematic of a microinverter design on the A3 sheet (Stage 3.10.4, owner 2026-11-08).

Each AC branch is a row: one module above each used input of every unit (a one-module
``PVSLD_PV_STRING_1M_UP``), the units on the trunk (each unit's AC output taps it, with a
junction dot), the junction box ``PVSLD_AC_JBOX`` at the end, and the home run ``C-R<n>`` to the
branch breaker. Without an AC combiner every branch breaker feeds the load centre; with one, the
branch breakers sit in a dashed enclosure ``CCA-1`` with the AC SPD on the bus, and the main
breaker ``ITM-1`` feeds the load centre through the point of connection. The service side (load
centre, service main, meter, grid) is the string design's.

The sheet draws at most :data:`MAX_UNITS` units in :data:`MAX_BRANCHES` branches (owner decision
2026-11-07); a larger design is calculated but its drawing is refused with a clear message. A row
that would not fit before the breakers is drawn smaller.
"""

from __future__ import annotations

from pvsld.core import layers
from pvsld.core.calc import Derived
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
    _breaker_instance,
    _g,
    _hvh,
    _instance,
    _key_devices,
    _n,
    _vh,
    ground_instance,
    service_instances,
)
from pvsld.core.model import AcBranch, Inverter, PvSystemSpec
from pvsld.symbols import get_symbol
from pvsld.symbols.cfe.definitions import microinverter_name, pv_string_name

MAX_UNITS = 8
MAX_BRANCHES = 3
X0 = 16.0
"""Left end of the rows (where the strings start on the string sheet)."""
BRANCH_BREAKER_X = 150.0
COMBINER_BREAKER_X = 112.0
COMBINER_BUS_X = 140.0
COMBINER_BOX_RIGHT = 166.0
"""Right side of the combiner enclosure (it holds the SPD and its tag)."""
COMBINER_MAIN_X = 174.0
"""The main breaker at the load centre; the combiner output C-CCA runs from the bus to it."""
ROWS: dict[int, tuple[float, ...]] = {1: (195.0,), 2: (214.0, 160.0), 3: (228.0, 183.0, 138.0)}
"""Trunk height of each row, by number of branches (the AC row of the load centre is at 195)."""
GAP_MM = 15.0
"""Space between two units on a row (their tag and the uINV caption sit in it)."""
JB_GAP_MM = 3.0
GROUND_X = 185.0
"""The electrode sits left of the load centre, above the protection schedule."""
CAPTION_MM = 2.0
COMBINER_CAPTION = "CCA-1  CAJA COMBINADORA DE CA"


def check_drawable(spec: PvSystemSpec) -> None:
    """The limits of the sheet (owner decision 2026-11-07).

    Raises:
        LayoutError: more units or branches than the sheet draws.
    """
    units = sum(len(b.microinverters) for b in spec.ac_branches)
    if units > MAX_UNITS or len(spec.ac_branches) > MAX_BRANCHES:
        raise LayoutError(
            f"El plano dibuja hasta {MAX_UNITS} microinversores en {MAX_BRANCHES} ramas; el diseño "
            f"tiene {units} en {len(spec.ac_branches)}. El diseño sí se valida y se calcula."
        )


def _row_scale(branch: AcBranch, inputs: int, right_x: float) -> float:
    """1, or less when the row would reach the breakers."""
    width = max(15.0, 7.5 * inputs)
    units = len(branch.microinverters)
    # The junction box, then room for the home run and its circuit marker.
    needed = units * (width + GAP_MM) + JB_GAP_MM + 8.0 + 18.0
    return min(1.0, (right_x - X0) / needed)


def build_micro_schematic(
    spec: PvSystemSpec, derived: Derived, placement: Placement, *, ground_y: float = 150.0
) -> Schematic:
    """Components and conductors of a microinverter design (see the module docstring).

    Raises:
        LayoutError: the design is larger than the sheet, or a key device is missing.
    """
    check_drawable(spec)
    i1, i2, meter, panel = _key_devices(spec)
    inverters = {i.id: i for i in spec.inverters}
    modules = {m.id: m for m in spec.modules}
    ocpds = {o.id: o for o in spec.ac_bos.ocpds}
    circuits = {c.from_.split(".", 1)[0]: c for c in spec.circuits}
    combiner = spec.ac_combiner
    breaker_x = COMBINER_BREAKER_X if combiner else BRANCH_BREAKER_X
    rows = ROWS[len(spec.ac_branches)]
    y_ac = placement.inverter[1]

    instances: list[SymbolInstance] = []
    connections: list[Connection] = []
    texts: list[TextItem] = []
    junctions: list[SymbolSample] = []
    polylines: list[PolylineItem] = []
    by_id: dict[str, SymbolInstance] = {}

    def add(item: SymbolInstance) -> SymbolInstance:
        instances.append(item)
        by_id[item.comp_id] = item
        return item

    def wire(
        conn_id: str,
        kind: str,
        layer: str,
        start: PortRef,
        end: PortRef,
        points: tuple[Point, ...],
        circuit_id: str | None = None,
    ) -> None:
        connections.append(Connection(conn_id, kind, layer, start, end, points, circuit_id))  # type: ignore[arg-type]

    def dot(x: float, y: float) -> None:
        junctions.append(
            SymbolSample(
                "PVSLD_JUNCTION",
                rnd(x),
                rnd(y),
                JUNCTION_SCALE,
                get_symbol("PVSLD_JUNCTION").layer,
                "model",
            )
        )

    for branch, y_t in zip(spec.ac_branches, rows, strict=True):
        model: Inverter = inverters[branch.microinverters[0].inverter]
        inputs = len(model.mppt)
        k = _row_scale(branch, inputs, breaker_x)
        width = max(15.0, 7.5 * inputs)
        step = (width + GAP_MM) * k
        taps: list[Point] = []
        for index, unit in enumerate(branch.microinverters):
            x = X0 + index * step
            micro = add(
                _instance(
                    microinverter_name(inputs),
                    unit.id,
                    x,
                    y_t + 13.5 * k,
                    {
                        "TAG": unit.id,
                        "DESC": "",
                        "N_INPUTS": str(inputs),
                        "MFR": model.manufacturer,
                        "MODEL": model.model,
                        "PAC_W": _g(model.pac_w),
                        "VAC_V": _g(model.vac_v),
                        "BRANCH": branch.id,
                    },
                    scale=k,
                )
            )
            for letter, module_id in unit.inputs.items():
                port = micro.port_xy(letter)
                module = modules[module_id]
                comp_id = f"M-{unit.id}{letter}"
                symbol = pv_string_name(1, "up")
                # The module's output sits 1.5 mm left of the input, 9 mm above the unit.
                out = get_symbol(symbol).port("OUT")
                mod = add(
                    _instance(
                        symbol,
                        comp_id,
                        port.x - (out.x + 1.5) * k,
                        port.y + 9.0 * k,
                        {
                            "TAG": "",
                            "DESC": "",
                            "MODEL": "",
                            "MFR": module.manufacturer,
                            "MPPT": letter,
                            "PMAX_W": _g(module.pmax_w),
                            "N_SERIES": "1",
                            "KWP": _n(module.pmax_w / 1000, 2),
                            "VOC_MAX_V": _n(module.voc_v),
                            "VMP_V": _n(module.vmp_v),
                            "ISC_A": _n(module.isc_a, 2),
                            "IMP_A": _n(module.imp_a, 2),
                        },
                        scale=k,
                    )
                )
                a = mod.port_xy("OUT")
                wire(
                    f"DC-{comp_id}",
                    "pv_source",
                    layers.DC_CONDUCTORS,
                    PortRef(comp_id, "OUT"),
                    PortRef(unit.id, letter),
                    (a, Point(port.x, a.y), port),
                )
            taps.append(micro.port_xy("AC"))
        x_jb = taps[-1].x + width * k / 2 + JB_GAP_MM
        jb_id = f"JB-{branch.id.removeprefix('R')}"
        jb = add(_instance("PVSLD_AC_JBOX", jb_id, x_jb, y_t, {"TAG": "", "DESC": ""}, scale=1.0))
        jb_in = jb.port_xy("IN")
        for unit, tap in zip(branch.microinverters, taps, strict=True):
            wire(
                f"TR-{unit.id}",
                "inverter_output",
                layers.AC_CONDUCTORS,
                PortRef(unit.id, "AC"),
                PortRef(jb_id, "IN"),
                (tap, Point(tap.x, y_t), Point(jb_in.x, y_t)),
            )
            dot(tap.x, y_t)
        caption = (
            f"{branch.id}: {len(branch.microinverters)} x {model.model}, troncal "
            f"{branch.trunk_size}"
        )
        texts.append(TextItem(layers.TAGS, X0, rnd(y_t - 7.0), CAPTION_MM, caption))

        ocpd = ocpds[branch.ocpd]
        breaker = add(
            _breaker_instance(
                ocpd.id,
                breaker_x,
                y_t,
                role="" if ocpd.role == "other" else ocpd.role,
                poles=ocpd.poles,
                rating_a=ocpd.rating_a,
                voltage_v=ocpd.voltage_v,
                kaic_ka=ocpd.kaic_ka,
                backfed=ocpd.backfed,
            )
        )
        circuit = circuits.get(branch.id)
        start = jb.port_xy("OUT")
        end = breaker.port_xy("IN")
        wire(
            circuit.id if circuit else f"AC-{branch.id}",
            "inverter_output",
            layers.AC_CONDUCTORS,
            PortRef(jb_id, "OUT"),
            PortRef(ocpd.id, "IN"),
            (start, end),
            circuit.id if circuit else None,
        )

    service = service_instances(spec, placement, i2, meter, panel)
    for item in service:
        add(item)
    panel_left = by_id[panel.id].port_xy("LEFT")
    branch_breakers = [ocpds[b.ocpd] for b in spec.ac_branches]

    ground_xy = (GROUND_X, ground_y)
    add(ground_instance(spec, ground_xy))
    if combiner is None:
        for ocpd in branch_breakers:
            out = by_id[ocpd.id].port_xy("OUT")
            wire(
                f"NET-{ocpd.id}",
                "ac_network",
                layers.AC_CONDUCTORS,
                PortRef(ocpd.id, "OUT"),
                PortRef(panel.id, "LEFT"),
                _hvh(out, panel_left, bend_x=panel_left.x - 6.0),
            )
    else:
        main = ocpds[combiner.main_ocpd]
        add(
            _breaker_instance(
                main.id,
                COMBINER_MAIN_X,
                y_ac,
                role=main.role,
                poles=main.poles,
                rating_a=main.rating_a,
                voltage_v=main.voltage_v,
                kaic_ka=main.kaic_ka,
                backfed=main.backfed,
            )
        )
        main_in = by_id[main.id].port_xy("IN")
        cca = circuits.get(combiner.id)
        # Every branch breaker feeds the bus; the run from the bus to the main breaker is the
        # combiner output C-CCA (carried by the first branch's wire, whose last segment it is).
        for index, ocpd in enumerate(branch_breakers):
            out = by_id[ocpd.id].port_xy("OUT")
            feeder = index == 0 and cca is not None
            wire(
                cca.id if feeder and cca else f"BUS-{ocpd.id}",
                "inverter_output",
                layers.AC_CONDUCTORS,
                PortRef(ocpd.id, "OUT"),
                PortRef(main.id, "IN"),
                _hvh(out, main_in, bend_x=COMBINER_BUS_X),
                cca.id if feeder and cca else None,
            )
            dot(COMBINER_BUS_X, out.y)
        wire(
            "NET-ITM1",
            "ac_network",
            layers.AC_CONDUCTORS,
            PortRef(main.id, "OUT"),
            PortRef(panel.id, "LEFT"),
            (by_id[main.id].port_xy("OUT"), panel_left),
        )
        low = min(by_id[o.id].y for o in branch_breakers)
        high = max(by_id[o.id].y for o in branch_breakers)
        spd_id = combiner.spd
        if spd_id is not None:
            spd = next(s for s in spec.ac_bos.spds if s.id == spd_id)
            spd_x = COMBINER_BUS_X + 4.0
            spd_item = add(
                _instance(
                    "PVSLD_SPD_AC",
                    spd.id,
                    spd_x,
                    low - 4.0,
                    {
                        "TAG": spd.id,
                        "DESC": f"DPS CA {spd.spd_type}",
                        "SPEC": "",
                        "SPD_TYPE": spd.spd_type,
                        "UC_V": _g(spd.uc_v) if spd.uc_v else "",
                        "UP_KV": "",
                        "IN_KA": _g(spd.in_ka),
                    },
                    scale=0.6,
                )
            )
            last = next(o for o in branch_breakers if by_id[o.id].y == low)
            out = by_id[last.id].port_xy("OUT")
            spd_l = spd_item.port_xy("L")
            wire(
                f"BUS-{spd.id}",
                "inverter_output",
                layers.AC_CONDUCTORS,
                PortRef(last.id, "OUT"),
                PortRef(spd.id, "L"),
                (out, Point(COMBINER_BUS_X, out.y), Point(COMBINER_BUS_X, spd_l.y), spd_l),
            )
            wire(
                f"GND-{spd.id}",
                "grounding",
                layers.GROUNDING,
                PortRef(spd.id, "PE"),
                PortRef(GROUND_COMP_ID, "PE"),
                _vh(spd_item.port_xy("PE"), by_id[GROUND_COMP_ID].port_xy("PE")),
            )
            low = spd_item.y - 20.0 * 0.6
        x0, x1 = breaker_x - 4.0, COMBINER_BOX_RIGHT
        y0, y1 = rnd(low - 4.0), rnd(high + 13.0)
        polylines.append(
            PolylineItem(
                layers.ENCLOSURES,
                (Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1)),
                closed=True,
            )
        )
        texts.append(TextItem(layers.ENCLOSURES, x0, rnd(y1 + 1.5), CAPTION_MM, COMBINER_CAPTION))

    chain = [
        (PortRef(panel.id, "RIGHT"), PortRef(i2.id, "IN")),
        (PortRef(i2.id, "OUT"), PortRef(meter.id, "IN")),
        (PortRef(meter.id, "OUT"), PortRef(GRID_COMP_ID, "IN")),
    ]
    for index, (start_ref, end_ref) in enumerate(chain, start=1):
        a = by_id[start_ref.comp_id].port_xy(start_ref.port)
        b = by_id[end_ref.comp_id].port_xy(end_ref.port)
        wire(f"NET-{index:02d}", "ac_network", layers.AC_CONDUCTORS, start_ref, end_ref, _hvh(a, b))
    wire(
        f"GND-{panel.id}",
        "grounding",
        layers.GROUNDING,
        PortRef(panel.id, "PE"),
        PortRef(GROUND_COMP_ID, "PE"),
        _vh(by_id[panel.id].port_xy("PE"), by_id[GROUND_COMP_ID].port_xy("PE")),
    )
    texts.append(
        TextItem(
            layers.TAGS,
            rnd(GROUND_X + 32.0),
            rnd(ground_y + 3.0),
            2.5,
            f"GEC CA: {spec.grounding.gec_ac} Cu desnudo",
        )
    )
    return Schematic(
        tuple(instances),
        tuple(connections),
        tuple(texts),
        i1,
        i2,
        tuple(polylines),
        tuple(junctions),
    )


__all__ = ["MAX_BRANCHES", "MAX_UNITS", "build_micro_schematic", "check_drawable"]
