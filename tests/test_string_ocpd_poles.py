"""String fuse and DC breaker poles follow grounding.dc_system (owner request 2026-11-06)."""

import copy
from pathlib import Path
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.palette import build_palette
from pvsld.resources import data_path
from pvsld.sizing import bos, size_pv_system
from pvsld.sizing.choices import evaluate_choices
from pvsld.sizing.dcbox import dc_box
from pvsld.sizing.io import load_request


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


@pytest.fixture(scope="module")
def request_path() -> Path:
    return Path(data_path("examples/sizing_jinko_growatt.yaml"))


def _sized(registry, request_path, dc_system: str) -> dict[str, Any]:
    request = load_request(request_path)
    template = copy.deepcopy(request.template)
    template["grounding"]["dc_system"] = dc_system
    request = request.model_copy(update={"template": template})
    result = size_pv_system(request, registry)
    assert result.candidates, [r.issues for r in result.rejected][:3]
    return result.candidates[0].spec


def _fuse(spec: dict[str, Any]) -> dict[str, Any]:
    return next(d for d in spec["dc_bos"]["disconnects"] if d["id"] == "FUS-S1")


@pytest.mark.parametrize(
    ("dc_system", "poles"), [("ungrounded_690_35", 2), ("grounded", 1), (None, 2)]
)
def test_the_rule(dc_system, poles) -> None:
    assert bos.string_ocpd_poles(dc_system) == poles


def test_sizing_follows_the_dc_system(registry, request_path) -> None:
    assert _fuse(_sized(registry, request_path, "ungrounded_690_35"))["poles"] == 2
    grounded = _sized(registry, request_path, "grounded")
    assert _fuse(grounded)["poles"] == 1
    # the box disconnect is unchanged (owner 2026-11-06: reviewed separately)
    switch = next(d for d in grounded["dc_bos"]["disconnects"] if d["id"] == "DCD-CD1")
    assert switch["poles"] == 4


def test_dc_box_follows_the_dc_system(registry, request_path) -> None:
    spec = _sized(registry, request_path, "ungrounded_690_35")
    spec["grounding"]["dc_system"] = "grounded"
    fuse = dc_box(spec, registry=registry).child("FUS-S1")
    assert fuse.poles == 1


def test_breaker_voltage_uses_the_same_poles(registry) -> None:
    need = bos.string_need(
        max_series_fuse_a=35, isc_a=18.17, strings_per_input=1, voc_cold_string_v=269, poles=1
    )
    breaker = registry.get("SUNTREE-SL7N-63-32A")
    why, _ue, _icu = bos.breaker_fit(breaker, need)
    assert why is not None
    assert "1P" in why  # the 1-pole SL7N stops at 250 V
    two = bos.string_need(
        max_series_fuse_a=35, isc_a=18.17, strings_per_input=1, voc_cold_string_v=269, poles=2
    )
    assert bos.breaker_fit(breaker, two)[0] is None


def test_choices_propose_the_poles(registry, request_path) -> None:
    spec = _sized(registry, request_path, "ungrounded_690_35")
    spec["grounding"]["dc_system"] = "grounded"
    report = evaluate_choices(spec, registry=registry)
    check = next(c for c in report.checks if c.target == "FUS-S1.poles")
    assert check.ok  # 2 poles still protect the ungrounded conductor
    assert {(u.target, u.proposed) for u in report.updates} >= {("FUS-S1.poles", 1)}
    spec["grounding"]["dc_system"] = "ungrounded_690_35"
    _fuse(spec)["poles"] = 1
    check = next(
        c for c in evaluate_choices(spec, registry=registry).checks if c.target == "FUS-S1.poles"
    )
    assert (check.ok, check.rule_id) == (False, "OCP-002")


def test_palette_publishes_the_rule() -> None:
    palette = build_palette()
    for type_id in ("dc_fuse_holder", "dc_breaker"):
        assert palette.type(type_id).poles_by_dc_system == {
            "ungrounded_690_35": 2,
            "grounded": 1,
        }
    assert palette.type("dc_switch").poles_by_dc_system is None
