"""Read a sizing request from a YAML or JSON file (``pvsld size``) or from a mapping.

The file holds the fields of :class:`pvsld.sizing.models.SizingRequest`. The ``template`` can be
written inline or referenced with ``template_file`` (a specification or a spec skeleton, path
relative to the request file)::

    module: JINKO-JKM650N-66HL4M-BDV
    inverters: [GROWATT-MIN-5000TL-X2]      # or "auto"
    target_dc_power_w: 6500
    template_file: ../examples/residential_7p7kwp.yaml
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pvsld.sizing.models import SizingInputError, SizingRequest
from pvsld.specfile import SpecFileError, load_spec_file


def _format(error: ValidationError) -> str:
    lines = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "(request)"
        lines.append(f"{location}: {item['msg'].removeprefix('Value error, ')}")
    return "; ".join(lines)


def request_from_mapping(data: Mapping[str, Any], *, base_dir: Path | None = None) -> SizingRequest:
    """Validate ``data`` as a sizing request.

    Raises:
        SizingInputError: a field is invalid or the ``template_file`` cannot be read; the message
            names the field.
    """
    values = dict(data)
    template_file = values.pop("template_file", None)
    if template_file is not None:
        if "template" in values:
            raise SizingInputError("give either template or template_file, not both")
        path = Path(template_file)
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        try:
            values["template"] = load_spec_file(path)
        except SpecFileError as error:
            raise SizingInputError(f"template_file: {error}") from error
    try:
        return SizingRequest.model_validate(values)
    except ValidationError as error:
        raise SizingInputError(_format(error)) from error


def load_request(path: Path) -> SizingRequest:
    """Read a request file (YAML or JSON).

    Raises:
        SizingInputError: the file cannot be read or the request is invalid.
    """
    try:
        data = load_spec_file(path)
    except SpecFileError as error:
        raise SizingInputError(str(error)) from error
    return request_from_mapping(data, base_dir=path.parent)
