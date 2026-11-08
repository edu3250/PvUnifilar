"""The MCP server of ADR-0001 (layer L1): workflow tools and resources over stdio.

Nine tools: ``list_components`` and ``get_component`` read the component catalogue (the bundled
records and the user's local ones), ``add_component_to_catalogue`` adds equipment to the local
catalogue from its datasheet (Stage 3.6.3),
``size_pv_system`` sizes a design from it (Stage 3.5), ``validate_pv_design`` checks a spec,
``generate_single_line_diagram`` draws it and ``design_and_draw`` does all of it in one step and
adds the AutoCAD (or pvsld) PDF, the calculation report, the project sheet and the bill of
materials (Stage 3.6.1); ``new_project_sheet`` and ``design_from_project_sheet`` are the
professional mode, driven by the project sheet the user fills in (Stage 3.6.2). Five resources:
the spec schema, the rule pack, the symbol library, an example of the non-sizing sections a
sizing request needs and example catalogue records (the format of a local record).
Three prompts: the plugin's skills, for the clients that do not load them (Stage 3.7).

Claude fills and corrects a parameter spec; the deterministic core validates it and draws it. The
tool surface is deliberately small and has no primitive drawing tool and no code execution, so the
model never produces geometry (ADR-0001, point 4).

Rules the server keeps, because stdio and Windows make them easy to break:

* nothing but protocol frames goes to stdout: logs go to stderr (``main`` configures it) and the
  SDK moves file descriptor 1 aside while serving;
* the server writes only inside its output sandbox (``PVSLD_OUTPUT_DIR``, default ``./out``), and
  a call carries a drawing *name*, never a path;
* a spec that does not validate is a tool error that carries the findings, so Claude can fix it;
* the core is not re-entrant (it pins a process-global ezdxf option while a file is built), so
  generation is serialised with a lock.

This module intentionally does not use ``from __future__ import annotations``: the SDK builds the
tool schemas from the annotations of functions defined inside :func:`create_server`.
"""

import argparse
import base64
import contextlib
import json
import logging
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
import yaml
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from pvsld import __version__, service
from pvsld.catalogue.errors import CatalogueError, UnknownComponentError
from pvsld.catalogue.local import load_catalogue, local_catalogue_dir
from pvsld.catalogue.registry import ComponentRegistry
from pvsld.core.model import RULEPACK_ID, SCHEMA_VERSION, export_json_schema
from pvsld.core.rules import rulepack_catalogue
from pvsld.core.validation import ValidationReport
from pvsld.mcp.catalogue_tool import AddComponentOutput, record_examples, run_add_component
from pvsld.mcp.design_tool import (
    DesignOutput,
    design_text,
    run_design,
    run_new_sheet,
    run_pro_design,
)
from pvsld.mcp.handshake import serve_stdio
from pvsld.mcp.models import (
    DRAFT_NOTICE_ES,
    GenerateOutput,
    ValidationOutput,
    generate_text,
    validation_output,
    validation_text,
)
from pvsld.mcp.preview import preview_limit_from_environment, shrink_png
from pvsld.mcp.prompts import WORKFLOWS, load_workflow, prompts_enabled
from pvsld.mcp.sandbox import (
    NAME_RULE,
    OUTPUT_DIR_ENV,
    OutputSandbox,
    SandboxError,
    sha256_of_file,
)
from pvsld.mcp.sizing_tools import (
    ComponentListOutput,
    ComponentOutput,
    SizingOutput,
    catalogue_dir_from_environment,
    component_list_output,
    component_output,
    sizing_output,
    sizing_text,
)
from pvsld.resources import data_path
from pvsld.sizing import SizingInputError, request_from_mapping, size_pv_system
from pvsld.sizing.models import REQUIRED_TEMPLATE_KEYS
from pvsld.symbols import symbol_catalogue

__all__ = [
    "RULEPACK_URI",
    "SCHEMA_URI",
    "SERVER_NAME",
    "SYMBOLS_URI",
    "create_server",
    "main",
]

SERVER_NAME = "pvsld"
SCHEMA_URI = "pvsld://schema/pv-system-spec"
RULEPACK_URI = f"pvsld://rulepack/{RULEPACK_ID}"
SYMBOLS_URI = "pvsld://symbols"
SIZING_TEMPLATE_URI = "pvsld://examples/sizing-template"
RECORD_EXAMPLES_URI = "pvsld://catalogue/record-examples"
_EXAMPLE_SPEC = data_path("examples/residential_7p7kwp.yaml")

# One installation spec is a few KiB; anything far larger is a mistake or an attempt to exhaust
# the server, so it is refused before validation or rendering touches it.
MAX_SPEC_BYTES = 256 * 1024

log = logging.getLogger("pvsld.mcp")


