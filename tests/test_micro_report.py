"""Calculation report and bill of materials of microinverter designs (Stage 3.10.3)."""

import math
import re
from typing import Any

import pytest

from pvsld.catalogue import ComponentRegistry
from pvsld.core.validation import validate_pv_design
from pvsld.design.pipeline import load_quick_template
from pvsld.report import (
    bill_of_materials,
    bom_text,
    build_memoria,
    write_memoria_pdf,
    write_memoria_xlsx,
)
from pvsld.resources import data_path
from pvsld.sizing import request_from_mapping, size_pv_system

MODULE = "JINKO-JKM650N-66HL4M-BDV"


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def _report(registry, modules: int = 10, **extra: Any):
    request = request_from_mapping(
        {
            "template": load_quick_template(),
            "module": MODULE,
            "microinverters": [f"GROWATT-NEO-{w}M-X2" for w in (1600, 1800, 2000, 2250, 2500)],
            "module_count_min": modules,
            "module_count_max": modules,
            **extra,
        }
    )
    report = validate_pv_design(size_pv_system(request, registry).spec)
    assert report.ok
    return report


def _evaluate(formula: str, values: dict[str, float]) -> float:
    """The Excel formula with its inputs and results substituted, evaluated in Python."""
    text = re.sub(r"\{(\w+)\}", lambda m: repr(values[m.group(1)]), formula)
    text = re.sub(r"\[(\w+)\]", lambda m: repr(values[m.group(1)]), text)
    text = text.replace("^", "**").replace("PI()", repr(math.pi))
    return float(eval(text, {"__builtins__": {}}))  # our own arithmetic


@pytest.mark.parametrize("combiner", [False, True])
def test_every_formula_gives_the_python_value(registry, combiner) -> None:
    report = _report(registry, ac_combiner=combiner)
    memoria = build_memoria(report.spec, report.derived, report.findings)
    values = memoria.values
    for calc in memoria.calcs:
        assert _evaluate(calc.formula, values) == pytest.approx(calc.value), calc.key
    assert memoria.ok


def test_the_report_covers_inputs_branches_and_the_busbar(registry) -> None:
    report = _report(registry, ac_combiner=True)
    memoria = build_memoria(report.spec, report.derived, report.findings)
    keys = {c.key for c in memoria.calcs}
    assert {"vocmod", "iscchk", "vmphot", "vservchk", "rule120", "minmain"} <= keys
    assert {"iR1", "minR1", "trunkR1", "vdR1", "iR2", "vdR2"} <= keys
    drop = next(c for c in memoria.calcs if c.key == "vdR1")
    circuit = next(c for c in report.derived.circuits if c.circuit_id == "C-R1")
    assert drop.value == pytest.approx(circuit.vd_pct)  # same daisy-chain drop as the engine
    assert "microinversores" in memoria.subtitle


def test_the_writers_render_it(registry, tmp_path) -> None:
    report = _report(registry)
    memoria = build_memoria(report.spec, report.derived, report.findings)
    assert write_memoria_xlsx(memoria, tmp_path / "m.xlsx").stat().st_size > 0
    assert write_memoria_pdf(memoria, tmp_path / "m.pdf").stat().st_size > 0


def test_the_bill_of_materials_lists_the_micro_parts(registry) -> None:
    report = _report(registry, ac_combiner=True)
    lines = {
        (line.group, line.item): line for line in bill_of_materials(report.spec, report.derived)
    }
    assert lines[("Generación", "Módulo fotovoltaico")].quantity == 10
    assert lines[("Generación", "Microinversor")].quantity == 3
    assert lines[("Microinversores", "Cable troncal 10 AWG")].unit == "m"
    assert lines[("Microinversores", "Caja de conexiones de CA")].quantity == 2
    assert lines[("Microinversores", "Caja combinadora de CA")].quantity == 1
    text = bom_text(report.spec, report.derived)
    assert "Microinversor" in text
    assert "Inversor " not in text


def test_the_project_sheet_is_written(registry, tmp_path) -> None:
    from pvsld.report import write_project_sheet

    report = _report(registry)
    path = write_project_sheet(tmp_path / "hoja.xlsx", registry, report.spec, report.derived)
    assert path.stat().st_size > 0
