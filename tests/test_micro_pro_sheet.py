"""Microinverter rows of the project sheet (professional mode, Stage 3.10.5)."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from pvsld.catalogue import ComponentRegistry
from pvsld.design.pro import read_project_sheet, rejection_issues, sheet_request
from pvsld.report import write_project_sheet
from pvsld.resources import data_path
from pvsld.sizing import request_from_mapping, size_pv_system
from pvsld.sizing.micro_sizing import MicroCandidate

TODAY = date(2026, 11, 8)


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def _row(ws: Any, label: str) -> int:
    return next(
        r for r in range(5, ws.max_row + 1) if ws.cell(r, 1).value == label and ws.cell(r, 4).value
    )


def _sheet(tmp_path: Path, registry, changes: dict[tuple[str, str], Any]) -> Path:
    path = write_project_sheet(tmp_path / "hoja.xlsx", registry)
    workbook = load_workbook(path)
    for (name, label), value in changes.items():
        ws = workbook[name]
        ws.cell(_row(ws, label), 2).value = value
    workbook.save(path)
    return path


BASE = {
    ("Equipos", "Módulo fotovoltaico"): "JINKO-JKM650N-66HL4M-BDV",
    ("Equipos", "Cantidad total de módulos"): 10,
    ("Equipos", "Tipo de inversor"): "Microinversor",
}


def _request(path: Path, registry) -> tuple[dict[str, Any], list]:
    return sheet_request(read_project_sheet(path), registry, today=TODAY)


def test_the_sheet_offers_the_micro_rows(tmp_path: Path, registry) -> None:
    workbook = load_workbook(write_project_sheet(tmp_path / "hoja.xlsx", registry))
    labels = {ws.title: {ws.cell(r, 1).value for r in range(5, ws.max_row + 1)} for ws in workbook}
    assert {"Tipo de inversor", "Microinversor", "Caja combinadora de CA"} <= labels["Equipos"]
    assert {"Calibre del troncal", "Separación entre microinversores"} <= labels["Conductores"]


def test_auto_micro_design(tmp_path: Path, registry) -> None:
    request, issues = _request(_sheet(tmp_path, registry, BASE), registry)
    assert issues == []
    assert request["microinverters"] == "auto"
    assert request["ac_combiner"] is False
    selected = size_pv_system(request_from_mapping(request), registry).selected
    assert isinstance(selected, MicroCandidate)


def test_fixed_micro_combiner_trunk_and_spacing(tmp_path: Path, registry) -> None:
    changes = {
        **BASE,
        ("Equipos", "Microinversor"): "GROWATT-NEO-2000M-X2",
        ("Equipos", "Caja combinadora de CA"): "Sí",
        ("Conductores", "Calibre del troncal"): "10 AWG",
        ("Conductores", "Separación entre microinversores"): 5,
    }
    request, issues = _request(_sheet(tmp_path, registry, changes), registry)
    assert issues == []
    assert request["microinverters"] == ["GROWATT-NEO-2000M-X2"]
    assert request["micro_trunk_size"] == "10 AWG"
    assert request["routing"]["micro_spacing_m"] == 5
    selected = size_pv_system(request_from_mapping(request), registry).selected
    assert selected.combiner
    assert {b.trunk_size for b in selected.branches} == {"10 AWG"}
    assert {b["spacing_m"] for b in selected.spec["ac_branches"]} == {5}


def test_a_trunk_too_small_is_put_back_on_its_cell(tmp_path: Path, registry) -> None:
    changes = {
        **BASE,
        ("Equipos", "Microinversor"): "GROWATT-NEO-2500M-X2",
        ("Conductores", "Calibre del troncal"): "12 AWG",
    }
    path = _sheet(tmp_path, registry, changes)
    cells = read_project_sheet(path)
    request, _issues = sheet_request(cells, registry, today=TODAY)
    sizing = size_pv_system(request_from_mapping(request), registry)
    assert sizing.selected is None or all(b.units == 1 for b in sizing.selected.branches)
    if sizing.selected is None:
        found = rejection_issues(sizing, request, cells)
        assert ("Conductores", "Calibre del troncal") in {(i.sheet, i.label) for i in found}


def test_a_string_sheet_ignores_the_micro_rows(tmp_path: Path, registry) -> None:
    changes = {**BASE, ("Equipos", "Tipo de inversor"): "Cadena"}
    request, issues = _request(_sheet(tmp_path, registry, changes), registry)
    assert issues == []
    assert "microinverters" not in request


def test_an_older_sheet_without_the_rows_is_a_string_design(tmp_path: Path, registry) -> None:
    path = _sheet(tmp_path, registry, {k: v for k, v in BASE.items() if k[1] != "Tipo de inversor"})
    workbook = load_workbook(path)
    ws = workbook["Equipos"]
    ws.delete_rows(_row(ws, "Tipo de inversor"), 3)
    workbook.save(path)
    request, issues = _request(path, registry)
    assert issues == []
    assert "microinverters" not in request


def test_the_sheet_designs_and_draws_a_micro_system(tmp_path: Path, registry) -> None:
    from pvsld.design.pipeline import DXF, PROJECT_SHEET
    from pvsld.design.pro import design_from_sheet
    from pvsld.finishers.core_console import AutocadInfo

    path = _sheet(tmp_path, registry, {**BASE, ("Equipos", "Caja combinadora de CA"): "Sí"})
    result = design_from_sheet(
        path,
        registry,
        tmp_path / "proyecto",
        use_autocad="never",
        autocad=AutocadInfo(None),
        today=TODAY,
    )
    assert result.ok, result
    assert result.design is not None
    assert DXF in result.design.files
    assert PROJECT_SHEET in result.design.files
    refreshed = load_workbook(result.design.files[PROJECT_SHEET])
    ws = refreshed["Equipos"]
    assert ws.cell(_row(ws, "Tipo de inversor"), 2).value == "Microinversor"
