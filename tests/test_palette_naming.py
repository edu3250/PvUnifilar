"""Naming rules and derived fields of the palette (ENG-013)."""

import re
from pathlib import Path
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.palette import Palette, build_palette, next_id
from pvsld.resources import data_path
from pvsld.sizing import size_pv_system
from pvsld.sizing.dcbox import dc_box
from pvsld.sizing.io import load_request


@pytest.fixture(scope="module")
def palette() -> Palette:
    return build_palette()


@pytest.fixture(scope="module")
def sized() -> dict[str, Any]:
    registry = ComponentRegistry.load(data_path("datasheets/records"))
    request = load_request(Path(data_path("examples/sizing_jinko_growatt.yaml")))
    return size_pv_system(request, registry).candidates[0].spec


def _regex(pattern: str) -> re.Pattern[str]:
    escaped = re.escape(pattern).replace(r"\{n\}", r"[1-9]\d*").replace(r"\{string\}", r"S\d+")
    return re.compile(f"^{escaped}$")


def _matches(palette: Palette, type_id: str, item_id: str) -> bool:
    return any(_regex(r.pattern).match(item_id) for r in palette.type(type_id).naming)


def test_every_type_has_a_naming_rule(palette) -> None:
    assert all(t.naming for t in palette.types)
    assert [r.pattern for r in palette.type("pv_string").naming] == ["S{n}"]
    contexts = {r.context: r.pattern for r in palette.type("terminal").naming}
    assert contexts == {
        "default": "X{n}",
        "dc_box_entry": "XE-{string}",
        "dc_box_exit": "XS-{string}",
    }
    assert {r.context for r in palette.type("ac_breaker").naming} == {"default", "service_main"}


def test_derived_fields_exist_in_their_spec_section(palette) -> None:
    for item in palette.types:
        properties = item.parameters_schema.get("properties", {})
        for name in item.derived_fields:
            assert name in properties, (item.type_id, name)
    assert palette.type("pv_string").derived_fields == ("inverter", "mppt")


def test_the_dc_box_follows_the_rules(palette, sized) -> None:
    for child in dc_box(sized).children:
        assert _matches(palette, child.type_id, child.id), child.id


def test_the_sized_spec_follows_the_rules(palette, sized) -> None:
    assert all(_matches(palette, "pv_string", s["id"]) for s in sized["strings"])
    assert all(_matches(palette, "inverter", i["id"]) for i in sized["inverters"])
    derived = [_regex(d.pattern) for d in palette.derived_names]
    for circuit in sized["circuits"]:
        assert any(r.match(circuit["id"]) for r in derived), circuit["id"]
    assert any(r.match(sized["modules"][0]["id"]) for r in derived)


@pytest.mark.parametrize(
    ("pattern", "taken", "string_id", "expected"),
    [
        ("S{n}", [], None, "S1"),
        ("S{n}", ["S1", "S3"], None, "S2"),
        ("ITM-{n}", ["ITM-1", "ITM-P"], None, "ITM-2"),
        ("FUS-{string}", [], "S2", "FUS-S2"),
        ("ITM-P", ["ITM-P"], None, "ITM-P"),
    ],
)
def test_next_id(pattern, taken, string_id, expected) -> None:
    assert next_id(pattern, taken, string_id=string_id) == expected


def test_next_id_needs_the_string() -> None:
    with pytest.raises(ValueError, match="string"):
        next_id("FUS-{string}", [])


def test_naming_is_in_the_json(palette) -> None:
    data = palette.model_dump(mode="json")
    assert data["types"][0]["naming"][0]["pattern"] == "S{n}"
    assert {d["pattern"] for d in data["derived_names"]} >= {"C-S{n}", "C-INV"}
