"""Microinverter rules (Stage 3.9.2): module inputs, service voltage, AC branch, voltage drop."""

import pytest

from pvsld.catalogue import ComponentRegistry, PVModule
from pvsld.core.severity import Severity
from pvsld.core.tables import get_tables
from pvsld.resources import data_path
from pvsld.sizing import micro
from pvsld.sizing.adapters import to_spec_module

TABLES = get_tables("NOM-001-SEDE-2012")
MODELS = ["1600", "1800", "2000", "2250", "2500"]


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def _neo(registry, model: str):
    return registry.get(f"GROWATT-NEO-{model}M-X2")


def _ids(issues) -> list[tuple[str, Severity]]:
    return [(i.rule_id, i.severity) for i in issues]


def _inputs(registry, model: str, module: PVModule, **overrides):
    values = {"t_min_c": -5.0, "t_amb_max_c": 40.0, "voc_method": "coefficient"}
    values.update(overrides)
    return micro.module_input_issues(
        _neo(registry, model), module, to_spec_module(module), **values
    )


# --- AC branch: the owner's tables of 2026-11-07 ---------------------------------------------


@pytest.mark.parametrize(
    ("trunk", "nom", "growatt", "allowed"),
    [
        ("12 AWG", [2, 1, 1, 1, 1], [3, 3, 2, 2, 2], [2, 1, 1, 1, 1]),
        ("10 AWG", [3, 2, 2, 2, 2], [4, 3, 3, 2, 2], [3, 2, 2, 2, 2]),
    ],
)
def test_units_per_branch_nom_then_manufacturer(registry, trunk, nom, growatt, allowed) -> None:
    limits = [micro.branch_limit(_neo(registry, m), trunk, TABLES) for m in MODELS]
    assert [limit.nom_units for limit in limits] == nom
    assert [limit.manufacturer_units for limit in limits] == growatt
    assert [limit.units for limit in limits] == allowed


def test_trunk_breaker_limit_is_240_4_d_before_the_manufacturer(registry) -> None:
    neo = _neo(registry, "2500")
    assert micro.branch_limit(neo, "12 AWG", TABLES).ocpd_limit_a == 20  # Growatt says 25
    assert micro.branch_limit(neo, "10 AWG", TABLES).ocpd_limit_a == 30  # Growatt says 32


def test_branch_breaker_is_125_percent_of_the_sum(registry) -> None:
    neo = _neo(registry, "2500")
    assert micro.branch_current_a(neo, 2) == pytest.approx(22.72)
    assert micro.branch_ocpd_a(neo, 2, TABLES) == 30  # 1.25 x 22.72 = 28.4 A


def test_a_compliant_branch_has_no_issues(registry) -> None:
    neo = _neo(registry, "2500")
    issues = micro.branch_issues(
        neo, branch_id="R1", units=2, trunk_size="10 AWG", ocpd_a=30, tables=TABLES
    )
    assert issues == []


def test_too_many_units_for_the_nom(registry) -> None:
    issues = micro.branch_issues(
        _neo(registry, "2500"),
        branch_id="R1",
        units=2,
        trunk_size="12 AWG",
        ocpd_a=None,
        tables=TABLES,
    )
    assert _ids(issues) == [("MIC-011", Severity.ERROR)]
    assert "Máximo 1 por rama" in issues[0].message_es


def test_manufacturer_cap_applies_after_the_nom(registry) -> None:
    neo = _neo(registry, "1600").model_copy(
        update={"max_units_per_trunk": {"12 AWG": 1, "10 AWG": 4}}
    )
    issues = micro.branch_issues(
        neo, branch_id="R1", units=2, trunk_size="12 AWG", ocpd_a=20, tables=TABLES
    )
    assert _ids(issues) == [("MIC-012", Severity.ERROR)]


@pytest.mark.parametrize(("ocpd", "fragment"), [(25, "menor que 1.25"), (40, "excede")])
def test_branch_breaker_too_small_or_too_large(registry, ocpd, fragment) -> None:
    issues = micro.branch_issues(
        _neo(registry, "2500"),
        branch_id="R1",
        units=2,
        trunk_size="10 AWG",
        ocpd_a=ocpd,
        tables=TABLES,
    )
    assert _ids(issues) == [("MIC-011", Severity.ERROR)]
    assert fragment in issues[0].message_es


def test_trunk_size_the_manufacturer_does_not_list(registry) -> None:
    issues = micro.branch_issues(
        _neo(registry, "1600"),
        branch_id="R1",
        units=1,
        trunk_size="8 AWG",
        ocpd_a=None,
        tables=TABLES,
    )
    assert ("MIC-013", Severity.ERROR) in _ids(issues)


# --- Service voltage -------------------------------------------------------------------------


