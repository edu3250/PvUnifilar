"""``pvsld catalogue``: validate, list and show the component records.

Usage::

    pvsld catalogue validate [PATH] [--require-reviewed]
    pvsld catalogue list [--records DIR] [--type TYPE] [--manufacturer NAME] [--include-unreviewed]
    pvsld catalogue show ID [--records DIR] [--include-unreviewed]
    pvsld catalogue verify RECORD --pdf PDF [--records DIR] [--markdown FILE]
    pvsld catalogue render PDF --pages N [N ...] --out DIR

``verify`` and ``render`` serve catalogue pull requests (docs/catalogue/contributing-records.md):
``verify`` checks a draft record against its datasheet and prints the report for the pull request;
``render`` writes the cited pages as PNG files for the reviewer. Both need ``pvsld[catalogue]``.

``PATH`` is a records folder (default ``datasheets/records``) or one record file, for example a
draft that has not been moved into the catalogue yet. ``validate`` exits with 1 when any record is
invalid; ``--require-reviewed`` also fails while a record has no ``reviewed_by``. ``list`` and
``show`` see reviewed records only, unless ``--include-unreviewed`` is given.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from pvsld.catalogue.conductors import Cable
from pvsld.catalogue.errors import CatalogueError, UnknownComponentError
from pvsld.catalogue.inverters import Inverter
from pvsld.catalogue.local import (
    DATASHEETS_FOLDER,
    LOCAL_DIR_ENV,
    LOCAL_REVIEWER,
    add_local_record,
    load_catalogue,
    local_catalogue_dir,
)
from pvsld.catalogue.microinverters import Microinverter
from pvsld.catalogue.modules import PVModule
from pvsld.catalogue.protection import AcBreaker, DcBreaker, DcFuse, DcSpd, DcSwitch
from pvsld.catalogue.registry import RECORD_FOLDERS, Component, ComponentRegistry, load_records
from pvsld.catalogue.verify import PdfReaderMissingError, render_pages, verify_record
from pvsld.resources import data_path

DEFAULT_RECORDS = data_path("datasheets/records")
"""The bundled catalogue: packaged with pvsld, or the repository's own records."""


def _rating(component: Component) -> str:
    if isinstance(component, PVModule):
        return f"{component.pmax_w:g} Wp"
    if isinstance(component, Inverter):
        return (
            f"{component.rated_ac_power_w / 1000:g} kW AC, "
            f"{component.recommended_max_pv_power_wp / 1000:g} kWp DC max"
        )
    if isinstance(component, Microinverter):
        return f"{component.rated_ac_power_w:g} W AC, {component.mppt_count} entradas"
    if isinstance(component, AcBreaker):
        return f"{component.rated_current_a:g} A {component.interrupting_ka:g} kA"
    if isinstance(component, DcSwitch):
        return f"{component.poles}P {component.enclosed_thermal_current_a:g} A"
    if isinstance(component, DcSpd):
        return f"{component.spd_type} {component.ucpv_v:g} V In {component.in_ka:g} kA"
    if isinstance(component, Cable):
        return f"{component.size} {component.outer_diameter_mm:g} mm"
    if isinstance(component, DcFuse):
        return f"{component.rated_current_a:g} A {component.operating_class}"
    assert isinstance(component, DcBreaker)
    return f"{component.rated_current_a:g} A"


def _fail(error: CatalogueError) -> int:
    print(f"FAILED: {len(error.problems)} problem(s)", file=sys.stderr)
    for problem in error.problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


def _validate(args: argparse.Namespace) -> int:
    try:
        records = load_records(args.path)
    except CatalogueError as error:
        return _fail(error)
    counts: Counter[str] = Counter()
    unreviewed = []
    for record in records:
        counts[record.family.component_type] += len(record.family.variants)
        if not record.family.source.reviewed:
            unreviewed.append(record)
    breakdown = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) or "no components"
    print(
        f"OK: {args.path}: {len(records)} record(s), {sum(counts.values())} component(s) "
        f"({breakdown}); {len(unreviewed)} unreviewed"
    )
    for record in unreviewed:
        print(f"  unreviewed: {record.path}", file=sys.stderr if args.require_reviewed else None)
    return 1 if args.require_reviewed and unreviewed else 0


