"""Sizing and reports with several string inverters (Stage 3.12.2, owner approval 2026-11-13).

The fewest inverters that reach the request, the strings spread evenly, mixed models when the
user names them, one shared breaker for all; the memoria, bill of materials and project sheet
cover every inverter.
"""

import math
import re
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.validation import validate_pv_design
from pvsld.design.pipeline import load_quick_template
from pvsld.report import bom_text, build_memoria, write_memoria_pdf, write_memoria_xlsx
from pvsld.report.project_sheet import write_project_sheet
from pvsld.resources import data_path
from pvsld.sizing import request_from_mapping, size_pv_system
from pvsld.sizing.models import Candidate
from pvsld.sizing.multi_sizing import JUNCTION_ID, MultiCandidate
from pvsld.sizing.report import format_report

MODULE = "JINKO-JKM650N-66HL4M-BDV"
MIN_5K = "GROWATT-MIN-5000TL-X2"
MIN_3K = "GROWATT-MIN-3000TL-X2"


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def _size(registry, **extra: Any):
    request = request_from_mapping({"template": load_quick_template(), "module": MODULE, **extra})
    return size_pv_system(request, registry)


@pytest.fixture(scope="module")
def ten_kw(registry):
    return _size(registry, target_dc_power_w=10400)


def _evaluate(formula: str, values: dict[str, float]) -> float:
    text = re.sub(r"\{(\w+)\}", lambda m: repr(values[m.group(1)]), formula)
    text = re.sub(r"\[(\w+)\]", lambda m: repr(values[m.group(1)]), text)
    text = text.replace("^", "**").replace("PI()", repr(math.pi))
    return float(eval(text, {"__builtins__": {}}))  # our own arithmetic


# --- Sizing ----------------------------------------------------------------------------------


def test_a_target_beyond_one_inverter_takes_the_fewest_inverters(ten_kw) -> None:
    selected = ten_kw.selected
    assert isinstance(selected, MultiCandidate)
    assert len(selected.units) == 2
    assert len(set(selected.inverter_ids)) == 1  # the same model, the same strings
    assert len({(u.n_strings, u.n_series) for u in selected.units}) == 1
    assert selected.p_dc_w == pytest.approx(10400)
    spec = selected.spec
    assert [i["id"] for i in spec["inverters"]] == ["INV1", "INV2"]
    assert spec["ac_bos"]["junction_boxes"] == [{"id": JUNCTION_ID}]
    itm = next(o for o in spec["ac_bos"]["ocpds"] if o["role"] == "I1")
    current = sum(i["iac_max_a"] for i in spec["inverters"])
    assert itm["rating_a"] >= 1.25 * current
    assert selected.breaker_a == itm["rating_a"]
    assert validate_pv_design(spec).ok
    assert "2 inversores" in selected.explanation_es
    assert any("CJ-CA1" in a for a in ten_kw.assumptions)


def test_a_target_one_inverter_reaches_stays_single(registry) -> None:
    result = _size(registry, target_dc_power_w=5200)
    assert isinstance(result.selected, Candidate)


def test_the_count_can_be_fixed(registry) -> None:
    result = _size(registry, module_count_max=12, inverter_count=3)
    selected = result.selected
    assert isinstance(selected, MultiCandidate)
    assert len(selected.units) == 3
    assert selected.modules <= 12


def test_mixed_models_share_the_modules_by_power(registry) -> None:
    result = _size(registry, target_dc_power_w=8000, inverter_set=[MIN_5K, MIN_3K])
    selected = result.selected
    assert isinstance(selected, MultiCandidate)
    assert selected.inverter_ids == (MIN_5K, MIN_3K)
    big, small = selected.units
    assert big.modules > small.modules
    assert validate_pv_design(selected.spec).ok


def test_the_busbar_rule_can_reject_the_shared_breaker(registry) -> None:
    # 2 x 5 kW: 60 A shared breaker + 100 A main > 1.2 x 125 A bus of the quick template
    result = _size(registry, module_count_max=16, inverters=[MIN_5K], inverter_count=2)
    assert result.selected is None
    assert any(r.errors[0].rule_id == "PCC-002" for r in result.rejected)


def test_fixed_protections_keep_the_automatic_search_single(registry) -> None:
    result = _size(registry, target_dc_power_w=10400, dc_fuses=["LITTELFUSE-SPF030"])
    assert not isinstance(result.selected, MultiCandidate)


def test_the_text_report_lists_each_inverter(ten_kw) -> None:
    text = format_report(ten_kw)
    assert "SELECTED: 2 x" in text
    assert "INV1:" in text
    assert "INV2:" in text


# --- Reports ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report(ten_kw):
    report = validate_pv_design(ten_kw.selected.spec)
    assert report.ok
    return report


def test_every_formula_of_the_memoria_gives_the_python_value(report, ten_kw) -> None:
    memoria = build_memoria(
        report.spec, report.derived, report.findings, explanation_es=ten_kw.selected.explanation_es
    )
    for calc in memoria.calcs:
        assert _evaluate(calc.formula, memoria.values) == pytest.approx(calc.value), calc.key
    assert memoria.ok
    keys = {c.key for c in memoria.calcs}
    assert {"iac125", "req75act", "ampact", "vdact"} <= keys
    shared = next(c for c in memoria.calcs if c.key == "iac125")
    assert "2 inversores" in shared.shown
    assert "2 ×" in memoria.subtitle


def test_mixed_strings_sum_the_array_power(registry) -> None:
    selected = _size(registry, target_dc_power_w=8000, inverter_set=[MIN_5K, MIN_3K]).selected
    report = validate_pv_design(selected.spec)
    memoria = build_memoria(report.spec, report.derived, report.findings)
    for calc in memoria.calcs:
        assert _evaluate(calc.formula, memoria.values) == pytest.approx(calc.value), calc.key
    pstc = next(c for c in memoria.calcs if c.key == "pstc")
    assert pstc.value == pytest.approx(selected.p_dc_w)
    assert len(memoria.equipment) == 3  # the module and both inverter models


def test_the_bill_of_materials_counts_every_inverter_and_box(report) -> None:
    text = bom_text(report.spec, report.derived)
    assert re.search(r"Inversor\.+\s+2 pza", text)
    assert re.search(r"Gabinete\.+\s+2 pza", text)
    assert re.search(r"Seccionador FV\.+\s+2 pza", text)
    assert "Caja de unión de CA" in text
    assert "C-INVT" in text


def test_the_writers_and_the_project_sheet(report, registry, tmp_path) -> None:
    memoria = build_memoria(report.spec, report.derived, report.findings)
    assert write_memoria_xlsx(memoria, tmp_path / "m.xlsx").stat().st_size > 0
    assert write_memoria_pdf(memoria, tmp_path / "m.pdf").stat().st_size > 0
    write_project_sheet(tmp_path / "hoja.xlsx", registry, report.spec, report.derived)
    assert (tmp_path / "hoja.xlsx").stat().st_size > 0
