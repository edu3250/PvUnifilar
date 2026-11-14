"""The diagram model built from the sample: identity, attributes, ports and sheet geometry."""

from __future__ import annotations

import re
from itertools import combinations
from typing import Any

import pytest

from pvsld.core import layers
from pvsld.core.diagram import (
    A3,
    Box,
    Connection,
    Diagram,
    Point,
    PortRef,
    SymbolInstance,
    Table,
    TextItem,
    check_diagram,
    text_width_mm,
)
from pvsld.core.layout import LayoutError, build_diagram
from pvsld.core.model import PvSystemSpec, parse_spec
from pvsld.core.validation import validate_pv_design
from pvsld.symbols import SYMBOLS
from s1_helpers import load_example, mutated

MIN_GAP_MM = 0.5


@pytest.fixture(scope="module")
def spec() -> PvSystemSpec:
    return parse_spec(load_example())


@pytest.fixture(scope="module")
def diagram(spec: PvSystemSpec) -> Diagram:
    return build_diagram(spec)


# --- Identity and counts ---------------------------------------------------------------------


def test_diagram_is_structurally_sound(diagram: Diagram) -> None:
    assert check_diagram(diagram) == []


def test_component_inventory_matches_the_model(diagram: Diagram) -> None:
    assert diagram.block_counts() == {
        "PVSLD_PV_STRING": 2,
        "PVSLD_INV": 1,
        "PVSLD_CB": 2,
        "PVSLD_PI": 1,
        "PVSLD_PANEL": 1,
        "PVSLD_METER": 1,
        "PVSLD_GRID": 1,
        "PVSLD_GND": 1,
        "PVSLD_TTLB": 1,
    }
    ids = [i.comp_id for i in diagram.instances]
    assert len(set(ids)) == len(ids)
    assert {"S1", "S2", "INV1", "ITM-1", "ITM-P", "CC-1", "M-1", "PI-1"} <= set(ids)


def test_model_component_ids_become_comp_id_attributes(
    diagram: Diagram, spec: PvSystemSpec
) -> None:
    for comp_id in [s.id for s in spec.strings] + [i.id for i in spec.inverters]:
        assert diagram.instance(comp_id).values["COMP_ID"] == comp_id


def test_every_instance_carries_every_tag_of_its_symbol_in_order(diagram: Diagram) -> None:
    for item in diagram.instances:
        assert [t for t, _ in item.attributes] == list(SYMBOLS[item.symbol].tags)


def test_power_flow_is_left_to_right(diagram: Diagram) -> None:
    order = ["S1", "INV1", "ITM-1", "PI-1", "CC-1", "ITM-P", "M-1", "GRID-1"]
    xs = [diagram.instance(name).x for name in order]
    assert xs == sorted(xs)


# --- Attribute values come from the model and the calculations ---------------------------------


def test_string_attributes_carry_the_computed_values(diagram: Diagram) -> None:
    values = diagram.instance("S1").values
    assert values["TAG"] == "S1"
    assert values["DESC"] == "7 × 550 W = 3.85 kWp"
    assert values["MODEL"] == "XM-550"
    assert values["MPPT"] == "A"
    assert values["VOC_MAX_V"] == "373.4"
    assert values["VMP_V"] == "291.2"
    assert values["ISC_A"] == "14.00"
    assert diagram.instance("S2").values["MPPT"] == "B"


def test_inverter_and_protection_attributes(diagram: Diagram) -> None:
    inverter = diagram.instance("INV1").values
    assert inverter["PAC_W"] == "6000"
    assert inverter["VAC_V"] == "220"
    assert inverter["DESC"] == "Inversor de red 6.0 kW, 220 V"
    i1 = diagram.instance("ITM-1").values
    assert (i1["ROLE"], i1["RATING_A"], i1["POLES"], i1["BACKFED"]) == ("I1", "35", "2", "SI")
    i2 = diagram.instance("ITM-P").values
    assert (i2["ROLE"], i2["RATING_A"], i2["VOLT_V"]) == ("I2", "100", "—")
    meter = diagram.instance("M-1").values
    assert (meter["METER_TYPE"], meter["BIDIRECTIONAL"]) == ("MF", "SI")
    assert diagram.instance("PI-1").values["PI_TYPE"] == "load_side"
    assert diagram.instance("CC-1").values["SPEC"] == "Barra 125 A; 2F-3H; 10 kA"


