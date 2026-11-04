"""The per-string DC box (owner request 2026-11-04) and the SPD checks of evaluate_choices."""

import copy
from pathlib import Path
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.draft import evaluate_draft
from pvsld.graph import graph_to_spec
from pvsld.resources import data_path
from pvsld.sizing import size_pv_system
from pvsld.sizing.choices import evaluate_choices
from pvsld.sizing.dcbox import apply_dc_box, box_layout, dc_box
from pvsld.sizing.io import load_request


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


@pytest.fixture(scope="module")
def base(registry) -> dict[str, Any]:
    request = load_request(Path(data_path("examples/sizing_jinko_growatt.yaml")))
    spec = size_pv_system(request, registry).candidates[0].spec
    for string in spec["strings"]:
        string["n_series"] = 5
    return spec


@pytest.fixture
def per_string(base, registry) -> dict[str, Any]:
    spec = copy.deepcopy(base)
    return apply_dc_box(spec, dc_box(spec, layout="per_string", registry=registry))


def _ids(box) -> list[str]:
    return [c.id for c in box.children]


def test_each_string_has_its_own_switch_and_spd(base, registry) -> None:
    box = dc_box(base, layout="per_string", registry=registry)
    assert {"DCD-S1", "DCD-S2", "DPS-S1", "DPS-S2"} <= set(_ids(box))
    assert "DCD-CD1" not in _ids(box)
    assert "DPS-CD1" not in _ids(box)
    switch = box.child("DCD-S1")
    assert switch.block == "PVSLD_DC_DISCONNECT"
    assert switch.ports == ("IN", "OUT")
    assert switch.string_ids == ("S1",)
    assert switch.requirements["poles_min"] == 2
    assert "2 polos por cadena" in switch.reason_es
    spd = box.child("DPS-S1")
    assert spd.block == "PVSLD_SPD"
    assert spd.string_ids == ("S1",)
    # one string's current, not both: 1.25 x 18.17 A
    assert spd.requirements["iscpv_min_a"] == pytest.approx(1.25 * 18.17, abs=0.1)
    links = {(link.from_port, link.to_port) for link in box.links}
    assert ("FUS-S1.OUT", "DCD-S1.IN") in links
    assert ("DCD-S1.OUT", "XS-S1.L") in links
    assert ("FUS-S1.OUT", "DPS-S1.L") in links
    assert not any("S2" in a and "S1" in b for a, b in links)  # nothing crosses strings


def test_per_string_spec_validates_and_keeps_its_layout(per_string, registry) -> None:
    assert box_layout(per_string) == "per_string"
    assert evaluate_draft(per_string).ok
    again = dc_box(per_string, registry=registry)  # "auto" keeps the drawn layout
    assert "DCD-S1" in _ids(again)
    assert evaluate_choices(per_string, registry=registry).updates == ()


def test_switching_layouts_replaces_the_parts(per_string, registry) -> None:
    shared = apply_dc_box(per_string, dc_box(per_string, layout="shared", registry=registry))
    ids = [d["id"] for d in shared["dc_bos"]["disconnects"]]
    assert "DCD-CD1" in ids
    assert not any(i.startswith("DCD-S") for i in ids)
    assert [s["id"] for s in shared["dc_bos"]["spds"]] == ["DPS-CD1"]
    assert "DCD-1" in ids  # the inverter's integrated switch stays
    assert box_layout(shared) == "shared"


def test_locks_work_per_string(per_string, registry) -> None:
    switch = next(d for d in per_string["dc_bos"]["disconnects"] if d["id"] == "DCD-S1")
    switch["ie_a"] = 10
    report = evaluate_choices(per_string, ["DCD-S1.ie_a"], registry=registry)
    check = next(c for c in report.checks if c.target == "DCD-S1.ie_a")
    assert (check.ok, check.rule_id, check.locked, check.suggested) == (
        False,
        "DIS-002",
        True,
        32.0,
    )


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [("ucpv_v", 150, "SPD-002"), ("in_ka", 3, "SPD-003"), ("iscpv_a", 10, "SPD-004")],
)
def test_spd_values_are_checked(per_string, registry, field, value, rule) -> None:
    spd = next(s for s in per_string["dc_bos"]["spds"] if s["id"] == "DPS-S2")
    spd[field] = value
    report = evaluate_choices(per_string, registry=registry)
    check = next(c for c in report.checks if c.target == f"DPS-S2.{field}")
    assert (check.ok, check.rule_id) == (False, rule)


def test_shared_spd_is_checked_too(base, registry) -> None:
    spec = apply_dc_box(copy.deepcopy(base), dc_box(base, registry=registry))
    spd = next(s for s in spec["dc_bos"]["spds"] if s["id"] == "DPS-CD1")
    spd["model"] = "CLAMPER-SOLAR-150V-18KA"  # 150 V < 269 V
    report = evaluate_choices(spec, registry=registry)
    check = next(c for c in report.checks if c.target == "DPS-CD1.model")
    assert (check.ok, check.rule_id) == (False, "SPD-002")
    assert any(u.target == "DPS-CD1.model" for u in report.updates)


def test_the_drawing_maps_a_per_string_box(per_string, registry) -> None:
    box = dc_box(per_string, registry=registry)
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
    template = {
        key: copy.deepcopy(per_string[key])
        for key in ("project", "utility", "ac_bos", "grounding", "storage", "title_block", "layout")
    }
    template["ac_bos"]["ocpds"] = []
    graph = {
        "nodes": [
            {
                "id": "S1",
                "type": "pv_string",
                "values": {"module": "JINKO-JKM650N-66HL4M-BDV", "n_series": 5},
            },
            {
                "id": "S2",
                "type": "pv_string",
                "values": {"module": "JINKO-JKM650N-66HL4M-BDV", "n_series": 5},
            },
            {"id": "CAJA-CD", "type": "dc_box", "children": children},
            {"id": "INV1", "type": "inverter", "values": {"model": "GROWATT-MIN-5000TL-X2"}},
            {"id": "ITM-1", "type": "ac_breaker"},
            {"id": "CC-1", "type": "load_centre"},
        ],
        "cables": [
            *({"from": link.from_port, "to": link.to_port} for link in box.links),
            {"from": "INV1.AC", "to": "ITM-1.IN"},
            {"from": "ITM-1.OUT", "to": "CC-1.LEFT"},
        ],
    }
    result = graph_to_spec(graph, template, registry=registry)
    assert result.ok, [i.to_dict() for i in result.issues]
    assert {"DCD-S1", "DCD-S2"} <= {d["id"] for d in result.spec["dc_bos"]["disconnects"]}
    assert [s["id"] for s in result.spec["dc_bos"]["spds"]] == ["DPS-S1", "DPS-S2"]
    assert evaluate_draft(result.spec).ok
    # a per-string switch on the other string is reported
    for cable in graph["cables"]:
        if cable["from"] == "FUS-S1.OUT" and cable["to"] == "DCD-S1.IN":
            cable["to"] = "DCD-S2.IN"
        elif cable["from"] == "FUS-S2.OUT" and cable["to"] == "DCD-S2.IN":
            cable["to"] = "DCD-S1.IN"
        elif cable["from"] == "DCD-S1.OUT":
            cable["from"] = "DCD-S2.OUT"
        elif cable["from"] == "DCD-S2.OUT":
            cable["from"] = "DCD-S1.OUT"
    crossed = graph_to_spec(graph, template, registry=registry)
    assert any(i.code == "topology" and "DCD-S1" in i.node_ids for i in crossed.issues)