def _load(args: argparse.Namespace) -> ComponentRegistry:
    """The bundled records merged with the user's local catalogue (Stage 3.6.3)."""
    registry = load_catalogue(
        args.records, local=local_catalogue_dir(), include_unreviewed=args.include_unreviewed
    )
    for problem in registry.local_problems:
        print(f"local catalogue: {problem}", file=sys.stderr)
    return registry


def _add(args: argparse.Namespace) -> int:
    """Add a record the user confirmed to the local catalogue, with its datasheet."""
    local = local_catalogue_dir()
    try:
        data = yaml.safe_load(args.record.read_text(encoding="utf-8"))
        registry = _load(args)
        sheets = local / DATASHEETS_FOLDER
        sheets.mkdir(parents=True, exist_ok=True)
        datasheet = sheets / args.datasheet.name
        if args.datasheet.resolve() != datasheet.resolve():
            shutil.copyfile(args.datasheet, datasheet)
        added = add_local_record(
            data,
            local,
            datasheet=datasheet,
            registry=registry,
            reviewed_by=args.reviewed_by,
            overwrite=args.overwrite,
        )
    except CatalogueError as error:
        return _fail(error)
    except (OSError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"added {', '.join(added.component_ids)} to the local catalogue: {added.path}")
    return 0


def _list(args: argparse.Namespace) -> int:
    try:
        registry = _load(args)
    except CatalogueError as error:
        return _fail(error)
    components = registry.find(component_type=args.type, manufacturer=args.manufacturer)
    rows = [
        (c.component_id, c.component_type, c.manufacturer, _rating(c), _status(c, registry))
        for c in components
    ]
    header = ("ID", "TYPE", "MANUFACTURER", "RATING", "STATUS")
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows] if rows else []:
        print(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        )
    print(f"{len(rows)} component(s)")
    if registry.skipped_unreviewed:
        print(
            f"{len(registry.skipped_unreviewed)} unreviewed record(s) skipped "
            f"({', '.join(registry.skipped_unreviewed)}); use --include-unreviewed",
            file=sys.stderr,
        )
    return 0


def _status(component: Component, registry: ComponentRegistry | None = None) -> str:
    if registry is not None and registry.is_local(component.component_id):
        return "local"
    return "reviewed" if component.reviewed else "UNREVIEWED"


def _show(args: argparse.Namespace) -> int:
    try:
        component = _load(args).get(args.component_id)
    except CatalogueError as error:
        return _fail(error)
    except UnknownComponentError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    data: dict[str, Any] = component.model_dump(mode="json", exclude_none=True)
    data["source"] = component.source.model_dump(mode="json")  # keep reviewed_by: null visible
    print(f"# {component.component_id} ({component.component_type}, {_status(component)})")
    print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), end="")
    return 0


def _verify(args: argparse.Namespace) -> int:
    try:
        report = verify_record(args.record, args.pdf, args.records)
    except PdfReaderMissingError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    text = report.to_markdown()
    print(text, end="")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(text, encoding="utf-8")
    return 0 if report.ok else 1


