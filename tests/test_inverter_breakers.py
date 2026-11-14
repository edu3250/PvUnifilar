"""A breaker per inverter, or an AC combiner of string inverters (Stage 3.14, owner 2026-11-14).

Besides the shared breaker (Stage 3.12): each inverter to its own breaker in the load centre
(each is an I1, owner option a; the busbar rule sums them), or each inverter to its breaker inside
an AC combiner whose main breaker (the I1, CMB-001: 1.25 x the sum of the currents) feeds the load
centre through C-CCA; the AC SPD of the combiner is optional.
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
from pvsld.resources import data_path
from pvsld.service import layout_diagram
from pvsld.sizing import size_pv_system
from pvsld.sizing.choices import evaluate_choices
from pvsld.sizing.dcbox import apply_dc_box, dc_box
from pvsld.sizing.io import load_request

MODULE = "JINKO-JKM650N-66HL4M-BDV"
GROWATT = "GROWATT-MIN-4200TL-X2"
SOLIS = "SOLIS-S5-GC20K-LV"
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
SPD = {"spd_type": "T2", "uc_v": 175, "in_ka": 20}


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def _template(registry, *, bus_a: float = 225, three_phase: bool = False) -> dict[str, Any]:
    request = load_request(Path(data_path("examples/sizing_jinko_growatt.yaml")))
    sized = size_pv_system(request, registry).candidates[0].spec
    template = copy.deepcopy({key: sized[key] for key in TEMPLATE_KEYS})
    template["ac_bos"]["ocpds"] = []
    for panel in template["ac_bos"]["panels"]:
        panel["bus_a"] = bus_a
    if three_phase:
        template["utility"]["system"] = "3F-4H"
        for panel in template["ac_bos"]["panels"]:
            panel["system"] = "3F-4H"
        for main in template["ac_bos"]["main_breakers"]:
            main.update(poles=3, rating_a=125)
            main.pop("model", None)
    return template


def _base(count: int, model: str, n_series: int, ports: tuple[str, ...]):
    nodes: list[dict[str, Any]] = []
    cables: list[dict[str, Any]] = []
    string = 0
    for k in range(1, count + 1):
        nodes.append({"id": f"INV{k}", "type": "inverter", "values": {"model": model}})
        for port in ports:
            string += 1
            nodes.append(
                {
                    "id": f"S{string}",
                    "type": "pv_string",
                    "values": {"module": MODULE, "n_series": n_series},
                }
            )
            cables.append({"from": f"S{string}.OUT", "to": f"INV{k}.{port}", "length_m": 20})
    nodes += [
        {"id": "CC-1", "type": "load_centre"},
        {"id": "ITM-P", "type": "ac_breaker"},
        {"id": "M-1", "type": "meter"},
        {"id": "CFE", "type": "grid"},
    ]
    cables += [
        {"from": "CC-1.RIGHT", "to": "ITM-P.IN"},
        {"from": "ITM-P.OUT", "to": "M-1.IN"},
        {"from": "M-1.OUT", "to": "CFE.IN"},
    ]
    return nodes, cables


def _per_inverter(count=2, model=GROWATT, n_series=8, ports=("A",)) -> dict[str, Any]:
    nodes, cables = _base(count, model, n_series, ports)
    for k in range(1, count + 1):
        nodes.append({"id": f"ITM-{k}", "type": "ac_breaker"})
        cables += [
            {"from": f"INV{k}.AC", "to": f"ITM-{k}.IN", "length_m": 12},
            {"from": f"ITM-{k}.OUT", "to": "CC-1.LEFT"},
        ]
    return {"format": "pvsld.graph/1", "nodes": nodes, "cables": cables}


def _combiner(count=2, model=GROWATT, n_series=8, ports=("A",), spd=True) -> dict[str, Any]:
    nodes, cables = _base(count, model, n_series, ports)
    children: list[dict[str, Any]] = [
        {"id": f"ITM-INV{k}", "type": "ac_breaker"} for k in range(1, count + 1)
    ]
    children += [{"id": f"JB{k}", "type": "junction"} for k in range(1, count + 1)]
    if spd:
        children.append({"id": "DPS-CA2", "type": "ac_spd", "values": SPD})
    nodes += [
        {"id": "CCA-1", "type": "ac_combiner", "children": children},
        {"id": "ITM-1", "type": "ac_breaker"},
    ]
    for k in range(1, count + 1):
        cables += [
            {"from": f"INV{k}.AC", "to": f"ITM-INV{k}.IN", "length_m": 12},
            {"from": f"ITM-INV{k}.OUT", "to": f"JB{k}.W"},
        ]
        if k > 1:
            cables.append({"from": f"JB{k - 1}.S", "to": f"JB{k}.N"})
    if spd:
        cables.append({"from": f"JB{count}.S", "to": "DPS-CA2.L"})
    cables += [
        {"from": "JB1.E", "to": "ITM-1.IN", "length_m": 8},
        {"from": "ITM-1.OUT", "to": "CC-1.LEFT"},
    ]
    return {"format": "pvsld.graph/1", "nodes": nodes, "cables": cables}


def _codes(result) -> list[tuple[str, tuple[str, ...]]]:
    return [(i.code, i.node_ids) for i in result.issues]


def _ocpds(spec: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(o["id"], o["role"], o["rating_a"], o.get("at")) for o in spec["ac_bos"]["ocpds"]]


# --- A breaker per inverter -----------------------------------------------------------------


def test_each_inverter_to_its_own_breaker(registry) -> None:
    result = graph_to_spec(_per_inverter(), _template(registry), registry=registry)
    assert result.ok, _codes(result)
    spec = result.spec
    # 1.25 x 19 A = 23.75 A -> 25 A each; every inverter breaker is an I1 (owner option a)
    assert _ocpds(spec) == [("ITM-1", "I1", 25.0, "CC-1"), ("ITM-2", "I1", 25.0, "CC-1")]
    assert [(c["id"], c["to"]) for c in spec["circuits"] if c["kind"] == "inverter_output"] == [
        ("C-INV1", "ITM-1"),
        ("C-INV2", "ITM-2"),
    ]
    assert spec["ac_bos"].get("junction_boxes", []) == []
    assert evaluate_draft(spec).complete
    assert validate_pv_design(spec).ok
    boxed = apply_dc_box(spec, dc_box(spec, registry=registry))
    assert evaluate_choices(boxed, registry=registry).updates == ()


def test_the_busbar_rule_sums_every_inverter_breaker(registry) -> None:
    # 100 A main + 25 A + 25 A > 1.2 x 100 A
    result = graph_to_spec(_per_inverter(), _template(registry, bus_a=100), registry=registry)
    report = validate_pv_design(result.spec)
    pcc = [f for f in report.findings if f.rule_id == "PCC-002"]
    assert pcc
    assert "ITM-1" in pcc[0].message_es
    assert "ITM-2" in pcc[0].message_es


def test_a_small_inverter_breaker_is_flagged(registry) -> None:
    graph = _per_inverter()
    next(n for n in graph["nodes"] if n["id"] == "ITM-2")["values"] = {
        "rating_a": 20,
        "kaic_ka": 10,
    }
    spec = graph_to_spec(graph, _template(registry), registry=registry).spec
    boxed = apply_dc_box(spec, dc_box(spec, registry=registry))
    failing = evaluate_choices(boxed, registry=registry).failing
    assert any(c.component_id == "ITM-2" and c.rule_id == "OCP-005" for c in failing)


# --- The AC combiner of inverters ------------------------------------------------------------


@pytest.mark.parametrize(("count", "spd"), [(2, True), (3, False)])
def test_the_inverter_combiner(registry, count, spd) -> None:
    result = graph_to_spec(_combiner(count, spd=spd), _template(registry), registry=registry)
    assert result.ok, _codes(result)
    spec = result.spec
    main = 50.0 if count == 2 else 80.0  # 1.25 x (n x 19 A), next standard rating
    assert _ocpds(spec) == [
        *((f"ITM-INV{k}", "other", 25.0, "CCA-1") for k in range(1, count + 1)),
        ("ITM-1", "I1", main, "CC-1"),
    ]
    assert spec["ac_combiner"] == {
        "id": "CCA-1",
        "main_ocpd": "ITM-1",
        "spd": "DPS-CA2" if spd else None,
    }
    assert spec["ac_bos"]["point_of_connection"]["breaker"] == "ITM-1"
    runs = [
        (c["id"], c["from"], c["to"]) for c in spec["circuits"] if c["kind"] == "inverter_output"
    ]
    assert runs[-1] == ("C-CCA", "CCA-1", "ITM-1")
    assert len(runs) == count + 1
    report = validate_pv_design(spec)
    assert report.ok
    assert not [f for f in report.findings if f.rule_id.startswith("MIC")]
    values = next(c for c in report.derived.circuits if c.circuit_id == "C-CCA")
    assert values.i_max_a == pytest.approx(count * 19.0)


def test_a_small_combiner_main_violates_cmb_001(registry) -> None:
    graph = _combiner()
    next(n for n in graph["nodes"] if n["id"] == "ITM-1")["values"] = {
        "rating_a": 40,
        "kaic_ka": 10,
    }
    spec = graph_to_spec(graph, _template(registry), registry=registry).spec
    findings = [f for f in validate_pv_design(spec).findings if f.rule_id == "CMB-001"]
    assert [(f.subject, f.severity.value) for f in findings] == [("ITM-1", "E")]


def test_three_phase_inverters_in_a_combiner(registry) -> None:
    graph = _combiner(2, model=SOLIS, n_series=12, ports=("A", "B", "C"), spd=False)
    result = graph_to_spec(
        graph, _template(registry, bus_a=400, three_phase=True), registry=registry
    )
    assert result.ok, _codes(result)
    ocpds = {o["id"]: o for o in result.spec["ac_bos"]["ocpds"]}
    assert (ocpds["ITM-INV1"]["poles"], ocpds["ITM-INV1"]["rating_a"]) == (3, 80)
    assert (ocpds["ITM-1"]["poles"], ocpds["ITM-1"]["rating_a"]) == (3, 150)
    cca = next(c for c in result.spec["circuits"] if c["id"] == "C-CCA")
    assert cca["conductors"]["qty"] == 3


def test_mixed_breakers_are_flagged(registry) -> None:
    graph = _combiner(2)
    box = next(n for n in graph["nodes"] if n["id"] == "CCA-1")
    box["children"] = [c for c in box["children"] if c["id"] != "ITM-INV2"]
    graph["nodes"].append({"id": "ITM-INV2", "type": "ac_breaker"})
    result = graph_to_spec(graph, _template(registry), registry=registry)
    assert any(
        i.code == "unsupported" and "tablero combinador" in i.message_es for i in result.issues
    )


# --- Round trip, sheet, capabilities ---------------------------------------------------------


@pytest.mark.parametrize("make", [_per_inverter, _combiner], ids=["per inverter", "combiner"])
def test_the_round_trip(registry, make) -> None:
    spec = graph_to_spec(make(), _template(registry), registry=registry).spec
    boxed = apply_dc_box(spec, dc_box(spec, registry=registry))
    exported = spec_to_graph(boxed, registry=registry)
    assert exported.issues == ()
    again = graph_to_spec(exported.graph, exported.template, registry=registry)
    assert again.ok, _codes(again)
    assert again.spec == boxed


def test_the_sheet_refuses_them_for_now(registry) -> None:
    spec = parse_spec(graph_to_spec(_combiner(), _template(registry), registry=registry).spec)
    with pytest.raises(LayoutError, match="tablero combinador"):
        layout_diagram(spec, derive(spec))


def test_capabilities() -> None:
    statuses = {c["id"]: c["status"] for c in capabilities()["capabilities"]}
    assert statuses["inverters.breakers"] == "supported"
    assert statuses["drawing.inverter_breakers"] == "pending"