def test_title_block_has_the_mexican_fields(diagram: Diagram) -> None:
    values = diagram.instance("TTLB-1").values
    assert values["PROYECTO"] == "Sistema fotovoltaico interconectado 7.70 kWp"
    assert values["RPU"] == "000000000000"
    assert values["NUM_SERVICIO"] == "000000000000"
    assert values["RESPONSABLE"] == "Ing. Nombre Apellido"
    assert values["CEDULA"] == "00000000"
    assert values["FECHA"] == "2026-10-05"
    assert values["PLANO_NO"] == "IE-01"
    assert values["ESCALA"] == "SIN ESCALA"
    assert values["UVIE"] == "NO APLICA"
    assert values["CAPACIDAD"] == "7.70 kWp / 6.00 kWac"
    assert values["UBICACION"].startswith("Av. Ejemplo 123, Centro, Zapopan, Jalisco")
    assert diagram.instance("TTLB-1").space == "paper"


def test_string_table_lists_every_string_and_the_total(diagram: Diagram) -> None:
    table = next(t for t in diagram.tables if t.id == "TBL-STRINGS")
    assert table.header[0] == "Rama"
    assert [row[0] for row in table.rows] == ["S1", "S2", "Total"]
    assert table.rows[0][4] == "373.4"  # Voc max at T min
    assert table.rows[-1][-1] == "7.70"
    assert "T mín = -3 °C" in table.title


def test_conductor_schedule_matches_the_vault_worked_example(diagram: Diagram) -> None:
    table = next(t for t in diagram.tables if t.id == "TBL-CONDUCTORS")
    by_id = {row[0]: row for row in table.rows}
    assert by_id["C-S1"][-1] == "0.92"  # DC drop %
    assert by_id["C-S1"][-2] == "22.7"  # corrected ampacity A
    assert by_id["C-INV"][-1] == "1.91"
    assert by_id["C-INV"][2] == "2-8 AWG Cu THW-2 + N 8 AWG"
    assert by_id["C-S2"][5] == "PVC 21 mm"  # raceway shared with C-S1


def test_protection_schedule_and_legend_and_revisions(diagram: Diagram) -> None:
    protections = next(t for t in diagram.tables if t.id == "TBL-PROTECTIONS")
    assert [row[0] for row in protections.rows] == ["ITM-1", "ITM-P", "DCD-1", "DPS-CD1", "DPS-CA1"]
    legend = next(t for t in diagram.tables if t.id == "TBL-LEGEND")
    used = {item.symbol for item in diagram.instances if item.space == "model"}
    assert sorted(row[0] for row in legend.rows) == sorted(used)  # one row each, none wrapped
    assert {row[0]: row[2] for row in legend.rows}["PVSLD_CB"] == "CFE G0100-04 Ap. C"
    revisions = next(t for t in diagram.tables if t.id == "TBL-REVISIONS")
    assert revisions.space == "paper"
    assert revisions.rows[0][2] == "Emisión para solicitud de interconexión"


def test_notes_state_method_assumptions_and_draft_status(diagram: Diagram) -> None:
    notes = " ".join(t.text for t in diagram.texts if t.layer == layers.NOTES)
    assert "β Voc" in notes
    assert "-3 °C" in notes
    assert "γ Pmax" in notes
    assert "NOM-008-SE-2021" in notes
    assert "mx-gd-2026.10" in notes
    assert "firmado por el responsable" in notes


def test_conductor_callouts_name_the_circuit_size_raceway_and_drop(diagram: Diagram) -> None:
    callouts = " | ".join(t.text for t in diagram.texts)
    assert "C-S1 | 2-10 AWG Cu PV / THW-2 | + 1-10 AWG desnudo (p.t.)" in callouts
    assert "C-INV | 2-8 AWG Cu THW-2 + N 8 AWG | + 1-10 AWG desnudo (p.t.)" in callouts
    assert "ΔV 0.92 %" in callouts
    assert "ΔV 1.91 %" in callouts


