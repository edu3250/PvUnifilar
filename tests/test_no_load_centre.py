"""A design without a load centre (schema 0.3.0, Stage 3.11.1, owner approval 2026-11-13).

The RES/142/2017 schemes (Anexo II) draw the plant, I1, the service breaker I2, the meter and the
grid; scheme (b) has no load centre. One breaker may be both I1 and I2, with warning DIS-005; an
I2 drawn without ``bidirectional`` is assumed bidirectional and listed as filled (option a).
"""

import copy
import itertools
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pvsld.capabilities import capabilities
from pvsld.catalogue import ComponentRegistry
from pvsld.core.calc import derive
from pvsld.core.draft import evaluate_draft
from pvsld.core.layout import LayoutError
from pvsld.core.model import SERVICE_SCHEMA_VERSION, parse_spec
from pvsld.core.validation import validate_pv_design
from pvsld.graph import graph_to_spec
from pvsld.graph_export import spec_to_graph
from pvsld.resources import data_path
from pvsld.service import layout_diagram
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
    """The editor's template: it still carries a load centre and ITM-P that are not drawn."""
    template = copy.deepcopy({key: sized[key] for key in TEMPLATE_KEYS})
    template["ac_bos"]["ocpds"] = []
    return template


def _graph(sized: dict[str, Any], registry: ComponentRegistry, ac: list[str]) -> dict[str, Any]:
    """Two strings, the DC box, the inverter and the AC chain ``ac`` (breaker ids, then M-1 and
    CFE)."""
    box = dc_box(sized, registry=registry)
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
    chain = ["INV1", *ac, "M-1", "CFE"]
    types = {"M-1": "meter", "CFE": "grid"}
    ports = {"INV1": ("", "AC"), "CFE": ("IN", "")}
    cables = [{"from": link.from_port, "to": link.to_port} for link in box.links]
    for left, right in itertools.pairwise(chain):
        cables.append({"from": f"{left}.{ports.get(left, ('IN', 'OUT'))[1]}", "to": f"{right}.IN"})
    return {
        "format": "pvsld.graph/1",
        "nodes": [
            {"id": "S1", "type": "pv_string", "values": {"module": MODULE, "n_series": 5}},
            {"id": "S2", "type": "pv_string", "values": {"module": MODULE, "n_series": 5}},
            {"id": "CAJA-CD", "type": "dc_box", "children": children},
            {"id": "INV1", "type": "inverter", "values": {"model": INVERTER}},
            *({"id": node, "type": types.get(node, "ac_breaker")} for node in chain[1:]),
        ],
        "cables": cables,
    }


def _codes(result) -> list[tuple[str, tuple[str, ...]]]:
    return [(i.code, i.node_ids) for i in result.issues]


# --- From the drawing ------------------------------------------------------------------------


def test_i1_then_i2_without_a_load_centre_is_a_valid_design(sized, template, registry) -> None:
    result = graph_to_spec(_graph(sized, registry, ["ITM-1", "ITM-P"]), template, registry=registry)
    assert result.ok, _codes(result)
    spec = result.spec
    ac = spec["ac_bos"]
    assert spec["schema_version"] == SERVICE_SCHEMA_VERSION
    assert ac["panels"] == []
    assert (ac["point_of_connection"]["panel"], ac["point_of_connection"]["breaker"]) == (
        None,
        "ITM-P",
    )
    assert [(o["id"], o["role"], o["backfed"]) for o in ac["ocpds"]] == [("ITM-1", "I1", True)]
    assert [
        (m["id"], m["role"], m.get("plant_disconnect", False)) for m in ac["main_breakers"]
    ] == [("ITM-P", "I2", False)]
    # the template's AC SPD sat at the load centre: now at the service breaker
    assert {s["at"] for s in ac["spds"]} == {"ITM-P"}
    assert [(c["id"], c["to"]) for c in spec["circuits"]][-1] == ("C-INV", "ITM-1")
    assert result.mapped["ITM-P"] == "ac_bos.main_breakers[0]"
    draft = evaluate_draft(spec)
    assert draft.complete, [i.to_dict() for i in draft.issues]
    assert draft.ok
    report = validate_pv_design(spec)
    assert report.ok
    assert not [f for f in report.findings if f.rule_id in ("PCC-002", "DIS-005")]
    assert evaluate_choices(spec, registry=registry).updates == ()


def test_one_breaker_as_i1_and_i2_warns_dis_005(sized, template, registry) -> None:
    result = graph_to_spec(_graph(sized, registry, ["ITM-1"]), template, registry=registry)
    assert result.ok, _codes(result)
    ac = result.spec["ac_bos"]
    assert ac["ocpds"] == []
    assert [(m["id"], m["role"], m["plant_disconnect"]) for m in ac["main_breakers"]] == [
        ("ITM-1", "I2", True)
    ]
    assert ac["point_of_connection"]["breaker"] == "ITM-1"
    # the inverter breaker's own values (1.25 x Iac) are filled on the service breaker
    filled = {f.path: f.value for f in result.filled}
    assert filled["ac_bos.main_breakers[0].rating_a"] == ac["main_breakers"][0]["rating_a"]
    assert filled["ac_bos.main_breakers[0].bidirectional"] is True
    assert [(c["id"], c["to"]) for c in result.spec["circuits"]][-1] == ("C-INV", "ITM-1")
    report = validate_pv_design(result.spec)
    assert report.ok
    warnings = [f for f in report.findings if f.rule_id == "DIS-005"]
    assert [(f.subject, f.severity.value) for f in warnings] == [("ITM-1", "W")]
    assert "I1 e I2" in warnings[0].message_es
    assert not [f for f in report.findings if f.rule_id in ("DIS-003", "DIS-004")]


