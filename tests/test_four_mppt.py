"""String inverters with up to 4 MPPT inputs (Stage 3.13.1, owner approval 2026-11-14).

A and B stay on the left of the inverter, C and D go on top (``PVSLD_INV_3MPPT`` /
``PVSLD_INV_4MPPT``); one string per MPPT, up to 4 per inverter. The catalogue has no such
single-phase inverter yet, so these tests use a 4-MPPT copy of a catalogue record.
"""

import copy
from pathlib import Path
from typing import Any

import pytest

from pvsld.capabilities import capabilities
from pvsld.catalogue import ComponentRegistry
from pvsld.core.calc import derive
from pvsld.core.draft import evaluate_draft
from pvsld.core.layout import LayoutError
from pvsld.core.model import parse_spec
from pvsld.core.validation import validate_pv_design
from pvsld.graph import graph_to_spec
from pvsld.graph_export import spec_to_graph
from pvsld.palette import build_palette
from pvsld.resources import data_path
from pvsld.service import layout_diagram
from pvsld.sizing import size_pv_system
from pvsld.sizing.choices import evaluate_choices
from pvsld.sizing.dcbox import apply_dc_box, dc_box
from pvsld.sizing.io import load_request
from pvsld.symbols import get_symbol

MODULE = "JINKO-JKM650N-66HL4M-BDV"
FOUR = "TEST-MIN-6000TL-4MPPT"
THREE = "TEST-MIN-6000TL-3MPPT"
TEMPLATE_KEYS = (
    "project",
    "utility",
    "ac_bos",
    "grounding",
    "storage",
    "title_block",
    "layout",
    "standards",
    "monitoring",
)


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    base = ComponentRegistry.load(data_path("datasheets/records"))
    growatt = base.get("GROWATT-MIN-6000TL-X2")
    extra = [
        growatt.model_copy(
            update={"component_id": FOUR, "model": "MIN 6000TL-4MPPT", "mppt_count": 4}
        ),
        growatt.model_copy(
            update={"component_id": THREE, "model": "MIN 6000TL-3MPPT", "mppt_count": 3}
        ),
    ]
    return ComponentRegistry([*base, *extra])


@pytest.fixture
def template(registry: ComponentRegistry) -> dict[str, Any]:
    request = load_request(Path(data_path("examples/sizing_jinko_growatt.yaml")))
    sized = size_pv_system(request, registry).candidates[0].spec
    template = copy.deepcopy({key: sized[key] for key in TEMPLATE_KEYS})
    template["ac_bos"]["ocpds"] = []
    return template


def _graph(model: str, ports: list[str], n_series: int = 3) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [{"id": "INV1", "type": "inverter", "values": {"model": model}}]
    cables: list[dict[str, Any]] = []
    for number, port in enumerate(ports, start=1):
        nodes.append(
            {
                "id": f"S{number}",
                "type": "pv_string",
                "values": {"module": MODULE, "n_series": n_series},
            }
        )
        cables.append({"from": f"S{number}.OUT", "to": f"INV1.{port}", "length_m": 20})
    nodes += [
        {"id": "ITM-1", "type": "ac_breaker"},
        {"id": "CC-1", "type": "load_centre"},
        {"id": "ITM-P", "type": "ac_breaker"},
        {"id": "M-1", "type": "meter"},
        {"id": "CFE", "type": "grid"},
    ]
    cables += [
        {"from": "INV1.AC", "to": "ITM-1.IN", "length_m": 10},
        {"from": "ITM-1.OUT", "to": "CC-1.LEFT"},
        {"from": "CC-1.RIGHT", "to": "ITM-P.IN"},
        {"from": "ITM-P.OUT", "to": "M-1.IN"},
        {"from": "M-1.OUT", "to": "CFE.IN"},
    ]
    return {"format": "pvsld.graph/1", "nodes": nodes, "cables": cables}


def _codes(result) -> list[tuple[str, tuple[str, ...]]]:
    return [(i.code, i.node_ids) for i in result.issues]


# --- Symbols and palette --------------------------------------------------------------------


