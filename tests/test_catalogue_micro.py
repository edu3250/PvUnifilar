"""The microinverter record type of the catalogue (Growatt NEO M-X2, 2026-11-07)."""

from typing import Any

import pytest
from pydantic import ValidationError

from pvsld.catalogue import ComponentRegistry, Microinverter, MicroinverterFamily
from pvsld.catalogue.registry import read_record
from pvsld.resources import data_path

RECORD = data_path("datasheets/records") / "inverters" / "growatt-neo-m-x2.yaml"


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"), include_unreviewed=True)


def _family_data() -> dict[str, Any]:
    return read_record(RECORD).model_dump(mode="json")


def test_neo_family_loads_every_model(registry) -> None:
    micros = registry.microinverters()
    assert [m.component_id for m in micros if m.manufacturer == "Growatt"] == [
        "GROWATT-NEO-1600M-X2",
        "GROWATT-NEO-1800M-X2",
        "GROWATT-NEO-2000M-X2",
        "GROWATT-NEO-2250M-X2",
        "GROWATT-NEO-2500M-X2",
    ]
    neo = registry.get("GROWATT-NEO-2500M-X2")
    assert isinstance(neo, Microinverter)
    assert (neo.mppt_count, neo.inputs_per_mppt) == (4, 1)
    assert (neo.max_input_voltage_v, neo.max_short_circuit_current_per_mppt_a) == (60, 20)
    assert neo.ac_voltage_range_v == (180, 275)
    assert neo.max_ac_output_current_a == 11.36
    assert neo.recommended_module_power_wp == (500, 700)
    assert neo.max_units_per_trunk == {"12 AWG": 2, "10 AWG": 2}
    assert neo.trunk_cable("10 AWG").max_ocpd_a == 32
    assert neo.trunk_cable("8 AWG") is None


def test_the_2250_keeps_its_apparent_power_as_rated_power(registry) -> None:
    neo = registry.get("GROWATT-NEO-2250M-X2")
    assert neo.rated_ac_power_w == neo.max_apparent_power_va == 2250


def test_microinverters_are_not_string_inverters(registry) -> None:
    assert not {m.component_id for m in registry.microinverters()} & {
        i.component_id for i in registry.inverters()
    }
    assert registry.find(component_type="microinverter") == registry.microinverters()


def test_real_power_above_apparent_power_is_rejected() -> None:
    data = _family_data()
    data["variants"][3]["rated_ac_power_w"] = 2550  # as printed on the datasheet
    with pytest.raises(ValidationError, match="rated_ac_power_w"):
        MicroinverterFamily.model_validate(data)


def test_ac_current_must_match_apparent_power() -> None:
    data = _family_data()
    data["variants"][0]["max_ac_output_current_a"] = 9.0
    with pytest.raises(ValidationError, match="max_ac_output_current_a"):
        MicroinverterFamily.model_validate(data)


def test_unit_caps_must_name_the_trunk_cable_sizes() -> None:
    data = _family_data()
    data["variants"][0]["max_units_per_trunk"] = {"12 AWG": 3, "8 AWG": 5}
    with pytest.raises(ValidationError, match="8 AWG"):
        MicroinverterFamily.model_validate(data)


def test_rated_ac_voltage_inside_the_operating_window() -> None:
    data = _family_data()
    data["ac_voltage_range_v"] = [180, 230]
    with pytest.raises(ValidationError, match="ac_voltage_range_v"):
        MicroinverterFamily.model_validate(data)


def test_more_than_one_module_per_input_is_rejected() -> None:
    data = _family_data()
    data["inputs_per_mppt"] = 2
    with pytest.raises(ValidationError, match="inputs_per_mppt"):
        MicroinverterFamily.model_validate(data)


# --- More microinverters (2026-11-08) and the owner's datasheet criteria -----------------------


@pytest.mark.parametrize(
    ("component_id", "inputs", "current", "units_12", "units_10"),
    [
        ("HOYMILES-HMS-800-2T", 2, 3.64, (4, 5), (6, 8)),
        ("HOYMILES-HMS-1000-2T", 2, 4.55, (3, 4), (5, 7)),
        ("ENPHASE-IQ8HC-72-M-US", 1, 1.73, (9, 9), None),
        ("APSYSTEMS-DS3-H", 2, 4.77, (3, 6), (5, 7)),
        ("APSYSTEMS-DS3-LV", 2, 7.5, (2, 3), (3, 4)),
    ],
)
def test_nom_and_manufacturer_branch_limits(
    registry, component_id, inputs, current, units_12, units_10
) -> None:
    from pvsld.core.tables import get_tables
    from pvsld.sizing import micro

    tables = get_tables("NOM-001-SEDE-2012")
    model = registry.get(component_id)
    assert (model.mppt_count, model.max_ac_output_current_a) == (inputs, current)
    for size, expected in (("12 AWG", units_12), ("10 AWG", units_10)):
        if expected is None:
            assert model.trunk_cable(size) is None
            continue
        limit = micro.branch_limit(model, size, tables)
        assert (limit.nom_units, limit.manufacturer_units) == expected


def test_criterion_a_real_power_from_apparent_power(registry) -> None:
    assert registry.get("HOYMILES-HMS-900-2T").rated_ac_power_w == 900
    assert registry.get("ENPHASE-IQ8HC-72-M-US").rated_ac_power_w == 380  # continuous, not 384


def test_criterion_b_no_breaker_leaves_the_nom_limit(registry) -> None:
    cable = registry.get("HOYMILES-HMS-800-2T").trunk_cable("12 AWG")
    assert cable is not None
    assert cable.max_ocpd_a is None
    assert registry.get("ENPHASE-IQ8HC-72-M-US").trunk_cable("12 AWG").max_ocpd_a == 20


def test_input_current_per_model(registry) -> None:
    currents = [
        registry.get(f"HOYMILES-HMS-{w}-2T").max_input_current_per_mppt_a for w in (800, 900, 1000)
    ]
    assert currents == [14, 15, 16]


def test_a_missing_start_voltage_falls_back_to_the_mppt_window(registry) -> None:
    from pvsld.sizing.adapters import to_spec_microinverter

    model = registry.get("APSYSTEMS-DS3-H")
    assert model.startup_voltage_v is None
    spec = to_spec_microinverter(model, inverter_id="INVM1", qty=1, ac_voltage_v=220, phases=2)
    assert spec.vdc_start_v == 28
    assert spec.micro.trunk_sizes == ["12 AWG", "10 AWG"]
    assert spec.micro.trunk_max_ocpd_a == {}
