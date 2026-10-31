"""The DC protection box, sized and assembled from the strings (ENG-004, Stage 3.8.4)."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.validation import validate_pv_design
from pvsld.resources import data_path
from pvsld.sizing import size_pv_system
from pvsld.sizing.dcbox import SPD_ID, apply_dc_box, dc_box
from pvsld.sizing.io import load_request
from pvsld.symbols.cfe import get_symbol


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


@pytest.fixture(scope="module")
def sized(registry: ComponentRegistry) -> dict[str, Any]:
    request = load_request(Path(data_path("examples/sizing_jinko_growatt.yaml")))
    return size_pv_system(request, registry).candidates[0].spec


def _two(sized: dict[str, Any]) -> dict[str, Any]:
    """The owner's acceptance case: two strings of five modules, one per MPPT."""
    spec = copy.deepcopy(sized)
    for string in spec["strings"]:
        string["n_series"] = 5
    return spec


def _one(spec: dict[str, Any]) -> dict[str, Any]:
    spec = copy.deepcopy(spec)
    spec["strings"] = spec["strings"][:1]
    spec["circuits"] = [c for c in spec["circuits"] if c["id"] != "C-S2"]
    return spec


def _ids(box) -> list[str]:
    return [c.id for c in box.children]


def test_one_string_box(sized, registry) -> None:
    box = dc_box(_one(_two(sized)), registry=registry)
    assert box.built
    assert _ids(box) == ["CX-S1", "XE-S1", "FUS-S1", "XS-S1", SPD_ID, "DCD-CD1"]
    fuse = box.child("FUS-S1")
    assert (fuse.block, fuse.poles, fuse.model) == ("PVSLD_FUSE_DISC_DC", 2, "LITTELFUSE-SPF030")
    assert fuse.values == {"ie_a": 30.0, "ue_v": 1000.0}
    assert "No se requiere" in fuse.reason_es
    assert "decisión de diseño" in fuse.reason_es
    switch = box.child("DCD-CD1")
    assert (switch.block, switch.poles) == ("PVSLD_DC_DISCONNECT_1S", 4)
    assert "4 polos por cadena" in switch.reason_es
    assert [r[0] for r in box.rows] == ["FUS-S1", SPD_ID, "DCD-CD1"]


def test_second_string_adds_its_parts(sized, registry) -> None:
    box = dc_box(_two(sized), registry=registry)
    assert _ids(box) == [
        "CX-S1",
        "XE-S1",
        "FUS-S1",
        "XS-S1",
        "CX-S2",
        "XE-S2",
        "FUS-S2",
        "XS-S2",
        SPD_ID,
        "DCD-CD1",
    ]
    switch = box.child("DCD-CD1")
    assert (switch.block, switch.poles) == ("PVSLD_DC_DISCONNECT_2S", 4)
    assert "2 polos por cadena" in switch.reason_es
    assert switch.string_ids == ("S1", "S2")
    assert box.child("FUS-S2").string_ids == ("S2",)
    assert [r[0] for r in box.rows] == ["FUS-S1", "FUS-S2", SPD_ID, "DCD-CD1"]


def test_removing_the_string_reverts_the_box(sized, registry) -> None:
    before = dc_box(_one(_two(sized)), registry=registry).to_dict()
    grown = _two(sized)
    applied = apply_dc_box(grown, dc_box(grown, registry=registry))
    after = dc_box(_one(applied), registry=registry).to_dict()
    for key in ("children", "links", "rows", "disconnects", "spds"):
        assert after[key] == before[key]


@pytest.mark.parametrize("strings", [1, 2])
def test_applied_box_validates_and_drops_old_parts(sized, registry, strings) -> None:
    grown = _two(sized)
    grown = apply_dc_box(grown, dc_box(grown, registry=registry))
    spec = grown if strings == 2 else _one(grown)
    result = apply_dc_box(spec, dc_box(spec, registry=registry))
    ids = [d["id"] for d in result["dc_bos"]["disconnects"]]
    assert ids[0] == "DCD-1"  # the inverter's integrated switch stays
    assert ("FUS-S2" in ids) is (strings == 2)
    assert [s["id"] for s in result["dc_bos"]["spds"]] == [SPD_ID]
    assert validate_pv_design(result).ok


