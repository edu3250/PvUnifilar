"""From the editor's drawing to the engine's specification (ENG-003, Stage 3.8.5)."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from pvsld.capabilities import MAX_STRINGS, capabilities
from pvsld.catalogue import ComponentRegistry
from pvsld.cli import main
from pvsld.core.draft import evaluate_draft
from pvsld.graph import graph_to_spec
from pvsld.resources import data_path
from pvsld.sizing import size_pv_system
from pvsld.sizing.choices import evaluate_choices
from pvsld.sizing.dcbox import dc_box
from pvsld.sizing.io import load_request

MODULE = "JINKO-JKM650N-66HL4M-BDV"
INVERTER = "GROWATT-MIN-5000TL-X2"
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
    return ComponentRegistry.load(data_path("datasheets/records"))


@pytest.fixture(scope="module")
def sized(registry: ComponentRegistry) -> dict[str, Any]:
    request = load_request(Path(data_path("examples/sizing_jinko_growatt.yaml")))
    spec = size_pv_system(request, registry).candidates[0].spec
    for string in spec["strings"]:
        string["n_series"] = 5
    return spec


@pytest.fixture
def template(sized: dict[str, Any]) -> dict[str, Any]:
    """Everything the drawing does not hold; ITM-1 comes from the drawing."""
    template = copy.deepcopy({key: sized[key] for key in TEMPLATE_KEYS})
    template["ac_bos"]["ocpds"] = []
    return template


def _box_node(spec: dict[str, Any], registry: ComponentRegistry) -> tuple[dict, list[dict]]:
    """The DC box as the editor places it: the group of ENG-004's parts and their links."""
    box = dc_box(spec, registry=registry)
    children = []
    for child in box.children:
        values: dict[str, Any] = {}
        if child.role in ("protection", "switch"):
            values = {**child.values, "poles": child.poles}
        elif child.role == "spd":
            values = dict(child.values)
        if child.model:
            values["model"] = child.model
        children.append({"id": child.id, "type": child.type_id, "values": values})
    cables = [{"from": link.from_port, "to": link.to_port} for link in box.links]
    return {"id": "CAJA-CD", "type": "dc_box", "children": children}, cables


@pytest.fixture
def graph(sized: dict[str, Any], registry: ComponentRegistry) -> dict[str, Any]:
    """The owner's example: two strings of five, the box, the inverter and the AC chain."""
    box, box_cables = _box_node(sized, registry)
    return {
        "format": "pvsld.graph/1",
        "nodes": [
            {"id": "S1", "type": "pv_string", "values": {"module": MODULE, "n_series": 5}},
            {"id": "S2", "type": "pv_string", "values": {"module": MODULE, "n_series": 5}},
            box,
            {"id": "INV1", "type": "inverter", "values": {"model": INVERTER}},
            {"id": "ITM-1", "type": "ac_breaker"},
            {"id": "CC-1", "type": "load_centre"},
            {"id": "ITM-P", "type": "ac_breaker"},
            {"id": "M-1", "type": "meter"},
            {"id": "CFE", "type": "grid"},
        ],
        "cables": [
            *box_cables,
            {"from": "INV1.AC", "to": "ITM-1.IN"},
            {"from": "ITM-1.OUT", "to": "CC-1.LEFT"},
            {"from": "CC-1.RIGHT", "to": "ITM-P.IN"},
            {"from": "ITM-P.OUT", "to": "M-1.IN"},
            {"from": "M-1.OUT", "to": "CFE.IN"},
        ],
    }


def _codes(result) -> list[tuple[str, tuple[str, ...]]]:
    return [(i.code, i.node_ids) for i in result.issues]


def test_drawing_becomes_a_complete_valid_spec(graph, template, registry) -> None:
    result = graph_to_spec(graph, template, registry=registry)
    assert result.ok, _codes(result)
    spec = result.spec
    assert [(s["id"], s["inverter"], s["mppt"]) for s in spec["strings"]] == [
        ("S1", "INV1", "A"),
        ("S2", "INV1", "B"),
    ]
    assert [d["id"] for d in spec["dc_bos"]["disconnects"]] == [
        "DCD-1",
        "FUS-S1",
        "FUS-S2",
        "DCD-CD1",
    ]
    ac = spec["ac_bos"]
    assert ac["point_of_connection"]["breaker"] == "ITM-1"
    assert ac["point_of_connection"]["panel"] == "CC-1"
    assert ac["panels"][0]["main_ocpd"] == "ITM-P"
    assert [c["id"] for c in spec["circuits"]] == ["C-S1", "C-S2", "C-INV"]
    draft = evaluate_draft(spec)
    assert draft.complete, [i.to_dict() for i in draft.issues]
    assert draft.ok
    # the engine's own values: nothing to update
    assert evaluate_choices(spec, registry=registry).updates == ()


def test_every_node_is_mapped(graph, template, registry) -> None:
    result = graph_to_spec(graph, template, registry=registry)
    assert result.mapped["S1"] == "strings[0]"
    assert result.mapped["INV1"] == "inverters[0]"
    assert result.mapped["FUS-S1"] == "dc_bos.disconnects[1]"
    assert result.mapped["ITM-P"] == "ac_bos.main_breakers[0]"
    assert result.mapped["CX-S1"] == "wiring"
    assert result.mapped["CAJA-CD"] == "dc_bos"
    assert result.mapped["CFE"] == "utility"
    ids = {c["id"] for n in graph["nodes"] for c in [n, *n.get("children", [])]}
    assert ids == set(result.mapped)


