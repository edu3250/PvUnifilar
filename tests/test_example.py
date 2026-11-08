"""The 7.70 kWp residential sample matches the vault's PV SLD Parameter Model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from pvsld.cli import main
from pvsld.core.model import PvSystemSpec

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "residential_7p7kwp.yaml"

# Sections the parameter model marks as required for a low-voltage residential system.
EXPECTED_REQUIRED_SECTIONS = (
    "schema_version",
    "project",
    "standards",
    "utility",
    "modules",
    "inverters",
    "dc_bos",
    "ac_bos",
    "circuits",
    "grounding",
    "storage",
    "title_block",
    "layout",
)


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    loaded = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_example_has_every_expected_top_level_section(spec: dict[str, Any]) -> None:
    assert set(EXPECTED_REQUIRED_SECTIONS) <= set(spec)
    assert "monitoring" in spec


def test_model_requires_the_sections_of_the_parameter_model() -> None:
    required = {
        (field.alias or name)
        for name, field in PvSystemSpec.model_fields.items()
        if field.is_required()
    }
    assert required == set(EXPECTED_REQUIRED_SECTIONS)


def test_example_declares_schema_and_rulepack_versions(spec: dict[str, Any]) -> None:
    assert spec["schema_version"] == "0.1.0"
    assert spec["standards"]["rulepack"] == "mx-gd-2026.10"


def test_example_is_the_worked_7p70_kwp_6_kw_220_v_system(spec: dict[str, Any]) -> None:
    modules = {module["id"]: module for module in spec["modules"]}
    kwp = sum(modules[s["module"]]["pmax_w"] * s["n_series"] for s in spec["strings"]) / 1000
    assert kwp == pytest.approx(7.70)

    (inverter,) = spec["inverters"]
    assert inverter["pac_w"] == 6000
    assert inverter["vac_v"] == 220
    assert spec["utility"]["nominal_voltage_v"] == 220


def test_example_strings_reference_existing_components(spec: dict[str, Any]) -> None:
    module_ids = {module["id"] for module in spec["modules"]}
    inverters = {inverter["id"]: inverter for inverter in spec["inverters"]}
    for string in spec["strings"]:
        assert string["module"] in module_ids
        inverter = inverters[string["inverter"]]
        assert string["mppt"] in {mppt["id"] for mppt in inverter["mppt"]}


def test_example_circuits_connect_known_endpoints(spec: dict[str, Any]) -> None:
    known = {string["id"] for string in spec["strings"]}
    known |= {inverter["id"] for inverter in spec["inverters"]}
    known |= {ocpd["id"] for ocpd in spec["ac_bos"]["ocpds"]}
    for circuit in spec["circuits"]:
        for endpoint in (circuit["from"], circuit["to"]):
            assert endpoint.split(".")[0] in known


def test_example_header_states_source_and_disclaimer() -> None:
    header = "\n".join(EXAMPLE.read_text(encoding="utf-8").splitlines()[:8])
    assert "PV SLD Parameter Model" in header
    assert "illustrative sample, not an engineering deliverable" in header


def test_cli_accepts_the_example(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(EXAMPLE)]) == 0
    assert capsys.readouterr().out.startswith("OK:")