def _ensure_spec_size(spec: dict[str, Any]) -> None:
    size = len(json.dumps(spec, ensure_ascii=False, default=str).encode("utf-8"))
    if size > MAX_SPEC_BYTES:
        raise ToolError(
            f"spec too large: {size} bytes (limit {MAX_SPEC_BYTES}); send one installation per call"
        )


INSTRUCTIONS = f"""\
Designs Mexican PV systems and draws their single-line diagrams (CFE distributed generation, \
NOM-001-SEDE-2012).
Quick mode ("un diagrama con 5 paneles Jinko"): find the module with list_components, then call \
design_and_draw once; it writes the DWG or DXF, the PDF, the calculation report, the project \
sheet and the bill of materials into one project folder. Pass on its explanation and summary in \
Spanish.
Professional mode: new_project_sheet gives a blank project sheet; the user fills it in the \
project folder; design_from_project_sheet designs from it or lists the cells to fix.
Step by step:
1. list_components / get_component give catalogue equipment. Never invent equipment data. \
Equipment not in the catalogue: ask for its datasheet PDF, read it, show the values, and once the \
user confirms them call add_component_to_catalogue (format: resource {RECORD_EXAMPLES_URI}).
2. size_pv_system sizes from a module, inverters (or "auto"), a target and the non-sizing \
sections of the spec (resource {SIZING_TEMPLATE_URI}); show the selection before drawing.
3. validate_pv_design checks the spec (schema {SCHEMA_URI}, rules {RULEPACK_URI}); fix every \
error and validate again.
4. generate_single_line_diagram draws a valid spec and returns a PNG preview: look at it.
The server computes all geometry. Explain findings in Spanish. Drawings are drafts that a \
responsible engineer must review and sign.
"""


def _spec_description() -> str:
    properties = list(export_json_schema()["properties"])
    return (
        f"The complete PV system specification as one JSON object (schema_version "
        f"{SCHEMA_VERSION}). Top-level keys: {', '.join(properties)}. Full JSON Schema: resource "
        f"{SCHEMA_URI}. Send the whole object on every call; the server keeps no state."
    )


VALIDATE_DESCRIPTION = f"""\
Check a PV system spec against the parameter schema and the Mexican rule pack {RULEPACK_ID} \
(NOM-001-SEDE-2012, CFE distributed generation) without drawing anything. Read-only, takes \
milliseconds. Call it first, and again after every change to the spec.

Returns ok, findings and derived values. Each finding has rule_id, severity (error blocks \
drawing, warning does not), a Spanish message that says what is wrong and usually the limit to \
respect, the MX checklist ids and the NOM citations. GEN-001 means the input does not fit the \
schema (the subject is the path of the bad field). Derived values: kWp, kWac, string Voc at \
T_min, voltage drop and ampacity per circuit.

Fix every error in the spec and call again until ok is true; then call \
generate_single_line_diagram. Findings are a normal result of this tool, not a tool error."""

GENERATE_DESCRIPTION = """\
Draw the A3 single-line diagram of a validated PV spec as DXF R2018 and verify the written file \
by reading it back. Call validate_pv_design first and call this only when it reports no errors; \
this tool validates again and, if the spec has errors, writes nothing and returns a tool error \
that lists the findings (same format as validate_pv_design), so fix the spec and retry.

Files go to the server's output folder; you choose only a name, never a path. The same spec \
always gives byte-identical output. If a drawing with that name already exists with different \
content the call fails unless overwrite is true; identical content is reported as unchanged. \
Takes under a second, or 2 to 4 s with the preview.

Returns drawing_id, dxf_path, sha256, the read-back counts (audit, attribute round trip by \
COMP_ID, dangling ports) and, when preview is true, a PNG image of the sheet. Look at the image \
to check the layout. The drawing is a draft until the responsible engineer signs it."""

NAME_DESCRIPTION = (
    f"Optional file name without extension; {NAME_RULE}. Default: derived from project.id of the "
    "spec."
)
OVERWRITE_DESCRIPTION = (
    "Replace an existing drawing of the same name that has different content. Leave false unless "
    "the user asked to replace it."
)
PREVIEW_DESCRIPTION = (
    "Attach a PNG preview of the sheet to the result (adds 1 to 3 s). The full-resolution PNG is "
    "also saved next to the DXF."
)


DESIGN_DESCRIPTION = f"""\
Design a grid-tied PV installation in one step and write every deliverable into one project \
folder: size it from the catalogue (the inverter with a DC/AC ratio nearest 1.10-1.25), validate \
it, draw the A3 single-line diagram (DXF), finish it with the local AutoCAD (DWG and PDF) or, \
without AutoCAD, render the PDF itself, then write the calculation report (memoria de calculo, \
xlsx with live formulas and pdf), the editable project sheet (hoja de proyecto, xlsx) and the bill \
of materials. 5 to 20 s.

Give module and the size (module_count_min/max for an exact count, or target_dc_power_w). Without \
template, the quick defaults apply: CFE BT 2F-3H 220/127 V, 10 kA, 100 A main breaker, default \
temperatures, personal data blank (the user fills it by hand or in the project sheet). Give \
template (the sections of resource {SIZING_TEMPLATE_URI}) only when the user gave service or site \
data.

Returns the folder, the files, explanation_es (why this inverter: tell the user), bom_text (the \
installation summary to show), whether AutoCAD was used, and a PNG preview. No configuration that \
meets the rules is a normal result with the rejections, not an error."""