def test_defaults_are_listed_as_filled(graph, template, registry) -> None:
    result = graph_to_spec(graph, template, registry=registry)
    filled = {f.path: f.value for f in result.filled}
    assert filled["circuits[0].length_m"] == 25
    assert filled["circuits[2].length_m"] == 30
    assert filled["circuits[0].raceway.rooftop_clearance_mm"] == 25
    assert filled["ac_bos.ocpds[0].rating_a"] == 30.0
    assert filled["circuits[0].conductors.size"] == "8 AWG"


def test_drawn_lengths_and_sizes_win(graph, template, registry) -> None:
    for cable in graph["cables"]:
        if cable["to"] == "INV1.A":
            cable.update(length_m=12, size="6 AWG")
        if cable["from"] == "INV1.AC":
            cable["length_m"] = 8
    result = graph_to_spec(graph, template, registry=registry)
    circuits = {c["id"]: c for c in result.spec["circuits"]}
    assert circuits["C-S1"]["length_m"] == 12
    assert circuits["C-S1"]["conductors"]["size"] == "6 AWG"
    assert circuits["C-INV"]["length_m"] == 8
    paths = {f.path for f in result.filled}
    assert "circuits[0].length_m" not in paths
    assert "circuits[0].conductors.size" not in paths


def test_the_mppt_follows_the_cable(graph, template, registry) -> None:
    for cable in graph["cables"]:
        if cable["to"] == "INV1.A":
            cable["to"] = "INV1.B"
        elif cable["to"] == "INV1.B":
            cable["to"] = "INV1.A"
    result = graph_to_spec(graph, template, registry=registry)
    assert [s["mppt"] for s in result.spec["strings"]] == ["B", "A"]


def test_string_without_a_path_to_the_inverter(graph, template, registry) -> None:
    graph["cables"] = [c for c in graph["cables"] if c["from"] != "XS-S2.R"]
    result = graph_to_spec(graph, template, registry=registry)
    assert ("topology", ("S2",)) in _codes(result)
    assert [s["id"] for s in result.spec["strings"]] == ["S1"]


def test_protection_on_the_wrong_string(graph, template, registry) -> None:
    for cable in graph["cables"]:
        if cable["from"] == "XE-S1.R":
            cable["to"] = "FUS-S2.IN"
        elif cable["from"] == "XE-S2.R":
            cable["to"] = "FUS-S1.IN"
    result = graph_to_spec(graph, template, registry=registry)
    assert ("topology", ("FUS-S1",)) in _codes(result)
    assert ("topology", ("FUS-S2",)) in _codes(result)


@pytest.mark.parametrize(
    ("change", "code", "node"),
    [
        (lambda g: g["cables"].append({"from": "INV1.C", "to": "S1.OUT"}), "unknown_port", "INV1"),
        (lambda g: g["cables"].append({"from": "X9.OUT", "to": "S1.OUT"}), "unknown_node", "X9"),
        (lambda g: g["nodes"].append({"id": "BAT1", "type": "battery"}), "unknown_type", "BAT1"),
        (lambda g: g["nodes"].append({"id": "M-2", "type": "meter"}), "disconnected", "M-2"),
        (lambda g: g["nodes"].append(dict(g["nodes"][0])), "duplicate_id", "S1"),
        (
            lambda g: g["nodes"][3]["values"].update(model="NOPE"),
            "unknown_component",
            "INV1",
        ),
    ],
)
def test_problems_name_their_node(graph, template, registry, change, code, node) -> None:
    change(graph)
    result = graph_to_spec(graph, template, registry=registry)
    assert any(i.code == code and node in i.node_ids for i in result.issues), _codes(result)


def test_unsupported_topology_is_kept_and_flagged(graph, template, registry) -> None:
    graph["nodes"].append(
        {"id": "S3", "type": "pv_string", "values": {"module": MODULE, "n_series": 5}}
    )
    graph["cables"].append({"from": "S3.OUT", "to": "INV1.B"})
    result = graph_to_spec(graph, template, registry=registry)
    assert len(result.spec["strings"]) == MAX_STRINGS + 1
    draft = evaluate_draft(result.spec)
    assert any(i.category == "unsupported" and i.path == "strings" for i in draft.issues)


def test_invalid_graph() -> None:
    result = graph_to_spec({"format": "other"}, {})
    assert [i.code for i in result.issues] == ["invalid_graph"]


def test_result_is_plain_data(graph, template, registry) -> None:
    data = graph_to_spec(graph, template, registry=registry).to_dict()
    json.dumps(data, default=str)
    assert data["ok"] is True


def test_capabilities_document(capsys) -> None:
    document = capabilities()
    assert document["format"] == "pvsld.capabilities/1"
    assert document["limits"]["max_strings"] == MAX_STRINGS
    statuses = {c["id"]: c["status"] for c in document["capabilities"]}
    assert statuses["storage"] == "pending"
    assert statuses["dc_box"] == "supported"
    assert main(["capabilities", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["format"] == "pvsld.capabilities/1"
