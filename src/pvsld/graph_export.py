"""From the engine's specification to the editor's drawing (Stage 3.10.2).

:func:`spec_to_graph` is the inverse of :func:`pvsld.graph.graph_to_spec`: a specification (from
the sizing engine, the plugin or the editor's chatbot) becomes a ``pvsld.graph/1`` drawing plus
the template of everything the drawing does not hold, so that ``graph_to_spec(graph, template)``
gives the same design back. Node ids are the specification ids, so they follow the palette's
naming rules; catalogue references are recovered from the manufacturer and model the
specification carries. Placing the nodes on the canvas is the editor's job.

What is drawn:

* **String inverters.** Each string (``pv_string``), the DC protection box as a ``dc_box`` group
  (the parts :func:`pvsld.sizing.dcbox.dc_box` assembles, with the specification's values), the
  inverter and its breaker; each circuit's length, size, ambient and raceway sit on its first
  cable.
* **Microinverters.** Each unit (``microinverter``) with a ``pv_module`` on each used input, the
  trunk as junction taps ending in an ``ac_junction_box``, the home run to the branch breaker
  (its length and size), and the ``ac_combiner`` group (branch breakers, a chain of junctions as
  the bus, the AC SPD) with its main breaker outside.
* **Service.** The load centre, the service main, the meter, the grid and the ground electrodes.
"""

from __future__ import annotations

import copy
import itertools
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pvsld.catalogue import ComponentRegistry, Microinverter
from pvsld.catalogue import Inverter as CatalogueInverter
from pvsld.catalogue import PVModule as CatalogueModule
from pvsld.graph import FORMAT
from pvsld.resources import data_path
from pvsld.sizing import adapters
from pvsld.sizing.dcbox import dc_box

DRAWN_SECTIONS = (
    "modules",
    "inverters",
    "strings",
    "circuits",
    "dc_bos",
    "ac_branches",
    "ac_combiner",
)
"""Sections the drawing rebuilds; the template keeps the rest."""
GRID_ID = "RED-1"


@dataclass(frozen=True)
class GraphExport:
    """The drawing, the template of what is not drawn, and what could not be expressed."""

    graph: dict[str, Any]
    template: dict[str, Any]
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "graph": self.graph,
            "template": self.template,
            "issues": list(self.issues),
        }


@dataclass
class _Drawing:
    registry: ComponentRegistry
    nodes: list[dict[str, Any]] = field(default_factory=list)
    cables: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    breakers: set[str] = field(default_factory=set)
    """Breakers the drawing holds (left out of the template)."""

    def node(self, node_id: str, type_id: str, **values: Any) -> dict[str, Any]:
        item: dict[str, Any] = {"id": node_id, "type": type_id}
        clean = {k: v for k, v in values.items() if v is not None}
        if clean:
            item["values"] = clean
        self.nodes.append(item)
        return item

    def cable(self, a: str, b: str, **extra: Any) -> dict[str, Any]:
        item = {"from": a, "to": b, **{k: v for k, v in extra.items() if v is not None}}
        self.cables.append(item)
        return item

    def catalogue_id(self, kind: type[Any], manufacturer: str, model: str) -> str | None:
        for component in self.registry:
            if (
                isinstance(component, kind)
                and component.manufacturer == manufacturer
                and adapters.display_model(component) == model
            ):
                return component.component_id
        self.issues.append(f"«{manufacturer} {model}» no está en el catálogo.")
        return None


def _circuit_extra(circuit: Mapping[str, Any] | None) -> dict[str, Any]:
    """Length, size, ambient and raceway of a circuit, for its first cable."""
    if not circuit:
        return {}
    raceway = circuit.get("raceway") or {}
    return {
        "length_m": circuit.get("length_m"),
        "size": (circuit.get("conductors") or {}).get("size"),
        "ambient_c": circuit.get("ambient_c"),
        "raceway_trade_size_mm": raceway.get("trade_size_mm"),
    }


def _values(entry: Mapping[str, Any], drop: Iterable[str]) -> dict[str, Any]:
    skip = set(drop)
    return {k: v for k, v in entry.items() if k not in skip and v is not None}