NEW_SHEET_DESCRIPTION = """\
Write a blank project sheet (hoja de proyecto, xlsx) into the project folder <output>/<name>/: \
one yellow cell per parameter, AUTO where the calculation may decide, catalogue dropdowns. The \
user fills it in and saves it there; then call design_from_project_sheet. A quick design \
(design_and_draw) already leaves a prefilled sheet in its folder."""

PRO_DESCRIPTION = """\
Professional mode: design from the project sheet the user filled, \
<output>/<name>/hoja_de_proyecto.xlsx. Every value the user fixed is kept and checked (inverter, \
strings, modules per string, fuse, breakers, conductor sizes, conduit); AUTO cells are decided \
by the calculation; personal data the user filled in reaches the drawing and the report.

If a cell cannot be used or a fixed value breaks a rule (a fuse below 1.56 x Isc, a conductor too \
small), nothing is designed: the result lists each cell with its Spanish reason, and \
hoja_de_proyecto_revisar.xlsx marks them in red. A model missing from the catalogue is listed in \
missing_components. Otherwise the result is the same as design_and_draw, written into the same \
folder (the sheet's "Ultimo calculo" column is refreshed). 5 to 20 s."""

ADD_COMPONENT_DESCRIPTION = f"""\
Add equipment the catalogue lacks (module, inverter, fuse, breaker, switch, cable) to the user's \
local catalogue, from its datasheet. Steps: copy the user's PDF into the datasheets_folder given \
by resource {RECORD_EXAMPLES_URI}; read the PDF; write the record in the format of the example of \
its component_type (every value as printed, never guessed); show the values to the user and ask \
them to confirm; then call this tool with user_confirmed=true.

The tool validates the record like the bundled ones (missing or implausible values are listed so \
you can fix them), adds the PDF's SHA-256 and the user as reviewer, and writes it into the local \
catalogue. From then on every tool sees the new component ids (list_components marks them local) \
and the project sheet offers them in its lists."""

LIST_COMPONENTS_DESCRIPTION = """\
List the components of the catalogue (datasheet records the owner has reviewed): PV modules, \
string and hybrid inverters, DC breakers. Read-only, instant.

Filter by component_type and/or manufacturer (any case). Each row has the component_id to use in \
size_pv_system or get_component and its main rating. Only use listed components; when the user \
names one that is missing, ask for its datasheet instead of inventing values."""

GET_COMPONENT_DESCRIPTION = """\
Return every datasheet value of one catalogue component (units in the field names) and its \
provenance: datasheet file, SHA-256, pages, extraction method and reviewer. Read-only, instant. \
An unknown id is a tool error that suggests the closest ids."""

SIZE_DESCRIPTION = """\
Size a grid-tied PV system from the catalogue: enumerate the string configurations of the module \
on each inverter, reject every configuration that breaks a rule (VOLT-001/002/003, \
STR-001/002/004/007), rank the rest by deliverable DC power against the target, and size the \
string OCPD, the inverter-output breaker and the copper conductors with voltage drop. Read-only, \
deterministic, about a second.

Give module, inverters (ids or "auto" for every catalogue inverter), a target \
(target_dc_power_w, or module_count_min/max) and template: the non-sizing sections of the spec \
(project with site, utility, ac_bos with panels, main_breakers, meters and point_of_connection, \
grounding, storage, title_block, layout). See the resource pvsld://examples/sizing-template. \
The engine fills modules, inverters, strings, circuits, dc_bos and the PV breaker.

Returns the selected candidate, its complete spec (pass it unchanged to validate_pv_design and \
generate_single_line_diagram), up to five alternatives, the rejected configurations grouped by \
rule with a Spanish reason, and the assumptions. No candidate is a normal result, not an error: \
explain the reasons and propose changes."""


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _text(text: str) -> TextContent:
    return TextContent(type="text", text=text)


def _log_call(tool: str, started: float, **fields: object) -> None:
    """One audit line per call; never the spec itself, which holds personal data."""
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    log.info("tool=%s ms=%.0f %s", tool, (time.perf_counter() - started) * 1000, details)


def _refusal(report: ValidationReport, timings: dict[str, float]) -> GenerateOutput:
    findings = validation_output(report, with_derived=False)
    return GenerateOutput(
        ok=False,
        summary=f"REFUSED: nothing was written. {findings.summary}",
        validation=findings,
        timings_ms=timings,
        next_step=(
            "Fix every error in the spec and call validate_pv_design, then this tool again."
        ),
    )