def test_metadata_pins_versions_not_the_package_version(diagram: Diagram) -> None:
    meta = dict(diagram.metadata)
    assert meta["PROJECT_ID"] == "PV-2026-0001"
    assert meta["RULEPACK"] == "mx-gd-2026.10"
    assert meta["LAYOUT_TEMPLATE"] == "bt_string_residential_v1"
    assert "VERSION" not in " ".join(meta).replace("SCHEMA_VERSION", "").replace(
        "SYMBOL_LIBRARY_VERSION", ""
    )


# --- Connectivity ---------------------------------------------------------------------------------


def test_every_circuit_of_the_model_is_a_connection(diagram: Diagram, spec: PvSystemSpec) -> None:
    drawn = {c.circuit_id for c in diagram.connections if c.circuit_id}
    assert drawn == {c.id for c in spec.circuits}


def test_wires_end_exactly_on_ports_and_use_orthogonal_segments(diagram: Diagram) -> None:
    for conn in diagram.connections:
        start = diagram.instance(conn.start.comp_id).port_xy(conn.start.port)
        end = diagram.instance(conn.end.comp_id).port_xy(conn.end.port)
        assert conn.points[0] == start
        assert conn.points[-1] == end


def test_no_required_port_is_left_dangling(diagram: Diagram) -> None:
    connected = {str(c.start) for c in diagram.connections} | {
        str(c.end) for c in diagram.connections
    }
    for item in diagram.instances:
        for port in SYMBOLS[item.symbol].ports:
            if port.required:
                assert f"{item.comp_id}.{port.id}" in connected


def test_the_optional_second_mppt_port_is_used_by_the_second_string(diagram: Diagram) -> None:
    assert PortRef("INV1", "B") in {c.end for c in diagram.connections}


def test_conductor_layers_follow_the_house_standard(diagram: Diagram) -> None:
    by_kind = {c.kind: c.layer for c in diagram.connections}
    assert by_kind["pv_source"] == layers.DC_CONDUCTORS
    assert by_kind["inverter_output"] == layers.AC_CONDUCTORS
    assert by_kind["ac_network"] == layers.AC_CONDUCTORS
    assert by_kind["grounding"] == layers.GROUNDING


def test_check_diagram_reports_a_wire_that_misses_its_port(diagram: Diagram) -> None:
    broken = diagram.connections[0]
    moved = Connection(
        broken.id,
        broken.kind,
        broken.layer,
        broken.start,
        broken.end,
        (Point(broken.points[0].x + 1, broken.points[0].y), *broken.points[1:]),
        broken.circuit_id,
    )
    problems = check_diagram(_with(diagram, connections=(moved, *diagram.connections[1:])))
    assert any("is not on port S1.OUT" in p for p in problems)


def test_check_diagram_reports_a_dangling_required_port(diagram: Diagram) -> None:
    problems = check_diagram(_with(diagram, connections=diagram.connections[1:]))
    assert any("S1.OUT is a dangling required port" in p for p in problems)


def test_check_diagram_reports_duplicate_ids_and_foreign_layers(diagram: Diagram) -> None:
    clone = diagram.instances[0]
    stray = TextItem("0", 20, 20, 2.5, "x")
    problems = check_diagram(
        _with(diagram, instances=(*diagram.instances, clone), texts=(*diagram.texts, stray))
    )
    assert any("duplicate COMP_ID S1" in p for p in problems)
    assert any("outside the house standard" in p and "0" in p for p in problems)


def test_check_diagram_reports_attribute_mismatches(diagram: Diagram) -> None:
    first = diagram.instances[0]
    wrong = SymbolInstance(
        first.comp_id,
        first.symbol,
        first.x,
        first.y,
        first.layer,
        first.attributes[:-1],
        first.space,
    )
    problems = check_diagram(_with(diagram, instances=(wrong, *diagram.instances[1:])))
    assert any("S1: attributes" in p for p in problems)


def _with(diagram: Diagram, **changes: Any) -> Diagram:
    from dataclasses import replace

    return replace(diagram, **changes)


# --- Sheet geometry -------------------------------------------------------------------------------


def _model_boxes(diagram: Diagram) -> list[tuple[str, Box]]:
    boxes: list[tuple[str, Box]] = []
    for item in diagram.instances:
        if item.space == "model":
            boxes += [(f"symbol {name}", box) for name, box in item.boxes()]
    boxes += [(f"text {t.text!r}", t.box()) for t in diagram.texts if t.space == "model"]
    boxes += [(f"table {t.id}", t.box()) for t in diagram.tables if t.space == "model"]
    return boxes