def _render(args: argparse.Namespace) -> int:
    try:
        written = render_pages(args.pdf, args.pages, args.out, scale=args.scale)
    except (PdfReaderMissingError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for path in written:
        print(path)
    return 0


def register(subcommands: Any) -> None:
    """Add the ``catalogue`` command group to the ``pvsld`` parser."""
    catalogue = subcommands.add_parser(
        "catalogue",
        help="validate, list and show the component catalogue (datasheet records)",
        description="Work with the YAML component records in datasheets/records (ADR-0004).",
    )
    actions = catalogue.add_subparsers(dest="catalogue_command", required=True)

    validate = actions.add_parser(
        "validate",
        help="validate a records folder or one record file",
        description=(
            "Check every record against the schema and the plausibility rules; the exit code is "
            "1 when a record is invalid. Drafts outside the catalogue can be validated one by one."
        ),
    )
    validate.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=DEFAULT_RECORDS,
        metavar="PATH",
        help=f"records folder or record file (default {DEFAULT_RECORDS})",
    )
    validate.add_argument(
        "--require-reviewed",
        action="store_true",
        help="also fail while a record has no reviewed_by",
    )
    validate.set_defaults(run=_validate)

    add = actions.add_parser(
        "add",
        help="add a record the user confirmed against its datasheet to the local catalogue",
        description="Validate RECORD (a YAML record in the format of datasheets/records), copy the "
        "datasheet into the local catalogue's hojas_tecnicas folder, stamp the provenance "
        "(SHA-256, "
        "reviewer, dates) and write the record into the local catalogue "
        f"(${LOCAL_DIR_ENV}, default %APPDATA%\\pvsld\\catalogo).",
    )
    add.add_argument("record", type=Path, metavar="RECORD", help="record YAML file")
    add.add_argument(
        "--datasheet", type=Path, required=True, metavar="PDF", help="the datasheet it comes from"
    )
    add.add_argument(
        "--reviewed-by", default=LOCAL_REVIEWER, metavar="NAME", help="who confirmed the values"
    )
    add.add_argument("--overwrite", action="store_true", help="replace a local record of that id")
    add.add_argument(
        "--records",
        type=Path,
        default=DEFAULT_RECORDS,
        metavar="DIR",
        help=f"bundled records folder (default {DEFAULT_RECORDS})",
    )
    add.set_defaults(run=_add, include_unreviewed=False)

    verify = actions.add_parser(
        "verify",
        help="check a draft record against its datasheet (catalogue pull requests)",
        description="Validate RECORD, check its place in the catalogue, the datasheet SHA-256 and "
        "pages, and look for every value of the record in the text layer of the cited pages. "
        "Prints a Markdown report for the pull request; the exit code is 1 when the record "
        "cannot be merged.",
    )
    verify.add_argument("record", type=Path, metavar="RECORD", help="record YAML file")
    verify.add_argument(
        "--pdf", type=Path, required=True, metavar="PDF", help="the datasheet it comes from"
    )
    verify.add_argument(
        "--records",
        type=Path,
        default=None,
        metavar="DIR",
        help="records folder to check against (default: two levels above RECORD)",
    )
    verify.add_argument(
        "--markdown", type=Path, default=None, metavar="FILE", help="also write the report here"
    )
    verify.set_defaults(run=_verify)

    render = actions.add_parser(
        "render",
        help="render datasheet pages to PNG for the reviewer",
        description="Write the given pages of PDF as OUT/page-<n>.png.",
    )
    render.add_argument("pdf", type=Path, metavar="PDF", help="the datasheet")
    render.add_argument(
        "--pages", type=int, nargs="+", required=True, metavar="N", help="1-based page numbers"
    )
    render.add_argument("--out", type=Path, required=True, metavar="DIR", help="output folder")
    render.add_argument("--scale", type=float, default=2.0, help="render scale (default 2)")
    render.set_defaults(run=_render)

    for name, help_text in (("list", "list the components"), ("show", "print one component")):
        action = actions.add_parser(name, help=help_text, description=help_text.capitalize() + ".")
        action.add_argument(
            "--records",
            type=Path,
            default=DEFAULT_RECORDS,
            metavar="DIR",
            help=f"records folder (default {DEFAULT_RECORDS})",
        )
        action.add_argument(
            "--include-unreviewed",
            action="store_true",
            help="also load records the owner has not reviewed (development only)",
        )
        if name == "list":
            action.add_argument("--type", choices=sorted(RECORD_FOLDERS), help="only this type")
            action.add_argument("--manufacturer", help="only this manufacturer (any case)")
            action.set_defaults(run=_list)
        else:
            action.add_argument(
                "component_id", metavar="ID", help="component_id, e.g. SUNTREE-SL7N-63-16A"
            )
            action.set_defaults(run=_show)
