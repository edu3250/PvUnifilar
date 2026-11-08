"""The local-catalogue tool of the plugin (Stage 3.6.3): ``add_component_to_catalogue``.

When the user needs equipment the catalogue lacks, Claude reads its datasheet, shows the values,
and after the user confirms them calls the tool with the record. The datasheet travels by file
name only: the user's PDF is copied into ``<local catalogue>/hojas_tecnicas/`` first (tools never
take paths), and the tool hashes it for the record's provenance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from pvsld.catalogue.errors import CatalogueError
from pvsld.catalogue.local import DATASHEETS_FOLDER, add_local_record
from pvsld.catalogue.registry import ComponentRegistry, load_records

__all__ = ["AddComponentOutput", "datasheet_path", "record_examples", "run_add_component"]


class AddComponentOutput(BaseModel):
    """Result of ``add_component_to_catalogue``."""

    ok: bool = True
    summary: str
    component_ids: list[str] = Field(description="Ids to use in the designs and the sheet.")
    record_path: str
    next_step: str


def datasheet_path(local: Path, name: str) -> Path:
    """The PDF ``name`` inside the local catalogue's datasheet folder; refuses any other place."""
    folder = local / DATASHEETS_FOLDER
    # Separators and drive colons of every system: a Windows path is refused on Linux too.
    if not name or any(c in name for c in "/\\:") or name in {".", ".."}:
        raise ToolError(f"datasheet must be a file name inside {folder}, not a path: {name!r}")
    if not name.lower().endswith(".pdf"):
        raise ToolError(f"datasheet must be the PDF of the datasheet, got {name!r}")
    path = folder / name
    if path.resolve().parent != folder.resolve():
        raise ToolError(f"{name!r} resolves outside {folder}")
    if not path.is_file():
        raise ToolError(
            f"{name} is not in {folder}. Copy the user's datasheet PDF into that folder first "
            "(create it if needed), then call again."
        )
    return path


def run_add_component(
    registry: ComponentRegistry,
    local: Path,
    *,
    record: dict[str, Any],
    datasheet: str,
    user_confirmed: bool,
    overwrite: bool,
) -> AddComponentOutput:
    """Validate and write the record; the body of the tool.

    Raises:
        ToolError: the user has not confirmed, the datasheet is not in place, or the record is
            invalid or already in the catalogue (every problem is listed).
    """
    if not user_confirmed:
        raise ToolError(
            "show the extracted values to the user (a table per variant) and call again with "
            "user_confirmed=true only after they confirm them against the datasheet"
        )
    path = datasheet_path(local, datasheet)
    try:
        added = add_local_record(
            record, local, datasheet=path, registry=registry, overwrite=overwrite
        )
    except CatalogueError as error:
        raise ToolError("the record cannot be added: " + "; ".join(error.problems[:15])) from error
    return AddComponentOutput(
        summary=(
            f"OK: {len(added.component_ids)} component(s) of {added.family_id} "
            f"({added.component_type}) added to the local catalogue."
        ),
        component_ids=list(added.component_ids),
        record_path=str(added.path),
        next_step=(
            "Tell the user in Spanish that the equipment is now in their local catalogue, then "
            "continue the design that needed it (design_and_draw, or design_from_project_sheet "
            "after they pick the new id in the sheet)."
        ),
    )


def record_examples(bundled: Path, local: Path) -> dict[str, Any]:
    """One bundled record per component type, as written, and where local records go."""
    examples: dict[str, str] = {}
    for record in sorted(load_records(bundled), key=lambda r: r.path.name):
        kind = record.family.component_type
        if kind not in examples:
            examples[kind] = record.path.read_text(encoding="utf-8")
    return {
        "local_catalogue": str(local),
        "datasheets_folder": str(local / DATASHEETS_FOLDER),
        "how_to": (
            "Copy the user's datasheet PDF into datasheets_folder. Read it and write one record in "
            "the format of the example of its component_type: one family, one variant per model "
            "or rating, every value as printed with its unit in the field name. In source give "
            "only title, pages, extraction_method and notes; the tool adds the file name, SHA-256 "
            "and the reviewer. Show the values to the user, and after they confirm call "
            "add_component_to_catalogue. Microinverters (owner criteria): a missing real power is "
            "the apparent power when the default power factor is at least 0.99; a missing largest "
            "branch breaker is left out (the NOM limit applies); caps that change with the AC "
            "voltage keep the 220 V column; say each deduction in source.notes."
        ),
        "examples": examples,
    }
