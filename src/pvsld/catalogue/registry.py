"""Read the YAML records and index their components in memory (ADR-0004, driver D6).

Example::

    registry = ComponentRegistry.load("datasheets/records")  # reviewed records only
    module = registry.get("JINKO-JKM640N-66HL4M-BDV")       # O(1), a PVModule
    inverters = registry.find(component_type="hybrid_inverter", manufacturer="Huawei")

Records that the owner has not reviewed yet (``source.reviewed_by`` is ``None``) are validated like
the others but kept out of the registry unless ``include_unreviewed=True``.
"""

from __future__ import annotations

import difflib
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import Field, TypeAdapter, ValidationError

from pvsld.catalogue.conductors import Cable, CableFamily
from pvsld.catalogue.errors import CatalogueError, UnknownComponentError
from pvsld.catalogue.inverters import Inverter, InverterFamily
from pvsld.catalogue.modules import PVModule, PVModuleFamily
from pvsld.catalogue.protection import (
    AcBreaker,
    AcBreakerFamily,
    DcBreaker,
    DcFuse,
    DcFuseFamily,
    DcSpd,
    DcSpdFamily,
    DcSwitch,
    DcSwitchFamily,
    ProtectionFamily,
)

Family = (
    PVModuleFamily
    | InverterFamily
    | ProtectionFamily
    | DcFuseFamily
    | CableFamily
    | AcBreakerFamily
    | DcSwitchFamily
    | DcSpdFamily
)
Component = PVModule | Inverter | DcBreaker | DcFuse | Cable | AcBreaker | DcSwitch | DcSpd

# ``datasheets/records/<folder>/<family-id lowercase>.yaml``
RECORD_FOLDERS: dict[str, str] = {
    "pv_module": "modules",
    "string_inverter": "inverters",
    "hybrid_inverter": "inverters",
    "dc_breaker": "protection",
    "dc_fuse": "protection",
    "ac_breaker": "protection",
    "dc_switch": "protection",
    "dc_spd": "protection",
    "conductor": "conductors",
}

_FAMILY_ADAPTER: TypeAdapter[Family] = TypeAdapter(
    Annotated[Family, Field(discriminator="component_type")]
)
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dataclass(frozen=True)
class LoadedRecord:
    """A validated record and the file it was read from."""

    path: Path
    family: Family


def _format_error(path: Path, error: ValidationError) -> list[str]:
    lines = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"])
        message = item["msg"].removeprefix("Value error, ")
        lines.append(f"{path}: {location}: {message}" if location else f"{path}: {message}")
    return lines