def test_everything_lies_inside_the_sheet_border(diagram: Diagram) -> None:
    inner = Box(
        A3.border.x0 + 1, A3.border.y0 + 1, A3.border.x1 - 1, A3.border.y1 - 1
    )  # 1 mm clearance from the frame
    for name, box in _model_boxes(diagram):
        assert inner.contains(box), f"{name} {box} leaves the border"
    # the title and revision blocks share the border line with the frame
    for table in diagram.tables:
        assert A3.border.contains(table.box()), f"{table.id} leaves the border"
    for item in diagram.instances:
        assert A3.border.contains(item.box()), f"{item.comp_id} leaves the border"


def test_no_two_items_of_the_schematic_overlap(diagram: Diagram) -> None:
    boxes = _model_boxes(diagram)
    for (name_a, a), (name_b, b) in combinations(boxes, 2):
        assert not a.intersects(b, gap=MIN_GAP_MM), (
            f"{name_a} is closer than 0.5 mm to {name_b}: {a} vs {b}"
        )


def test_paper_furniture_does_not_overlap_the_model_or_itself(diagram: Diagram) -> None:
    paper = [(t.id, t.box()) for t in diagram.tables if t.space == "paper"]
    paper += [(i.comp_id, i.box()) for i in diagram.instances if i.space == "paper"]
    for (name_a, a), (name_b, b) in combinations(paper, 2):
        assert not a.intersects(b), f"{name_a} overlaps {name_b}"
    for name_a, a in paper:
        for name_b, b in _model_boxes(diagram):
            assert not a.intersects(b), f"paper {name_a} overlaps model {name_b}"


def _segment_hits(box: Box, a: Point, b: Point) -> bool:
    """True when the axis-aligned segment a-b passes through the interior of ``box``."""
    if a.y == b.y:
        low, high = sorted((a.x, b.x))
        return box.y0 < a.y < box.y1 and low < box.x1 and high > box.x0
    low, high = sorted((a.y, b.y))
    return box.x0 < a.x < box.x1 and low < box.y1 and high > box.y0


def test_wires_do_not_cross_texts_tables_or_foreign_symbols(diagram: Diagram) -> None:
    for conn in diagram.connections:
        own = {conn.start.comp_id, conn.end.comp_id}
        targets = _model_boxes(diagram)
        for name, box in targets:
            if name.removeprefix("symbol ").split(" ")[0].split(".")[0] in own:
                continue
            for a, b in zip(conn.points, conn.points[1:], strict=False):
                assert not _segment_hits(box, a, b), f"wire {conn.id} crosses {name}"


def test_table_text_fits_its_cells(diagram: Diagram) -> None:
    for table in diagram.tables:
        for width, text in table.cells():
            needed = text_width_mm(text, table.text_height) + 2 * 1.5
            assert needed <= width, f"{table.id}: {text!r} needs {needed:.1f} mm, cell is {width}"
        title_needed = text_width_mm(table.title, table.text_height) + 3
        assert title_needed <= table.width, f"{table.id}: title does not fit"


def test_table_rows_have_a_cell_for_every_column(diagram: Diagram) -> None:
    for table in diagram.tables:
        for row in (table.header, *table.rows):
            assert len(row) == len(table.col_widths), f"{table.id}: {row}"


def test_all_text_is_at_least_2_5_mm_high(diagram: Diagram) -> None:
    assert all(t.height >= 2.5 for t in diagram.texts)
    assert all(t.text_height >= 2.5 for t in diagram.tables)


def test_viewport_is_one_to_one_on_the_border(diagram: Diagram) -> None:
    vp = diagram.viewport
    assert vp.layer == layers.NON_PLOT
    assert vp.view_height == vp.size[1]  # 1:1
    assert vp.center == vp.view_center  # model and paper coordinates coincide
    assert vp.size == (A3.border.x1 - A3.border.x0, A3.border.y1 - A3.border.y0)


