"""Draft evaluation for the editor (ENG-002, Stage 3.8.2a)."""

from typing import Any

import pytest
import yaml

from pvsld.core.draft import DraftReport, evaluate_draft
from pvsld.core.rules import RULES
from pvsld.core.validation import validate_pv_design
from pvsld.resources import data_path

ALL_RULES = tuple(rule.id for rule in RULES)
STRING_RULES = ("VOLT-001", "STR-001", "STR-004", "STR-007")
OTHER_RULES = tuple(r for r in ALL_RULES if r not in STRING_RULES)


@pytest.fixture
def spec() -> dict[str, Any]:
    text = data_path("examples/residential_7p7kwp.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _only(report: DraftReport) -> dict[str, Any]:
    assert len(report.issues) == 1, [i.to_dict() for i in report.issues]
    return report.issues[0].to_dict()


def test_complete_spec_matches_strict_validation(spec: dict[str, Any]) -> None:
    report = evaluate_draft(spec)
    strict = validate_pv_design(spec)
    assert report.complete
    assert report.ok
    assert report.findings == strict.findings
    assert report.evaluated == ALL_RULES
    assert report.not_evaluated == ()


def test_missing_ac_section_still_runs_the_string_rules(spec: dict[str, Any]) -> None:
    del spec["grounding"]
    report = evaluate_draft(spec)
    issue = _only(report)
    assert (issue["category"], issue["path"]) == ("missing", "grounding")
    assert not report.complete
    assert report.partial
    assert report.evaluated == STRING_RULES
    assert tuple(n.rule_id for n in report.not_evaluated) == OTHER_RULES
    assert all(n.blocking_paths == ("grounding",) for n in report.not_evaluated)


def test_empty_required_list_is_missing(spec: dict[str, Any]) -> None:
    spec["strings"] = []
    assert _only(evaluate_draft(spec))["category"] == "missing"


def test_missing_field_names_its_component(spec: dict[str, Any]) -> None:
    del spec["strings"][1]["n_series"]
    issue = _only(evaluate_draft(spec))
    assert issue == {
        "rule_id": "GEN-001",
        "category": "missing",
        "path": "strings[1].n_series",
        "component_ids": ["S2"],
        "message_es": "Falta «strings[1].n_series».",
    }


def test_missing_value_is_never_filled_in(spec: dict[str, Any]) -> None:
    del spec["modules"][0]["voc_v"]
    report = evaluate_draft(spec)
    assert report.spec is None
    assert report.derived is None
    assert report.findings == ()


@pytest.mark.parametrize(
    ("change", "path"),
    [
        (lambda s: s["strings"][0].update(n_series=-1), "strings[0].n_series"),
        (lambda s: s["modules"][0].update(voc_v="abc"), "modules[0].voc_v"),
        (lambda s: s["layout"].update(sheet="A1"), "layout.sheet"),
    ],
)
def test_bad_values_are_incompatible(spec: dict[str, Any], change, path: str) -> None:
    change(spec)
    issue = _only(evaluate_draft(spec))
    assert (issue["category"], issue["path"]) == ("incompatible", path)


def test_module_vmp_above_voc_is_incompatible(spec: dict[str, Any]) -> None:
    module = spec["modules"][0]
    module["vmp_v"] = module["voc_v"] + 1
    issue = _only(evaluate_draft(spec))
    assert (issue["category"], issue["component_ids"]) == ("incompatible", [module["id"]])


@pytest.mark.parametrize(
    ("change", "path"),
    [
        (lambda s: s.update(extra=1), "extra"),
        (lambda s: s.update(strings={"S1": 1}), "strings"),
    ],
)
def test_wrong_shape_is_invalid_structure(spec: dict[str, Any], change, path: str) -> None:
    change(spec)
    issue = _only(evaluate_draft(spec))
    assert (issue["category"], issue["path"]) == ("invalid_structure", path)


def test_broken_references_are_invalid_structure(spec: dict[str, Any]) -> None:
    spec["strings"][1]["inverter"] = "INV9"
    spec["strings"][0]["module"] = "MOD9"
    report = evaluate_draft(spec)
    assert {i.category for i in report.issues} == {"invalid_structure"}
    assert len(report.issues) == 2
    assert {i.component_ids for i in report.issues} == {("S1",), ("S2",)}


def test_not_a_mapping_is_invalid_structure() -> None:
    issue = _only(evaluate_draft(["not", "a", "spec"]))  # type: ignore[arg-type]
    assert (issue["category"], issue["path"]) == ("invalid_structure", "(raíz)")


@pytest.mark.parametrize(
    ("change", "path"),
    [
        (lambda s: s["inverters"].append({**s["inverters"][0], "id": "INV2"}), "inverters"),
        (lambda s: s["inverters"][0].update(type="micro"), "inverters[0].type"),
        (lambda s: s["inverters"][0].update(qty=2), "inverters[0].qty"),
        (lambda s: s["storage"].update(coupling="dc"), "storage.coupling"),
    ],
)
def test_unsupported_designs_still_run_the_rules(spec: dict[str, Any], change, path: str) -> None:
    change(spec)
    report = evaluate_draft(spec)
    assert [(i.category, i.path) for i in report.issues] == [("unsupported", path)]
    assert report.spec is not None
    assert report.evaluated == ALL_RULES
    assert not report.complete
    assert not report.ok


def test_three_strings_are_unsupported(spec: dict[str, Any]) -> None:
    extra = {**spec["strings"][1], "id": "S3"}
    spec["strings"].append(extra)
    report = evaluate_draft(spec)
    issue = next(i for i in report.issues if i.path == "strings")
    assert issue.category == "unsupported"
    assert issue.component_ids == ("S1", "S2", "S3")


def test_report_is_plain_data(spec: dict[str, Any]) -> None:
    del spec["grounding"]
    data = evaluate_draft(spec).to_dict()
    assert data["complete"] is False
    assert data["ok"] is False
    assert data["issues"][0]["category"] == "missing"
    assert data["not_evaluated"][0]["rule_id"] == OTHER_RULES[0]


# --- Partial evaluation (Stage 3.8.2b) ---------------------------------------------------------


def _strip_ac_side(spec: dict[str, Any]) -> None:
    del spec["ac_bos"]
    del spec["circuits"]


def test_string_rules_run_before_the_ac_side_exists(spec: dict[str, Any]) -> None:
    spec["strings"][0]["n_series"] = 30  # too many modules: VOLT-001 and STR-007 fail
    strict = [f for f in validate_pv_design(spec).findings if f.rule_id in STRING_RULES]
    _strip_ac_side(spec)
    report = evaluate_draft(spec)
    assert report.partial
    assert report.evaluated == STRING_RULES
    assert report.findings == tuple(strict)
    assert {f.rule_id for f in report.findings} == {"VOLT-001", "STR-007"}
    assert report.derived is not None
    assert report.derived.circuits == ()
    assert [s.string_id for s in report.derived.strings] == ["S1", "S2"]
    assert tuple(n.rule_id for n in report.not_evaluated) == OTHER_RULES


@pytest.mark.parametrize(
    ("change", "blocking"),
    [
        (lambda s: s["project"]["site"].pop("t_min_c"), ("project.site",)),
        (lambda s: s["modules"][0].pop("voc_v"), ("modules",)),
        (lambda s: s.pop("standards"), ("standards",)),
        (lambda s: s["inverters"][0].update(vdc_max_v=-1), ("inverters",)),
    ],
)
def test_string_rules_wait_for_their_sections(spec: dict[str, Any], change, blocking) -> None:
    _strip_ac_side(spec)
    change(spec)
    report = evaluate_draft(spec)
    assert report.evaluated == ()
    assert not report.partial
    assert report.derived is None
    assert report.findings == ()
    waiting = {n.rule_id: n.blocking_paths for n in report.not_evaluated}
    assert all(waiting[rule] == blocking for rule in STRING_RULES)


def test_string_references_are_checked_on_a_draft(spec: dict[str, Any]) -> None:
    _strip_ac_side(spec)
    spec["strings"][1]["inverter"] = "INV9"
    spec["strings"][0]["mppt"] = "Z"
    report = evaluate_draft(spec)
    references = report.issues_of("invalid_structure")
    assert [(i.path, i.component_ids) for i in references] == [
        ("strings[0]", ("S1",)),
        ("strings[1]", ("S2",)),
    ]
    assert report.evaluated == ()
    waiting = {n.rule_id: n.blocking_paths for n in report.not_evaluated}
    assert waiting["VOLT-001"] == ("strings",)


def test_partial_report_is_plain_data(spec: dict[str, Any]) -> None:
    _strip_ac_side(spec)
    data = evaluate_draft(spec).to_dict()
    assert data["partial"] is True
    assert data["evaluated"] == list(STRING_RULES)
    assert data["complete"] is False
