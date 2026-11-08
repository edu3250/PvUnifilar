"""The per-string DC box on the A3 sheet (owner request 2026-11-04, drawn 2026-11-08)."""

from pathlib import Path

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.calc import derive
from pvsld.core.model import parse_spec
from pvsld.design.pipeline import load_quick_template
from pvsld.resources import data_path
from pvsld.service import generate_single_line_diagram, layout_diagram
from pvsld.sizing import request_from_mapping, size_pv_system
from pvsld.sizing.dcbox import apply_dc_box, dc_box


@pytest.fixture(scope="module")
def spec() -> dict:
    registry = ComponentRegistry.load(data_path("datasheets/records"))
    request = request_from_mapping(
        {
            "template": load_quick_template(),
            "module": "JINKO-JKM650N-66HL4M-BDV",
            "module_count_max": 10,
        }
    )
    sized = size_pv_system(request, registry).spec
    return apply_dc_box(sized, dc_box(sized, layout="per_string", registry=registry))


def test_each_string_has_its_spd_and_switch(spec) -> None:
    model = parse_spec(spec)
    diagram = layout_diagram(model, derive(model))
    symbols = {i.comp_id: i.symbol for i in diagram.instances}
    assert symbols["DCD-S1"] == symbols["DCD-S2"] == "PVSLD_DC_DISCONNECT_1S"
    assert symbols["DPS-S1"] == symbols["DPS-S2"] == "PVSLD_SPD_DC_BOX"
    assert "DCD-CD1" not in symbols
    assert not any(c.startswith("DCD-1") for c in symbols)  # the box replaces the integrated one
    grounded = {c.start.comp_id for c in diagram.connections if c.kind == "grounding"}
    assert {"DPS-S1", "DPS-S2"} <= grounded


def test_the_sheet_reads_back(spec, tmp_path: Path) -> None:
    result = generate_single_line_diagram(spec, tmp_path / "unifilar.dxf")
    assert result.ok, result.readback.summary() if result.readback else result.validation
