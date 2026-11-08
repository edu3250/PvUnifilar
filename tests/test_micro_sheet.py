"""The A3 sheet of microinverter designs (Stage 3.10.4, owner approval 2026-11-08)."""

from pathlib import Path
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.calc import derive
from pvsld.core.layout import LayoutError
from pvsld.core.model import parse_spec
from pvsld.design.pipeline import load_quick_template
from pvsld.resources import data_path
from pvsld.service import generate_single_line_diagram, layout_diagram
from pvsld.sizing import request_from_mapping, size_pv_system

GROWATT = [f"GROWATT-NEO-{w}M-X2" for w in (1600, 1800, 2000, 2250, 2500)]
CASES = {
    "two branches, breakers in the load centre": (
        "JINKO-JKM650N-66HL4M-BDV",
        10,
        {"microinverters": GROWATT},
    ),
    "AC combiner": (
        "JINKO-JKM650N-66HL4M-BDV",
        10,
        {"microinverters": GROWATT, "ac_combiner": True},
    ),
    "eight one-input units on one row": (
        "JINKO-JKM480N-60HL4",
        8,
        {"microinverters": ["ENPHASE-IQ8HC-72-M-US"]},
    ),
    "eight two-input units in two rows": (
        "JINKO-JKM650N-66HL4M-BDV",
        16,
        {"microinverters": ["APSYSTEMS-DS3-H"]},
    ),
}


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def _spec(registry, module: str, count: int, extra: dict[str, Any]) -> dict[str, Any]:
    request = request_from_mapping(
        {"template": load_quick_template(), "module": module, "module_count_max": count, **extra}
    )
    spec = size_pv_system(request, registry).spec
    assert spec is not None
    return spec


@pytest.mark.parametrize(("module", "count", "extra"), CASES.values(), ids=CASES.keys())
def test_the_sheet_is_drawn_and_reads_back(registry, tmp_path: Path, module, count, extra) -> None:
    spec = _spec(registry, module, count, extra)
    result = generate_single_line_diagram(spec, tmp_path / "unifilar.dxf")
    assert result.ok, result.readback.summary() if result.readback else result.validation
    blocks = result.readback.summary()["inserts_per_block"]
    units = sum(len(b["microinverters"]) for b in spec["ac_branches"])
    inputs = len(spec["inverters"][0]["mppt"])
    assert blocks[f"PVSLD_MICROINV_{inputs}IN"] == units + 1  # the units and the symbology
    assert blocks["PVSLD_PV_STRING_1M_UP"] == count
    assert blocks["PVSLD_AC_JBOX"] == len(spec["ac_branches"]) + 1


def test_the_combiner_and_its_markers(registry) -> None:
    spec = parse_spec(_spec(registry, *CASES["AC combiner"]))
    diagram = layout_diagram(spec, derive(spec))
    ids = {i.comp_id for i in diagram.instances}
    assert {"ITM-R1", "ITM-R2", "ITM-1", "DPS-CA1", "JB-1", "JB-2", "MI3"} <= ids
    circuits = {c.circuit_id for c in diagram.connections if c.circuit_id}
    assert circuits == {"C-R1", "C-R2", "C-CCA"}
    markers = sorted(  # the schematic's circuit markers (the circuit boxes repeat 1 and 2 below)
        t.text for t in diagram.texts if t.text in {"1", "2"} and t.align == "center" and t.y > 100
    )
    assert markers == ["1", "1", "2"]
    assert any("CAJA COMBINADORA" in t.text for t in diagram.texts)


def test_free_inputs_are_not_dangling(registry) -> None:
    spec = parse_spec(_spec(registry, *CASES["two branches, breakers in the load centre"]))
    diagram = layout_diagram(spec, derive(spec))
    mi3 = next(i for i in diagram.instances if i.comp_id == "MI3")
    modules = [i for i in diagram.instances if i.comp_id.startswith("M-MI3")]
    assert mi3.symbol == "PVSLD_MICROINV_4IN"
    assert len(modules) == 2  # MI3 has two free inputs


def test_a_design_larger_than_the_sheet_is_refused(registry) -> None:
    spec = parse_spec(
        _spec(registry, "JINKO-JKM480N-60HL4", 10, {"microinverters": ["ENPHASE-IQ8HC-72-M-US"]})
    )
    with pytest.raises(LayoutError, match="hasta 8 microinversores"):
        layout_diagram(spec, derive(spec))
