"""Microinverter drawings in pvsld.graph/1 (Stage 3.9.4): modules, branches, trunk, combiner."""

import copy
import itertools
from pathlib import Path
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.draft import evaluate_draft
from pvsld.graph import graph_to_spec
from pvsld.resources import data_path
from pvsld.sizing import size_pv_system
from pvsld.sizing.choices import evaluate_choices
from pvsld.sizing.io import load_request

MODULE = "JINKO-JKM650N-66HL4M-BDV"
MICRO = "GROWATT-NEO-2500M-X2"
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
    template["ac_bos"]["spds"] = []
    return template


def _units(prefix: str, count: int, first: int = 1) -> tuple[list[dict], list[dict]]:
    """``count`` NEO 2500 units, each with four modules on A-D through connectors."""
    nodes: list[dict] = []
    cables: list[dict] = []
    for n in range(first, first + count):
        unit = f"MI{n}"
        nodes.append({"id": unit, "type": "microinverter", "values": {"model": MICRO}})
        for letter in "ABCD":
            module = f"M{n}{letter}"
            nodes.append({"id": module, "type": "pv_module", "values": {"module": MODULE}})
            nodes.append({"id": f"CX-{module}", "type": "pv_connector"})
            cables += [
                {"from": f"{module}.NEG", "to": f"CX-{module}.IN"},
                {"from": f"CX-{module}.OUT", "to": f"{unit}.{letter}"},
            ]
    return nodes, cables


def _trunk(branch: str, units: list[str], breaker: str, spacing: float = 4.5) -> tuple:
    """Taps on junctions J<branch>-<k>, a junction box at the far end, home run of 12 m."""
    nodes = [{"id": f"JB-{branch}", "type": "ac_junction_box"}]
    cables = []
    taps = [f"J{branch}-{k}" for k in range(1, len(units) + 1)]
    for tap, unit in zip(taps, units, strict=True):
        nodes.append({"id": tap, "type": "junction"})
        cables.append({"from": f"{unit}.AC", "to": f"{tap}.N", "length_m": 0.5})
    for left, right in itertools.pairwise(taps):
        cables.append({"from": f"{left}.E", "to": f"{right}.W", "length_m": spacing})
    cables.append({"from": f"{taps[0]}.W", "to": f"JB-{branch}.OUT", "length_m": 1})
    cables.append({"from": f"JB-{branch}.IN", "to": f"{breaker}.IN", "length_m": 12})
    return nodes, cables


SERVICE = [
    {"id": "CC-1", "type": "load_centre"},
    {"id": "ITM-P", "type": "ac_breaker"},
    {"id": "M-1", "type": "meter"},
    {"id": "CFE", "type": "grid"},
]
SERVICE_CABLES = [
    {"from": "CC-1.RIGHT", "to": "ITM-P.IN"},
    {"from": "ITM-P.OUT", "to": "M-1.IN"},
    {"from": "M-1.OUT", "to": "CFE.IN"},
]


@pytest.fixture
def graph() -> dict[str, Any]:
    """Two NEO 2500 units on one branch, breaker ITM-R1 in the load centre."""
    nodes, cables = _units("MI", 2)
    trunk_nodes, trunk_cables = _trunk("R1", ["MI1", "MI2"], "ITM-R1")
    return {
        "format": "pvsld.graph/1",
        "nodes": [*nodes, *trunk_nodes, {"id": "ITM-R1", "type": "ac_breaker"}, *SERVICE],
        "cables": [
            *cables,
            *trunk_cables,
            {"from": "ITM-R1.OUT", "to": "CC-1.LEFT"},
            *SERVICE_CABLES,
        ],
    }


def _codes(result) -> list[tuple[str, tuple[str, ...]]]:
    return [(i.code, i.node_ids) for i in result.issues]


def test_a_micro_drawing_becomes_a_valid_spec(graph, template, registry) -> None:
    result = graph_to_spec(graph, template, registry=registry)
    assert result.ok, _codes(result)
    spec = result.spec
    assert spec["schema_version"] == "0.2.0"
    assert spec["strings"] == []
    (inverter,) = spec["inverters"]
    assert (inverter["id"], inverter["type"], inverter["qty"]) == ("INVM1", "micro", 2)
    (branch,) = spec["ac_branches"]
    assert branch["id"] == "R1"
    assert branch["ocpd"] == "ITM-R1"
    assert [u["id"] for u in branch["microinverters"]] == ["MI1", "MI2"]
    assert branch["microinverters"][0]["inputs"] == dict.fromkeys("ABCD", "MOD1")
    assert branch["spacing_m"] == 4.5
    assert branch["trunk_size"] == "10 AWG"  # two NEO 2500 do not fit a 12 AWG branch
    itm = next(o for o in spec["ac_bos"]["ocpds"] if o["id"] == "ITM-R1")
    assert (itm["role"], itm["backfed"], itm["at"], itm["rating_a"]) == ("I1", True, "CC-1", 30)
    assert spec["ac_bos"]["point_of_connection"]["breaker"] == "ITM-R1"
    (circuit,) = spec["circuits"]
    assert (circuit["id"], circuit["from"], circuit["to"]) == ("C-R1", "R1", "ITM-R1")
    assert circuit["length_m"] == 12
    assert result.mapped["MI2"] == "ac_branches[0].microinverters[1]"
    assert result.mapped["M2C"] == "ac_branches[0].microinverters[1].inputs"
    assert result.mapped["JB-R1"] == "wiring"
    draft = evaluate_draft(spec)
    assert draft.issues == (), draft.issues
    assert [f.rule_id for f in draft.findings if f.severity == "E"] == []
    assert evaluate_choices(spec).skipped_es is None