def test_service_voltage_inside_the_ac_window(registry) -> None:
    neo = _neo(registry, "2500")
    assert micro.service_voltage_issue(neo, 220) is None
    issue = micro.service_voltage_issue(neo, 127)
    assert issue is not None
    assert (issue.rule_id, issue.severity) == ("MIC-010", Severity.ERROR)


# --- Module on each input --------------------------------------------------------------------


def test_jinko_650_fits_the_2500(registry) -> None:
    issues = _inputs(registry, "2500", registry.get("JINKO-JKM650N-66HL4M-BDV"))
    assert issues == []  # BNPI Isc 18.16 A <= 20 A; Imp 15.64 A <= 18 A; 650 W in 500-700 W


def test_isc_is_checked_without_the_125_factor(registry) -> None:
    module = registry.get("JINKO-JKM650N-66HL4M-BDV")
    tight = _neo(registry, "2500").model_copy(update={"max_short_circuit_current_per_mppt_a": 18})
    issues = micro.module_input_issues(
        tight,
        module,
        to_spec_module(module),
        t_min_c=-5,
        t_amb_max_c=40,
        voc_method="coefficient",
    )
    assert _ids(issues) == [("MIC-002", Severity.ERROR)]
    assert "BNPI" in issues[0].message_es


def test_cold_voc_above_the_input_limit(registry) -> None:
    module = registry.get("JINKO-JKM650N-66HL4M-BDV")
    low = _neo(registry, "2500").model_copy(update={"max_input_voltage_v": 52})
    issues = micro.module_input_issues(
        low,
        module,
        to_spec_module(module),
        t_min_c=-5,
        t_amb_max_c=40,
        voc_method="coefficient",
    )
    assert ("MIC-001", Severity.ERROR) in _ids(issues)


def test_module_power_below_and_above_the_recommended_range(registry) -> None:
    small = _inputs(registry, "2500", registry.get("JINKO-JKM460N-60HL4"))
    assert ("MIC-004", Severity.INFO) in _ids(small)
    big = _inputs(registry, "1600", registry.get("ETSOLAR-ET-M672BH550"))
    assert ("MIC-004", Severity.WARNING) in _ids(big)


def test_imp_above_the_input_current_is_info(registry) -> None:
    module = registry.get("JINKO-JKM650N-66HL4M-BDV")
    tight = _neo(registry, "2500").model_copy(update={"max_input_current_per_mppt_a": 15})
    issues = micro.module_input_issues(
        tight,
        module,
        to_spec_module(module),
        t_min_c=-5,
        t_amb_max_c=40,
        voc_method="coefficient",
    )
    assert _ids(issues) == [("MIC-003", Severity.INFO)]


def test_hot_vmp_below_the_mppt_window(registry) -> None:
    module = registry.get("JINKO-JKM460N-60HL4")
    high = _neo(registry, "2500").model_copy(update={"mppt_voltage_range_v": (40, 55)})
    issues = micro.module_input_issues(
        high,
        module,
        to_spec_module(module),
        t_min_c=-5,
        t_amb_max_c=40,
        voc_method="coefficient",
    )
    assert ("MIC-005", Severity.WARNING) in _ids(issues)


# --- Voltage drop ----------------------------------------------------------------------------


def test_trunk_drop_follows_the_daisy_chain(registry) -> None:
    neo = _neo(registry, "2500")
    one = micro.trunk_drop_pct(
        neo,
        units=1,
        trunk_size="10 AWG",
        spacing_m=2,
        home_run_m=10,
        voltage_v=220,
        tables=TABLES,
    )
    two = micro.trunk_drop_pct(
        neo,
        units=2,
        trunk_size="10 AWG",
        spacing_m=2,
        home_run_m=10,
        voltage_v=220,
        tables=TABLES,
    )
    resistance = TABLES.resistance_ac_pvc_ohm_km["10 AWG"] / 1000
    assert one == pytest.approx(100 * 2 * 10 * 11.36 * resistance / 220)
    assert two == pytest.approx(100 * 2 * (10 * 22.72 + 2 * 11.36) * resistance / 220)
    assert (
        micro.trunk_drop_issue(branch_id="R1", drop_pct=two, limit_pct=3, trunk_size="10 AWG")
        is None
    )
    issue = micro.trunk_drop_issue(branch_id="R1", drop_pct=3.5, limit_pct=3, trunk_size="10 AWG")
    assert issue is not None
    assert (issue.rule_id, issue.severity) == ("VD-002", Severity.WARNING)


def test_drop_cable_length_is_capped_by_the_manufacturer(registry) -> None:
    neo = _neo(registry, "2500")
    assert micro.drop_cable_issue(neo, size="14 AWG", length_m=20) is None
    too_long = micro.drop_cable_issue(neo, size="14 AWG", length_m=21)
    assert too_long is not None
    assert too_long.rule_id == "MIC-014"
    unknown = micro.drop_cable_issue(neo, size="12 AWG", length_m=5)
    assert unknown is not None
    assert "14 AWG" in unknown.message_es