def spec_to_graph(
    spec: Mapping[str, Any], *, registry: ComponentRegistry | None = None
) -> GraphExport:
    """The editor drawing of ``spec`` and the template of its undrawn sections."""
    registry = (
        registry
        if registry is not None
        else ComponentRegistry.load(data_path("datasheets/records"))
    )
    d = _Drawing(registry)
    circuits = {c["id"]: c for c in spec.get("circuits", [])}
    by_from = {c["from"].split(".", 1)[0]: c for c in spec.get("circuits", [])}
    ac = spec.get("ac_bos") or {}
    ocpds = {o["id"]: o for o in ac.get("ocpds", [])}
    panel_id = (ac.get("point_of_connection") or {}).get("panel") or "CC-1"
    modules = {m["id"]: m for m in spec.get("modules", [])}
    module_refs = {
        mid: d.catalogue_id(CatalogueModule, m["manufacturer"], m["model"])
        for mid, m in modules.items()
    }

    def breaker(breaker_id: str) -> None:
        entry = ocpds.get(breaker_id, {"id": breaker_id})
        d.node(breaker_id, "ac_breaker", **_values(entry, ("id", "role", "backfed", "at")))
        d.breakers.add(breaker_id)

    # String inverters, strings and the DC box.
    for inverter in spec.get("inverters", []):
        if inverter.get("type") == "micro":
            continue
        ref = d.catalogue_id(CatalogueInverter, inverter["manufacturer"], inverter["model"])
        d.node(inverter["id"], "inverter", model=ref)
    for string in spec.get("strings", []):
        d.node(
            string["id"],
            "pv_string",
            module=module_refs.get(string["module"]),
            n_series=string["n_series"],
            tilt_deg=string.get("tilt_deg"),
            azimuth_deg=string.get("azimuth_deg"),
        )
    _dc_side(d, spec, circuits, registry)

    itm_id = (ac.get("point_of_connection") or {}).get("breaker")
    for inverter in spec.get("inverters", []):
        if inverter.get("type") == "micro" or not itm_id:
            continue
        breaker(itm_id)
        d.cable(
            f"{inverter['id']}.AC", f"{itm_id}.IN", **_circuit_extra(by_from.get(inverter["id"]))
        )
        d.cable(f"{itm_id}.OUT", f"{panel_id}.LEFT")

    # Microinverters.
    _micro_side(d, spec, by_from, ocpds, panel_id, module_refs, breaker)

    # Service.
    panel = next((p for p in ac.get("panels", []) if p["id"] == panel_id), {"id": panel_id})
    d.node(panel_id, "load_centre", **_values(panel, ("id", "main_ocpd", "system")))
    for main in ac.get("main_breakers", []):
        d.node(main["id"], "ac_breaker", **_values(main, ("id", "role", "bidirectional")))
        d.cable(f"{panel_id}.RIGHT", f"{main['id']}.IN")
        upstream = f"{main['id']}.OUT"
        for meter in ac.get("meters", []):
            d.node(meter["id"], "meter", **_values(meter, ("id",)))
            d.cable(upstream, f"{meter['id']}.IN")
            upstream = f"{meter['id']}.OUT"
        d.node(GRID_ID, "grid")
        d.cable(upstream, f"{GRID_ID}.IN")
        break
    for index, electrode in enumerate((spec.get("grounding") or {}).get("electrodes", []), 1):
        electrode_id = f"TE-{index}"
        d.node(electrode_id, "ground_electrode", **_values(electrode, ()))
        d.cable(f"{electrode_id}.PE", f"{panel_id}.PE")

    template = {k: copy.deepcopy(v) for k, v in spec.items() if k not in DRAWN_SECTIONS}
    if "ac_bos" in template:
        template["ac_bos"]["ocpds"] = [
            o for o in template["ac_bos"].get("ocpds", []) if o["id"] not in d.breakers
        ]
        combiner = spec.get("ac_combiner") or {}
        if combiner.get("spd"):
            template["ac_bos"]["spds"] = [
                s for s in template["ac_bos"].get("spds", []) if s["id"] != combiner["spd"]
            ]
    graph = {"format": FORMAT, "nodes": d.nodes, "cables": d.cables}
    return GraphExport(graph, template, tuple(dict.fromkeys(d.issues)))


def _dc_side(
    d: _Drawing,
    spec: Mapping[str, Any],
    circuits: Mapping[str, Mapping[str, Any]],
    registry: ComponentRegistry,
) -> None:
    strings = spec.get("strings", [])
    if not strings:
        return
    disconnects = {x["id"]: x for x in (spec.get("dc_bos") or {}).get("disconnects", [])}
    spds = {x["id"]: x for x in (spec.get("dc_bos") or {}).get("spds", [])}
    string_circuit = {c["from"]: c for c in circuits.values() if c.get("kind") == "pv_source"}
    has_box = any(not x.get("integrated_in") for x in disconnects.values())
    if not has_box:
        for string in strings:
            d.cable(
                f"{string['id']}.OUT",
                f"{string['inverter']}.{string['mppt']}",
                **_circuit_extra(string_circuit.get(string["id"])),
            )
        return
    device = "breaker" if any(i.startswith("DCB-") for i in disconnects) else "fuse"
    box = dc_box(dict(spec), device=device, locks=[*disconnects, *spds], registry=registry)
    if box.skipped_es:
        d.issues.append(box.skipped_es)
        return
    children = []
    for child in box.children:
        entry = disconnects.get(child.id) or spds.get(child.id)
        values = (
            _values(entry, ("id", "integrated_in", "at"))
            if entry is not None
            else dict(child.values)
        )
        item: dict[str, Any] = {"id": child.id, "type": child.type_id}
        if values:
            item["values"] = values
        children.append(item)
    d.nodes.append({"id": "CAJA-CD", "type": "dc_box", "children": children})
    first: dict[str, bool] = {}
    for link in box.links:
        extra = {}
        if link.string_id not in first and link.from_port == f"{link.string_id}.OUT":
            first[link.string_id] = True
            extra = _circuit_extra(string_circuit.get(link.string_id))
        d.cable(link.from_port, link.to_port, **extra)