def test_the_box_needs_no_ac_side(sized, registry) -> None:
    spec = _two(sized)
    del spec["ac_bos"]
    del spec["circuits"]
    box = dc_box(spec, registry=registry)
    assert box.built
    assert "FUS-S2" in _ids(box)


def test_breaker_on_request(sized, registry) -> None:
    box = dc_box(_two(sized), device="breaker", registry=registry)
    breaker = box.child("DCB-S1")
    assert (breaker.type_id, breaker.block) == ("dc_breaker", "PVSLD_CB_DC")
    assert breaker.model is not None
    assert box.rows[0][1] == "ITM de CD (cadena)"


def test_spd_is_sized_from_the_strings(sized, registry) -> None:
    box = dc_box(_two(sized), registry=registry)
    spd = box.child(SPD_ID)
    needs = spd.requirements
    assert spd.values["spd_type"] == "T2"
    assert spd.values["ucpv_v"] >= needs["ucpv_min_v"]
    assert spd.values["in_ka"] >= needs["in_min_ka"] == 5
    assert needs["iscpv_min_a"] == pytest.approx(2 * 1.25 * 18.17, abs=0.1)
    assert spd.model is None
    assert any("DPS" in a for a in box.assumptions)
    assert spd.ports == ("L1", "L2", "PE")


def test_locked_replacement_is_kept(sized, registry) -> None:
    spec = _two(sized)
    fuse = next(d for d in spec["dc_bos"]["disconnects"] if d["id"] == "FUS-S1")
    fuse.update(ie_a=32, model="MY-FUSE")
    spd = spec["dc_bos"].setdefault("spds", [])
    spd[:] = [{"id": SPD_ID, "at": "INV1.dc", "spd_type": "T2", "ucpv_v": 1000, "in_ka": 40}]
    box = dc_box(spec, locks=["FUS-S1.ie_a", "FUS-S1.model", SPD_ID], registry=registry)
    kept = box.child("FUS-S1")
    assert (kept.values["ie_a"], kept.model, kept.source) == (32, "MY-FUSE", "drawn")
    assert box.child(SPD_ID).values["in_ka"] == 40
    assert box.child(SPD_ID).source == "drawn"
    # unlocked parts follow the engine
    assert box.child("FUS-S2").values["ie_a"] == 30.0
    assert box.child("FUS-S2").source == "engine"


def test_ports_and_links_are_stable(sized, registry) -> None:
    box = dc_box(_two(sized), registry=registry)
    ports = {f"{c.id}.{p}" for c in box.children for p in c.ports}
    for child in box.children:
        assert child.ports == tuple(p.id for p in get_symbol(child.block).ports)
    outside = {"S1.OUT", "S2.OUT", "INV1.A", "INV1.B"}
    for link in box.links:
        assert link.from_port in ports | outside
        assert link.to_port in ports | outside
    assert box.to_dict() == dc_box(_two(sized), registry=registry).to_dict()


def test_incomplete_strings_skip_the_box(sized, registry) -> None:
    spec = _two(sized)
    del spec["modules"][0]["voc_v"]
    box = dc_box(spec, registry=registry)
    assert not box.built
    assert box.children == ()
    with pytest.raises(ValueError, match="caja"):
        apply_dc_box(spec, box)


def test_box_is_plain_data(sized, registry) -> None:
    data = dc_box(_two(sized), registry=registry).to_dict()
    json.dumps(data)
    assert data["built"] is True
    assert data["header"][0] == "Equipo"


def test_evaluate_choices_agrees_with_the_box(sized, registry) -> None:
    from pvsld.sizing.choices import evaluate_choices

    spec = _two(sized)
    spec = apply_dc_box(spec, dc_box(spec, registry=registry))
    report = evaluate_choices(spec, registry=registry)
    box_parts = ("FUS-", "DCD-CD1")
    assert [c.target for c in report.updates if c.target.startswith(box_parts)] == []
    assert all(c.ok for c in report.checks if c.target.startswith(box_parts))
