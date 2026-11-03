"""Connera SHIELD switch: a record whose datasheet omits category, Ui and standards (2026-11-03)."""

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.resources import data_path
from pvsld.sizing import bos


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def test_unpublished_fields_stay_empty(registry) -> None:
    switch = registry.get("CONNERA-SHIELD-DC-4P32A-10")
    assert (switch.utilization_category, switch.insulation_voltage_v, switch.standards) == (
        None,
        None,
        None,
    )
    assert switch.rating_a(2, 600) == (13, 600)
    assert switch.rating_a(4, 1000) == (32, 1000)
    assert registry.get("CONNERA-SHIELD-DC-4P32A-12").rating_a(2, 1100) == (32, 1200)


def test_a_tie_prefers_the_published_category(registry) -> None:
    choice, _ = bos.select_box_switch(
        position="DCD-CD1",
        n_strings=2,
        voc_cold_string_v=269,
        string_i_max_a=22.7,
        switches=registry.dc_switches(),
    )
    assert choice is not None
    assert choice.device_id == "SUNTREE-SISO-40-32"


def test_the_unpublished_switch_is_still_chosen_and_says_so(registry) -> None:
    connera = [s for s in registry.dc_switches() if s.family_id == "CONNERA-SHIELD-DC-4P32A"]
    choice, _ = bos.select_box_switch(
        position="DCD-CD1",
        n_strings=1,
        voc_cold_string_v=900,
        string_i_max_a=22.7,
        switches=connera,
    )
    assert choice is not None
    assert choice.device_id == "CONNERA-SHIELD-DC-4P32A-10"
    assert "no publicada" in choice.note_es
