"""Draft evaluation for an interactive editor (ENG-002, Stage 3.8.2a).

:func:`~pvsld.core.validation.validate_pv_design` stays strict: a specification that does not parse
gets ``GEN-001`` findings and no rule runs. An editor builds the design one component at a time, so
:func:`evaluate_draft` says, for every problem, *what kind* it is and which components it touches:

* ``missing``: a required value or section is absent (an empty required list counts);
* ``incompatible``: the value is there but not acceptable (type, range, allowed choice, Vmp >= Voc);
* ``invalid_structure``: wrong shape, unknown field, or a reference to a missing component;
* ``unsupported``: valid for the schema, but the engine does not calculate or draw it yet.

A missing value is never replaced by zero or a default: whatever cannot be evaluated is listed in
``not_evaluated`` with the reason. Every issue keeps the rule id ``GEN-001`` (the plugin already
reads it) and adds its ``category``.

Stage 3.8.2a evaluates the rule pack only when the whole specification parses; Stage 3.8.2b will run
each rule as soon as the sections it reads are valid. The cross-reference check (a string naming an
inverter that is not there) belongs to the root model, so Pydantic runs it only once every field
parses: broken references show up after the other issues are fixed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from pvsld.core import calc
from pvsld.core.calc import Derived
from pvsld.core.model import RULEPACK_ID, SCHEMA_VERSION, PvSystemSpec, parse_spec
from pvsld.core.policy import DEFAULT_DC_AC_POLICY, DcAcPolicy
from pvsld.core.rules import RULES, Finding, Severity, run_rules

__all__ = [
    "Category",
    "DraftIssue",
    "DraftReport",
    "NotEvaluated",
    "evaluate_draft",
]

Category = Literal["missing", "incompatible", "invalid_structure", "unsupported"]

RULE_ID = "GEN-001"

_STRUCTURE_TYPES = frozenset(
    {
        "extra_forbidden",
        "dict_type",
        "list_type",
        "tuple_type",
        "model_type",
        "model_attributes_type",
        "is_instance_of",
    }
)

SUPPORTED_INVERTER_TYPES = frozenset({"string", "hybrid"})
MAX_INVERTERS = 1
MAX_STRINGS = 2
INVERTER_DC_PORTS = ("A", "B")
"""DC inputs of the inverter symbol ``PVSLD_INV`` that a string can be drawn on."""


@dataclass(frozen=True)
class DraftIssue:
    """One problem of the draft: its category, where it is and which components it touches."""

    category: Category
    path: str
    message_es: str
    component_ids: tuple[str, ...] = ()
    rule_id: str = RULE_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "path": self.path,
            "component_ids": list(self.component_ids),
            "message_es": self.message_es,
        }


@dataclass(frozen=True)
class NotEvaluated:
    """A rule that did not run, and why."""

    rule_id: str
    reason_es: str
    blocking_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "reason_es": self.reason_es,
            "blocking_paths": list(self.blocking_paths),
        }


@dataclass(frozen=True)
class DraftReport:
    """Result of :func:`evaluate_draft`.

    ``complete`` is ``True`` when the draft parses and nothing in it is unsupported, so it can be
    drawn; ``ok`` when, in addition, no rule finding has severity ``E``.
    """

    issues: tuple[DraftIssue, ...]
    findings: tuple[Finding, ...]
    not_evaluated: tuple[NotEvaluated, ...]
    derived: Derived | None
    spec: PvSystemSpec | None = None
    rulepack: str = RULEPACK_ID
    schema_version: str = SCHEMA_VERSION
    evaluated: tuple[str, ...] = field(default=())

    @property
    def complete(self) -> bool:
        return self.spec is not None and not self.issues

    @property
    def ok(self) -> bool:
        return self.complete and not any(f.severity is Severity.ERROR for f in self.findings)

    def issues_of(self, category: Category) -> tuple[DraftIssue, ...]:
        return tuple(i for i in self.issues if i.category == category)

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "ok": self.ok,
            "rulepack": self.rulepack,
            "schema_version": self.schema_version,
            "issues": [i.to_dict() for i in self.issues],
            "findings": [f.to_dict() for f in self.findings],
            "evaluated": list(self.evaluated),
            "not_evaluated": [n.to_dict() for n in self.not_evaluated],
        }


# --- Paths and component ids ---------------------------------------------------------------------


def _path(loc: Sequence[str | int]) -> str:
    text = ""
    for part in loc:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else str(part))
    return text or "(raíz)"


def _ids_along(data: Any, loc: Sequence[str | int]) -> tuple[str, ...]:
    """The ``id`` of the deepest object on ``loc`` that has one."""
    found: str | None = None
    node = data
    for part in loc:
        try:
            node = node[part]
        except (KeyError, IndexError, TypeError):
            break
        if isinstance(node, Mapping) and isinstance(node.get("id"), str):
            found = node["id"]
    return (found,) if found else ()


def _all_ids(data: Any) -> set[str]:
    ids: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            if isinstance(node.get("id"), str) and node["id"]:
                ids.add(node["id"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return ids


def _ids_in_text(text: str, known: Iterable[str]) -> tuple[str, ...]:
    tokens = set(re.findall(r"[A-Za-z0-9_.\-]+", text))
    return tuple(sorted(i for i in known if i in tokens))


# --- Classification ------------------------------------------------------------------------------


def _category(item: Mapping[str, Any]) -> Category:
    kind = item["type"]
    if kind == "missing":
        return "missing"
    if kind == "too_short" and isinstance(item.get("input"), list):
        return "missing"
    if kind in _STRUCTURE_TYPES:
        return "invalid_structure"
    return "incompatible"


_PREFIX: dict[Category, str] = {
    "missing": "Falta",
    "incompatible": "Valor no válido en",
    "invalid_structure": "Estructura no válida en",
    "unsupported": "No soportado en",
}


def _schema_issues(error: ValidationError, data: Any) -> Iterator[DraftIssue]:
    known = _all_ids(data)
    for item in error.errors():
        loc = tuple(item["loc"])
        if not loc and item["type"] == "value_error":
            # The cross-reference check of the root model: one issue per broken reference.
            message = str(item["msg"]).removeprefix("Value error, ")
            for problem in message.split("; "):
                yield DraftIssue(
                    category="invalid_structure",
                    path="(raíz)",
                    message_es=f"Referencia o identificador no válido: {problem}.",
                    component_ids=_ids_in_text(problem, known),
                )
            continue
        category = _category(item)
        path = _path(loc)
        message = f"{_PREFIX[category]} «{path}»"
        if category != "missing":
            message += f": {item['msg']}"
        yield DraftIssue(
            category=category,
            path=path,
            message_es=message + ".",
            component_ids=_ids_along(data, loc),
        )


def _unsupported(spec: PvSystemSpec) -> Iterator[DraftIssue]:
    """What the schema accepts but the engine does not calculate or draw yet."""
    inverter_ids = tuple(i.id for i in spec.inverters)
    if len(spec.inverters) > MAX_INVERTERS:
        yield DraftIssue(
            "unsupported",
            "inverters",
            f"Por ahora el motor calcula y dibuja un solo inversor; hay {len(spec.inverters)}.",
            inverter_ids,
        )
    for index, inverter in enumerate(spec.inverters):
        if inverter.type not in SUPPORTED_INVERTER_TYPES:
            yield DraftIssue(
                "unsupported",
                f"inverters[{index}].type",
                f"El inversor {inverter.id} es de tipo «{inverter.type}»; por ahora solo se "
                "calculan inversores de cadena e híbridos.",
                (inverter.id,),
            )
        if inverter.qty > 1:
            yield DraftIssue(
                "unsupported",
                f"inverters[{index}].qty",
                f"El inversor {inverter.id} tiene cantidad {inverter.qty}; por ahora el motor "
                "dibuja un solo equipo.",
                (inverter.id,),
            )
    if len(spec.strings) > MAX_STRINGS:
        yield DraftIssue(
            "unsupported",
            "strings",
            f"Por ahora el motor dibuja hasta {MAX_STRINGS} cadenas; hay {len(spec.strings)}.",
            tuple(s.id for s in spec.strings),
        )
    for index, string in enumerate(spec.strings):
        if string.mppt not in INVERTER_DC_PORTS:
            yield DraftIssue(
                "unsupported",
                f"strings[{index}].mppt",
                f"La cadena {string.id} usa el MPPT «{string.mppt}»; por ahora solo se dibujan "
                f"las entradas {' y '.join(INVERTER_DC_PORTS)}.",
                (string.id, string.inverter),
            )
    if spec.storage.coupling != "none":
        yield DraftIssue(
            "unsupported",
            "storage.coupling",
            "Por ahora el motor no calcula almacenamiento de energía.",
        )
    if spec.utility.voltage_level != "BT":
        yield DraftIssue(
            "unsupported",
            "utility.voltage_level",
            "Por ahora el motor solo calcula servicios en baja tensión.",
        )
    for section in ("transformer", "mt_protection"):
        if getattr(spec, section) is not None:
            yield DraftIssue(
                "unsupported",
                section,
                "Por ahora el motor no calcula transformador ni protección de media tensión.",
            )


def _top_sections(issues: Iterable[DraftIssue]) -> tuple[str, ...]:
    sections = {re.split(r"[.\[]", issue.path, maxsplit=1)[0] for issue in issues}
    return tuple(sorted(sections))


# --- Entry point ---------------------------------------------------------------------------------


def evaluate_draft(
    data: Mapping[str, Any] | PvSystemSpec,
    *,
    dc_ac_policy: DcAcPolicy = DEFAULT_DC_AC_POLICY,
) -> DraftReport:
    """Classify the problems of a draft specification and run the rules it allows.

    Never raises on bad input. Nothing missing is filled in: rules that cannot run are listed in
    ``not_evaluated``.
    """
    if isinstance(data, PvSystemSpec):
        spec = data
    elif not isinstance(data, Mapping):
        issue = DraftIssue(
            "invalid_structure",
            "(raíz)",
            "La especificación debe ser un objeto con secciones; se recibió "
            f"{type(data).__name__}.",
        )
        return _blocked((issue,))
    else:
        try:
            spec = parse_spec(data)
        except ValidationError as error:
            return _blocked(tuple(_schema_issues(error, data)))
    issues = tuple(_unsupported(spec))
    derived = calc.derive(spec)
    findings = tuple(run_rules(spec, derived, dc_ac_policy))
    return DraftReport(
        issues=issues,
        findings=findings,
        not_evaluated=(),
        derived=derived,
        spec=spec,
        evaluated=tuple(rule.id for rule in RULES),
    )


def _blocked(issues: tuple[DraftIssue, ...]) -> DraftReport:
    blocking = _top_sections(issues)
    reason = (
        "No se evaluó: la especificación todavía no está completa; primero hay que corregir "
        + ", ".join(f"«{section}»" for section in blocking)
        + "."
    )
    return DraftReport(
        issues=issues,
        findings=(),
        not_evaluated=tuple(NotEvaluated(rule.id, reason, blocking) for rule in RULES),
        derived=None,
    )
