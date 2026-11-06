"""The DC protection box, sized and assembled from the strings (ENG-004, Stage 3.8.4).

:func:`dc_box` tells the editor what goes inside the box of the template
``bt_string_residential_v1`` (the owner's reference box, 2026-10-08), so the box comes ready to
place and its parts can then be replaced. The box is a function of the strings alone: adding a
string adds its parts, removing it takes them away again.

Per string, in the order the sheet draws them (upper MPPT first):

* ``CX-<string>`` field connector (MC4 type), ``XE-<string>`` entry terminal;
* ``FUS-<string>`` gPV fuse-disconnector (default, owner decision 2026-10-08) or ``DCB-<string>``
  DC breaker: the smallest catalogue device in the window of :mod:`pvsld.sizing.bos`. With one or
  two strings per MPPT input NOM 690-9(a) requires none, so the reason says it is a design choice.
  When no catalogue fuse fits, a breaker is used and the reason says why (as sizing does);
* ``XS-<string>`` exit terminal.

Shared by the strings:

* ``DPS-CD1``, the DC surge protective device (owner 2026-10-30: the engine sizes it). Type 2
  (no external lightning protection assumed), U_CPV >= Voc(T_min) of the strings (SPD-002),
  I_n >= 5 kA (SPD-003) and I_SCPV >= 1.25 x the strings' Isc (SPD-004), vault rules after
  IEC 60364-7-712. The catalogue SPD is :func:`pvsld.sizing.bos.spd_for` (certified versions
  first, then without remote signalling, then the lowest U_CPV). When none fits, U_CPV is the
  first of the usual commercial classes (:data:`SPD_UCPV_CLASSES_V`) at or above the minimum and
  I_n the 20 kA of the owner's template, and the box states both as assumptions.
* ``DCD-CD1``, the ganged PV switch-disconnector: two poles per string, four in series for a
  single string (690-15(d) and the manufacturer's wiring), rated at Voc(T_min) for 1.25 x Isc.

Values a user replaced survive when locked (``locks`` as in :mod:`pvsld.sizing.choices`); the
others take the engine's values. :func:`apply_dc_box` writes the box into a specification.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from pvsld.catalogue import ComponentRegistry
from pvsld.core import calc
from pvsld.core.draft import DraftReport, evaluate_draft
from pvsld.core.model import Module
from pvsld.resources import data_path
from pvsld.sizing import bos
from pvsld.sizing.engine import BOX_SWITCH_ID, STRING_BREAKER_PREFIX, STRING_FUSE_PREFIX
from pvsld.sizing.models import DcOcpdChoice
from pvsld.symbols.cfe import get_symbol
from pvsld.symbols.cfe.definitions import dc_disconnect_name

SPD_ID = "DPS-CD1"
SPD_PREFIX = "DPS-"
"""Per-string SPD: ``DPS-<string>`` (``DPS-S1``)."""
SWITCH_PREFIX = "DCD-"
"""Per-string box disconnect: ``DCD-<string>`` (``DCD-S1``)."""
SPD_TYPE = "T2"
SPD_IN_MIN_KA = bos.SPD_IN_MIN_KA
SPD_IN_KA = 20.0
"""Nominal discharge current of the owner's template; an assumption until a model is chosen."""
SPD_UCPV_CLASSES_V = (600.0, 800.0, 1000.0, 1200.0, 1500.0)
"""Usual U_CPV classes of PV SPDs; an assumption until a catalogue model is chosen."""
DESIGN_CHOICE_ES = "Se instala por decisión de diseño (caja de protecciones CD, 2026-10-08)."

Role = Literal["connector", "entry", "protection", "spd", "switch", "exit"]


