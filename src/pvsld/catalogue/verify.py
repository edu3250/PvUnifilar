"""Check a catalogue record against its datasheet before review (catalogue pull requests).

A contributor (often a model) drafts a record from a manufacturer datasheet and opens a pull
request. This module does the mechanical part of the review so the reviewer only looks at what a
machine cannot confirm:

* the record passes the schema and its plausibility rules, sits in the right folder and adds no
  ``component_id`` the catalogue already has;
* the local PDF is the one the record names (SHA-256) and has the cited pages;
* every number of the record appears in the text layer of the cited pages. Datasheets exported
  with outlined fonts have no text layer; their values are left for the reviewer, who reads the
  rendered pages (``render_pages``).

Example::

    report = verify_record(Path("datasheets/records/modules/acme-x.yaml"), Path("acme.pdf"))
    print(report.to_markdown())

PDF reading needs ``pypdfium2`` (``pip install "pvsld[catalogue]"``); it is imported lazily so the
design engine and the MCP server do not depend on it.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pvsld.catalogue.errors import CatalogueError
from pvsld.catalogue.registry import RECORD_FOLDERS, Family, read_record

MIN_TEXT_CHARS = 40
"""A page with fewer extracted characters is treated as having no text layer."""

SKIPPED_KEYS = frozenset({"source"})
"""Record sections that are not datasheet values (provenance is checked on its own)."""

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


class PdfReaderMissingError(RuntimeError):
    """``pypdfium2`` is not installed."""


@dataclass(frozen=True)
class ValueCheck:
    """One number of the record and whether the cited pages print it."""

    field: str
    value: float
    found: bool


@dataclass
class VerifyReport:
    """What ``verify_record`` found; ``ok`` is False when the pull request cannot be merged."""

    record: Path
    pdf: Path
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[ValueCheck] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    pages_without_text: list[int] = field(default_factory=list)
    family_id: str | None = None
    reviewed_by: str | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def unconfirmed(self) -> list[ValueCheck]:
        return [check for check in self.checks if not check.found]

    @property
    def fully_confirmed(self) -> bool:
        """Every value was found in the text layer (a condition for automatic approval)."""
        return self.ok and bool(self.checks) and not self.unconfirmed

    def to_markdown(self) -> str:
        """The report as pasted into the pull request ("Verifier report" section)."""
        found = len(self.checks) - len(self.unconfirmed)
        result = "PASS" if self.ok else "FAIL"
        lines = [
            "### pvsld catalogue verify",
            "",
            f"- Record: `{self.record.as_posix()}` (family `{self.family_id or '?'}`)",
            f"- Datasheet: `{self.pdf.name}`, pages {_pages(self.pages)}",
            f"- Text layer: {found} of {len(self.checks)} values found on the cited pages",
        ]
        if self.pages_without_text:
            lines.append(
                f"- Pages without a text layer: {_pages(self.pages_without_text)} "
                "(the reviewer reads the rendered pages)"
            )
        lines.append(
            f"- Fully confirmed by the text layer: {'yes' if self.fully_confirmed else 'no'}"
        )
        lines.append(f"- reviewed_by: `{self.reviewed_by}`")
        lines.append(f"- **Result: {result}**")
        for title, items in (("Problems", self.problems), ("Warnings", self.warnings)):
            if items:
                lines += ["", f"**{title}**", ""] + [f"- {item}" for item in items]
        if self.unconfirmed:
            lines += ["", "**Values not found in the text layer**", ""]
            lines += ["| Field | Value |", "|---|---|"]
            lines += [f"| `{c.field}` | {c.value:g} |" for c in self.unconfirmed]
        return "\n".join(lines) + "\n"


def _pages(pages: list[int]) -> str:
    return ", ".join(str(page) for page in pages) or "none"


def sha256_of(path: Path) -> str:
    """SHA-256 of a file, as stored in ``source.sha256``."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdfium() -> Any:
    try:
        import pypdfium2
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise PdfReaderMissingError(
            'reading PDFs needs pypdfium2: pip install "pvsld[catalogue]"'
        ) from error
    return pypdfium2


def page_texts(pdf: Path) -> list[str]:
    """The text layer of every page (index 0 = page 1)."""
    document = _pdfium().PdfDocument(str(pdf))
    try:
        texts = []
        for page in document:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range())
            textpage.close()
            page.close()
        return texts
    finally:
        document.close()


def render_pages(pdf: Path, pages: list[int], out: Path, *, scale: float = 2.0) -> list[Path]:
    """Render the given 1-based pages to ``out/page-<n>.png`` for the reviewer."""
    document = _pdfium().PdfDocument(str(pdf))
    try:
        count = len(document)
        missing = [page for page in pages if not 1 <= page <= count]
        if missing:
            raise ValueError(f"{pdf.name} has {count} page(s); no page {_pages(missing)}")
        out.mkdir(parents=True, exist_ok=True)
        written = []
        for number in pages:
            target = out / f"page-{number}.png"
            document[number - 1].render(scale=scale).to_pil().save(target)
            written.append(target)
        return written
    finally:
        document.close()