def render_drawing(
    box: OutputSandbox,
    spec: dict[str, Any],
    *,
    name: str | None,
    overwrite: bool,
    preview: bool,
    max_preview_bytes: int,
) -> tuple[GenerateOutput, ImageContent | None]:
    """Validate, draw, verify and publish one drawing; the body of the generator tool.

    Returns the structured result and the preview image block (``None`` when there is none). A
    result with ``ok`` false is a refusal or a failed verification; nothing is published then.

    Raises:
        ToolError: the name is refused, the target exists and ``overwrite`` is false, or the
            file system rejects the write.
    """
    try:
        stem = box.drawing_name(name, spec)
        target = box.dxf_path(stem)
        with box.staging() as stage:
            staged = stage / target.name
            result = service.generate_single_line_diagram(spec, staged, png=preview, overwrite=True)
            if result.dxf_path is None or result.readback is None or result.sha256 is None:
                return _refusal(result.validation, result.timings_ms), None
            if not result.ok:
                problems = list(result.readback.problems)
                return (
                    GenerateOutput(
                        ok=False,
                        summary=(
                            f"FAILED: the DXF did not pass read-back verification with "
                            f"{len(problems)} problem(s) and was discarded."
                        ),
                        readback=result.readback.summary(),
                        timings_ms=result.timings_ms,
                        next_step=(
                            "This is a defect of the generator, not of the spec. Report the "
                            "problems to the user; do not retry with the same spec."
                        ),
                    ),
                    None,
                )

            existed = target.exists()
            unchanged = existed and sha256_of_file(target) == result.sha256
            if existed and not unchanged and not overwrite:
                raise ToolError(
                    f"{target.name} already exists with different content. Call again with "
                    "overwrite=true to replace it (only if the user asked for that), or choose "
                    "another name."
                )
            box.publish(staged, target)

            png_path = image = None
            note = "not requested"
            if preview and result.png_path is not None:
                full_resolution = result.png_path.read_bytes()
                shrunk = shrink_png(full_resolution, max_preview_bytes)
                if shrunk is None:
                    note = "omitted: it did not fit the result size budget; open png_path"
                else:
                    image = ImageContent(
                        type="image",
                        data=base64.b64encode(shrunk.data).decode("ascii"),
                        mime_type="image/png",
                    )
                    note = (
                        f"PNG {shrunk.width}x{shrunk.height} px, {len(shrunk.data) / 1024:.0f} "
                        "KiB, attached; png_path is the full-resolution file"
                    )
                try:
                    box.publish(result.png_path, target.with_suffix(".png"))
                    png_path = target.with_suffix(".png")
                except SandboxError as error:
                    # The DXF is the deliverable and is already in place: keep going.
                    note += f"; the full-resolution PNG was not saved ({error})"
    except SandboxError as error:
        raise ToolError(str(error)) from error
    except OSError as error:
        raise ToolError(f"cannot write the drawing: {error}") from error

    drawing_id = f"{stem}-{result.sha256[:8]}"
    verb = "unchanged (identical file already existed)" if unchanged else "written"
    return (
        GenerateOutput(
            ok=True,
            summary=f"OK: {target.name} {verb}, {result.size_bytes or 0} bytes, read-back clean.",
            drawing_id=drawing_id,
            dxf_path=str(target),
            png_path=str(png_path) if png_path else None,
            sha256=result.sha256,
            size_bytes=result.size_bytes,
            unchanged=unchanged,
            readback=result.readback.summary(),
            preview=note,
            timings_ms=result.timings_ms,
            notice_es=DRAFT_NOTICE_ES,
            next_step="Check the preview, then tell the user where the files are and the notice.",
        ),
        image,
    )