@dataclass(frozen=True)
class BoxChild:
    """One part of the box."""

    id: str
    type_id: str
    """The palette type (``pvsld.palette``)."""
    block: str
    role: Role
    ports: tuple[str, ...]
    string_ids: tuple[str, ...]
    """The strings it belongs to (one for per-string parts, all for the shared ones)."""
    reason_es: str
    poles: int | None = None
    values: dict[str, Any] = field(default_factory=dict)
    """The spec values of the part (``ie_a``, ``ue_v``, ``ucpv_v``, ``in_ka``...)."""
    requirements: dict[str, Any] = field(default_factory=dict)
    """What the part must meet (``minimum_a``, ``ucpv_min_v``...)."""
    model: str | None = None
    source: Literal["engine", "drawn"] = "engine"
    """``drawn`` when a locked value of the drawing was kept."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type_id": self.type_id,
            "block": self.block,
            "role": self.role,
            "ports": list(self.ports),
            "string_ids": list(self.string_ids),
            "poles": self.poles,
            "values": dict(self.values),
            "requirements": dict(self.requirements),
            "model": self.model,
            "source": self.source,
            "reason_es": self.reason_es,
        }


@dataclass(frozen=True)
class BoxLink:
    """A conductor between two ports, ``<component id>.<port>``."""

    from_port: str
    to_port: str
    string_id: str

    def to_dict(self) -> dict[str, str]:
        return {"from": self.from_port, "to": self.to_port, "string_id": self.string_id}


PROTECTION_HEADER = ("Equipo", "Función", "Polos", "Capacidad", "Tensión", "kAIC", "Ubicación")


@dataclass(frozen=True)
class DcBox:
    """What :func:`dc_box` returns; ``to_dict`` gives plain JSON types."""

    draft: DraftReport
    children: tuple[BoxChild, ...] = ()
    links: tuple[BoxLink, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    """The box's rows of the protection schedule (:data:`PROTECTION_HEADER`)."""
    disconnects: tuple[dict[str, Any], ...] = ()
    """The box's entries of ``dc_bos.disconnects``."""
    spds: tuple[dict[str, Any], ...] = ()
    """The box's entry of ``dc_bos.spds``."""
    assumptions: tuple[str, ...] = ()
    skipped_es: str | None = None

    @property
    def built(self) -> bool:
        return self.skipped_es is None

    def child(self, child_id: str) -> BoxChild:
        return next(c for c in self.children if c.id == child_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "built": self.built,
            "skipped_es": self.skipped_es,
            "children": [c.to_dict() for c in self.children],
            "links": [link.to_dict() for link in self.links],
            "header": list(PROTECTION_HEADER),
            "rows": [list(r) for r in self.rows],
            "disconnects": [dict(d) for d in self.disconnects],
            "spds": [dict(s) for s in self.spds],
            "assumptions": list(self.assumptions),
            "draft": self.draft.to_dict(),
        }


def _g(value: float) -> str:
    return f"{value:g}"


def _n(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}"


def _ports(block: str) -> tuple[str, ...]:
    return tuple(port.id for port in get_symbol(block).ports)


def _drawn(data: Any) -> dict[str, dict[str, Any]]:
    """The drawn parts of the box by id (disconnects and SPDs of ``dc_bos``)."""
    dc = data.get("dc_bos") if isinstance(data, Mapping) else None
    if not isinstance(dc, Mapping):
        return {}
    found: dict[str, dict[str, Any]] = {}
    for key in ("disconnects", "spds"):
        for item in dc.get(key) or []:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                found[item["id"]] = dict(item)
    return found