def read_record(path: Path | str) -> Family:
    """Parse and validate one record file; raise ``CatalogueError`` naming the file and field."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CatalogueError([f"{path}: cannot read the file: {error}"]) from error
    try:
        data: Any = yaml.load(text, Loader=_YAML_LOADER)
    except yaml.YAMLError as error:
        raise CatalogueError([f"{path}: invalid YAML: {error}"]) from error
    return validate_family(data, path)


def validate_family(data: Any, where: Path | str = "record") -> Family:
    """Validate a record given as a mapping (a parsed file, or one built by Claude).

    Raises:
        CatalogueError: every problem, each prefixed with ``where`` and the field.
    """
    if not isinstance(data, dict):
        raise CatalogueError(
            [f"{where}: a record must be a YAML mapping, not {type(data).__name__}"]
        )
    component_type = data.get("component_type")
    if component_type not in RECORD_FOLDERS:
        raise CatalogueError(
            [
                f"{where}: component_type: {component_type!r} is not supported; "
                f"use one of {', '.join(RECORD_FOLDERS)}"
            ]
        )
    try:
        return _FAMILY_ADAPTER.validate_python(data)
    except ValidationError as error:
        raise CatalogueError(_format_error(Path(str(where)), error)) from error


def _layout_problems(path: Path, family: Family) -> list[str]:
    expected_folder = RECORD_FOLDERS[family.component_type]
    problems = []
    if path.parent.name != expected_folder:
        problems.append(
            f"{path}: a {family.component_type} record belongs in a folder named "
            f"{expected_folder!r}, not {path.parent.name!r}"
        )
    if path.stem != family.family_id.lower():
        problems.append(
            f"{path}: family_id: file name must be {family.family_id.lower()}.yaml "
            f"(family_id {family.family_id!r} in lowercase)"
        )
    return problems


def load_records(path: Path | str) -> list[LoadedRecord]:
    """Validate a records folder (every ``*.yaml`` below it) or a single record file.

    All problems of all files are collected and raised together in one ``CatalogueError``. For a
    folder the layout is enforced (type folder, file name = lowercase family_id) and every
    ``component_id`` must be unique across the catalogue.
    """
    root = Path(path)
    if root.is_file():
        return [LoadedRecord(root, read_record(root))]
    if not root.is_dir():
        raise CatalogueError([f"{root}: no such records folder or file"])
    records: list[LoadedRecord] = []
    problems: list[str] = []
    for file in sorted(root.rglob("*.yaml")):
        try:
            family = read_record(file)
        except CatalogueError as error:
            problems += error.problems
            continue
        problems += _layout_problems(file, family)
        records.append(LoadedRecord(file, family))
    owners: dict[str, list[Path]] = {}
    for record in records:
        for variant in record.family.variants:
            owners.setdefault(variant.component_id, []).append(record.path)
    problems += [
        f"{files[1]}: component_id: duplicate {component_id!r} (also in {files[0]})"
        for component_id, files in owners.items()
        if len(files) > 1
    ]
    if problems:
        raise CatalogueError(problems)
    return records


class ComponentRegistry:
    """In-memory index of standalone components, keyed by ``component_id``."""

    def __init__(
        self,
        components: Iterable[Component],
        *,
        skipped_unreviewed: Iterable[str] = (),
        local_ids: Iterable[str] = (),
        local_problems: Iterable[str] = (),
    ) -> None:
        components = list(components)
        repeated = sorted(
            name for name, count in Counter(c.component_id for c in components).items() if count > 1
        )
        if repeated:
            raise CatalogueError([f"duplicate component_id {name!r}" for name in repeated])
        self._components: dict[str, Component] = {c.component_id: c for c in components}
        self.skipped_unreviewed: tuple[str, ...] = tuple(skipped_unreviewed)
        self.local_ids: frozenset[str] = frozenset(local_ids)
        """Components from the user's local catalogue (:mod:`pvsld.catalogue.local`)."""
        self.local_problems: tuple[str, ...] = tuple(local_problems)
        """Local records left out: invalid, or repeating a component of the bundled catalogue."""

    @classmethod
    def load(cls, path: Path | str, *, include_unreviewed: bool = False) -> ComponentRegistry:
        """Read every record below ``path`` and index its components.

        Every record is validated, reviewed or not. Unreviewed records are then left out (their
        family ids are kept in ``skipped_unreviewed``) unless ``include_unreviewed`` is true, which
        is meant for development, never for a design that gets issued.
        """
        return cls.from_records(load_records(path), include_unreviewed=include_unreviewed)

    def is_local(self, component_id: str) -> bool:
        """True for a component the user added to the local catalogue."""
        return component_id in self.local_ids

    @classmethod
    def from_records(
        cls,
        records: Iterable[LoadedRecord],
        *,
        include_unreviewed: bool = False,
        local_records: Iterable[LoadedRecord] = (),
        local_problems: Iterable[str] = (),
    ) -> ComponentRegistry:
        """Build a registry from already validated records.

        ``local_records`` come from the user's local catalogue: the user confirmed them when they
        were added, so they are always loaded, and their ids are kept in ``local_ids``.
        """
        components: list[Component] = []
        skipped: list[str] = []
        for record in records:
            if record.family.source.reviewed or include_unreviewed:
                components += record.family.expand()
            else:
                skipped.append(record.family.family_id)
        local = [c for record in local_records for c in record.family.expand()]
        return cls(
            [*components, *local],
            skipped_unreviewed=skipped,
            local_ids=[c.component_id for c in local],
            local_problems=local_problems,
        )

    def get(self, component_id: str) -> Component:
        """Return the component with this id; ``UnknownComponentError`` (a ``KeyError``) if none."""
        try:
            return self._components[component_id]
        except KeyError:
            close = difflib.get_close_matches(component_id.upper(), self._components, n=3)
            raise UnknownComponentError(component_id, suggestions=close) from None

    def find(
        self, *, component_type: str | None = None, manufacturer: str | None = None
    ) -> list[Component]:
        """Components sorted by id, optionally filtered by type and manufacturer.

        The manufacturer matches in any case and by a part of its name: users say "Jinko" for
        "Jinko Solar" (real-prompt test of Stage 3.6.5).
        """
        wanted = None if manufacturer is None else manufacturer.casefold().strip()
        return [
            component
            for component in self
            if (component_type is None or component.component_type == component_type)
            and (wanted is None or wanted in component.manufacturer.casefold())
        ]

    def modules(self) -> list[PVModule]:
        """Every PV module, sorted by id."""
        return [c for c in self if isinstance(c, PVModule)]

    def inverters(self) -> list[Inverter]:
        """Every inverter (string and hybrid), sorted by id."""
        return [c for c in self if isinstance(c, Inverter)]

    def dc_breakers(self) -> list[DcBreaker]:
        """Every DC breaker, sorted by id."""
        return [c for c in self if isinstance(c, DcBreaker)]

    def dc_fuses(self) -> list[DcFuse]:
        """Every PV fuse link, sorted by id."""
        return [c for c in self if isinstance(c, DcFuse)]

    def ac_breakers(self) -> list[AcBreaker]:
        """Every AC breaker, sorted by id."""
        return [c for c in self if isinstance(c, AcBreaker)]

    def dc_switches(self) -> list[DcSwitch]:
        """Every PV switch-disconnector, sorted by id."""
        return [c for c in self if isinstance(c, DcSwitch)]

    def dc_spds(self) -> list[DcSpd]:
        """Every PV surge protective device, sorted by id."""
        return [c for c in self if isinstance(c, DcSpd)]

    def cables(self) -> list[Cable]:
        """Every cable size, sorted by id."""
        return [c for c in self if isinstance(c, Cable)]

    def __contains__(self, component_id: object) -> bool:
        return component_id in self._components

    def __len__(self) -> int:
        return len(self._components)

    def __iter__(self) -> Iterator[Component]:
        return iter(sorted(self._components.values(), key=lambda c: c.component_id))