def test_an_undrawn_bidirectional_is_assumed_and_listed(sized, template, registry) -> None:
    template["ac_bos"]["main_breakers"] = []
    graph = _graph(sized, registry, ["ITM-1", "ITM-P"])
    next(n for n in graph["nodes"] if n["id"] == "ITM-P")["values"] = {"rating_a": 100}
    result = graph_to_spec(graph, template, registry=registry)
    assert result.ok, _codes(result)
    main = result.spec["ac_bos"]["main_breakers"][0]
    assert (main["id"], main["bidirectional"], main["rating_a"]) == ("ITM-P", True, 100)
    filled = [f for f in result.filled if f.path == "ac_bos.main_breakers[0].bidirectional"]
    assert len(filled) == 1
    assert "confírmelo" in filled[0].reason_es


def test_a_drawn_one_way_service_breaker_violates_dis_004(sized, template, registry) -> None:
    graph = _graph(sized, registry, ["ITM-1", "ITM-P"])
    next(n for n in graph["nodes"] if n["id"] == "ITM-P")["values"] = {"bidirectional": False}
    result = graph_to_spec(graph, template, registry=registry)
    assert not [f for f in result.filled if f.path.endswith(".bidirectional")]
    report = validate_pv_design(result.spec)
    assert [f.rule_id for f in report.findings if f.severity.value == "E"] == ["DIS-004"]


def test_without_meter_or_grid_the_breaker_is_flagged(sized, template, registry) -> None:
    graph = _graph(sized, registry, ["ITM-1"])
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in ("M-1", "CFE")]
    graph["cables"] = [c for c in graph["cables"] if not c["to"].startswith(("M-1", "CFE"))]
    result = graph_to_spec(graph, template, registry=registry)
    assert ("topology", ("ITM-1",)) in _codes(result)
    assert any("ni al medidor" in i.message_es for i in result.issues)


def test_a_drawn_load_centre_keeps_todays_design(sized, template, registry) -> None:
    graph = _graph(sized, registry, ["ITM-1", "ITM-P"])
    graph["nodes"].append({"id": "CC-1", "type": "load_centre"})
    graph["cables"] = [c for c in graph["cables"] if c["from"] != "ITM-1.OUT"] + [
        {"from": "ITM-1.OUT", "to": "CC-1.LEFT"},
        {"from": "CC-1.RIGHT", "to": "ITM-P.IN"},
    ]
    result = graph_to_spec(graph, template, registry=registry)
    assert result.ok, _codes(result)
    ac = result.spec["ac_bos"]
    assert result.spec["schema_version"] == "0.1.0"
    assert ac["point_of_connection"]["panel"] == "CC-1"
    assert ac["panels"][0]["main_ocpd"] == "ITM-P"


# --- Round trip ------------------------------------------------------------------------------


@pytest.mark.parametrize("chain", [["ITM-1", "ITM-P"], ["ITM-1"]], ids=["I1 and I2", "one ITM"])
def test_spec_to_graph_round_trips_without_a_load_centre(sized, template, registry, chain) -> None:
    spec = graph_to_spec(_graph(sized, registry, chain), template, registry=registry).spec
    exported = spec_to_graph(spec, registry=registry)
    assert exported.issues == ()
    assert not [n for n in exported.graph["nodes"] if n["type"] == "load_centre"]
    again = graph_to_spec(exported.graph, exported.template, registry=registry)
    assert again.ok, _codes(again)
    assert again.spec == spec


# --- The model -------------------------------------------------------------------------------


def _no_panel_spec(sized, template, registry) -> dict[str, Any]:
    return graph_to_spec(
        _graph(sized, registry, ["ITM-1", "ITM-P"]), template, registry=registry
    ).spec


def test_leaving_out_the_load_centre_needs_schema_0_3_0(sized, template, registry) -> None:
    spec = _no_panel_spec(sized, template, registry)
    spec["schema_version"] = "0.1.0"
    with pytest.raises(ValidationError) as error:
        parse_spec(spec)
    text = str(error.value)
    assert "ac_bos.panels needs at least one load centre" in text
    assert "point_of_connection.panel is required" in text


def test_without_a_load_centre_the_point_of_connection_is_i2(sized, template, registry) -> None:
    spec = _no_panel_spec(sized, template, registry)
    spec["ac_bos"]["point_of_connection"]["breaker"] = "ITM-1"
    with pytest.raises(ValidationError, match="must be the service breaker I2"):
        parse_spec(spec)


def test_plant_disconnect_is_only_for_the_service_breaker(sized, template, registry) -> None:
    spec = _no_panel_spec(sized, template, registry)
    spec["ac_bos"]["main_breakers"][0].update(role="other", plant_disconnect=True)
    with pytest.raises(ValidationError, match="plant_disconnect is for the service breaker I2"):
        parse_spec(spec)


# --- Drawing and capabilities ----------------------------------------------------------------


def test_the_a3_sheet_refuses_it_for_now(sized, template, registry) -> None:
    spec = parse_spec(_no_panel_spec(sized, template, registry))
    with pytest.raises(LayoutError, match="sin centro de carga"):
        layout_diagram(spec, derive(spec))


def test_capabilities_list_it() -> None:
    statuses = {c["id"]: c["status"] for c in capabilities()["capabilities"]}
    assert statuses["service.no_load_centre"] == "supported"
    assert statuses["drawing.no_load_centre"] == "pending"
