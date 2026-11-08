"""Schema 0.2.0 microinverter designs: model, derived values, rules, draft and choices (3.9.3)."""

from typing import Any

import pytest
from pydantic import ValidationError

from micro_spec import combiner_spec_data, micro_spec_data
from pvsld.capabilities import capabilities
from pvsld.core.calc import derive
from pvsld.core.draft import evaluate_draft
from pvsld.core.layout import LayoutError
from pvsld.core.model import parse_spec
from pvsld.core.severity import Severity
from pvsld.core.tables import get_tables
from pvsld.service import layout_diagram
from pvsld.sizing.choices import evaluate_choices

TABLES = get_tables("NOM-001-SEDE-2012")


@pytest.fixture
def data() -> dict[str, Any]:
    return micro_spec_data()


def _rules(data: dict[str, Any]) -> list[tuple[str, Severity, str]]:
    report = evaluate_draft(data)
    assert report.issues == (), report.issues
    return [(f.rule_id, f.severity, f.subject) for f in report.findings]


# --- Model -------------------------------------------------------------------------------------


def test_a_compliant_micro_design_parses_and_passes(data) -> None:
    spec = parse_spec(data)
    assert [u.id for u in spec.ac_branches[0].microinverters] == ["MI1", "MI2"]
    assert {"R1", "MI1", "MI2"} <= set(spec.component_ids())
    assert _rules(data) == []


def test_ac_branches_need_schema_0_2_0(data) -> None:
    data["schema_version"] = "0.1.0"
    with pytest.raises(ValidationError, match=r"0.2.0"):
        parse_spec(data)


def test_a_design_needs_strings_or_branches(data) -> None:
    data["ac_branches"] = []
    with pytest.raises(ValidationError, match="at least one string or one AC branch"):
        parse_spec(data)


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda d: d["ac_branches"][0].update(ocpd="ITM-9"), "unknown ocpd"),
        (lambda d: d["ac_branches"][0]["microinverters"][0].update(inverter="INV9"), "INV9"),
        (lambda d: d["ac_branches"][0]["microinverters"][0]["inputs"].update(E="MOD1"), "'E'"),
        (lambda d: d["ac_branches"][0]["microinverters"][0]["inputs"].update(A="MOD9"), "MOD9"),
        (lambda d: d["inverters"][0].update(qty=3), "qty 3 but 2"),
        (lambda d: d["inverters"][0].pop("micro"), "micro block"),
        (lambda d: d["ac_branches"][0]["microinverters"][1].update(id="MI1"), "MI1"),
    ],
)
def test_branch_references_are_checked(data, change, fragment) -> None:
    change(data)
    with pytest.raises(ValidationError, match=fragment):
        parse_spec(data)


def test_a_string_cannot_feed_a_microinverter(data) -> None:
    data["strings"] = [
        {
            "id": "S1",
            "module": "MOD1",
            "n_series": 1,
            "inverter": "INV1",
            "mppt": "A",
            "tilt_deg": 20,
            "azimuth_deg": 180,
        }
    ]
    with pytest.raises(ValidationError, match="ac_branches"):
        parse_spec(data)


# --- Derived values ----------------------------------------------------------------------------


def test_derived_power_current_and_trunk_drop(data) -> None:
    derived = derive(parse_spec(data))
    assert derived.kwp_total == pytest.approx(8 * 0.650)
    (circuit,) = derived.circuits
    assert circuit.i_max_a == pytest.approx(2 * 11.36)
    r = TABLES.resistance_ac_pvc_ohm_km["10 AWG"] / 1000
    expected = 100 * 2 * (15 * 22.72 + 4.5 * 11.36) * r / 220
    assert circuit.vd_pct == pytest.approx(expected)


# --- Rules -------------------------------------------------------------------------------------


def test_a_12_awg_branch_of_two_2500_breaks_the_nom() -> None:
    found = _rules(micro_spec_data(trunk="12 AWG", ocpd_a=20))
    assert ("MIC-011", Severity.ERROR, "R1") in found
    assert ("VD-002", Severity.WARNING, "R1") in found


def test_three_2500_on_10_awg_exceed_the_branch() -> None:
    found = _rules(micro_spec_data(units=3, ocpd_a=30))
    assert ("MIC-011", Severity.ERROR, "R1") in found


def test_a_127_v_service_cannot_feed_the_neo(data) -> None:
    data["utility"].update(system="1F-2H", nominal_voltage_v=127)
    data["ac_bos"]["panels"][0]["system"] = "1F-2H"
    data["inverters"][0].update(phases=1, vac_v=127)
    assert ("MIC-010", Severity.ERROR, "INV1") in _rules(data)


def test_module_findings_name_the_micro_model(data) -> None:
    data["modules"][0].update(isc_a=21.0)
    assert ("MIC-002", Severity.ERROR, "INV1") in _rules(data)


