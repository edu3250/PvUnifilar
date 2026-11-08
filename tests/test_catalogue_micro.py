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
    assert [m.component_id for m in micros] == [
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
