"""Three-phase service 3F-4H 220/127 V (Stage 3.13.2, owner approval 2026-11-14).

Three-phase inverters (the Solis S5-GC of the catalogue, 3 MPPT) on a 3F-4H service: 3-pole
breaker at 1.25 x Iac (no 3-pole breaker in the catalogue: the rating stays without a model),
three phases + neutral, voltage drop with sqrt(3). A single-phase inverter on 3F-4H runs between
two phases with an imbalance warning; a three-phase inverter on a single-phase service is an
error (SRV-001).
"""

import copy
import math
from pathlib import Path
from typing import Any

import pytest

from pvsld.capabilities import capabilities
from pvsld.catalogue import ComponentRegistry
from pvsld.core.draft import evaluate_draft
from pvsld.core.tables import get_tables
from pvsld.core.validation import validate_pv_design
from pvsld.graph import graph_to_spec
from pvsld.graph_export import spec_to_graph
from pvsld.palette import build_palette
from pvsld.resources import data_path
from pvsld.sizing import size_pv_system
from pvsld.sizing.choices import evaluate_choices
from pvsld.sizing.dcbox import apply_dc_box, dc_box
from pvsld.sizing.io import load_request

MODULE = "JINKO-JKM650N-66HL4M-BDV"
SOLIS = "SOLIS-S5-GC20K-LV"
GROWATT = "GROWATT-MIN-6000TL-X2"
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


def _template(registry: ComponentRegistry, system: str = "3F-4H") -> dict[str, Any]:
    request = load_request(Path(data_path("examples/sizing_jinko_growatt.yaml")))
    sized = size_pv_system(request, registry).candidates[0].spec
    template = copy.deepcopy({key: sized[key] for key in TEMPLATE_KEYS})
    template["ac_bos"]["ocpds"] = []
    template["utility"]["system"] = system
    if system == "3F-4H":
        for panel in template["ac_bos"]["panels"]:
            panel.update(system="3F-4H", bus_a=225)
        for main in template["ac_bos"]["main_breakers"]:
            main.update(poles=3, rating_a=125)
            main.pop("model", None)
    return template


def _graph(models: list[str], ports: list[str], n_series: int) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    cables: list[dict[str, Any]] = []
    string = 0
    for number, model in enumerate(models, start=1):
        nodes.append({"id": f"INV{number}", "type": "inverter", "values": {"model": model}})
        for port in ports:
            string += 1
            nodes.append(
                {
                    "id": f"S{string}",
                    "type": "pv_string",
                    "values": {"module": MODULE, "n_series": n_series},
                }
            )
            cables.append({"from": f"S{string}.OUT", "to": f"INV{number}.{port}", "length_m": 20})
        cables.append({"from": f"INV{number}.AC", "to": "J1.W" if len(models) > 1 else "ITM-1.IN"})
    if len(models) > 1:
        nodes.append({"id": "J1", "type": "junction"})
        cables.append({"from": "J1.E", "to": "ITM-1.IN", "length_m": 15})
    nodes += [
        {"id": "ITM-1", "type": "ac_breaker"},
        {"id": "CC-1", "type": "load_centre"},
        {"id": "ITM-P", "type": "ac_breaker"},
        {"id": "M-1", "type": "meter"},
        {"id": "CFE", "type": "grid"},
    ]
    cables += [
        {"from": "ITM-1.OUT", "to": "CC-1.LEFT"},
        {"from": "CC-1.RIGHT", "to": "ITM-P.IN"},
        {"from": "ITM-P.OUT", "to": "M-1.IN"},
        {"from": "M-1.OUT", "to": "CFE.IN"},
    ]
    if len(models) == 1:
        cables[-5]["length_m"] = 15  # the inverter's run to ITM-1
    return {"format": "pvsld.graph/1", "nodes": nodes, "cables": cables}


def _codes(result) -> list[tuple[str, tuple[str, ...]]]:
    return [(i.code, i.node_ids) for i in result.issues]


def _rules(spec: dict[str, Any]) -> list[tuple[str, str]]:
    return [(f.rule_id, f.severity.value) for f in validate_pv_design(spec).findings]


