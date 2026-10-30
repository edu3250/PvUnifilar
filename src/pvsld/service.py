"""Public entry points of the deterministic pipeline: what the CLI and the MCP server call.

Both functions are named after the MCP tools of ADR-0001 so the server of Stage 2.3 can wrap them
one to one:

* :func:`validate_pv_design` parses a specification and runs the rule pack ``mx-gd-2026.10``.
* :func:`generate_single_line_diagram` re-validates, lays the sheet out, writes the DXF, optionally
  a PNG preview, and reloads the DXF to verify it (a successful write is not evidence).

Neither raises on an invalid *specification*: problems come back as findings in the result, so the
caller can show them and ask for a fix. Environment problems (unreadable file, existing output
without ``overwrite``) raise.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pvsld.backends.dxf import write_dxf
from pvsld.backends.readback import ReadBackReport, verify
from pvsld.core.calc import Derived
from pvsld.core.diagram import Diagram
from pvsld.core.layout import build_diagram
from pvsld.core.layout_sheet import TEMPLATE as SHEET_LAYOUT
from pvsld.core.layout_sheet import build_sheet_diagram
from pvsld.core.model import PvSystemSpec
from pvsld.core.policy import DEFAULT_DC_AC_POLICY, DcAcPolicy
from pvsld.core.validation import ValidationReport
from pvsld.core.validation import validate_pv_design as _validate
from pvsld.specfile import SpecFileError, load_spec_file

__all__ = [
    "GenerationResult",
    "SpecFileError",
    "generate_single_line_diagram",
    "load_spec_file",
    "validate_pv_design",
]


def layout_diagram(spec: PvSystemSpec, derived: Derived) -> Diagram:
    """Lay out ``spec`` with the layout template it names (``layout.template``).

    Raises:
        LayoutError: the spec is outside the template.
        SheetTemplateError: the sheet template file is missing or does not match.
    """
    if spec.layout.template == SHEET_LAYOUT:
        from pvsld.sheets import load_sheet_template

        return build_sheet_diagram(spec, derived, load_sheet_template(spec.layout.template))
    return build_diagram(spec, derived)


def validate_pv_design(
    data: Mapping[str, Any] | PvSystemSpec,
    *,
    dc_ac_policy: DcAcPolicy = DEFAULT_DC_AC_POLICY,
) -> ValidationReport:
    """Validate a PV design (a mapping as parsed from YAML or JSON, or a model object).

    ``dc_ac_policy`` sets the DC/AC ratio thresholds of STR-007 (see :mod:`pvsld.core.policy`).
    """
    return _validate(data, dc_ac_policy=dc_ac_policy)


@dataclass(frozen=True)
class GenerationResult:
    """Outcome of :func:`generate_single_line_diagram`.

    ``ok`` is ``True`` only when the specification passed validation, the DXF was written and the
    read-back verification found no problem. Paths and the fingerprint are ``None`` when nothing
    was written.
    """

    ok: bool
    validation: ValidationReport
    dxf_path: Path | None = None
    png_path: Path | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    readback: ReadBackReport | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON types, safe to return from an MCP tool."""
        return {
            "ok": self.ok,
            "validation": self.validation.to_dict(),
            "dxf_path": str(self.dxf_path) if self.dxf_path else None,
            "png_path": str(self.png_path) if self.png_path else None,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "readback": self.readback.summary() if self.readback else None,
            "timings_ms": self.timings_ms,
        }


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def generate_single_line_diagram(
    data: Mapping[str, Any] | PvSystemSpec,
    output: Path,
    *,
    png: bool = False,
    overwrite: bool = True,
    deterministic: bool = True,
) -> GenerationResult:
    """Validate ``data``, render the A3 single-line diagram to ``output`` (DXF) and verify it.

    Args:
        data: the parameter model as a mapping or a :class:`PvSystemSpec`.
        output: DXF path; the PNG preview, when requested, goes next to it with ``.png``.
        png: also render a PNG preview of the A3 sheet.
        overwrite: replace existing files; when ``False`` an existing file raises.
        deterministic: pin ezdxf timestamps and GUIDs so equal input gives byte-identical output.

    Raises:
        FileExistsError: ``output`` exists and ``overwrite`` is ``False``.
    """
    timings: dict[str, float] = {}
    started = time.perf_counter()
    report = _validate(data)
    timings["validate"] = _ms(started)
    if not report.ok or report.spec is None or report.derived is None:
        return GenerationResult(ok=False, validation=report, timings_ms=timings)

    if output.exists() and not overwrite:
        raise FileExistsError(f"{output} exists; pass overwrite=True to replace it")

    started = time.perf_counter()
    diagram = layout_diagram(report.spec, report.derived)
    timings["layout"] = _ms(started)

    started = time.perf_counter()
    rendered = write_dxf(diagram, output, deterministic=deterministic)
    timings["build_write"] = _ms(started)

    png_path: Path | None = None
    if png:
        from pvsld.backends.preview import write_png

        png_path = output.with_suffix(".png")
        started = time.perf_counter()
        write_png(rendered.document, png_path)
        timings["png"] = _ms(started)

    started = time.perf_counter()
    readback = verify(diagram, rendered.data)
    timings["readback"] = _ms(started)

    return GenerationResult(
        ok=readback.ok,
        validation=report,
        dxf_path=output,
        png_path=png_path,
        sha256=rendered.sha256,
        size_bytes=len(rendered.data),
        readback=readback,
        timings_ms=timings,
    )
