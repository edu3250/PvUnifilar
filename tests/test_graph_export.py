"""Specification -> editor drawing -> specification (Stage 3.10.2): the round trip."""

from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.draft import evaluate_draft
from pvsld.design.pipeline import load_quick_template
from pvsld.graph import Graph, graph_to_spec
from pvsld.graph_export import spec_to_graph
from pvsld.resources import data_path
from pvsld.sizing import request_from_mapping, size_pv_system

MODULE = "JINKO-JKM650N-66HL4M-BDV"


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def _sized(registry, modules: int = 10, **extra: Any) -> dict[str, Any]:
    request = request_from_mapping(
        {
            "template": load_quick_template(),
            "module": MODULE,
            "module_count_min": modules,
            "module_count_max": modules,
            **extra,
        }
    )
    spec = size_pv_system(request, registry).spec
    assert spec is not None
    return spec


def _by_id(items: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, tuple]:
    return {item["id"]: tuple(item.get(k) for k in keys) for item in items}


def _circuits(spec: dict[str, Any]) -> dict[str, tuple]:
    return {
        c["id"]: (
            c["kind"],
            c["from"],
            c["to"],
            c["conductors"]["size"],
            c["length_m"],
            c["raceway"].get("trade_size_mm"),
        )
        for c in spec["circuits"]
    }


CASES = {
    "string inverter and DC box": {},
    "microinverters": {"microinverters": "auto"},
    "microinverters and AC combiner": {"microinverters": "auto", "ac_combiner": True},
}


@pytest.mark.parametrize("extra", CASES.values(), ids=CASES.keys())
def test_the_drawing_gives_the_same_design_back(registry, extra) -> None:
    spec = _sized(registry, **extra)
    export = spec_to_graph(spec, registry=registry)
    assert export.ok, export.issues
    Graph.model_validate(export.graph)  # a valid pvsld.graph/1 document
    back = graph_to_spec(export.graph, export.template, registry=registry)
    assert back.ok, [i.message_es for i in back.issues]
    result = back.spec
    assert result["schema_version"] == spec["schema_version"]
    assert result["strings"] == spec["strings"]
    assert _by_id(result["inverters"], ("type", "model", "qty")) == _by_id(
        spec["inverters"], ("type", "model", "qty")
    )
    assert result.get("ac_branches", []) == spec.get("ac_branches", [])
    assert result.get("ac_combiner") == spec.get("ac_combiner")
    ocpd_keys = ("role", "rating_a", "model", "kaic_ka", "backfed", "at", "poles")
    assert _by_id(result["ac_bos"]["ocpds"], ocpd_keys) == _by_id(
        spec["ac_bos"]["ocpds"], ocpd_keys
    )
    assert result["ac_bos"]["point_of_connection"] == spec["ac_bos"]["point_of_connection"]
    dc_keys = ("model", "ie_a", "ue_v", "poles")
    assert _by_id(result["dc_bos"]["disconnects"], dc_keys) == _by_id(
        spec["dc_bos"]["disconnects"], dc_keys
    )
    assert _circuits(result) == _circuits(spec)
    assert evaluate_draft(result).issues == ()


def test_the_template_keeps_what_is_not_drawn(registry) -> None:
    spec = _sized(registry, microinverters="auto", ac_combiner=True)
    template = spec_to_graph(spec, registry=registry).template
    assert {"project", "utility", "grounding", "title_block", "layout"} <= set(template)
    assert not {"modules", "inverters", "strings", "circuits", "ac_branches"} & set(template)
    assert template["ac_bos"]["ocpds"] == []  # every breaker is drawn
    assert template["ac_bos"]["spds"] == []  # the combiner's SPD is drawn inside it


def test_node_ids_follow_the_spec_and_the_palette(registry) -> None:
    spec = _sized(registry, microinverters="auto")
    graph = spec_to_graph(spec, registry=registry).graph
    types = {n["id"]: n["type"] for n in graph["nodes"]}
    assert types["MI1"] == "microinverter"
    assert types["ITM-R1"] == "ac_breaker"
    assert types["JB-1"] == "ac_junction_box"
    assert types["M-MI1A"] == "pv_module"
    micro = next(n for n in graph["nodes"] if n["id"] == "MI1")
    assert micro["values"] == {"model": "GROWATT-NEO-2000M-X2"}


def test_an_unknown_model_is_reported(registry) -> None:
    spec = _sized(registry)
    spec["modules"][0]["model"] = "NO-EXISTE"
    export = spec_to_graph(spec, registry=registry)
    assert not export.ok
    assert "NO-EXISTE" in export.issues[0]


def test_the_per_string_box_round_trips(registry) -> None:
    from pvsld.sizing.dcbox import apply_dc_box, dc_box

    spec = _sized(registry)
    spec = apply_dc_box(spec, dc_box(spec, layout="per_string", registry=registry))
    export = spec_to_graph(spec, registry=registry)
    back = graph_to_spec(export.graph, export.template, registry=registry)
    assert back.ok, [i.message_es for i in back.issues]
    keys = ("model", "ie_a", "ue_v", "poles")
    assert _by_id(back.spec["dc_bos"]["disconnects"], keys) == _by_id(
        spec["dc_bos"]["disconnects"], keys
    )
    assert sorted(s["id"] for s in back.spec["dc_bos"]["spds"]) == ["DPS-S1", "DPS-S2"]