def test_the_generated_symbols_put_c_and_d_on_top() -> None:
    four = {p.id: (p.x, p.y, p.direction) for p in get_symbol("PVSLD_INV_4MPPT").ports}
    assert four["A"] == (0, 10, "left")
    assert four["B"] == (0, -10, "left")
    assert four["C"] == (10, 15, "up")
    assert four["D"] == (20, 15, "up")
    assert four["PE"][:2] == (15, -15)  # C and D are symmetric about the centre, like PE
    three = {p.id: (p.x, p.y) for p in get_symbol("PVSLD_INV_3MPPT").ports}
    assert three["C"] == (15, 15)
    assert "D" not in three
    assert {p.id for p in get_symbol("PVSLD_INV").ports} == {"A", "B", "AC", "PE"}


def test_the_palette_names_the_variant_of_each_model(registry) -> None:
    palette = build_palette(registry)
    inverter = palette.type("inverter")
    assert [v.block for v in inverter.symbol_variants] == ["PVSLD_INV_3MPPT", "PVSLD_INV_4MPPT"]
    blocks = {e.component_id: e.block for e in palette.equipment if e.type_id == "inverter"}
    assert blocks[FOUR] == "PVSLD_INV_4MPPT"
    assert blocks[THREE] == "PVSLD_INV_3MPPT"
    assert blocks["GROWATT-MIN-6000TL-X2"] is None


def test_capabilities() -> None:
    limits = capabilities()["limits"]
    assert limits["inverter_dc_ports"] == ["A", "B", "C", "D"]
    assert limits["max_strings_per_inverter"] == 4


# --- The design -----------------------------------------------------------------------------


def test_four_strings_on_four_mppt(registry, template) -> None:
    result = graph_to_spec(_graph(FOUR, ["A", "B", "C", "D"]), template, registry=registry)
    assert result.ok, _codes(result)
    spec = result.spec
    assert [(s["id"], s["mppt"]) for s in spec["strings"]] == [
        ("S1", "A"),
        ("S2", "B"),
        ("S3", "C"),
        ("S4", "D"),
    ]
    assert [m["id"] for m in spec["inverters"][0]["mppt"]] == ["A", "B", "C", "D"]
    draft = evaluate_draft(spec)
    assert draft.complete, [i.to_dict() for i in draft.issues]
    assert not draft.issues_of("unsupported")
    assert validate_pv_design(spec).ok


def test_the_dc_box_takes_four_strings(registry, template) -> None:
    spec = graph_to_spec(_graph(FOUR, ["A", "B", "C", "D"]), template, registry=registry).spec
    box = dc_box(spec, registry=registry)
    assert box.built, box.skipped_es
    protections = [c.id for c in box.children if c.role == "protection"]
    assert protections == ["FUS-S1", "FUS-S2", "FUS-S3", "FUS-S4"]
    switch = next(c for c in box.children if c.role == "switch")
    assert switch.block == "PVSLD_DC_DISCONNECT_4S"
    assert switch.requirements["poles_min"] == 8
    boxed = apply_dc_box(spec, box)
    report = evaluate_choices(boxed, registry=registry)
    assert report.skipped_es is None


def test_three_strings_on_three_mppt_and_the_round_trip(registry, template) -> None:
    result = graph_to_spec(_graph(THREE, ["A", "B", "C"]), template, registry=registry)
    assert result.ok, _codes(result)
    boxed = apply_dc_box(result.spec, dc_box(result.spec, registry=registry))
    exported = spec_to_graph(boxed, registry=registry)
    assert exported.issues == ()
    again = graph_to_spec(exported.graph, exported.template, registry=registry)
    assert again.ok, _codes(again)
    assert again.spec == boxed


def test_an_input_the_model_lacks_is_an_error(registry, template) -> None:
    spec = graph_to_spec(_graph(THREE, ["A", "B", "D"]), template, registry=registry).spec
    issues = evaluate_draft(spec).issues
    assert any("has no MPPT 'D'" in i.message_es for i in issues), [i.to_dict() for i in issues]


def test_two_mppt_inverters_keep_their_symbol(registry, template) -> None:
    result = graph_to_spec(_graph("GROWATT-MIN-6000TL-X2", ["A", "B"]), template, registry=registry)
    assert result.ok, _codes(result)
    spec = parse_spec(result.spec)
    diagram = layout_diagram(spec, derive(spec))
    assert next(i for i in diagram.instances if i.comp_id == "INV1").symbol == "PVSLD_INV"


def test_the_a3_sheet_refuses_c_and_d_for_now(registry, template) -> None:
    spec = parse_spec(
        graph_to_spec(_graph(FOUR, ["A", "B", "C", "D"]), template, registry=registry).spec
    )
    with pytest.raises(LayoutError, match="entradas A y B"):
        layout_diagram(spec, derive(spec))
