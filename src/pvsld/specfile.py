"""Read a specification file (YAML or JSON) into a mapping.

Kept apart from :mod:`pvsld.service` so that importing it does not load the render backends (and
ezdxf): :mod:`pvsld.sizing` reads its files through here and stays light enough for an on-device
runtime (ENG-014).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

__all__ = ["SpecFileError", "load_spec_file"]


class SpecFileError(ValueError):
    """A specification file cannot be read; the message says why and is safe to show the user."""


def load_spec_file(path: Path) -> dict[str, Any]:
    """Read a ``.yaml``, ``.yml`` or ``.json`` specification into a mapping.

    Raises:
        SpecFileError: the file is missing, unreadable, not valid YAML or JSON, or not a mapping.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SpecFileError(f"file not found: {path}") from None
    except OSError as error:
        raise SpecFileError(f"cannot read {path}: {error.strerror or error}") from error
    try:
        document = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as error:
        raise SpecFileError(f"invalid {path.suffix or 'YAML'} in {path}: {error}") from error
    if not isinstance(document, dict):
        raise SpecFileError(
            f"{path} must contain a mapping at the top level, found {type(document).__name__}"
        )
    return document
