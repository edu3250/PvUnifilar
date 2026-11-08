"""Rule pack mx-gd-2026.10 (S1 subset): passes on the sample, fails as expected on mutations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from pvsld.core.rules import RULES, Finding, Severity, rulepack_catalogue
from pvsld.core.validation import ValidationReport, validate_pv_design
from s1_helpers import load_example, mutated

SUBSET = (
    "VOLT-001",
    "STR-001",
    "STR-004",
    "STR-007",
    "CON-003",
    "CON-006",
    "PCC-002",
    "MET-001",
    "DIS-003",
    "DIS-004",
)
# Microinverter rules of schema 0.2.0 (Stage 3.9.3), after the S1 subset.
MICRO = (
    "MIC-001",
    "MIC-002",
    "MIC-003",
    "MIC-004",
    "MIC-005",
    "MIC-010",
    "MIC-011",
    "MIC-012",
    "MIC-013",
    "MIC-014",
    "MIC-020",
    "MIC-021",
    "VD-002",
)
# STR-007 and CON-006 have no Mexican checklist item in the vault ("MX" column is "—"), nor do
# the microinverter rules (equipment and installation-manual limits).
NO_CHECKLIST_ITEM = {"STR-007", "CON-006", *MICRO}


def _ids(report: ValidationReport, severity: Severity | None = None) -> list[str]:
    return [f.rule_id for f in report.findings if severity is None or f.severity == severity]


def _only(report: ValidationReport, rule_id: str) -> Finding:
    found = [f for f in report.findings if f.rule_id == rule_id]
    assert len(found) == 1, f"expected exactly one {rule_id}, got {_ids(report)}"
    return found[0]


# --- The pack itself ---------------------------------------------------------------------------


def test_pack_implements_exactly_the_implemented_subset() -> None:
    assert tuple(rule.id for rule in RULES) == SUBSET + MICRO


def test_catalogue_documents_every_rule_in_spanish_with_citations() -> None:
    catalogue = rulepack_catalogue()
    assert [entry["id"] for entry in catalogue] == [*SUBSET, *MICRO]
    for entry in catalogue:
        assert entry["title_es"]
        assert entry["mx_ids"] or entry["id"] in NO_CHECKLIST_ITEM
        assert entry["cites"]
        assert entry["severity"] in {"E", "W", "I", "E/W", "I/W", "E/W/I"}
        assert entry["basis"]


@pytest.mark.parametrize(
    ("rule_id", "mx_id"),
    [
        ("VOLT-001", "MX-C02"),
        ("STR-001", "MX-C04"),
        ("STR-004", "MX-C05"),
        ("CON-003", "MX-C07"),
        ("PCC-002", "MX-E05"),
        ("MET-001", "MX-F01"),
        ("DIS-003", "MX-E01"),
        ("DIS-004", "MX-E02"),
    ],
)
def test_rules_carry_their_mexican_checklist_ids(rule_id: str, mx_id: str) -> None:
    rule = next(r for r in RULES if r.id == rule_id)
    assert mx_id in rule.mx_ids


# --- The sample passes ---------------------------------------------------------------------------


def test_sample_passes_every_rule_of_the_subset() -> None:
    report = validate_pv_design(load_example())
    assert report.ok
    assert report.findings == ()
    assert report.rulepack == "mx-gd-2026.10"
    assert report.derived is not None
    assert report.derived.kwp_total == pytest.approx(7.70)


# --- Mutations fail as expected ------------------------------------------------------------------


def _twelve_modules(spec: dict[str, Any]) -> None:
    for string in spec["strings"]:
        string["n_series"] = 12


def test_twelve_modules_per_string_violates_volt_001() -> None:
    report = validate_pv_design(mutated(_twelve_modules))
    assert not report.ok
    finding = next(f for f in report.findings if f.rule_id == "VOLT-001")
    assert finding.severity is Severity.ERROR
    assert finding.subject == "S1"
    assert "MX-C02" in finding.mx_ids
    # The message names the string, the computed voltage, the inverter limit and the way out.
    assert "S1" in finding.message_es
    assert "640.2 V" in finding.message_es
    assert "600 V" in finding.message_es
    assert "11 módulos" in finding.message_es
    assert [f.subject for f in report.findings if f.rule_id == "VOLT-001"] == ["S1", "S2"]


def test_eleven_modules_per_string_is_still_valid_with_the_coefficient_method() -> None:
    def eleven(spec: dict[str, Any]) -> None:
        for string in spec["strings"]:
            string["n_series"] = 11

    assert "VOLT-001" not in _ids(validate_pv_design(mutated(eleven)))


def test_table_method_makes_eleven_modules_violate_volt_001() -> None:
    def table_method(spec: dict[str, Any]) -> None:
        spec["standards"]["voc_method"] = "table_690_7"
        spec["modules"][0]["beta_voc_pct_c"] = None
        for string in spec["strings"]:
            string["n_series"] = 11

    # 11 x 55.55 V = 611.1 V > 600 V (vault worked example)
    assert "VOLT-001" in _ids(validate_pv_design(mutated(table_method)))


def test_two_strings_on_one_mppt_violate_str_004() -> None:
    report = validate_pv_design(mutated(lambda s: s["strings"][1].update({"mppt": "A"})))
    finding = _only(report, "STR-004")
    assert finding.severity is Severity.ERROR
    assert finding.subject == "INV1.A"
    assert "35.0 A" in finding.message_es
    assert "22 A" in finding.message_es
    assert "MX-C05" in finding.mx_ids


def test_inverter_output_on_10_awg_with_a_35_a_breaker_violates_con_003() -> None:
    def ten_awg(spec: dict[str, Any]) -> None:
        circuit = next(c for c in spec["circuits"] if c["id"] == "C-INV")
        circuit["conductors"]["size"] = "10 AWG"

    report = validate_pv_design(mutated(ten_awg))
    finding = _only(report, "CON-003")
    assert finding.subject == "C-INV"
    assert "ITM-1" in finding.message_es
    assert "30 A" in finding.message_es  # 240-4(d) limit of a 10 AWG copper conductor
    assert "MX-C07" in finding.mx_ids


def test_a_breaker_larger_than_the_conductor_ampacity_violates_con_003() -> None:
    def big_breaker(spec: dict[str, Any]) -> None:
        spec["ac_bos"]["ocpds"][0]["rating_a"] = 80
        spec["ac_bos"]["panels"][0]["bus_a"] = 200

    finding = _only(validate_pv_design(mutated(big_breaker)), "CON-003")
    assert "80 A" in finding.message_es
    assert "50 A" in finding.message_es  # 8 AWG, 75 degC column


def test_busbar_overload_violates_pcc_002() -> None:
    report = validate_pv_design(mutated(lambda s: s["ac_bos"]["panels"][0].update({"bus_a": 100})))
    finding = _only(report, "PCC-002")
    assert finding.subject == "CC-1"
    assert "135 A" in finding.message_es
    assert "120 A" in finding.message_es
    assert "MX-E05" in finding.mx_ids


def test_supply_side_connection_is_not_checked_by_the_120_percent_rule() -> None:
    def supply_side(spec: dict[str, Any]) -> None:
        spec["ac_bos"]["panels"][0]["bus_a"] = 100
        spec["ac_bos"]["point_of_connection"]["type"] = "supply_side"

    assert "PCC-002" not in _ids(validate_pv_design(mutated(supply_side)))


def _meters(spec: dict[str, Any], meters: list[dict[str, Any]]) -> None:
    spec["ac_bos"]["meters"] = meters


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda s: _meters(s, []), "no se declara"),
        (lambda s: s["ac_bos"]["meters"][0].update({"bidirectional": False}), "bidireccional"),
        (
            lambda s: s["ac_bos"]["meters"].append({**s["ac_bos"]["meters"][0], "id": "M-2"}),
            "exactamente uno",
        ),
        (lambda s: s["ac_bos"]["meters"][0].update({"role": "MCE"}), "no se declara"),
    ],
)
def test_metering_mutations_violate_met_001(
    change: Callable[[dict[str, Any]], None], expected: str
) -> None:
    finding = _only(validate_pv_design(mutated(change)), "MET-001")
    assert finding.severity is Severity.ERROR
    assert expected in finding.message_es.lower()
    assert "MX-F01" in finding.mx_ids


def test_missing_i1_violates_dis_003() -> None:
    report = validate_pv_design(
        mutated(lambda s: s["ac_bos"]["ocpds"][0].update({"role": "other"}))
    )
    finding = _only(report, "DIS-003")
    assert "I1" in finding.message_es
    assert "MX-E01" in finding.mx_ids


def test_i1_declared_not_lockable_violates_dis_003() -> None:
    report = validate_pv_design(
        mutated(lambda s: s["ac_bos"]["ocpds"][0].update({"lockable": False}))
    )
    assert "bloqueable" in _only(report, "DIS-003").message_es


def test_missing_i2_violates_dis_004() -> None:
    def no_i2(spec: dict[str, Any]) -> None:
        spec["ac_bos"]["main_breakers"][0]["role"] = "other"

    finding = _only(validate_pv_design(mutated(no_i2)), "DIS-004")
    assert "I2" in finding.message_es
    assert "MX-E02" in finding.mx_ids


def test_unidirectional_i2_violates_dis_004() -> None:
    def one_way(spec: dict[str, Any]) -> None:
        spec["ac_bos"]["main_breakers"][0]["bidirectional"] = False

    assert "bidireccional" in _only(validate_pv_design(mutated(one_way)), "DIS-004").message_es


@pytest.mark.parametrize(
    ("n_series", "severity"),
    [(2, Severity.ERROR), (5, Severity.WARNING)],
)
def test_short_strings_violate_str_001(n_series: int, severity: Severity) -> None:
    def shorten(spec: dict[str, Any]) -> None:
        spec["strings"][0]["n_series"] = n_series

    finding = _only(validate_pv_design(mutated(shorten)), "STR-001")
    assert finding.severity is severity
    assert finding.subject == "S1"
    assert "MX-C04" in finding.mx_ids
    assert validate_pv_design(mutated(shorten)).ok is (severity is Severity.WARNING)


def test_a_failing_spec_collects_every_violated_rule_in_pack_order() -> None:
    def broken(spec: dict[str, Any]) -> None:
        _twelve_modules(spec)
        spec["ac_bos"]["meters"] = []
        spec["ac_bos"]["main_breakers"][0]["bidirectional"] = False

    # 2 x 12 x 550 W = 13.2 kWp also exceeds the 9 kW inverter limit (STR-007, pack order).
    assert _ids(validate_pv_design(mutated(broken))) == [
        "VOLT-001",
        "VOLT-001",
        "STR-007",
        "MET-001",
        "DIS-004",
    ]


# --- Schema errors become GEN-001 findings -------------------------------------------------------


def test_schema_errors_are_reported_as_gen_001_without_running_the_other_rules() -> None:
    def broken(spec: dict[str, Any]) -> None:
        spec["project"]["site"]["t_min_c"] = -99
        del spec["utility"]

    report = validate_pv_design(mutated(broken))
    assert not report.ok
    assert report.derived is None
    assert set(_ids(report)) == {"GEN-001"}
    subjects = {f.subject for f in report.findings}
    assert "project.site.t_min_c" in subjects
    assert "utility" in subjects
    assert all(f.severity is Severity.ERROR for f in report.findings)
    assert all(f.message_es for f in report.findings)


def test_non_mapping_input_is_a_gen_001_error() -> None:
    report = validate_pv_design(["not", "a", "mapping"])  # type: ignore[arg-type]
    assert not report.ok
    assert _ids(report) == ["GEN-001"]


def test_report_serialises_to_plain_json_types() -> None:
    import json

    report = validate_pv_design(mutated(_twelve_modules))
    payload = report.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["ok"] is False
    assert payload["rulepack"] == "mx-gd-2026.10"
    assert payload["findings"][0]["rule_id"] == "VOLT-001"
    assert payload["findings"][0]["severity"] == "E"
    assert "derived" in payload


def test_a_conductor_with_no_ampacity_at_the_design_temperature_violates_con_003() -> None:
    def furnace(spec: dict[str, Any]) -> None:
        circuit = next(c for c in spec["circuits"] if c["id"] == "C-INV")
        circuit["ambient_c"] = 95  # above the last correction band: no ampacity left

    finding = _only(validate_pv_design(mutated(furnace)), "CON-003")
    assert "no tiene ampacidad" in finding.message_es