def test_unit_order_follows_the_trunk_from_the_breaker(graph, template, registry) -> None:
    # Swap the taps: MI2 now sits next to the junction box.
    for cable in graph["cables"]:
        if cable["from"] == "MI1.AC":
            cable["from"] = "MI2.AC"
        elif cable["from"] == "MI2.AC":
            cable["from"] = "MI1.AC"
    result = graph_to_spec(graph, template, registry=registry)
    units = result.spec["ac_branches"][0]["microinverters"]
    assert [u["id"] for u in units] == ["MI2", "MI1"]


def test_defaults_when_the_drawing_has_no_lengths(graph, template, registry) -> None:
    for cable in graph["cables"]:
        cable.pop("length_m", None)
    result = graph_to_spec(graph, template, registry=registry)
    branch = result.spec["ac_branches"][0]
    assert branch["spacing_m"] == 4.8  # 1.2 m per module, four modules per unit
    assert result.spec["circuits"][0]["length_m"] == 30
    filled = {f.path for f in result.filled}
    assert {"ac_branches[0].spacing_m", "circuits[0].length_m"} <= filled


def test_a_drawn_trunk_size_is_kept(graph, template, registry) -> None:
    for cable in graph["cables"]:
        if cable["from"].startswith("JR1-1.E"):
            cable["size"] = "12 AWG"
    result = graph_to_spec(graph, template, registry=registry)
    assert result.spec["ac_branches"][0]["trunk_size"] == "12 AWG"
    rules = {f.rule_id for f in evaluate_draft(result.spec).findings}
    assert "MIC-011" in rules


def test_two_modules_on_one_input_is_a_topology_issue(graph, template, registry) -> None:
    graph["cables"].append({"from": "M1B.POS", "to": "MI1.A"})
    result = graph_to_spec(graph, template, registry=registry)
    assert ("topology", ("MI1", "M1B")) in _codes(result) or any(
        i.code == "topology" and "M1B" in i.node_ids for i in result.issues
    )


def test_input_beyond_the_model_is_reported(graph, template, registry) -> None:
    graph["nodes"].append({"id": "MX", "type": "pv_module", "values": {"module": MODULE}})
    graph["cables"].append({"from": "MX.NEG", "to": "MI1.E"})
    result = graph_to_spec(graph, template, registry=registry)
    assert any(i.code == "unknown_port" and "MX" in i.node_ids for i in result.issues)


def test_two_branches_in_an_ac_combiner(template, registry) -> None:
    nodes, cables = _units("MI", 3)
    r1_nodes, r1_cables = _trunk("R1", ["MI1", "MI2"], "ITM-R1")
    r2_nodes, r2_cables = _trunk("R2", ["MI3"], "ITM-R2")
    combiner = {
        "id": "CCA-1",
        "type": "ac_combiner",
        "children": [
            {"id": "ITM-R1", "type": "ac_breaker"},
            {"id": "ITM-R2", "type": "ac_breaker"},
            {"id": "BUS", "type": "junction"},
            {
                "id": "DPS-CA1",
                "type": "ac_spd",
                "values": {"spd_type": "T2", "uc_v": 275, "in_ka": 20},
            },
        ],
    }
    graph = {
        "format": "pvsld.graph/1",
        "nodes": [
            *nodes,
            *r1_nodes,
            *r2_nodes,
            combiner,
            {"id": "ITM-1", "type": "ac_breaker"},
            *SERVICE,
        ],
        "cables": [
            *cables,
            *r1_cables,
            *r2_cables,
            {"from": "ITM-R1.OUT", "to": "BUS.W"},
            {"from": "ITM-R2.OUT", "to": "BUS.N"},
            {"from": "BUS.S", "to": "DPS-CA1.L"},
            {"from": "BUS.E", "to": "ITM-1.IN", "length_m": 8},
            {"from": "ITM-1.OUT", "to": "CC-1.LEFT"},
            *SERVICE_CABLES,
        ],
    }
    result = graph_to_spec(graph, template, registry=registry)
    assert result.ok, _codes(result)
    spec = result.spec
    assert spec["ac_combiner"] == {"id": "CCA-1", "main_ocpd": "ITM-1", "spd": "DPS-CA1"}
    ocpds = {o["id"]: o for o in spec["ac_bos"]["ocpds"]}
    assert (ocpds["ITM-R1"]["at"], ocpds["ITM-R1"]["role"]) == ("CCA-1", "other")
    assert ocpds["ITM-R2"]["rating_a"] == 15
    assert (ocpds["ITM-1"]["at"], ocpds["ITM-1"]["rating_a"]) == ("CC-1", 45)
    assert [b["id"] for b in spec["ac_branches"]] == ["R1", "R2"]
    circuits = {c["id"]: c for c in spec["circuits"]}
    assert circuits["C-CCA"]["from"] == "CCA-1"
    assert circuits["C-CCA"]["length_m"] == 8
    assert result.mapped["CCA-1"] == "ac_combiner"
    assert result.mapped["DPS-CA1"] == "ac_bos.spds[0]"
    draft = evaluate_draft(spec)
    assert draft.issues == (), draft.issues
    assert [f.rule_id for f in draft.findings if f.severity == "E"] == []
