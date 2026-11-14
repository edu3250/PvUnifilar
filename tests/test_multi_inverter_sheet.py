"""The A3 sheet with several string inverters (Stage 3.12.3, owner approval 2026-11-13).

Each inverter is a row (its strings, DC box and inverter as the one-inverter sheet draws them,
scaled), their outputs meet at the junction box CJ-CA1 and one run reaches the shared breaker.
Up to 3 inverters with 2 strings each; a larger design is refused with a clear message.
"""

import itertools
from pathlib import Path
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.calc import derive
from pvsld.core.layout import LayoutError
from pvsld.core.layout_multi import JUNCTION_ID
from pvsld.core.model import parse_spec
from pvsld.design.pipeline import load_quick_template
from pvsld.graph import graph_to_spec
from pvsld.resources import data_path
from pvsld.service import generate_single_line_diagram, layout_diagram
from pvsld.sizing import request_from_mapping, size_pv_system
from test_multi_inverter import GROWATT_3K, _graph

MODULE = "JINKO-JKM650N-66HL4M-BDV"
CASES = {
    "two inverters": {"target_dc_power_w": 10400},
    "three inverters": {"module_count_max": 12, "inverter_count": 3},
}


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def _spec(registry, **extra: Any) -> dict[str, Any]:
    request = request_from_mapping({"template": load_quick_template(), "module": MODULE, **extra})
    spec = size_pv_system(request, registry).spec
    assert spec is not None
    return spec


@pytest.mark.parametrize("extra", CASES.values(), ids=CASES.keys())
def test_the_sheet_is_drawn_and_reads_back(registry, tmp_path: Path, extra) -> None:
    spec = _spec(registry, **extra)
    result = generate_single_line_diagram(spec, tmp_path / "unifilar.dxf")
    assert result.ok, result.readback.summary() if result.readback else result.validation
    blocks = result.readback.summary()["inserts_per_block"]
    inverters = len(spec["inverters"])
    assert blocks["PVSLD_INV"] == inverters + 1  # the inverters and the symbology
    assert blocks["PVSLD_AC_JBOX"] == 2  # CJ-CA1 and the symbology


def test_every_inverter_reaches_the_shared_breaker(registry) -> None:
    spec = parse_spec(_spec(registry, **CASES["three inverters"]))
    diagram = layout_diagram(spec, derive(spec))
    ids = {i.comp_id for i in diagram.instances}
    assert {"INV1", "INV2", "INV3", JUNCTION_ID, "ITM-1", "CC-1", "ITM-P", "M-1"} <= ids
    assert {"DCD-CD1", "DCD-CD2", "DCD-CD3", "DPS-CD1", "DPS-CD2", "DPS-CD3"} <= ids
    runs = {
        c.circuit_id: (c.start.comp_id, c.end.comp_id)
        for c in diagram.connections
        if c.kind == "inverter_output"
    }
    assert runs == {
        "C-INV1": ("INV1", JUNCTION_ID),
        "C-INV2": ("INV2", JUNCTION_ID),
        "C-INV3": ("INV3", JUNCTION_ID),
        "C-INVT": (JUNCTION_ID, "ITM-1"),
    }
    earths = {c.start.comp_id for c in diagram.connections if c.kind == "grounding"}
    assert {"INV1", "INV2", "INV3", "DPS-CD1", "DPS-CD2", "DPS-CD3", "CC-1"} <= earths
    # the rows do not overlap: each inverter sits in its own row
    heights = sorted(i.y for i in diagram.instances if i.comp_id.startswith("INV"))
    assert all(b - a > 30 for a, b in itertools.pairwise(heights))


def test_the_boxes_describe_every_inverter(registry) -> None:
    spec = parse_spec(_spec(registry, **CASES["two inverters"]))
    diagram = layout_diagram(spec, derive(spec))
    text = "\n".join(t.text for t in diagram.texts)
    assert "2 x C-INV:" in text
    assert "C-INVT:" in text
    assert "ITM compartido" in text
    assert "(x 2)" in text  # the inverter box
    assert "+ 2 x GROWATT" in text  # the project line
    assert "8,400 W" in text  # the AC capacity of both inverters


def test_a_design_larger_than_the_sheet_is_refused(registry) -> None:
    # four inverters drawn in the editor (the busbar rule would reject them in sizing)
    template = _spec(registry, **CASES["two inverters"])
    template = {k: v for k, v in template.items() if k not in ("modules", "inverters", "strings")}
    template["ac_bos"]["ocpds"] = []
    template["ac_bos"]["junction_boxes"] = []
    result = graph_to_spec(_graph([GROWATT_3K] * 4), template, registry=registry)
    spec = parse_spec(result.spec)
    assert len(spec.inverters) == 4
    with pytest.raises(LayoutError, match="hasta 3 inversores"):
        layout_diagram(spec, derive(spec))


def test_one_inverter_keeps_its_sheet(registry) -> None:
    spec = parse_spec(_spec(registry, module_count_max=10))
    diagram = layout_diagram(spec, derive(spec))
    ids = {i.comp_id for i in diagram.instances}
    assert JUNCTION_ID not in ids
    assert "PI-1" in ids
