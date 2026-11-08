"""The Pydantic parameter model accepts the sample, rejects bad input and publishes a schema."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from pvsld.core.model import SCHEMA_VERSION, PvSystemSpec, export_json_schema, parse_spec
from s1_helpers import load_example, mutated


def test_sample_parses_into_the_model() -> None:
    spec = parse_spec(load_example())
    assert spec.schema_version == SCHEMA_VERSION == "0.1.0"
    assert spec.project.id == "PV-2026-0001"
    assert [s.id for s in spec.strings] == ["S1", "S2"]
    assert spec.title_block.date == date(2026, 10, 5)
    assert spec.utility.nominal_voltage_v == 220


def test_circuit_endpoints_use_the_from_alias() -> None:
    spec = parse_spec(load_example())
    circuit = next(c for c in spec.circuits if c.id == "C-INV")
    assert circuit.from_ == "INV1.ac"
    assert circuit.to == "ITM-1"


def test_model_is_immutable() -> None:
    spec = parse_spec(load_example())
    with pytest.raises(ValidationError):
        spec.project.id = "other"  # type: ignore[misc]


def test_unknown_fields_are_rejected_so_typos_are_caught() -> None:
    data = mutated(lambda s: s["project"].update({"nmae": "typo"}))
    with pytest.raises(ValidationError, match="nmae"):
        parse_spec(data)


def test_unsupported_schema_version_is_rejected() -> None:
    data = mutated(lambda s: s.update({"schema_version": "9.9.9"}))
    with pytest.raises(ValidationError, match="schema_version"):
        parse_spec(data)


@pytest.mark.parametrize(
    ("change", "location"),
    [
        (lambda s: s["project"]["site"].update({"t_min_c": -50}), "t_min_c"),
        (lambda s: s["utility"].update({"nominal_voltage_v": 230}), "nominal_voltage_v"),
        (lambda s: s["modules"][0].update({"vmp_v": 60.0}), "vmp_v"),
        (lambda s: s["modules"][0].update({"imp_a": 15.0}), "imp_a"),
        (lambda s: s["strings"][0].update({"n_series": 0}), "n_series"),
        (lambda s: s["circuits"][0]["conductors"].update({"material": "Al"}), "material"),
        (lambda s: s["circuits"][0]["conductors"].update({"size": "7 AWG"}), "size"),
    ],
)
def test_out_of_range_values_are_rejected(change: Any, location: str) -> None:
    with pytest.raises(ValidationError, match=location):
        parse_spec(mutated(change))


def test_personal_data_is_accepted_as_written() -> None:
    """Its format is the user's business (owner decision 2026-10-09)."""

    def free(spec: dict[str, Any]) -> None:
        spec["utility"]["rpu"] = "123"
        spec["project"]["site"]["address"]["cp"] = "481129"
        spec["project"]["site"]["lat"] = "20°43' N"
        spec["project"]["site"]["lon"] = 60
        spec["project"]["client"]["email"] = "sin correo"
        spec["title_block"]["responsible"]["cedula_profesional"] = "en trámite"

    spec = parse_spec(mutated(free))
    assert spec.utility.rpu == "123"
    assert spec.project.site.address.cp == "481129"
    assert spec.project.site.lat == "20°43' N"
    assert spec.project.site.lon == 60
    assert spec.title_block.responsible.cedula_profesional == "en trámite"


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda s: s["strings"][0].update({"module": "NOPE"}), "NOPE"),
        (lambda s: s["strings"][0].update({"inverter": "NOPE"}), "NOPE"),
        (lambda s: s["strings"][0].update({"mppt": "Z"}), "Z"),
        (lambda s: s["circuits"][0].update({"to": "INV9.A"}), "INV9"),
        (lambda s: s["circuits"][1]["raceway"].update({"ref": "C-NOPE"}), "C-NOPE"),
        (lambda s: s["ac_bos"]["panels"][0].update({"main_ocpd": "ITM-NOPE"}), "ITM-NOPE"),
        (lambda s: s["ac_bos"]["point_of_connection"].update({"panel": "CC-9"}), "CC-9"),
    ],
)
def test_dangling_references_are_reported_by_name(change: Any, fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        parse_spec(mutated(change))


def test_duplicate_component_ids_are_rejected() -> None:
    data = mutated(lambda s: s["strings"][1].update({"id": "S1"}))
    with pytest.raises(ValidationError, match="S1"):
        parse_spec(data)


def test_json_schema_is_2020_12_and_describes_the_root() -> None:
    schema = export_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "PvSystemSpec"
    assert {"project", "modules", "inverters", "title_block"} <= set(schema["required"])
    # Schema 0.2.0: a microinverter design has ac_branches instead of strings.
    assert {"strings", "ac_branches"} <= set(schema["properties"])
    assert "from" in schema["$defs"]["Circuit"]["properties"]


def test_json_schema_is_json_serialisable_and_stable() -> None:
    first = json.dumps(export_json_schema(), sort_keys=True)
    assert first == json.dumps(export_json_schema(), sort_keys=True)


def test_schema_validates_the_sample_as_json() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    instance = json.loads(parse_spec(load_example()).model_dump_json(by_alias=True))
    jsonschema.Draft202012Validator(export_json_schema()).validate(instance)


def test_model_class_is_exported_for_the_mcp_layer() -> None:
    assert PvSystemSpec.model_config.get("extra") == "forbid"