class _Locks:
    def __init__(self, locks: Iterable[str], drawn: dict[str, dict[str, Any]]) -> None:
        self.locks = frozenset(lock.strip() for lock in locks if lock.strip())
        self.drawn = drawn

    def keep(self, part_id: str, values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """``values`` with the locked fields of the drawn part; True when any was kept."""
        drawn = self.drawn.get(part_id)
        if drawn is None:
            return values, False
        kept = dict(values)
        changed = False
        names = {*values, "model"}
        if part_id in self.locks:  # the whole part is locked: keep every drawn value
            names |= set(drawn) - {"id", "at", "integrated_in"}
        for name in sorted(names):
            if (part_id in self.locks or f"{part_id}.{name}" in self.locks) and name in drawn:
                changed = changed or kept.get(name) != drawn[name]
                kept[name] = drawn[name]
        return kept, changed


def _protection(
    need: bos.StringNeed,
    device: Literal["fuse", "breaker"],
    registry: ComponentRegistry,
    reason: str,
) -> DcOcpdChoice:
    if device == "fuse":
        choice = bos.fuse_for(need, registry.dc_fuses(), reason)
        if choice.device_id is not None:
            return choice
        reason = f"{choice.reason_es} Se usa un ITM de CD."
    return bos.breaker_for(need, registry.dc_breakers(), reason)


def _spd_values(
    voc_v: float, isc_total_a: float, registry: ComponentRegistry
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """The SPD's spec values, its requirements, and the assumption made when none fits."""
    need = bos.spd_need(voc_v, isc_total_a)
    requirements = {
        "ucpv_min_v": round(need.ucpv_min_v, 1),
        "in_min_ka": need.in_min_ka,
        "iscpv_min_a": round(need.iscpv_min_a, 2),
    }
    spd = bos.spd_for(need, registry.dc_spds())
    if spd is not None:
        values: dict[str, Any] = {
            "spd_type": spd.spd_type,
            "ucpv_v": spd.ucpv_v,
            "in_ka": spd.in_ka,
            "iscpv_a": spd.iscpv_ka * 1000,
            "model": spd.component_id,
        }
        return values, requirements, None
    values = {"spd_type": SPD_TYPE, "in_ka": SPD_IN_KA}
    ucpv = next((v for v in SPD_UCPV_CLASSES_V if v >= need.ucpv_min_v), None)
    if ucpv is not None:
        values["ucpv_v"] = ucpv
    assumption = (
        f"{SPD_ID}: ningún DPS del catálogo cumple; tipo 2 (sin pararrayos externo), U_CPV de la "
        f"primera clase comercial usual de al menos {_n(voc_v)} V e In {_g(SPD_IN_KA)} kA, "
        "confirme con el modelo."
    )
    return values, requirements, assumption


def dc_box(
    data: Any,
    *,
    device: Literal["fuse", "breaker"] = "fuse",
    layout: Literal["auto", "shared", "per_string"] = "auto",
    locks: Iterable[str] = (),
    registry: ComponentRegistry | None = None,
) -> DcBox:
    """Size and assemble the DC protection box of ``data`` (see the module docstring).

    ``data`` is a specification as plain data; only ``project.site``, ``standards``,
    ``modules``, ``inverters`` and ``strings`` must be valid (the box needs no AC side).
    ``device`` is the string protection ("fuse" by default, or "breaker"). ``layout`` is
    "shared" (one SPD and one ganged switch for every string) or "per_string" (each string has
    its own two-pole switch ``DCD-<string>`` and SPD ``DPS-<string>``, like the commercial
    two-circuit box; only the ground is shared, owner request 2026-11-04). The default "auto"
    keeps the layout of the drawn parts (:func:`box_layout`), so a re-evaluation does not lose it,
    and is "shared" when there are none.
    """
    draft = evaluate_draft(data)
    if draft.derived is None:
        return DcBox(
            draft,
            skipped_es="No se armó la caja: primero hay que completar o corregir las cadenas, "
            "los módulos, el inversor o el sitio (ver «draft»).",
        )
    unsupported = draft.issues_of("unsupported")
    if unsupported:
        return DcBox(
            draft,
            skipped_es="No se armó la caja: el diseño usa algo que el motor aún no dimensiona "
            "(ver los problemas «unsupported»).",
        )
    registry = (
        registry
        if registry is not None
        else ComponentRegistry.load(data_path("datasheets/records"))
    )
    try:
        modules = {m.id: m for m in TypeAdapter(list[Module]).validate_python(data["modules"])}
    except (ValidationError, KeyError, TypeError):  # pragma: no cover - the draft checked them
        return DcBox(draft, skipped_es="No se armó la caja: los módulos no son válidos.")
    lock = _Locks(locks, _drawn(data))
    grounding = data.get("grounding") if isinstance(data, Mapping) else None
    ocpd_poles = bos.string_ocpd_poles(
        grounding.get("dc_system") if isinstance(grounding, Mapping) else None
    )
    if layout == "auto":
        layout = box_layout(data) or "shared"
    inverter = get_symbol("PVSLD_INV")
    strings = sorted(
        draft.derived.strings, key=lambda s: (-inverter.port(s.mppt_id).y, s.string_id)
    )
    n = len(strings)

    children: list[BoxChild] = []
    links: list[BoxLink] = []
    disconnects: list[dict[str, Any]] = []
    rows: list[tuple[str, ...]] = []
    protections: list[DcOcpdChoice] = []
    assumptions: list[str] = []
    switch_block = dc_disconnect_name(n)
    for index, string in enumerate(strings, start=1):
        sid = string.string_id
        module = modules[string.module_id]
        need = bos.string_need(
            max_series_fuse_a=module.max_series_fuse_a,
            isc_a=string.isc_a,
            strings_per_input=string.n_parallel,
            voc_cold_string_v=string.voc_max_string_v,
            poles=ocpd_poles,
        )
        required, why = bos.string_ocpd_required(module, string.isc_a, string.n_parallel)
        reason = why if required else f"{why} {DESIGN_CHOICE_ES}"
        choice = _protection(need, device, registry, reason)
        protections.append(choice)
        is_fuse = choice.device == "fuse"
        prefix = STRING_FUSE_PREFIX if is_fuse else STRING_BREAKER_PREFIX
        block = "PVSLD_FUSE_DISC_DC" if is_fuse else "PVSLD_CB_DC"
        part_id = f"{prefix}{sid}"
        values: dict[str, Any] = {}
        if choice.rating_a is not None:
            values = {"ie_a": choice.rating_a, "ue_v": choice.ue_v, "model": choice.device_id}
        values, kept = lock.keep(part_id, values)
        poles = choice.poles or ocpd_poles
        connector, entry, exit_ = f"CX-{sid}", f"XE-{sid}", f"XS-{sid}"
        children += [
            BoxChild(
                connector,
                "pv_connector",
                "PVSLD_PV_CONNECTOR",
                "connector",
                _ports("PVSLD_PV_CONNECTOR"),
                (sid,),
                f"Conector de campo de la cadena {sid}.",
            ),
            BoxChild(
                entry,
                "terminal",
                "PVSLD_TERMINAL",
                "entry",
                _ports("PVSLD_TERMINAL"),
                (sid,),
                f"Borne de entrada de la cadena {sid} a la caja.",
            ),
            BoxChild(
                part_id,
                "dc_fuse_holder" if is_fuse else "dc_breaker",
                block,
                "protection",
                _ports(block),
                (sid,),
                choice.reason_es,
                poles=poles,
                values={k: v for k, v in values.items() if k != "model"},
                requirements={
                    "minimum_a": round(need.minimum_a, 2),
                    "maximum_a": need.maximum_a,
                    "voc_cold_v": round(need.voc_cold_v, 1),
                    "fault_a": round(need.fault_a, 2),
                },
                model=values.get("model"),
                source="drawn" if kept else "engine",
            ),
            BoxChild(
                exit_,
                "terminal",
                "PVSLD_TERMINAL",
                "exit",
                _ports("PVSLD_TERMINAL"),
                (sid,),
                f"Borne de salida de la cadena {sid} hacia el inversor.",
            ),
        ]
        if layout == "per_string":
            switch_id, spd_id = f"{SWITCH_PREFIX}{sid}", f"{SPD_PREFIX}{sid}"
            switch_in, switch_out, spd_port = f"{switch_id}.IN", f"{switch_id}.OUT", "L"
        else:
            switch_id, spd_id = BOX_SWITCH_ID, SPD_ID
            switch_in, switch_out = f"{switch_id}.IN{index}", f"{switch_id}.OUT{index}"
            spd_port = f"L{index}"
        links += [
            BoxLink(f"{sid}.OUT", f"{connector}.IN", sid),
            BoxLink(f"{connector}.OUT", f"{entry}.L", sid),
            BoxLink(f"{entry}.R", f"{part_id}.IN", sid),
            BoxLink(f"{part_id}.OUT", switch_in, sid),
            BoxLink(switch_out, f"{exit_}.L", sid),
            BoxLink(f"{exit_}.R", f"{string.inverter_id}.{string.mppt_id}", sid),
        ]
        if layout == "per_string" or index <= 2:
            links.append(BoxLink(f"{part_id}.OUT", f"{spd_id}.{spd_port}", sid))
        if "ie_a" in values:
            disconnects.append(
                {
                    "id": part_id,
                    "integrated_in": None,
                    "poles": poles,
                    "ue_v": values["ue_v"],
                    "ie_a": values["ie_a"],
                    "model": values.get("model"),
                }
            )
            rows.append(
                (
                    part_id,
                    "Fusible gPV (cadena)" if is_fuse else "ITM de CD (cadena)",
                    str(poles),
                    f"{_g(values['ie_a'])} A",
                    f"{_g(values['ue_v'])} V",
                    "—",
                    "Caja CD",
                )
            )

    inverter_id = strings[0].inverter_id
    first = next((p for p in protections if p.rating_a is not None), None)
    spds: list[dict[str, Any]] = []

    def add_spd(spd_id: str, members: list[Any], block: str) -> None:
        voc = max(s.voc_max_string_v for s in members)
        values, needs, assumption = _spd_values(voc, sum(s.isc_a for s in members), registry)
        values, kept = lock.keep(spd_id, values)
        if assumption is not None:
            assumptions.append(assumption.replace(SPD_ID, spd_id, 1))
        spds.append(
            {
                "id": spd_id,
                "at": f"{inverter_id}.dc",
                **{k: v for k, v in values.items() if v is not None},
            }
        )
        children.append(
            BoxChild(
                spd_id,
                "dc_spd",
                block,
                "spd",
                _ports(block),
                tuple(s.string_id for s in members),
                f"DPS de CD: U_CPV >= {_n(voc)} V (SPD-002), In >= {_g(SPD_IN_MIN_KA)} kA "
                f"(SPD-003), I_SCPV >= {_n(needs['iscpv_min_a'])} A (SPD-004).",
                values={k: v for k, v in values.items() if k != "model"},
                requirements=needs,
                model=values.get("model"),
                source="drawn" if kept else "engine",
            )
        )
        volts = values.get("ucpv_v")
        rows.append(
            (
                spd_id,
                f"DPS de CD tipo {values['spd_type']}",
                "—",
                f"In {_g(values['in_ka'])} kA",
                f"{_g(volts)} V" if volts else "—",
                "—",
                "Caja CD",
            )
        )

    def add_switch(
        switch_id: str, members: list[Any], block: str, poles_per_string: int | None
    ) -> None:
        count = len(members)
        voc = max(s.voc_max_string_v for s in members)
        i_max = max(calc.ISC_SAFETY_FACTOR * s.isc_a for s in members)
        per_string = bos.box_switch_poles_per_string(count, poles_per_string)
        switch, found = bos.select_box_switch(
            position=switch_id,
            n_strings=count,
            voc_cold_string_v=voc,
            string_i_max_a=i_max,
            switches=registry.dc_switches(),
            poles_per_string=poles_per_string,
        )
        if switch is not None:
            values: dict[str, Any] = {
                "poles": switch.poles,
                "ue_v": switch.voltage_v,
                "ie_a": switch.rating_a,
                "model": switch.device_id,
            }
            used = per_string * count
            reason = (
                f"Seccionador de la caja: {per_string} polos por cadena para {count} cadena(s), "
                f"{_g(switch.rating_a)} A a {_g(switch.voltage_v)} V (>= {_n(i_max)} A, "
                f">= {_n(voc)} V)."
            )
            if (switch.poles or 0) > used:
                reason += f" Usa {used} de sus {switch.poles} polos."
        else:
            values = {
                "poles": per_string * count,
                "ue_v": first.ue_v if first else None,
                "ie_a": first.rating_a if first else None,
            }
            reason = found[0].message_es if found else ""
            assumptions.append(
                f"{switch_id}: sin seccionador adecuado en el catálogo; toma los valores de la "
                "protección de cadena."
            )
        values, kept = lock.keep(switch_id, values)
        children.append(
            BoxChild(
                switch_id,
                "dc_switch",
                block,
                "switch",
                _ports(block),
                tuple(s.string_id for s in members),
                reason,
                poles=values.get("poles"),
                values={k: v for k, v in values.items() if k not in ("model", "poles")},
                requirements={
                    "poles_min": per_string * count,
                    "ie_min_a": round(i_max, 2),
                    "ue_min_v": round(voc, 1),
                },
                model=values.get("model"),
                source="drawn" if kept else "engine",
            )
        )
        if values.get("ie_a") is not None and values.get("ue_v") is not None:
            disconnects.append(
                {
                    "id": switch_id,
                    "integrated_in": None,
                    "poles": values["poles"],
                    "ue_v": values["ue_v"],
                    "ie_a": values["ie_a"],
                    "model": values.get("model"),
                }
            )
            rows.append(
                (
                    switch_id,
                    "Desconectador de CD",
                    str(values["poles"]),
                    f"{_g(values['ie_a'])} A",
                    f"{_g(values['ue_v'])} V",
                    "—",
                    "Caja CD",
                )
            )

    if layout == "per_string":
        for string in strings:
            add_spd(f"{SPD_PREFIX}{string.string_id}", [string], "PVSLD_SPD")
            add_switch(
                f"{SWITCH_PREFIX}{string.string_id}",
                [string],
                "PVSLD_DC_DISCONNECT",
                bos.STRING_SWITCH_POLES,
            )
    else:
        add_spd(SPD_ID, list(strings), "PVSLD_SPD_DC_BOX")
        add_switch(BOX_SWITCH_ID, list(strings), switch_block, None)
    return DcBox(
        draft,
        children=tuple(children),
        links=tuple(links),
        rows=tuple(rows),
        disconnects=tuple(disconnects),
        spds=tuple(spds),
        assumptions=tuple(assumptions),
    )


BOX_PREFIXES = (STRING_FUSE_PREFIX, STRING_BREAKER_PREFIX, BOX_SWITCH_ID)
_BOX_PART = re.compile(r"^(FUS-|DCB-|DCD-CD\d+$|DCD-S\d+$|DPS-CD\d+$|DPS-S\d+$)")


_PER_STRING_PART = re.compile(r"^(DCD|DPS)-S\d+$")
_SHARED_PART = re.compile(r"^(DCD|DPS)-CD\d+$")


def box_layout(data: Any) -> Literal["shared", "per_string"] | None:
    """The layout of the DC box drawn in ``data``: ``per_string`` when it has ``DCD-<string>`` or
    ``DPS-<string>`` parts, ``shared`` when it has ``DCD-CD<n>`` or ``DPS-CD<n>``, else None.

    The layout lives in the specification through these ids; no extra field is needed.
    """
    ids = list(_drawn(data))
    if any(_PER_STRING_PART.match(i) for i in ids):
        return "per_string"
    if any(_SHARED_PART.match(i) for i in ids):
        return "shared"
    return None


def is_box_part(part_id: str) -> bool:
    """True for an id the DC box owns in either layout (the inverter's DCD-1 is not one)."""
    return _BOX_PART.match(part_id) is not None


def apply_dc_box(data: Mapping[str, Any], box: DcBox) -> dict[str, Any]:
    """A copy of ``data`` whose box parts in ``dc_bos`` are those of ``box``.

    Parts outside the box (the inverter's integrated disconnect, other SPDs) stay; box parts of
    strings that no longer exist go away.
    """
    if not box.built:
        raise ValueError(box.skipped_es)
    result = copy.deepcopy(dict(data))
    dc = dict(result.get("dc_bos") or {})
    kept = [
        d
        for d in dc.get("disconnects") or []
        if not (isinstance(d, Mapping) and is_box_part(str(d.get("id", ""))))
    ]
    other_spds = [
        s
        for s in dc.get("spds") or []
        if not (isinstance(s, Mapping) and is_box_part(str(s.get("id", ""))))
    ]
    dc["disconnects"] = kept + [
        {k: v for k, v in d.items() if v is not None or k == "integrated_in"}
        for d in box.disconnects
    ]
    dc["spds"] = other_spds + [dict(s) for s in box.spds]
    dc.setdefault("combiners", [])
    result["dc_bos"] = dc
    return result


__all__ = ["BoxChild", "BoxLink", "DcBox", "apply_dc_box", "box_layout", "dc_box", "is_box_part"]
