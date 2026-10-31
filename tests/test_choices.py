"""Evaluate drawn choices without reselecting them (ENG-005, Stage 3.8.3)."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.severity import Severity
from pvsld.resources import data_path
from pvsld.sizing import size_pv_system
from pvsld.sizing.choices import ChoiceReport, evaluate_choices
from pvsld.sizing.io import load_request


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


@pytest.fixture(scope="module")
def sized(registry: ComponentRegistry) -> dict[str, Any]:
    """The engine's own design: two strings with gPV fuses, the box switch, ITM-1 and ITM-P."""
    request = load_request(Path(data_path("examples/sizing_jinko_growatt.yaml")))
    return size_pv_system(request, registry).candidates[0].spec


@pytest.fixture
def spec(sized: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(sized)


def _evaluate(spec: dict[str, Any], registry: ComponentRegistry, *locks: str) -> ChoiceReport:
    return evaluate_choices(spec, locks, registry=registry)


def _device(spec: dict[str, Any], device_id: str) -> dict[str, Any]:
    for section in (spec["dc_bos"]["disconnects"], spec["ac_bos"]["ocpds"]):
        for device in section:
            if device["id"] == device_id:
                return device
    for main in spec["ac_bos"]["main_breakers"]:
        if main["id"] == device_id:
            return main
    raise KeyError(device_id)


def _circuit(spec: dict[str, Any], circuit_id: str) -> dict[str, Any]:
    return next(c for c in spec["circuits"] if c["id"] == circuit_id)


def _check(report: ChoiceReport, target: str):
    return next(c for c in report.checks if c.target == target)


def _targets(changes) -> dict[str, tuple[Any, Any]]:
    return {c.target: (c.current, c.proposed) for c in changes}


def test_the_engines_own_design_needs_no_change(spec, registry) -> None:
    report = _evaluate(spec, registry)
    assert report.evaluated
    assert report.ok
    assert report.failing == ()
    assert report.updates == ()
    assert report.recommendations == ()
    covered = {c.target for c in report.checks}
    assert {
        "FUS-S1.model",
        "FUS-S1.ie_a",
        "FUS-S2.ue_v",
        "DCD-CD1.poles",
        "ITM-1.rating_a",
        "ITM-1.kaic_ka",
        "ITM-P.model",
        "C-S1.conductors.size",
        "C-INV.conductors.size",
        "C-S1.raceway.trade_size_mm",
    } <= covered


def test_unlocked_bad_value_fails_and_gets_an_update(spec, registry) -> None:
    fuse = _device(spec, "FUS-S1")
    fuse.update(ie_a=10, model=None)
    report = _evaluate(spec, registry)
    check = _check(report, "FUS-S1.ie_a")
    assert (check.ok, check.rule_id, check.severity) == (False, "OCP-002", Severity.ERROR)
    assert not check.locked
    assert check.suggested is None
    assert _targets(report.updates)["FUS-S1.ie_a"] == (10, 30.0)
    assert _targets(report.updates)["FUS-S1.model"] == (None, "LITTELFUSE-SPF030")
    assert not report.ok


@pytest.mark.parametrize("lock", ["FUS-S1", "FUS-S1.ie_a"])
def test_locked_bad_value_is_a_finding_not_a_replacement(spec, registry, lock) -> None:
    _device(spec, "FUS-S1")["ie_a"] = 10
    report = _evaluate(spec, registry, lock)
    check = _check(report, "FUS-S1.ie_a")
    assert (check.ok, check.locked, check.suggested) == (False, True, 30.0)
    assert "FUS-S1.ie_a" not in _targets(report.updates)
    assert "FUS-S1.ie_a" not in _targets(report.recommendations)
    assert not report.ok


def test_locked_value_that_passes_gets_an_informative_recommendation(spec, registry) -> None:
    itm = _device(spec, "ITM-1")
    itm.update(rating_a=40, model=None)
    report = _evaluate(spec, registry, "ITM-1.rating_a")
    check = _check(report, "ITM-1.rating_a")
    assert (check.ok, check.locked, check.rule_id) == (True, True, "OCP-005")
    assert _targets(report.recommendations) == {"ITM-1.rating_a": (40, 30.0)}
    assert "ITM-1.rating_a" not in _targets(report.updates)
    assert report.ok


def test_unlocked_value_that_passes_is_still_updated_to_the_engines(spec, registry) -> None:
    _device(spec, "ITM-1").update(rating_a=40, model=None)
    report = _evaluate(spec, registry)
    assert _check(report, "ITM-1.rating_a").ok
    assert _targets(report.updates)["ITM-1.rating_a"] == (40, 30.0)
    assert report.recommendations == ()


def test_small_itm_fails_ocp_005(spec, registry) -> None:
    _device(spec, "ITM-1").update(rating_a=20, model=None)
    check = _check(_evaluate(spec, registry), "ITM-1.rating_a")
    assert (check.ok, check.rule_id) == (False, "OCP-005")


def test_low_interrupting_rating_fails(spec, registry) -> None:
    _device(spec, "ITM-P").update(kaic_ka=5)
    check = _check(_evaluate(spec, registry, "ITM-P"), "ITM-P.kaic_ka")
    assert (check.ok, check.rule_id, check.suggested) == (False, "OCP-003", 10.0)


def test_model_that_does_not_fit_is_an_error(spec, registry) -> None:
    _device(spec, "FUS-S1")["model"] = "LITTELFUSE-SPF015"
    check = _check(_evaluate(spec, registry), "FUS-S1.model")
    assert (check.ok, check.severity) == (False, Severity.ERROR)
    assert "LITTELFUSE-SPF015" in check.message_es


def test_unknown_model_cannot_be_verified(spec, registry) -> None:
    _device(spec, "ITM-1")["model"] = "NO-SUCH-BREAKER"
    check = _check(_evaluate(spec, registry, "ITM-1"), "ITM-1.model")
    assert (check.ok, check.severity) == (False, Severity.WARNING)
    assert "no está en el catálogo" in check.message_es


def test_model_that_disagrees_with_the_drawn_rating_is_a_warning(spec, registry) -> None:
    _device(spec, "ITM-1")["rating_a"] = 40  # the drawn model is the 30 A QO230
    check = _check(_evaluate(spec, registry, "ITM-1"), "ITM-1.model")
    assert (check.ok, check.severity) == (False, Severity.WARNING)


def test_box_switch_with_too_few_poles_fails(spec, registry) -> None:
    _device(spec, "DCD-CD1")["poles"] = 2
    report = _evaluate(spec, registry)
    check = _check(report, "DCD-CD1.poles")
    assert (check.ok, check.rule_id) == (False, "DIS-001")
    assert _targets(report.updates)["DCD-CD1.poles"] == (2, 4)


def test_thin_conductor_fails_and_its_grounding_follows_the_update(spec, registry) -> None:
    circuit = _circuit(spec, "C-INV")
    circuit["conductors"]["size"] = "14 AWG"
    circuit["neutral"]["size"] = "14 AWG"
    circuit["egc"]["size"] = "14 AWG"
    report = _evaluate(spec, registry)
    check = _check(report, "C-INV.conductors.size")
    assert (check.ok, check.rule_id) == (False, "CON-002")
    updates = _targets(report.updates)
    assert updates["C-INV.conductors.size"] == ("14 AWG", "8 AWG")
    assert updates["C-INV.egc.size"] == ("14 AWG", "8 AWG")
    assert updates["C-INV.neutral.size"] == ("14 AWG", "8 AWG")


def test_locked_conductor_keeps_its_grounding(spec, registry) -> None:
    circuit = _circuit(spec, "C-S1")
    circuit["conductors"]["size"] = "6 AWG"
    report = _evaluate(spec, registry, "C-S1.conductors.size")
    assert _check(report, "C-S1.conductors.size").ok
    assert _targets(report.recommendations)["C-S1.conductors.size"] == ("6 AWG", "8 AWG")
    # the grounding follows the conductor that stays: 6 AWG
    assert _targets(report.updates)["C-S1.egc.size"] == ("8 AWG", "6 AWG")


def test_overfilled_raceway_fails_con_006(spec, registry) -> None:
    _circuit(spec, "C-S1")["raceway"]["trade_size_mm"] = 16
    report = _evaluate(spec, registry)
    check = _check(report, "C-S1.raceway.trade_size_mm")
    assert (check.ok, check.rule_id) == (False, "CON-006")
    assert _targets(report.updates)["C-S1.raceway.trade_size_mm"] == (16, 35.0)


def test_unknown_locks_are_reported(spec, registry) -> None:
    report = _evaluate(spec, registry, "FUS-S9", "ITM-1.rating_a", " ", "ITM-1.rating_a")
    assert report.locks == ("FUS-S9", "ITM-1.rating_a")
    assert report.unknown_locks == ("FUS-S9",)


def test_incomplete_draft_is_not_evaluated(spec, registry) -> None:
    del spec["grounding"]
    report = _evaluate(spec, registry)
    assert not report.evaluated
    assert not report.ok
    assert report.checks == ()
    assert report.draft.issues_of("missing")


def test_unsupported_design_is_not_evaluated(spec, registry) -> None:
    spec["inverters"][0]["qty"] = 2
    report = _evaluate(spec, registry)
    assert not report.evaluated
    assert "unsupported" in (report.skipped_es or "")


def test_report_is_plain_data(spec, registry) -> None:
    _device(spec, "FUS-S1").update(ie_a=10, model=None)
    data = _evaluate(spec, registry, "FUS-S1").to_dict()
    json.dumps(data)
    assert data["ok"] is False
    failing = [c for c in data["checks"] if not c["ok"]]
    assert failing[0]["target"] == "FUS-S1.ie_a"
    assert failing[0]["suggested"] == 30.0
    assert data["draft"]["complete"] is True


def test_the_bundled_catalogue_is_the_default(spec) -> None:
    assert evaluate_choices(spec).updates == ()
