"""Several string inverters on one shared breaker (Stage 3.12.1, owner approval 2026-11-13).

Any number of string inverters, of equal or different models; their outputs join (at a junction
or AC junction box) and reach one breaker rated 1.25 x the sum of their currents. Each inverter's
run must be protected by that breaker (CON-003), like the owner's 16-module example: two 5 kW
inverters, 6 AWG runs and a 60 A breaker. Each inverter has its own DC box.
"""

import copy
from pathlib import Path
from typing import Any

import pytest

from pvsld.capabilities import capabilities
from pvsld.catalogue import ComponentRegistry
from pvsld.core.calc import derive
from pvsld.core.draft import evaluate_draft
from pvsld.core.model import parse_spec
from pvsld.core.validation import validate_pv_design
from pvsld.graph import graph_to_spec
from pvsld.graph_export import spec_to_graph
from pvsld.resources import data_path
from pvsld.service import layout_diagram
from pvsld.sizing import size_pv_system
from pvsld.sizing.choices import evaluate_choices
from pvsld.sizing.dcbox import apply_dc_box, dc_box
from pvsld.sizing.io import load_request

MODULE = "JINKO-JKM650N-66HL4M-BDV"
GROWATT_5K = "GROWATT-MIN-5000TL-X2"
GROWATT_3K = "GROWATT-MIN-3000TL-X2"
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


@pytest.fixture
def template(registry: ComponentRegistry) -> dict[str, Any]:
    request = load_request(Path(data_path("examples/sizing_jinko_growatt.yaml")))
    sized = size_pv_system(request, registry).candidates[0].spec
    template = copy.deepcopy({key: sized[key] for key in TEMPLATE_KEYS})
    template["ac_bos"]["ocpds"] = []
    return template


def _graph(models: list[str], *, panel: bool = True, junction: bool = True) -> dict[str, Any]:
    """One string of 8 modules on each inverter; the outputs join at J1 (or land on ITM-1)."""
    nodes: list[dict[str, Any]] = []
    cables: list[dict[str, Any]] = []
    for number, model in enumerate(models, start=1):
        nodes += [
            {"id": f"S{number}", "type": "pv_string", "values": {"module": MODULE, "n_series": 8}},
            {"id": f"INV{number}", "type": "inverter", "values": {"model": model}},
        ]
        cables += [
            {"from": f"S{number}.OUT", "to": f"INV{number}.A", "length_m": 5},
            {
                "from": f"INV{number}.AC",
                "to": "J1.W" if junction else "ITM-1.IN",
                "length_m": 5 if number == 1 else 1,
            },
        ]
    if junction:
        nodes.append({"id": "J1", "type": "junction"})
        cables.append({"from": "J1.E", "to": "ITM-1.IN", "length_m": 10})
    nodes += [{"id": "ITM-1", "type": "ac_breaker"}, {"id": "M-1", "type": "meter"}]
    nodes.append({"id": "CFE", "type": "grid"})
    if panel:
        nodes += [{"id": "CC-1", "type": "load_centre"}, {"id": "ITM-P", "type": "ac_breaker"}]
        cables += [
            {"from": "ITM-1.OUT", "to": "CC-1.LEFT"},
            {"from": "CC-1.RIGHT", "to": "ITM-P.IN"},
            {"from": "ITM-P.OUT", "to": "M-1.IN"},
        ]
    else:  # the owner's example: one breaker as I1 and I2, no load centre
        cables.append({"from": "ITM-1.OUT", "to": "M-1.IN"})
    cables.append({"from": "M-1.OUT", "to": "CFE.IN"})
    return {"format": "pvsld.graph/1", "nodes": nodes, "cables": cables}


def _codes(result) -> list[tuple[str, tuple[str, ...]]]:
    return [(i.code, i.node_ids) for i in result.issues]


