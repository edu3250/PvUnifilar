"""``pvsld catalogue verify`` / ``render``: the mechanical review of catalogue pull requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from reportlab.pdfgen import canvas

from catalogue_helpers import ET_MODULE, mark_unreviewed, raw, write
from pvsld.catalogue.verify import (
    printed_numbers,
    record_numbers,
    render_pages,
    sha256_of,
    verify_record,
)
from pvsld.cli import main

pytest.importorskip("pypdfium2")

URL = "https://example.com/datasheets/et-m672bh.pdf"


def _datasheet(path: Path, data: dict[str, Any], *, skip: str | None = None) -> Path:
    """A two-page PDF: page 1 prints every number of the record (but ``skip``), page 2 is blank."""
    pdf = canvas.Canvas(str(path))
    y = 800.0
    for where, value in record_numbers(data):
        if where != skip:
            pdf.drawString(40, y, f"{where.rsplit('.', 1)[-1]} {value:g}")
            y -= 9
            if y < 40:
                pdf.showPage()
                y = 800.0
    pdf.showPage()
    pdf.showPage()  # the blank page
    pdf.save()
    return path


def _draft(tmp_path: Path, **source: Any) -> tuple[Path, Path, dict[str, Any]]:
    """An unreviewed ET Solar draft in a fresh records folder, with its datasheet."""
    data = mark_unreviewed(raw(ET_MODULE))
    data["source"].update(url=URL, filename="et.pdf", pages=[1])
    pdf = _datasheet(tmp_path / "et.pdf", data)
    data["source"]["sha256"] = sha256_of(pdf)
    data["source"].update(source)
    record = write(tmp_path / "records", ET_MODULE, data)
    return record, pdf, data


def test_a_correct_draft_passes_and_is_fully_confirmed(tmp_path: Path) -> None:
    record, pdf, _ = _draft(tmp_path)
    report = verify_record(record, pdf)
    assert report.ok, report.problems
    assert report.fully_confirmed
    assert report.reviewed_by is None
    assert "**Result: PASS**" in report.to_markdown()
    assert "Fully confirmed by the text layer: yes" in report.to_markdown()


def test_a_value_the_datasheet_does_not_print_is_listed(tmp_path: Path) -> None:
    record, pdf, data = _draft(tmp_path)
    where = "variants[ETSOLAR-ET-M672BH530].voc_v"
    _datasheet(pdf, data, skip=where)
    write(
        tmp_path / "records",
        ET_MODULE,
        data | {"source": data["source"] | {"sha256": sha256_of(pdf)}},
    )
    report = verify_record(record, pdf)
    assert report.ok
    assert [check.field for check in report.unconfirmed] == [where]
    assert not report.fully_confirmed
    assert f"| `{where}` | 49.26 |" in report.to_markdown()


def test_another_pdf_fails_on_the_sha256(tmp_path: Path) -> None:
    record, pdf, _ = _draft(tmp_path, sha256="0" * 64)
    report = verify_record(record, pdf)
    assert not report.ok
    assert any("source.sha256 does not match et.pdf" in problem for problem in report.problems)


def test_the_official_url_is_required(tmp_path: Path) -> None:
    record, pdf, _ = _draft(tmp_path, url=None)
    report = verify_record(record, pdf)
    assert report.problems == ["source.url is required: the official datasheet address"]


def test_an_owner_supplied_datasheet_needs_no_url(tmp_path: Path) -> None:
    record, pdf, _ = _draft(tmp_path, url=None)
    report = verify_record(record, pdf, owner_supplied=True)
    assert report.ok, report.problems
    assert "(supplied by the owner)" in report.to_markdown()
    assert main(["catalogue", "verify", str(record), "--pdf", str(pdf), "--owner-supplied"]) == 0


def test_a_component_id_already_in_the_catalogue_fails(tmp_path: Path) -> None:
    record, pdf, _ = _draft(tmp_path)
    other = mark_unreviewed(raw(ET_MODULE))
    other["family_id"] = "OTHER-FAMILY"
    write(tmp_path / "records", "modules/other-family.yaml", other)
    report = verify_record(record, pdf)
    assert any(
        "component_id ETSOLAR-ET-M672BH520 already exists in" in problem
        for problem in report.problems
    )


def test_the_record_must_sit_in_its_type_folder(tmp_path: Path) -> None:
    _, pdf, data = _draft(tmp_path)
    misplaced = write(tmp_path / "records", "inverters/etsolar-et-m672bh.yaml", data)
    report = verify_record(misplaced, pdf, tmp_path / "records")
    assert any("must be in" in problem and "/modules/" in problem for problem in report.problems)


def test_a_page_without_text_leaves_every_value_to_the_reviewer(tmp_path: Path) -> None:
    record, pdf, _ = _draft(tmp_path, pages=[2])
    report = verify_record(record, pdf)
    assert report.ok
    assert report.pages_without_text == [2]
    assert len(report.unconfirmed) == len(report.checks) > 0
    assert "Pages without a text layer: 2" in report.to_markdown()


def test_a_page_the_pdf_does_not_have_fails(tmp_path: Path) -> None:
    record, pdf, _ = _draft(tmp_path, pages=[1, 5])
    report = verify_record(record, pdf)
    assert "et.pdf has 2 page(s); no page 5" in report.problems


def test_a_schema_error_is_reported_without_reading_the_pdf(tmp_path: Path) -> None:
    record, pdf, data = _draft(tmp_path)
    data["variants"][0]["voc_v"] = 1.0  # below vmp_v
    write(tmp_path / "records", ET_MODULE, data)
    report = verify_record(record, pdf)
    assert not report.ok
    assert any("vmp_v" in problem for problem in report.problems)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Voc 42,54 V", {42.54}),
        ("1,000 VDC", {1.0, 1000.0}),
        ("-0.30%/°C", {0.30}),
        ("1903×1134×30mm", {1903.0, 1134.0, 30.0}),
    ],
)
def test_printed_numbers_reads_both_decimal_conventions(text: str, expected: set[float]) -> None:
    assert expected <= printed_numbers(text)


def test_render_writes_the_cited_pages(tmp_path: Path) -> None:
    _, pdf, _ = _draft(tmp_path)
    written = render_pages(pdf, [1, 2], tmp_path / "review", scale=0.5)
    assert [path.name for path in written] == ["page-1.png", "page-2.png"]
    assert all(path.stat().st_size > 0 for path in written)
    with pytest.raises(ValueError, match="no page 3"):
        render_pages(pdf, [3], tmp_path / "review")


def test_cli_verify_prints_and_writes_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record, pdf, _ = _draft(tmp_path)
    report_file = tmp_path / "report.md"
    code = main(
        ["catalogue", "verify", str(record), "--pdf", str(pdf), "--markdown", str(report_file)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "**Result: PASS**" in out
    assert report_file.read_text(encoding="utf-8") == out


def test_cli_verify_exits_1_when_the_record_cannot_be_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record, pdf, _ = _draft(tmp_path, url=None)
    assert main(["catalogue", "verify", str(record), "--pdf", str(pdf)]) == 1
    assert "**Result: FAIL**" in capsys.readouterr().out


def test_cli_render(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, pdf, _ = _draft(tmp_path)
    code = main(["catalogue", "render", str(pdf), "--pages", "1", "--out", str(tmp_path / "png")])
    assert code == 0
    assert "page-1.png" in capsys.readouterr().out