def printed_numbers(text: str) -> set[float]:
    """Every number printed in ``text``, without sign, in both decimal conventions.

    "42.54" and "42,54" give 42.54; "1,000" gives 1.0 and 1000.0 because a comma can be a decimal
    or a thousands separator.
    """
    numbers: set[float] = set()
    for token in _NUMBER.findall(text):
        # English style "1,000.5" and Spanish/European style "1.000,5"; extra readings are harmless.
        for reading in (token.replace(",", ""), token.replace(".", "").replace(",", ".")):
            try:
                numbers.add(float(reading))
            except ValueError:
                continue
    return numbers


def record_numbers(data: Any, path: str = "") -> Iterator[tuple[str, float]]:
    """Every numeric leaf of a record, with a readable location (variants by component_id)."""
    if isinstance(data, dict):
        for key, value in data.items():
            if not path and key in SKIPPED_KEYS:
                continue
            yield from record_numbers(value, f"{path}.{key}" if path else str(key))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            label = value.get("component_id") if isinstance(value, dict) else None
            yield from record_numbers(value, f"{path}[{label if label else index}]")
    elif isinstance(data, bool):
        return
    elif isinstance(data, int | float):
        yield path, float(data)


def _is_printed(value: float, numbers: set[float]) -> bool:
    return any(math.isclose(abs(value), number, rel_tol=0, abs_tol=1e-9) for number in numbers)


def _catalogue_problems(record: Path, family: Family, records_dir: Path) -> list[str]:
    """Folder, file name and component ids against the rest of the catalogue."""
    problems: list[str] = []
    folder = RECORD_FOLDERS[family.component_type]
    if record.parent.name != folder or record.parent.parent.resolve() != records_dir.resolve():
        problems.append(f"the record must be in {records_dir.as_posix()}/{folder}/")
    if record.stem != family.family_id.lower():
        problems.append(f"the file name must be {family.family_id.lower()}.yaml")
    taken: dict[str, Path] = {}
    families: set[str] = set()
    for path in sorted(records_dir.rglob("*.yaml")):
        if path.resolve() == record.resolve():
            continue
        try:
            other = read_record(path)
        except CatalogueError as error:
            problems += [f"catalogue: {problem}" for problem in error.problems]
            continue
        families.add(other.family_id)
        taken.update((variant.component_id, path) for variant in other.variants)
    problems += [
        f"component_id {variant.component_id} already exists in "
        f"{taken[variant.component_id].as_posix()}"
        for variant in family.variants
        if variant.component_id in taken
    ]
    if family.family_id in families:
        problems.append(f"family_id {family.family_id} already exists in the catalogue")
    return problems


def _module_warnings(data: dict[str, Any]) -> list[str]:
    warnings = []
    signs = {
        "temp_coef_pmax_pct_per_c": -1,
        "temp_coef_voc_pct_per_c": -1,
        "temp_coef_isc_pct_per_c": 1,
    }
    for key, sign in signs.items():
        value = data.get(key)
        if isinstance(value, int | float) and value * sign <= 0:
            warnings.append(f"{key} = {value:g} has an unusual sign for a PV module")
    powers = [v.get("pmax_w") for v in data.get("variants", []) if isinstance(v, dict)]
    if powers != sorted(powers):
        warnings.append("variants are not listed from the lowest to the highest pmax_w")
    return warnings


def verify_record(record: Path, pdf: Path, records_dir: Path | None = None) -> VerifyReport:
    """Run every mechanical check of a catalogue pull request; see the module docstring."""
    report = VerifyReport(record=record, pdf=pdf)
    try:
        family = read_record(record)
    except CatalogueError as error:
        report.problems += error.problems
        return report
    data = yaml.safe_load(record.read_text(encoding="utf-8"))
    report.family_id = family.family_id
    report.reviewed_by = family.source.reviewed_by
    source = family.source
    report.pages = list(source.pages)

    if records_dir is None:
        records_dir = record.parent.parent
    report.problems += _catalogue_problems(record, family, records_dir)
    if not source.url:
        report.problems.append("source.url is required: the official datasheet address")
    if family.component_type == "pv_module":
        report.warnings += _module_warnings(data)

    if not pdf.is_file():
        report.problems.append(f"datasheet not found: {pdf}")
        return report
    actual = sha256_of(pdf)
    if actual != source.sha256:
        report.problems.append(f"source.sha256 does not match {pdf.name} (its SHA-256 is {actual})")
    if pdf.name != source.filename:
        report.warnings.append(
            f"source.filename is {source.filename!r} but the file is {pdf.name!r}"
        )

    texts = page_texts(pdf)
    missing = [page for page in source.pages if page > len(texts)]
    if missing:
        report.problems.append(f"{pdf.name} has {len(texts)} page(s); no page {_pages(missing)}")
    cited = [texts[page - 1] for page in source.pages if page <= len(texts)]
    report.pages_without_text = [
        page
        for page in source.pages
        if page <= len(texts) and len(texts[page - 1].strip()) < MIN_TEXT_CHARS
    ]
    numbers: set[float] = set()
    for text in cited:
        numbers |= printed_numbers(text)
    report.checks = [
        ValueCheck(where, value, _is_printed(value, numbers))
        for where, value in record_numbers(data)
    ]
    return report