def create_server(
    sandbox: OutputSandbox | None = None,
    *,
    max_preview_bytes: int | None = None,
    catalogue_dir: Path | None = None,
    local_catalogue: Path | None = None,
) -> MCPServer:
    """Build the server.

    ``sandbox`` defaults to ``$PVSLD_OUTPUT_DIR`` or ``./out``; ``max_preview_bytes`` (the size of
    the PNG attached to a result) to ``$PVSLD_PREVIEW_MAX_BYTES`` or 62 000; ``catalogue_dir`` (the
    component records) to ``$PVSLD_CATALOGUE_DIR`` or ``datasheets/records`` of the repository;
    ``local_catalogue`` (the user's own records, Stage 3.6.3) to ``$PVSLD_LOCAL_CATALOGUE_DIR`` or
    the per-user data folder.
    """
    box = sandbox if sandbox is not None else OutputSandbox.from_environment()
    catalogue = catalogue_dir if catalogue_dir is not None else catalogue_dir_from_environment()
    local = local_catalogue if local_catalogue is not None else local_catalogue_dir()
    preview_limit = (
        max_preview_bytes if max_preview_bytes is not None else preview_limit_from_environment()
    )
    # The drawing add-on logs one INFO line per hidden attribute (hundreds per preview).
    logging.getLogger("ezdxf").setLevel(logging.WARNING)
    server = MCPServer(
        SERVER_NAME,
        title="PV single-line diagrams (Mexico)",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    generation_lock = threading.Lock()
    spec_field = Field(description=_spec_description())

    @server.tool(
        title="Validate PV design",
        description=VALIDATE_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Validate PV design",
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def validate_pv_design(
        spec: Annotated[dict[str, Any], spec_field],
    ) -> Annotated[CallToolResult, ValidationOutput]:
        started = time.perf_counter()
        _ensure_spec_size(spec)
        output = validation_output(service.validate_pv_design(spec))
        _log_call(
            "validate_pv_design",
            started,
            ok=output.ok,
            errors=output.errors,
            warnings=output.warnings,
        )
        return CallToolResult(
            content=[_text(validation_text(output))],
            structured_content=output.model_dump(mode="json", exclude_none=True),
        )

    @server.tool(
        title="Generate single-line diagram",
        description=GENERATE_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Generate single-line diagram",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def generate_single_line_diagram(
        spec: Annotated[dict[str, Any], spec_field],
        name: Annotated[str | None, Field(description=NAME_DESCRIPTION)] = None,
        overwrite: Annotated[bool, Field(description=OVERWRITE_DESCRIPTION)] = False,
        preview: Annotated[bool, Field(description=PREVIEW_DESCRIPTION)] = True,
    ) -> Annotated[CallToolResult, GenerateOutput]:
        started = time.perf_counter()
        _ensure_spec_size(spec)
        with generation_lock:
            output, image = render_drawing(
                box,
                spec,
                name=name,
                overwrite=overwrite,
                preview=preview,
                max_preview_bytes=preview_limit,
            )
        _log_call(
            "generate_single_line_diagram", started, ok=output.ok, drawing_id=output.drawing_id
        )
        content: list[TextContent | ImageContent] = [_text(generate_text(output))]
        if image is not None:
            content.append(image)
        return CallToolResult(
            content=content,
            structured_content=output.model_dump(mode="json", exclude_none=True),
            is_error=not output.ok,
        )

    def load_registry(include_unreviewed: bool) -> ComponentRegistry:
        """The bundled catalogue merged with the user's local one (read on every call)."""
        try:
            registry = load_catalogue(catalogue, local=local, include_unreviewed=include_unreviewed)
        except CatalogueError as error:
            raise ToolError(
                "the component catalogue is invalid: " + "; ".join(error.problems[:5])
            ) from error
        for problem in registry.local_problems:
            log.warning("local catalogue: %s", problem)
        return registry

    unreviewed_field = Field(
        description="Also use records the owner has not reviewed yet (development only; never "
        "for a design that gets issued)."
    )

    @server.tool(
        title="List catalogue components",
        description=LIST_COMPONENTS_DESCRIPTION,
        annotations=ToolAnnotations(
            title="List catalogue components",
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def list_components(
        component_type: Annotated[
            Literal[
                "pv_module",
                "string_inverter",
                "hybrid_inverter",
                "microinverter",
                "dc_breaker",
                "dc_fuse",
                "ac_breaker",
                "dc_switch",
                "conductor",
            ]
            | None,
            Field(description="Only this type. Default: every type."),
        ] = None,
        manufacturer: Annotated[
            str | None,
            Field(description="Only this manufacturer; any case, a part of the name is enough."),
        ] = None,
        include_unreviewed: Annotated[bool, unreviewed_field] = False,
    ) -> Annotated[CallToolResult, ComponentListOutput]:
        started = time.perf_counter()
        output = component_list_output(
            load_registry(include_unreviewed), component_type, manufacturer
        )
        _log_call("list_components", started, count=output.count)
        lines = [
            f"{c.component_id}  {c.component_type}  {c.manufacturer}  {c.rating}"
            + ("  LOCAL" if c.local else "" if c.reviewed else "  UNREVIEWED")
            for c in output.components
        ]
        lines.append(f"{output.count} component(s)")
        return CallToolResult(
            content=[_text("\n".join(lines))],
            structured_content=output.model_dump(mode="json"),
        )

    @server.tool(
        title="Get catalogue component",
        description=GET_COMPONENT_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Get catalogue component",
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def get_component(
        component_id: Annotated[str, Field(description="Id from list_components.")],
        include_unreviewed: Annotated[bool, unreviewed_field] = False,
    ) -> Annotated[CallToolResult, ComponentOutput]:
        started = time.perf_counter()
        try:
            output = component_output(load_registry(include_unreviewed), component_id)
        except UnknownComponentError as error:
            raise ToolError(str(error)) from error
        _log_call("get_component", started, component_id=output.component_id)
        return CallToolResult(
            content=[_text(_json(output.model_dump(mode="json")))],
            structured_content=output.model_dump(mode="json"),
        )

    @server.tool(
        name="size_pv_system",
        title="Size PV system",
        description=SIZE_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Size PV system",
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def size_pv_system_tool(
        module: Annotated[str, Field(description="component_id of the PV module.")],
        template: Annotated[
            dict[str, Any],
            Field(
                description="Non-sizing sections of the spec: "
                + ", ".join(REQUIRED_TEMPLATE_KEYS)
                + f". Example: resource {SIZING_TEMPLATE_URI}."
            ),
        ],
        inverters: Annotated[
            list[str] | Literal["auto"],
            Field(description='Inverter component_ids to evaluate, or "auto" for all.'),
        ] = "auto",
        target_dc_power_w: Annotated[
            float | None, Field(description="DC power to approach, in W (STC).", gt=0)
        ] = None,
        module_count_min: Annotated[
            int | None, Field(description="Total modules, lower bound.", gt=0)
        ] = None,
        module_count_max: Annotated[
            int | None, Field(description="Total modules, upper bound.", gt=0)
        ] = None,
        routing: Annotated[
            dict[str, Any] | None,
            Field(
                description="Data no catalogue provides: dc_string_length_m, "
                "ac_output_length_m, rooftop_clearance_mm, tilt_deg, azimuth_deg."
            ),
        ] = None,
        dc_ocpd: Annotated[
            Literal["auto", "always"],
            Field(
                description="always (default): one protection per string in the DC protection "
                "box; auto: only where NOM 690-9(a) needs one."
            ),
        ] = "always",
        dc_ocpd_device: Annotated[
            Literal["fuse", "breaker"],
            Field(
                description="fuse (default): a gPV fuse-disconnector per string, or a DC "
                "breaker when no catalogue fuse fits the module; breaker: a DC breaker."
            ),
        ] = "fuse",
        layout_template: Annotated[
            Literal["a3_plantilla_v1", "bt_string_residential_v1"],
            Field(description="Sheet of the drawing (default the owner's A3 template)."),
        ] = "a3_plantilla_v1",
        include_unreviewed: Annotated[bool, unreviewed_field] = False,
    ) -> Annotated[CallToolResult, SizingOutput]:
        started = time.perf_counter()
        _ensure_spec_size(template)
        values: dict[str, Any] = {
            "template": template,
            "module": module,
            "inverters": inverters,
            "dc_ocpd": dc_ocpd,
            "dc_ocpd_device": dc_ocpd_device,
            "layout_template": layout_template,
        }
        optional = {
            "target_dc_power_w": target_dc_power_w,
            "module_count_min": module_count_min,
            "module_count_max": module_count_max,
            "routing": routing,
        }
        values.update({key: value for key, value in optional.items() if value is not None})
        try:
            request = request_from_mapping(values)
            result = size_pv_system(request, load_registry(include_unreviewed))
        except SizingInputError as error:
            raise ToolError(f"invalid sizing request: {error}") from error
        output = sizing_output(result)
        _log_call("size_pv_system", started, ok=output.ok, candidates=len(result.candidates))
        return CallToolResult(
            content=[_text(sizing_text(output))],
            structured_content=output.model_dump(mode="json", exclude_none=True),
        )

    @server.tool(
        name="design_and_draw",
        title="Design and draw PV system",
        description=DESIGN_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Design and draw PV system",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def design_and_draw_tool(
        module: Annotated[str, Field(description="component_id of the PV module.")],
        module_count_min: Annotated[
            int | None,
            Field(description="Total modules, lower bound (= max for an exact count).", gt=0),
        ] = None,
        module_count_max: Annotated[
            int | None, Field(description="Total modules, upper bound.", gt=0)
        ] = None,
        target_dc_power_w: Annotated[
            float | None, Field(description="DC power to approach, in W (STC).", gt=0)
        ] = None,
        inverters: Annotated[
            list[str] | Literal["auto"],
            Field(description='Inverter component_ids to choose from, or "auto" for all.'),
        ] = "auto",
        dc_ocpd_device: Annotated[
            Literal["fuse", "breaker"],
            Field(description="String protection: gPV fuse-disconnector (default) or DC breaker."),
        ] = "fuse",
        routing: Annotated[
            dict[str, Any] | None,
            Field(
                description="Run lengths when the user gave them: dc_string_length_m (default "
                "25), ac_output_length_m (default 30)."
            ),
        ] = None,
        template: Annotated[
            dict[str, Any] | None,
            Field(
                description="Non-sizing sections of the spec, only when the user gave service or "
                f"site data (see resource {SIZING_TEMPLATE_URI}). Default: the quick defaults."
            ),
        ] = None,
        name: Annotated[
            str | None,
            Field(description=f"Project folder name; {NAME_RULE}. Default: sfv_<kWp>kWp."),
        ] = None,
        overwrite: Annotated[bool, Field(description=OVERWRITE_DESCRIPTION)] = False,
        use_autocad: Annotated[
            Literal["auto", "never"],
            Field(
                description="auto (default): use AutoCAD when installed; never: DXF + pvsld PDF."
            ),
        ] = "auto",
        include_unreviewed: Annotated[bool, unreviewed_field] = False,
    ) -> Annotated[CallToolResult, DesignOutput]:
        started = time.perf_counter()
        values: dict[str, Any] = {
            "module": module,
            "inverters": inverters,
            "dc_ocpd_device": dc_ocpd_device,
        }
        optional = {
            "module_count_min": module_count_min,
            "module_count_max": module_count_max,
            "target_dc_power_w": target_dc_power_w,
            "routing": routing,
            "template": template,
        }
        values.update({key: value for key, value in optional.items() if value is not None})
        _ensure_spec_size(values)
        registry = load_registry(include_unreviewed)
        with generation_lock:
            output, image = run_design(
                box,
                registry,
                values,
                name=name,
                overwrite=overwrite,
                use_autocad=use_autocad,
                max_preview_bytes=preview_limit,
            )
        _log_call("design_and_draw", started, ok=output.ok, files=len(output.files))
        content: list[TextContent | ImageContent] = [_text(design_text(output))]
        if image is not None:
            content.append(image)
        return CallToolResult(
            content=content,
            structured_content=output.model_dump(mode="json", exclude_none=True),
        )

    @server.tool(
        name="add_component_to_catalogue",
        title="Add component to local catalogue",
        description=ADD_COMPONENT_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Add component to local catalogue",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def add_component_to_catalogue_tool(
        record: Annotated[
            dict[str, Any],
            Field(
                description="The record as read from the datasheet, in the format of the "
                f"examples of resource {RECORD_EXAMPLES_URI}."
            ),
        ],
        datasheet: Annotated[
            str,
            Field(description="File name of the PDF in the local catalogue's datasheets folder."),
        ],
        user_confirmed: Annotated[
            bool,
            Field(description="True only after the user confirmed the values you showed them."),
        ],
        overwrite: Annotated[
            bool, Field(description="Replace a local record with the same ids (user asked).")
        ] = False,
    ) -> Annotated[CallToolResult, AddComponentOutput]:
        started = time.perf_counter()
        _ensure_spec_size(record)
        output = run_add_component(
            load_registry(include_unreviewed=False),
            local,
            record=record,
            datasheet=datasheet,
            user_confirmed=user_confirmed,
            overwrite=overwrite,
        )
        _log_call("add_component_to_catalogue", started, components=len(output.component_ids))
        lines = [output.summary, *(f"id: {i}" for i in output.component_ids), output.next_step]
        return CallToolResult(
            content=[_text("\n".join(lines))],
            structured_content=output.model_dump(mode="json"),
        )

    @server.resource(
        RECORD_EXAMPLES_URI,
        name="catalogue-record-examples",
        title="Catalogue record examples",
        description=(
            "One reviewed record per component type, as written in the catalogue, the local "
            "catalogue folders and how to add equipment from a datasheet."
        ),
        mime_type="application/json",
    )
    def record_examples_resource() -> str:
        return json.dumps(record_examples(catalogue, local), ensure_ascii=False)

    project_name = Field(description=f"Project folder name; {NAME_RULE}.")

    @server.tool(
        name="new_project_sheet",
        title="New project sheet",
        description=NEW_SHEET_DESCRIPTION,
        annotations=ToolAnnotations(
            title="New project sheet",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def new_project_sheet_tool(
        name: Annotated[str, project_name],
        overwrite: Annotated[bool, Field(description=OVERWRITE_DESCRIPTION)] = False,
        include_unreviewed: Annotated[bool, unreviewed_field] = False,
    ) -> Annotated[CallToolResult, DesignOutput]:
        started = time.perf_counter()
        output = run_new_sheet(
            box, load_registry(include_unreviewed), name=name, overwrite=overwrite
        )
        _log_call("new_project_sheet", started, ok=output.ok)
        return CallToolResult(
            content=[_text(design_text(output))],
            structured_content=output.model_dump(mode="json", exclude_none=True),
        )

    @server.tool(
        name="design_from_project_sheet",
        title="Design from project sheet",
        description=PRO_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Design from project sheet",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def design_from_project_sheet_tool(
        name: Annotated[str, project_name],
        use_autocad: Annotated[
            Literal["auto", "never"],
            Field(
                description="auto (default): use AutoCAD when installed; never: DXF + pvsld PDF."
            ),
        ] = "auto",
        include_unreviewed: Annotated[bool, unreviewed_field] = False,
    ) -> Annotated[CallToolResult, DesignOutput]:
        started = time.perf_counter()
        registry = load_registry(include_unreviewed)
        with generation_lock:
            output, image = run_pro_design(
                box,
                registry,
                name=name,
                use_autocad=use_autocad,
                max_preview_bytes=preview_limit,
            )
        _log_call("design_from_project_sheet", started, ok=output.ok, issues=len(output.issues))
        content: list[TextContent | ImageContent] = [_text(design_text(output))]
        if image is not None:
            content.append(image)
        return CallToolResult(
            content=content,
            structured_content=output.model_dump(mode="json", exclude_none=True),
        )

    @server.resource(
        SIZING_TEMPLATE_URI,
        name="sizing-template-example",
        title="Example of the non-sizing sections",
        description=(
            "The sections size_pv_system needs besides the equipment (project and site, utility, "
            "ac_bos, grounding, storage, title_block, layout), taken from the 7.70 kWp sample. "
            "Replace every value with the user's data; never send these sample values."
        ),
        mime_type="application/json",
    )
    def sizing_template_resource() -> str:
        sample = yaml.safe_load(_EXAMPLE_SPEC.read_text(encoding="utf-8"))
        sections = {key: sample[key] for key in REQUIRED_TEMPLATE_KEYS}
        sections["layout"] = {**sections["layout"], "template": "a3_plantilla_v1"}
        return json.dumps(sections, ensure_ascii=False, default=str)

    @server.resource(
        SCHEMA_URI,
        name="pv-system-spec-schema",
        title="PV system parameter schema",
        description=(
            "JSON Schema (draft 2020-12) of the spec accepted by both tools. Read it before "
            "building a spec from datasheets; field names are English, free text is Spanish."
        ),
        mime_type="application/json",
    )
    def schema_resource() -> str:
        return _json(export_json_schema())

    @server.resource(
        RULEPACK_URI,
        name="rulepack-catalogue",
        title=f"Rule pack {RULEPACK_ID}",
        description=(
            "The implemented validation rules: id, Spanish title, severity, MX checklist ids "
            "and NOM citations. Use it to explain a finding."
        ),
        mime_type="application/json",
    )
    def rulepack_resource() -> str:
        return _json(
            {
                "rulepack": RULEPACK_ID,
                "schema_version": SCHEMA_VERSION,
                "rules": rulepack_catalogue(),
            }
        )

    @server.resource(
        SYMBOLS_URI,
        name="symbol-catalogue",
        title="Drawing symbol library",
        description="Blocks of the symbol library with their ports and attribute tags.",
        mime_type="application/json",
    )
    def symbols_resource() -> str:
        return _json(symbol_catalogue())

    log.info("output directory: %s", box.root)
    if prompts_enabled():
        _register_prompts(server)
    return server


def _register_prompts(server: MCPServer) -> None:
    """The skills of the plugin as prompts, for the clients that do not load the plugin."""
    quick, pro, catalogue = (load_workflow(name) for name in WORKFLOWS)

    @server.prompt(name=quick.name, title="PvUnifilar: diagrama", description=quick.description)
    def diagrama(
        peticion: Annotated[
            str, Field(description="Qué diseñar, p. ej. 8 paneles Jinko de 475 W.")
        ] = "",
    ) -> str:
        return quick.text(peticion)

    @server.prompt(name=pro.name, title="PvUnifilar: modo profesional", description=pro.description)
    def profesional(
        proyecto: Annotated[
            str, Field(description="nuevo, el nombre de un proyecto o una hoja .xlsx.")
        ] = "",
    ) -> str:
        return pro.text(proyecto)

    @server.prompt(
        name=catalogue.name, title="PvUnifilar: catálogo", description=catalogue.description
    )
    def catalogo(
        hoja_tecnica: Annotated[
            str, Field(description="La hoja técnica en PDF del equipo que falta.")
        ] = "",
    ) -> str:
        return catalogue.text(hoja_tecnica)


def configure_logging(level: str = "INFO") -> None:
    """Send every log record to stderr, which is the only safe stream on stdio."""
    logging.basicConfig(
        stream=sys.stderr,
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _use_utf8() -> None:
    """Spanish messages carry accents; do not let a legacy Windows code page garble stderr."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pvsld-mcp",
        description="MCP server (stdio) that validates and draws Mexican PV single-line diagrams.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        help=f"folder the server may write to (default: ${OUTPUT_DIR_ENV} or ./out)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="log level; logs go to stderr",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the ``pvsld-mcp`` console script: serve MCP over stdio until EOF."""
    args = build_parser().parse_args(argv)
    _use_utf8()
    configure_logging(args.log_level)
    sandbox = OutputSandbox.at(args.output_dir) if args.output_dir else None
    server = create_server(sandbox)
    log.info("pvsld-mcp %s serving on stdio", __version__)
    anyio.run(serve_stdio, server)
    return 0