def test_a_three_phase_inverter_on_3f_4h(registry) -> None:
    result = graph_to_spec(
        _graph([SOLIS], ["A", "B", "C"], 12), _template(registry), registry=registry
    )
    assert result.ok, _codes(result)
    spec = result.spec
    inverter = spec["inverters"][0]
    assert (inverter["phases"], inverter["vac_v"]) == (3, 220)
    assert [m["id"] for m in inverter["mppt"]] == ["A", "B", "C"]
    itm = spec["ac_bos"]["ocpds"][0]
    # 1.25 x 57.7 A = 72.1 A -> 80 A, 3 poles; no 3-pole breaker in the catalogue: no model
    assert (itm["poles"], itm["rating_a"]) == (3, 80)
    assert "model" not in itm
    circuit = next(c for c in spec["circuits"] if c["id"] == "C-INV")
    assert circuit["conductors"]["qty"] == 3
    assert circuit["neutral"] is not None
    assert circuit["raceway"]["ccc_count"] == 3  # the balanced neutral does not count
    draft = evaluate_draft(spec)
    assert draft.complete, [i.to_dict() for i in draft.issues]
    assert not draft.issues_of("unsupported")
    report = validate_pv_design(spec)
    assert report.ok
    values = next(c for c in report.derived.circuits if c.circuit_id == "C-INV")
    tables = get_tables("NOM-001-SEDE-2012")
    size = circuit["conductors"]["size"]
    expected = 100 * math.sqrt(3) * 15 * 57.7 * tables.resistance_ac_pvc_ohm_km[size] / 1000 / 220
    assert values.vd_pct == pytest.approx(expected)


def test_the_missing_3_pole_breaker_is_a_warning_not_an_error(registry) -> None:
    spec = graph_to_spec(
        _graph([SOLIS], ["A", "B", "C"], 12), _template(registry), registry=registry
    ).spec
    boxed = apply_dc_box(spec, dc_box(spec, registry=registry))
    report = evaluate_choices(boxed, registry=registry)
    itm = [c for c in report.failing if c.component_id == "ITM-1"]
    assert itm
    assert all(c.severity is not None and c.severity.value == "W" for c in itm)
    assert "3 polos" in itm[0].message_es


def test_a_three_phase_inverter_on_a_single_phase_service_is_an_error(registry) -> None:
    template = _template(registry, system="2F-3H")
    spec = graph_to_spec(_graph([SOLIS], ["A", "B", "C"], 12), template, registry=registry).spec
    assert ("SRV-001", "E") in _rules(spec)


def test_a_single_phase_inverter_on_3f_4h_warns_of_imbalance(registry) -> None:
    result = graph_to_spec(_graph([GROWATT], ["A", "B"], 6), _template(registry), registry=registry)
    assert result.ok, _codes(result)
    spec = result.spec
    assert spec["inverters"][0]["phases"] == 2  # between two phases, at 220 V
    assert spec["ac_bos"]["ocpds"][0]["poles"] == 2
    report = validate_pv_design(spec)
    assert report.ok
    warning = next(f for f in report.findings if f.rule_id == "SRV-001")
    assert warning.severity.value == "W"
    assert "entre dos fases" in warning.message_es


def test_two_three_phase_inverters_share_a_3_pole_breaker(registry) -> None:
    result = graph_to_spec(
        _graph([SOLIS, SOLIS], ["A", "B", "C"], 12), _template(registry), registry=registry
    )
    assert result.ok, _codes(result)
    itm = result.spec["ac_bos"]["ocpds"][0]
    # 1.25 x (57.7 + 57.7) A = 144.25 A -> 150 A
    assert (itm["poles"], itm["rating_a"]) == (3, 150)
    shared = next(c for c in result.spec["circuits"] if c["id"] == "C-INVT")
    assert shared["conductors"]["qty"] == 3


def test_the_round_trip(registry) -> None:
    spec = graph_to_spec(
        _graph([SOLIS], ["A", "B", "C"], 12), _template(registry), registry=registry
    ).spec
    boxed = apply_dc_box(spec, dc_box(spec, registry=registry))
    exported = spec_to_graph(boxed, registry=registry)
    assert exported.issues == ()
    again = graph_to_spec(exported.graph, exported.template, registry=registry)
    assert again.ok, _codes(again)
    assert again.spec == boxed


def test_the_palette_and_the_capabilities(registry) -> None:
    palette = build_palette(registry)
    solis = next(e for e in palette.equipment if e.component_id == SOLIS)
    assert solis.designable
    assert solis.block == "PVSLD_INV_3MPPT"
    assert "3F-4H" in capabilities()["limits"]["utility_systems"]