def test_table_geometry_expands_to_frame_rules_and_texts() -> None:
    table = Table(
        id="T",
        layer=layers.TABLES,
        x=10,
        y_top=50,
        col_widths=(20, 30),
        title="Título",
        header=("a", "b"),
        rows=(("1", "2"),),
    )
    assert table.width == 50
    assert table.height == 15
    assert table.box() == Box(10, 35, 60, 50)
    lines = table.lines()
    assert len(lines) == 4 + 2 + 1  # frame, two inner row rules, one column rule
    texts = table.texts()
    assert [t.text for t in texts] == ["Título", "a", "b", "1", "2"]
    assert texts[0].y == 50 - 5 + 1.25  # title baseline inside the first row


# --- The template rejects what it cannot draw -----------------------------------------------------


def test_build_is_deterministic(spec: PvSystemSpec) -> None:
    assert build_diagram(spec) == build_diagram(spec)


def test_more_than_two_strings_are_rejected() -> None:
    def three(data: dict[str, Any]) -> None:
        data["strings"].append({**data["strings"][0], "id": "S3"})

    with pytest.raises(LayoutError, match="at most two strings"):
        build_diagram(parse_spec(mutated(three)))


def test_two_inverters_are_rejected() -> None:
    def two(data: dict[str, Any]) -> None:
        data["inverters"].append({**data["inverters"][0], "id": "INV2"})

    with pytest.raises(LayoutError, match="un solo inversor"):
        build_diagram(parse_spec(mutated(two)))


def test_a_third_mppt_is_not_drawn_yet() -> None:
    def third(data: dict[str, Any]) -> None:
        data["inverters"][0]["mppt"].append({**data["inverters"][0]["mppt"][0], "id": "C"})
        data["strings"][1]["mppt"] = "C"

    with pytest.raises(LayoutError, match="entradas A y B"):
        build_diagram(parse_spec(mutated(third)))


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda s: s["ac_bos"]["ocpds"][0].update({"role": "other"}), "I1"),
        (lambda s: s["ac_bos"]["main_breakers"][0].update({"role": "other"}), "I2"),
        (lambda s: s["ac_bos"].update({"meters": []}), "MF"),
    ],
)
def test_missing_key_devices_are_rejected(change: Any, fragment: str) -> None:
    with pytest.raises(LayoutError, match=fragment):
        build_diagram(parse_spec(mutated(change)))


def test_too_many_revisions_are_rejected() -> None:
    def many(data: dict[str, Any]) -> None:
        data["title_block"]["revisions"] = [
            {"rev": str(n), "date": "2026-10-05", "description": "x", "by": "ABC"} for n in range(6)
        ]

    with pytest.raises(LayoutError, match="at most 5 revisions"):
        build_diagram(parse_spec(mutated(many)))


def test_the_diagram_of_the_sample_has_no_validation_errors() -> None:
    assert validate_pv_design(load_example()).ok


def test_notes_and_text_are_spanish() -> None:
    spec = parse_spec(load_example())
    diagram = build_diagram(spec)
    english = re.compile(r"\b(the|and|with|drawing|title|revision block|notes)\b", re.IGNORECASE)
    for text in diagram.texts:
        assert not english.search(text.text), text.text


# --- Text metrics ---------------------------------------------------------------------------------


def test_arial_table_covers_printable_ascii_with_known_widths() -> None:
    from pvsld.core.diagram import _ARIAL_WIDTHS

    assert len(_ARIAL_WIDTHS) == 95
    spot = {" ": 278, "0": 556, "A": 667, "W": 944, "i": 222, "m": 833, "a": 556, "~": 584}
    assert {char: _ARIAL_WIDTHS[char] for char in spot} == spot


def test_text_width_scales_with_cap_height_and_matches_the_rendered_preview() -> None:
    # "PVSLD_PV_STRING" at 2.5 mm measured about 33 mm in the ezdxf preview (see the S1 notes)
    assert 32 <= text_width_mm("PVSLD_PV_STRING", 2.5) <= 36
    assert text_width_mm("abc", 5.0) == pytest.approx(2 * text_width_mm("abc", 2.5))
    assert text_width_mm("", 2.5) == 0


def test_accented_letters_have_the_width_of_their_base_letter() -> None:
    assert text_width_mm("É", 2.5) == text_width_mm("E", 2.5)
    assert text_width_mm("ñ", 2.5) == text_width_mm("n", 2.5)
    assert text_width_mm("Ω", 2.5) > 0  # symbols outside ASCII have a width too
