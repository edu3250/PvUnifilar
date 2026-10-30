"""Palette descriptors for the editor (ENG-001, Stage 3.8.1)."""

import json

import pytest

from pvsld.catalogue.registry import ComponentRegistry
from pvsld.cli import main
from pvsld.core import model as spec_model
from pvsld.palette import (
    FORMAT,
    TYPES,
    Palette,
    build_palette,
    palette_json_schema,
    symbol_from_descriptor,
)
from pvsld.resources import data_path
from pvsld.symbols.cfe import get_symbol
from pvsld.symbols.cfe.model import LIBRARY_VERSION

ENGINE_TYPES = {
    "dc_spd",
    "interconnection_point",
    "load_centre",
    "meter",
    "grid",
    "ground_electrode",
    "junction",
    "terminal",
    "pv_connector",
}


@pytest.fixture(scope="module")
def palette() -> Palette:
    return build_palette()


def test_types_are_the_ones_the_engine_calculates(palette: Palette) -> None:
    assert [t.type_id for t in palette.types] == [t.type_id for t in TYPES]
    assert len({t.type_id for t in palette.types}) == len(palette.types) == 15
    assert palette.format == FORMAT
    assert palette.library_version == LIBRARY_VERSION
    assert palette.schema_version == spec_model.SCHEMA_VERSION


@pytest.mark.parametrize("definition", TYPES, ids=lambda t: t.type_id)
def test_symbol_round_trips_to_the_library_block(palette: Palette, definition) -> None:
    descriptor = palette.type(definition.type_id)
    assert symbol_from_descriptor(descriptor.symbol) == get_symbol(definition.block)


@pytest.mark.parametrize("definition", TYPES, ids=lambda t: t.type_id)
def test_ports_match_the_library(palette: Palette, definition) -> None:
    library = get_symbol(definition.block)
    ports = [p for p in palette.type(definition.type_id).symbol.ports if not p.anchor_only]
    assert [(p.id, p.x, p.y, p.direction, p.kind, p.required) for p in ports] == [
        (p.id, p.x, p.y, p.direction, p.kind, p.required) for p in library.ports
    ]


def test_junction_has_anchors_at_the_centre_of_each_edge(palette: Palette) -> None:
    junction = palette.type("junction").symbol
    xmin, ymin, xmax, ymax = junction.bounds
    anchors = {p.id: (p.x, p.y, p.direction) for p in junction.ports}
    assert anchors == {
        "W": (xmin, 0.0, "left"),
        "E": (xmax, 0.0, "right"),
        "N": (0.0, ymax, "up"),
        "S": (0.0, ymin, "down"),
    }
    assert all(p.anchor_only and not p.required and p.kind == "ANY" for p in junction.ports)
    assert not get_symbol("PVSLD_JUNCTION").ports  # the DXF block is unchanged


def test_engine_types_have_no_manufacturer(palette: Palette) -> None:
    for descriptor in palette.types:
        if descriptor.type_id in ENGINE_TYPES:
            assert descriptor.provenance == "pvsld"
            assert descriptor.component_types == ()
            assert not palette.equipment_for(descriptor.type_id)
        else:
            assert descriptor.provenance == "catalogue"


def test_equipment_comes_from_the_reviewed_catalogue(palette: Palette) -> None:
    registry = ComponentRegistry.load(data_path("datasheets/records"))
    expected = {c.component_id for c in registry if c.component_type != "conductor"}
    assert {e.component_id for e in palette.equipment} == expected
    for entry in palette.equipment:
        assert entry.provenance.reviewed_by
        assert len(entry.provenance.sha256) == 64
        assert entry.component_type in palette.type(entry.type_id).component_types


def test_parameters_schema_is_the_spec_section(palette: Palette) -> None:
    assert palette.type("pv_string").parameters_schema == spec_model.StringSpec.model_json_schema()
    assert palette.type("dc_fuse_holder").id_prefix == "FUS-"
    assert palette.type("dc_breaker").id_prefix == "DCB-"
    assert palette.type("junction").parameters_schema == {}
    assert palette.type("junction").spec_section is None


def test_document_is_deterministic_and_fingerprinted() -> None:
    first, second = build_palette(), build_palette()
    assert first.model_dump_json() == second.model_dump_json()
    assert len(first.fingerprint) == 64
    assert len({t.fingerprint for t in first.types}) == len(first.types)


def test_document_validates_against_its_schema(palette: Palette) -> None:
    schema = palette_json_schema()
    assert schema["title"] == "Palette"
    assert Palette.model_validate_json(palette.model_dump_json()) == palette


def test_cli_prints_the_palette(capsys: pytest.CaptureFixture[str], palette: Palette) -> None:
    assert main(["palette", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["fingerprint"] == palette.fingerprint
    assert main(["palette"]) == 0
    assert "pv_string" in capsys.readouterr().out
    assert main(["palette", "--schema"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Palette"