def _circuits(spec: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    return [(c["id"], c["from"], c["to"], c["conductors"]["size"]) for c in spec["circuits"]]


# --- The owner's example ---------------------------------------------------------------------


def test_two_inverters_share_one_breaker_sized_for_their_sum(template, registry) -> None:
    result = graph_to_spec(
        _graph([GROWATT_5K, GROWATT_5K], panel=False), template, registry=registry
    )
    assert result.ok, _codes(result)
    spec = result.spec
    assert [i["id"] for i in spec["inverters"]] == ["INV1", "INV2"]
    assert [(s["id"], s["inverter"]) for s in spec["strings"]] == [("S1", "INV1"), ("S2", "INV2")]
    # 1.25 x (22.7 A + 22.7 A) = 56.75 A -> 60 A, one breaker as I1 and I2
    main = spec["ac_bos"]["main_breakers"]
    assert [(m["id"], m["rating_a"], m["plant_disconnect"]) for m in main] == [
        ("ITM-1", 60.0, True)
    ]
    assert spec["ac_bos"]["junction_boxes"] == [{"id": "J1"}]
    # each inverter's run is protected by the 60 A breaker: 6 AWG, as in the owner's drawing
    assert _circuits(spec) == [
        ("C-S1", "S1", "INV1.A", "10 AWG"),
        ("C-S2", "S2", "INV2.A", "10 AWG"),
        ("C-INV1", "INV1.ac", "J1", "6 AWG"),
        ("C-INV2", "INV2.ac", "J1", "6 AWG"),
        ("C-INVT", "J1", "ITM-1", "6 AWG"),
    ]
    assert [c["length_m"] for c in spec["circuits"][2:]] == [5, 1, 10]
    assert [d["id"] for d in spec["dc_bos"]["disconnects"]] == ["DCD-1", "DCD-2"]
    draft = evaluate_draft(spec)
    assert draft.complete, [i.to_dict() for i in draft.issues]
    report = validate_pv_design(spec)
    assert report.ok
    assert [f.rule_id for f in report.findings if f.severity.value != "I"] == ["DIS-005"]
    assert evaluate_choices(spec, registry=registry).updates == ()


def test_the_shared_breaker_feeds_the_busbar_rule(template, registry) -> None:
    result = graph_to_spec(_graph([GROWATT_5K, GROWATT_5K]), template, registry=registry)
    assert result.ok, _codes(result)
    ac = result.spec["ac_bos"]
    assert [(o["id"], o["role"], o["rating_a"]) for o in ac["ocpds"]] == [("ITM-1", "I1", 60.0)]
    filled = {f.path: f.reason_es for f in result.filled}
    assert "ITM compartido de los 2 inversores" in filled["ac_bos.ocpds[0].rating_a"]
    # 100 A main + 60 A > 1.2 x 125 A bus: the engine reports it, as the NOM asks
    report = validate_pv_design(result.spec)
    assert [f.rule_id for f in report.findings if f.severity.value == "E"] == ["PCC-002"]


def test_a_small_breaker_on_a_large_run_is_flagged(template, registry) -> None:
    graph = _graph([GROWATT_5K, GROWATT_5K], panel=False)
    next(n for n in graph["nodes"] if n["id"] == "ITM-1")["values"] = {"rating_a": 60}
    next(c for c in graph["cables"] if c["from"] == "INV1.AC")["size"] = "10 AWG"
    result = graph_to_spec(graph, template, registry=registry)
    report = validate_pv_design(result.spec)
    con = [f for f in report.findings if f.rule_id == "CON-003"]
    assert [f.subject for f in con] == ["C-INV1"]
    assert "ITM-1" in con[0].message_es


def test_mixed_models_are_checked_each_on_its_own(template, registry) -> None:
    result = graph_to_spec(
        _graph([GROWATT_5K, GROWATT_3K], panel=False), template, registry=registry
    )
    assert result.ok, _codes(result)
    spec = result.spec
    assert spec["inverters"][0]["model"] != spec["inverters"][1]["model"]
    current = sum(i["iac_max_a"] for i in spec["inverters"])
    rating = spec["ac_bos"]["main_breakers"][0]["rating_a"]
    assert rating >= 1.25 * current
    report = validate_pv_design(spec)
    # STR-007 runs per inverter: 8 x 650 W on a 3 kW inverter is over its PV limit
    assert {f.subject for f in report.findings if f.rule_id == "STR-007"} == {"INV2"}


def test_inverters_on_different_breakers_are_flagged(template, registry) -> None:
    graph = _graph([GROWATT_5K, GROWATT_5K], panel=False, junction=False)
    graph["nodes"].append({"id": "ITM-2", "type": "ac_breaker"})
    graph["cables"] = [c for c in graph["cables"] if c["from"] != "INV2.AC"] + [
        {"from": "INV2.AC", "to": "ITM-2.IN"},
        {"from": "ITM-2.OUT", "to": "M-1.IN"},
    ]
    result = graph_to_spec(graph, template, registry=registry)
    # A breaker per inverter goes in the load centre (Stage 3.14); without one it is unsupported.
    assert any(
        i.code == "unsupported" and "sin centro de carga" in i.message_es for i in result.issues
    )


def test_runs_landing_on_the_breaker_need_no_junction(template, registry) -> None:
    result = graph_to_spec(
        _graph([GROWATT_5K, GROWATT_5K], panel=False, junction=False), template, registry=registry
    )
    assert result.ok, _codes(result)
    assert result.spec["ac_bos"].get("junction_boxes", []) == []
    assert [c[:3] for c in _circuits(result.spec)][2:] == [
        ("C-INV1", "INV1.ac", "ITM-1"),
        ("C-INV2", "INV2.ac", "ITM-1"),
    ]


# --- One DC box per inverter and the round trip ---------------------------------------------


def test_each_inverter_gets_its_own_dc_box(template, registry) -> None:
    spec = graph_to_spec(_graph([GROWATT_5K, GROWATT_5K]), template, registry=registry).spec
    box = dc_box(spec, registry=registry)
    assert box.built, box.skipped_es
    shared = {c.id: (c.box, c.string_ids) for c in box.children if c.role in ("spd", "switch")}
    assert shared == {
        "DPS-CD1": ("CAJA-CD1", ("S1",)),
        "DCD-CD1": ("CAJA-CD1", ("S1",)),
        "DPS-CD2": ("CAJA-CD2", ("S2",)),
        "DCD-CD2": ("CAJA-CD2", ("S2",)),
    }
    boxed = apply_dc_box(spec, box)
    assert {s["id"]: s["at"] for s in boxed["dc_bos"]["spds"]} == {
        "DPS-CD1": "INV1.dc",
        "DPS-CD2": "INV2.dc",
    }
    assert evaluate_choices(boxed, registry=registry).updates == ()


@pytest.mark.parametrize("panel", [True, False], ids=["load centre", "one breaker"])
def test_spec_to_graph_round_trips_several_inverters(template, registry, panel) -> None:
    spec = graph_to_spec(_graph([GROWATT_5K, GROWATT_5K], panel=panel), template, registry=registry)
    boxed = apply_dc_box(spec.spec, dc_box(spec.spec, registry=registry))
    exported = spec_to_graph(boxed, registry=registry)
    assert exported.issues == ()
    boxes = [n["id"] for n in exported.graph["nodes"] if n["type"] == "dc_box"]
    assert boxes == ["CAJA-CD1", "CAJA-CD2"]
    again = graph_to_spec(exported.graph, exported.template, registry=registry)
    assert again.ok, _codes(again)
    assert again.spec == boxed


def test_one_inverter_keeps_todays_names(template, registry) -> None:
    spec = graph_to_spec(
        _graph([GROWATT_5K], panel=True, junction=False), template, registry=registry
    )
    assert [c[0] for c in _circuits(spec.spec)] == ["C-S1", "C-INV"]
    box = dc_box(spec.spec, registry=registry)
    assert {c.box for c in box.children} == {"CAJA-CD"}


# --- Limits ----------------------------------------------------------------------------------


def test_five_strings_on_one_inverter_are_still_unsupported(template, registry) -> None:
    graph = _graph([GROWATT_5K, GROWATT_5K])
    for n in (3, 4, 5, 6):
        graph["nodes"].append(
            {"id": f"S{n}", "type": "pv_string", "values": {"module": MODULE, "n_series": 8}}
        )
        graph["cables"].append({"from": f"S{n}.OUT", "to": "INV1.B"})
    spec = graph_to_spec(graph, template, registry=registry).spec
    issues = [i for i in evaluate_draft(spec).issues if i.category == "unsupported"]
    assert [i.component_ids for i in issues] == [("S1", "S3", "S4", "S5", "S6")]


def test_capabilities_and_the_sheet(template, registry) -> None:
    limits = capabilities()["limits"]
    assert limits["max_inverters"] is None
    assert limits["max_strings_per_inverter"] == 4
    spec = parse_spec(
        graph_to_spec(_graph([GROWATT_5K, GROWATT_5K]), template, registry=registry).spec
    )
    # the A3 sheet draws them since Stage 3.12.3 (tests/test_multi_inverter_sheet.py)
    ids = {i.comp_id for i in layout_diagram(spec, derive(spec)).instances}
    assert {"INV1", "INV2", "J1"} <= ids