def test_drop_cable_beyond_the_manufacturer_length(data) -> None:
    data["ac_branches"][0]["microinverters"][1]["drop_cable"] = {"size": "14 AWG", "length_m": 25}
    assert ("MIC-014", Severity.ERROR, "MI2") in _rules(data)


# --- Draft and capabilities --------------------------------------------------------------------


def test_micro_designs_are_supported_but_mixing_is_not(data) -> None:
    assert evaluate_draft(data).issues == ()
    string = {**data["inverters"][0], "id": "INV2", "type": "string", "qty": 1}
    string.pop("micro")
    data["inverters"].append(string)
    issues = evaluate_draft(data).issues
    assert [i.category for i in issues] == ["unsupported"]
    assert "junto con un inversor de cadena" in issues[0].message_es


def test_two_micro_models_are_not_supported_yet(data) -> None:
    second = {**data["inverters"][0], "id": "INV2", "qty": 1}
    data["inverters"][0]["qty"] = 1
    data["inverters"].append(second)
    data["ac_branches"][0]["microinverters"][1]["inverter"] = "INV2"
    issues = evaluate_draft(data).issues
    assert any("un solo modelo de microinversor" in i.message_es for i in issues)


def test_capabilities_list_microinverters() -> None:
    doc = capabilities()
    status = {c["id"]: c["status"] for c in doc["capabilities"]}
    assert status["microinverters"] == "supported"
    assert status["microinverters.ac_combiner"] == "supported"
    assert status["drawing.microinverters"] == "pending"
    assert "micro" in doc["limits"]["inverter_types"]
    assert doc["limits"]["max_micro_models"] == 1


def test_the_drawing_says_microinverters_are_pending(data) -> None:
    spec = parse_spec(data)
    with pytest.raises(LayoutError, match="microinversores"):
        layout_diagram(spec, derive(spec))


# --- Choices -----------------------------------------------------------------------------------


def test_choices_check_the_branch_breaker_and_the_trunk_drop(data) -> None:
    report = evaluate_choices(data)
    assert report.skipped_es is None
    checks = {c.target: c for c in report.checks}
    assert checks["ITM-R1.rating_a"].ok
    assert checks["C-R1.conductors.size"].ok
    assert "ITM-1.rating_a" not in checks


def test_choices_propose_the_nom_breaker() -> None:
    report = evaluate_choices(micro_spec_data(ocpd_a=25))
    checks = {c.target: c for c in report.checks}
    assert not checks["ITM-R1.rating_a"].ok
    assert checks["ITM-R1.rating_a"].rule_id == "MIC-011"
    updates = {u.target: u for u in report.updates}
    assert updates["ITM-R1.rating_a"].proposed == 30


# --- AC combiner box ---------------------------------------------------------------------------


def test_a_combiner_design_passes() -> None:
    data = combiner_spec_data()
    assert _rules(data) == []
    circuits = {c.circuit_id: c for c in derive(parse_spec(data)).circuits}
    assert circuits["C-CCA"].i_max_a == pytest.approx(3 * 11.36)


def test_combiner_main_breaker_and_spd() -> None:
    found = _rules(combiner_spec_data(main_a=40, spd=False))
    assert ("MIC-020", Severity.ERROR, "ITM-1") in found
    assert ("MIC-021", Severity.WARNING, "CCA-1") in found


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda d: d["ac_combiner"].update(main_ocpd="ITM-9"), "unknown main_ocpd"),
        (lambda d: d["ac_combiner"].update(spd="DPS-9"), "unknown spd"),
        (lambda d: d["ac_bos"]["ocpds"][1].update(at="CC-1"), "is at 'CCA-1'"),
        (lambda d: d["ac_bos"]["ocpds"][2].update(at="CCA-1"), "feeds the load centre"),
    ],
)
def test_combiner_references_are_checked(change, fragment) -> None:
    data = combiner_spec_data()
    change(data)
    with pytest.raises(ValidationError, match=fragment):
        parse_spec(data)


def test_a_combiner_needs_branches(data) -> None:
    data["ac_combiner"] = {"id": "CCA-1", "main_ocpd": "ITM-R1"}
    data["ac_branches"] = []
    with pytest.raises(ValidationError, match="ac_combiner needs ac_branches"):
        parse_spec(data)


def test_choices_check_the_combiner_main_breaker() -> None:
    report = evaluate_choices(combiner_spec_data(main_a=40))
    checks = {c.target: c for c in report.checks}
    assert not checks["ITM-1.rating_a"].ok
    assert checks["ITM-1.rating_a"].rule_id == "MIC-020"
    assert checks["ITM-R1.rating_a"].ok
    assert checks["C-CCA.conductors.size"].ok
    assert {u.target: u.proposed for u in report.updates}["ITM-1.rating_a"] == 45