def _micro_side(
    d: _Drawing,
    spec: Mapping[str, Any],
    by_from: Mapping[str, Mapping[str, Any]],
    ocpds: Mapping[str, Mapping[str, Any]],
    panel_id: str,
    module_refs: Mapping[str, str | None],
    breaker: Any,
) -> None:
    branches = spec.get("ac_branches", [])
    if not branches:
        return
    models = {
        i["id"]: d.catalogue_id(Microinverter, i["manufacturer"], i["model"])
        for i in spec.get("inverters", [])
        if i.get("type") == "micro"
    }
    combiner = spec.get("ac_combiner")
    for branch in branches:
        units = branch["microinverters"]
        for unit in units:
            d.node(unit["id"], "microinverter", model=models.get(unit["inverter"]))
            for letter, module_id in unit["inputs"].items():
                module_node = f"M-{unit['id']}{letter}"
                d.node(module_node, "pv_module", module=module_refs.get(module_id))
                d.cable(f"{module_node}.NEG", f"{unit['id']}.{letter}")
        number = branch["id"].removeprefix("R")
        taps = [f"J{branch['id']}-{k}" for k in range(1, len(units) + 1)]
        box = f"JB-{number}"
        d.node(box, "ac_junction_box")
        for tap, unit in zip(taps, units, strict=True):
            d.node(tap, "junction")
            d.cable(f"{unit['id']}.AC", f"{tap}.N")
        for left, right in itertools.pairwise(taps):
            d.cable(
                f"{left}.E", f"{right}.W", length_m=branch["spacing_m"], size=branch["trunk_size"]
            )
        d.cable(f"{taps[0]}.W", f"{box}.OUT", size=branch["trunk_size"])
        if combiner is None:
            breaker(branch["ocpd"])
            d.cable(
                f"{box}.IN", f"{branch['ocpd']}.IN", **_circuit_extra(by_from.get(branch["id"]))
            )
            d.cable(f"{branch['ocpd']}.OUT", f"{panel_id}.LEFT")
        else:
            d.cable(
                f"{box}.IN", f"{branch['ocpd']}.IN", **_circuit_extra(by_from.get(branch["id"]))
            )
    if combiner is None:
        return
    # The combiner: branch breakers on a chain of junctions (the bus), the SPD on the first one,
    # the main breaker outside towards the load centre.
    spds = {s["id"]: s for s in (spec.get("ac_bos") or {}).get("spds", [])}
    children: list[dict[str, Any]] = []
    bus = [f"BUS-{k}" for k in range(1, len(branches) + 1)]
    for branch in branches:
        entry = ocpds.get(branch["ocpd"], {"id": branch["ocpd"]})
        child = {"id": branch["ocpd"], "type": "ac_breaker"}
        d.breakers.add(branch["ocpd"])
        values = _values(entry, ("id", "role", "backfed", "at"))
        if values:
            child["values"] = values
        children.append(child)
    children += [{"id": junction, "type": "junction"} for junction in bus]
    spd_id = combiner.get("spd")
    if spd_id:
        children.append(
            {"id": spd_id, "type": "ac_spd", "values": _values(spds.get(spd_id, {}), ("id", "at"))}
        )
    d.nodes.append({"id": combiner["id"], "type": "ac_combiner", "children": children})
    for junction, branch in zip(bus, branches, strict=True):
        d.cable(f"{branch['ocpd']}.OUT", f"{junction}.N")
    for left, right in itertools.pairwise(bus):
        d.cable(f"{left}.E", f"{right}.W")
    if spd_id:
        d.cable(f"{bus[0]}.S", f"{spd_id}.L")
    main = combiner["main_ocpd"]
    breaker(main)
    d.cable(f"{bus[-1]}.E", f"{main}.IN", **_circuit_extra(by_from.get(combiner["id"])))
    d.cable(f"{main}.OUT", f"{panel_id}.LEFT")


__all__ = ["DRAWN_SECTIONS", "GraphExport", "spec_to_graph"]
