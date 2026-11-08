"""Sizing of microinverter designs (Stage 3.10.1, owner decisions of 2026-11-07)."""

from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.draft import evaluate_draft
from pvsld.design.pipeline import load_quick_template
from pvsld.mcp.sizing_tools import sizing_output, sizing_text
from pvsld.resources import data_path
from pvsld.sizing import request_from_mapping, size_pv_system
from pvsld.sizing.micro_sizing import MicroCandidate, rank_key, split_branches
from pvsld.sizing.report import format_report

MODULE = "JINKO-JKM650N-66HL4M-BDV"
GROWATT = [f"GROWATT-NEO-{w}M-X2" for w in (1600, 1800, 2000, 2250, 2500)]
"""The cases below pin the Growatt family; ``test_auto_picks_across_brands`` covers "auto"."""


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def _size(registry, modules: int, **extra: Any):
    request = request_from_mapping(
        {
            "template": load_quick_template(),
            "module": MODULE,
            "microinverters": GROWATT,
            "module_count_min": modules,
            "module_count_max": modules,
            **extra,
        }
    )
    return size_pv_system(request, registry)


@pytest.mark.parametrize(
    ("units", "capacity", "expected"),
    [(1, 2, [1]), (3, 2, [2, 1]), (4, 2, [2, 2]), (5, 2, [2, 2, 1]), (7, 3, [3, 2, 2])],
)
def test_fewest_branches_spread_evenly(units, capacity, expected) -> None:
    assert split_branches(units, capacity) == expected


def test_the_model_range_holding_the_module_wins(registry) -> None:
    micros = [m for m in registry.microinverters() if m.manufacturer == "Growatt"]
    ranked = sorted(micros, key=lambda m: rank_key(m, 650))
    # 650 W fits 2000 (400-670), 2250 and 2500; the smallest of them wins.
    assert [m.component_id for m in ranked][:3] == [
        "GROWATT-NEO-2000M-X2",
        "GROWATT-NEO-2250M-X2",
        "GROWATT-NEO-2500M-X2",
    ]
    assert rank_key(registry.get("GROWATT-NEO-1600M-X2"), 650)[0] == 1  # outside 320-530 W


def test_eight_modules_on_two_units_one_branch(registry) -> None:
    result = _size(registry, 8)
    selected = result.selected
    assert isinstance(selected, MicroCandidate)
    assert (selected.micro_id, selected.units, selected.free_inputs) == (
        "GROWATT-NEO-2000M-X2",
        2,
        0,
    )
    (branch,) = selected.branches
    assert (branch.units, branch.trunk_size, branch.ocpd_a) == (2, "10 AWG", 25)
    spec = selected.spec
    assert spec["schema_version"] == "0.2.0"
    assert spec["strings"] == []
    assert spec["inverters"][0]["qty"] == 2
    assert spec["ac_bos"]["point_of_connection"]["breaker"] == "ITM-R1"
    assert evaluate_draft(spec).issues == ()


def test_ten_modules_leave_two_free_inputs(registry) -> None:
    selected = _size(registry, 10).selected
    assert selected.units == 3
    assert selected.free_inputs == 2
    assert [i.rule_id for i in selected.issues] == ["MIC-015"]
    last = selected.spec["ac_branches"][-1]["microinverters"][-1]
    assert sorted(last["inputs"]) == ["A", "B"]
    assert [(b.units, b.trunk_size) for b in selected.branches] == [(2, "10 AWG"), (1, "12 AWG")]


def test_the_ac_combiner_only_on_request(registry) -> None:
    plain = _size(registry, 12).selected
    assert "ac_combiner" not in plain.spec
    boxed = _size(registry, 12, ac_combiner=True).selected
    assert boxed.spec["ac_combiner"]["main_ocpd"] == "ITM-1"
    ocpds = {o["id"]: o for o in boxed.spec["ac_bos"]["ocpds"]}
    assert ocpds["ITM-R1"]["at"] == "CCA-1"
    assert ocpds["ITM-1"]["rating_a"] == 35  # 1.25 x 3 x 9.09 A = 34.1 A
    assert boxed.spec["ac_bos"]["spds"][0]["at"] == "CCA-1"
    assert evaluate_draft(boxed.spec).issues == ()


def test_the_busbar_rule_rejects_large_branches(registry) -> None:
    result = _size(registry, 16)
    rejected = {r.config.inverter_id: r.rule_id for r in result.rejected}
    assert rejected.get("GROWATT-NEO-2500M-X2") == "PCC-002"
    assert result.selected.micro_id == "GROWATT-NEO-2000M-X2"


def test_a_127_v_service_rejects_every_micro(registry) -> None:
    template = load_quick_template()
    template["utility"].update(system="1F-2H", nominal_voltage_v=127)
    template["ac_bos"]["panels"][0]["system"] = "1F-2H"
    request = request_from_mapping(
        {"template": template, "module": MODULE, "microinverters": "auto", "module_count_max": 8}
    )
    result = size_pv_system(request, registry)
    # Only the 120 V APsystems DS3-LV works phase to neutral at 127 V.
    assert result.selected.micro_id == "APSYSTEMS-DS3-LV"
    rejected = {r.config.inverter_id: r.rule_id for r in result.rejected}
    assert rejected["GROWATT-NEO-2000M-X2"] == "MIC-010"
    assert rejected["ENPHASE-IQ8HC-72-M-US"] == "MIC-010"


def test_string_inverters_stay_the_default(registry) -> None:
    request = request_from_mapping(
        {"template": load_quick_template(), "module": MODULE, "module_count_max": 10}
    )
    assert not isinstance(size_pv_system(request, registry).selected, MicroCandidate)


def test_texts_describe_the_branches(registry) -> None:
    result = _size(registry, 10)
    text = sizing_text(sizing_output(result))
    assert "R2: 1 unit(s), trunk 12 AWG" in text
    assert "MIC-015" in text
    assert "R1: 2 unit(s)" in format_report(result)


def test_a_target_power_gives_the_closest_count(registry) -> None:
    request = request_from_mapping(
        {
            "template": load_quick_template(),
            "module": MODULE,
            "microinverters": ["GROWATT-NEO-2000M-X2"],
            "target_dc_power_w": 5000,
        }
    )
    assert size_pv_system(request, registry).selected.modules == 8  # 5000 / 650 = 7.7


def test_auto_picks_across_brands(registry) -> None:
    request = request_from_mapping(
        {
            "template": load_quick_template(),
            "module": MODULE,
            "microinverters": "auto",
            "module_count_max": 8,
        }
    )
    selected = size_pv_system(request, registry).selected
    # 650 W fits the DS3-H (330-660 W, 1050 W) and the NEO 2000 (400-670 W); the smaller wins.
    assert selected.micro_id == "APSYSTEMS-DS3-H"
    assert selected.units == 4
