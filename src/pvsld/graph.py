"""From the editor's drawing to the engine's specification (ENG-003, Stage 3.8.5).

The editor stores a drawing as a graph (``pvsld.graph/1``): **nodes** placed from the palette
(``type`` is a palette type id, ``values`` its spec values) and **cables** from port to port
(``"S1.OUT"`` -> ``"CX-S1.IN"``). The DC protection box is a **group node** (``type: dc_box``)
whose ``children`` are the parts :func:`pvsld.sizing.dcbox.dc_box` assembles; the user places it
whole and may replace parts inside (owner decision 2026-11-03).

:func:`graph_to_spec` turns the graph plus the project template (everything that is not drawn:
project, utility, grounding, storage, title block, layout, and the AC service details) into a
specification, deducing from the cables what the specification otherwise states by hand:

* the inverter and MPPT of each string: the cable path from the string reaches ``INV1.A``;
* the string circuits ``C-S<n>`` and the inverter-output circuit ``C-INV`` with their lengths
  (the sum of the cable lengths on the path);
* the DC box parts (``FUS-``/``DCB-``/``DCD-CD1``/the SPD), each checked to sit on its string;
* the AC chain: the breaker the inverter feeds is ITM-1, the load centre it reaches is the point
  of connection, a breaker between the load centre and the meter or grid is the service main.

**Values not drawn.** Lengths take the engine's defaults (25 m DC, 30 m AC, 25 mm rooftop
clearance: owner decision 2026-11-03), and conductor sizes, raceways, ITM-1 and box parts take the
engine's selection; each such value is listed in :attr:`GraphResult.filled` so the editor can show
it. Nothing else is invented: a missing value stays missing and :func:`pvsld.core.draft.
evaluate_draft` reports it.

**No node is dropped.** Every node ends in :attr:`GraphResult.mapped` (the spec path it became) or
in :attr:`GraphResult.issues` (why not). What the engine does not cover yet (a second inverter,
a third string) is still written to the specification, and the draft evaluation marks it
``unsupported`` from :mod:`pvsld.capabilities`.
"""

from __future__ import annotations

import copy
import re
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pvsld.catalogue import ComponentRegistry, UnknownComponentError
from pvsld.catalogue import Inverter as CatalogueInverter
from pvsld.catalogue import PVModule as CatalogueModule
from pvsld.core import calc
from pvsld.core.model import SCHEMA_VERSION, Site, Standards, Utility
from pvsld.core.tables import get_tables
from pvsld.resources import data_path
from pvsld.sizing import adapters, bos
from pvsld.sizing.engine import AC_BREAKER_VOLTAGE_V
from pvsld.sizing.models import SUPPORTED_SYSTEMS, Routing

FORMAT = "pvsld.graph/1"
GROUP_TYPE = "dc_box"
BOX_CHILD_TYPES = frozenset(
    {"pv_connector", "terminal", "dc_fuse_holder", "dc_breaker", "dc_spd", "dc_switch"}
)
WIRING_TYPES = frozenset({"junction", "terminal", "pv_connector"})
DEFAULTS = Routing()
"""The engine's defaults for what the drawing does not give (25 m DC, 30 m AC, 25 mm,
20 deg tilt, 180 deg azimuth)."""


# --- The drawing ----------------------------------------------------------------------------


class GraphNode(BaseModel):
    """One placed symbol: a palette type, its spec values, and (for the DC box) its parts."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: str = Field(min_length=1, description="Palette type id, or 'dc_box' for the group")
    values: dict[str, Any] = Field(default_factory=dict)
    children: list[GraphNode] = Field(default_factory=list)


class GraphCable(BaseModel):
    """A conductor between two ports, ``<node id>.<port>``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", pattern=r"^[^.]+\.[^.]+$")
    to: str = Field(pattern=r"^[^.]+\.[^.]+$")
    length_m: float | None = Field(default=None, gt=0, lt=1000)
    ambient_c: float | None = None
    size: str | None = Field(default=None, description="Conductor size, e.g. '10 AWG'")
    raceway_trade_size_mm: float | None = Field(
        default=None,
        gt=0,
        description="EMT designation (mm) of the raceway the cable runs in; default: the engine's",
    )


class Graph(BaseModel):
    """The editor's drawing (``pvsld.graph/1``)."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["pvsld.graph/1"] = FORMAT
    nodes: list[GraphNode] = Field(default_factory=list)
    cables: list[GraphCable] = Field(default_factory=list)


# --- The result -----------------------------------------------------------------------------

IssueCode = Literal[
    "invalid_graph",
    "duplicate_id",
    "unknown_type",
    "unknown_port",
    "unknown_node",
    "disconnected",
    "topology",
    "unknown_component",
    "missing_value",
    "conflict",
]


@dataclass(frozen=True)
class GraphIssue:
    """Why a node or a cable did not map, in Spanish, with the ids it concerns."""

    code: IssueCode
    message_es: str
    node_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message_es": self.message_es, "node_ids": list(self.node_ids)}


@dataclass(frozen=True)
class FilledValue:
    """A value the drawing did not give and the engine put in."""

    path: str
    value: Any
    reason_es: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "value": self.value, "reason_es": self.reason_es}


@dataclass(frozen=True)
class CircuitCables:
    """The cables of the drawing that make up one circuit of the specification."""

    id: str
    kind: str
    from_: str
    to: str
    cables: tuple[int, ...]
    """Indices in ``graph.cables``, in the order of the path (an SPD tap is not on it)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "from": self.from_,
            "to": self.to,
            "cables": list(self.cables),
        }


@dataclass(frozen=True)
class GraphResult:
    """What :func:`graph_to_spec` returns; ``to_dict`` gives plain JSON types."""

    spec: dict[str, Any]
    issues: tuple[GraphIssue, ...] = ()
    filled: tuple[FilledValue, ...] = ()
    mapped: dict[str, str] = field(default_factory=dict)
    """Node id -> the spec path it became (``wiring`` for junctions, terminals, connectors)."""
    circuits: tuple[CircuitCables, ...] = ()
    """Which cables of the drawing make up each circuit (C-S<n>, C-INV)."""

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "spec": self.spec,
            "issues": [i.to_dict() for i in self.issues],
            "filled": [f.to_dict() for f in self.filled],
            "mapped": dict(self.mapped),
            "circuits": [c.to_dict() for c in self.circuits],
        }


# --- Ports and connectivity -----------------------------------------------------------------

_SWITCH_PORT = re.compile(r"^(IN|OUT)(\d+)$")
_PORTS: dict[str, frozenset[str]] = {
    "pv_string": frozenset({"OUT"}),
    "inverter": frozenset({"A", "B", "AC", "PE"}),
    "dc_fuse_holder": frozenset({"IN", "OUT"}),
    "dc_breaker": frozenset({"IN", "OUT"}),
    "dc_spd": frozenset({"L", "L1", "L2", "PE"}),
    "ac_breaker": frozenset({"IN", "OUT"}),
    "interconnection_point": frozenset({"IN", "OUT"}),
    "load_centre": frozenset({"LEFT", "RIGHT", "PE"}),
    "meter": frozenset({"IN", "OUT"}),
    "grid": frozenset({"IN"}),
    "ground_electrode": frozenset({"PE"}),
    "junction": frozenset({"W", "E", "N", "S"}),
    "terminal": frozenset({"L", "R"}),
    "pv_connector": frozenset({"IN", "OUT"}),
}
_THROUGH: dict[str, tuple[tuple[str, ...], ...]] = {
    "dc_fuse_holder": (("IN", "OUT"),),
    "dc_breaker": (("IN", "OUT"),),
    "ac_breaker": (("IN", "OUT"),),
    "interconnection_point": (("IN", "OUT"),),
    "meter": (("IN", "OUT"),),
    "terminal": (("L", "R"),),
    "pv_connector": (("IN", "OUT"),),
    "junction": (("W", "E", "N", "S"),),
    "load_centre": (("LEFT", "RIGHT"),),
}
"""Ports a current passes through inside a node (the switch pairs INk-OUTk; SPDs, inverters,
strings, the grid and electrodes end a path)."""


def _port_ok(node_type: str, port: str) -> bool:
    if node_type == "dc_switch":
        return _SWITCH_PORT.match(port) is not None
    return port in _PORTS.get(node_type, frozenset())


def _inside(node_type: str, port: str, ports_used: Iterable[str]) -> list[str]:
    """The other ports of the same node that ``port`` reaches inside it."""
    if node_type == "dc_switch":
        match = _SWITCH_PORT.match(port)
        if match is None:
            return []
        other = "OUT" if match.group(1) == "IN" else "IN"
        return [f"{other}{match.group(2)}"]
    for group in _THROUGH.get(node_type, ()):
        if port in group:
            return [p for p in group if p != port and (node_type != "junction" or p in ports_used)]
    return []


@dataclass
class _Net:
    nodes: dict[str, GraphNode]
    adjacency: dict[str, list[tuple[str, GraphCable | None]]]

    def node_of(self, port: str) -> GraphNode:
        return self.nodes[port.split(".", 1)[0]]

    def trace(self, start: str) -> tuple[list[str], dict[str, tuple[str, GraphCable | None]]]:
        """Ports reached from ``start`` (breadth first) and the step that reached each."""
        seen = {start}
        parent: dict[str, tuple[str, GraphCable | None]] = {}
        queue = deque([start])
        order = []
        while queue:
            port = queue.popleft()
            order.append(port)
            for nxt, cable in self.adjacency.get(port, []):
                if nxt not in seen:
                    seen.add(nxt)
                    parent[nxt] = (port, cable)
                    queue.append(nxt)
        return order, parent


def _path(parent: Mapping[str, tuple[str, GraphCable | None]], end: str) -> list[GraphCable]:
    cables: list[GraphCable] = []
    port = end
    while port in parent:
        port, cable = parent[port]
        if cable is not None:
            cables.append(cable)
    return list(reversed(cables))


# --- The mapping ----------------------------------------------------------------------------


class _Builder:
    def __init__(self, graph: Graph, template: Mapping[str, Any], registry: ComponentRegistry):
        self.graph = graph
        self.template = template
        self.registry = registry
        self.issues: list[GraphIssue] = []
        self.filled: list[FilledValue] = []
        self.mapped: dict[str, str] = {}
        self.nodes: dict[str, GraphNode] = {}
        self.group_of: dict[str, str] = {}
        self.net_cache: _Net | None = None
        self.cable_index = {id(cable): index for index, cable in enumerate(graph.cables)}
        self.circuit_cables: list[CircuitCables] = []

    def indices(self, cables: Iterable[GraphCable]) -> tuple[int, ...]:
        return tuple(self.cable_index[id(c)] for c in cables)

    # recording
    def issue(self, code: IssueCode, message: str, *ids: str) -> None:
        self.issues.append(GraphIssue(code, message, tuple(ids)))

    def fill(self, path: str, value: Any, reason: str) -> Any:
        self.filled.append(FilledValue(path, value, reason))
        return value

    # structure
    def collect(self) -> None:
        def add(node: GraphNode, group: str | None) -> None:
            if node.id in self.nodes:
                self.issue("duplicate_id", f"El id «{node.id}» se repite en el dibujo.", node.id)
                return
            self.nodes[node.id] = node
            if group is not None:
                self.group_of[node.id] = group
            if node.type == GROUP_TYPE:
                self.mapped[node.id] = "dc_bos"
                for child in node.children:
                    if child.type not in BOX_CHILD_TYPES:
                        self.issue(
                            "topology",
                            f"{child.id} ({child.type}) no va dentro de la caja de protecciones "
                            "CD.",
                            child.id,
                            node.id,
                        )
                    add(child, node.id)
            elif node.children:
                self.issue(
                    "topology", f"{node.id} no es un grupo y no puede tener piezas.", node.id
                )
            elif node.type not in _PORTS and node.type != "dc_switch":
                self.issue(
                    "unknown_type",
                    f"{node.id}: el tipo «{node.type}» no está en la paleta del motor.",
                    node.id,
                )

        for node in self.graph.nodes:
            add(node, None)

    def net(self) -> _Net:
        adjacency: dict[str, list[tuple[str, GraphCable | None]]] = {}
        used: dict[str, set[str]] = {}
        good: list[GraphCable] = []
        for cable in self.graph.cables:
            ends = (cable.from_, cable.to)
            bad = False
            for end in ends:
                node_id, port = end.split(".", 1)
                node = self.nodes.get(node_id)
                if node is None:
                    self.issue(
                        "unknown_node", f"El cable llega a «{node_id}», que no está.", node_id
                    )
                    bad = True
                elif node.type == GROUP_TYPE:
                    self.issue(
                        "unknown_port",
                        f"{end}: los cables se conectan a las piezas de la caja, no a la caja.",
                        node_id,
                    )
                    bad = True
                elif node.type in _PORTS or node.type == "dc_switch":
                    if not _port_ok(node.type, port):
                        self.issue(
                            "unknown_port",
                            f"{end}: {node_id} ({node.type}) no tiene el puerto «{port}».",
                            node_id,
                        )
                        bad = True
            if bad:
                continue
            good.append(cable)
            for end in ends:
                node_id, port = end.split(".", 1)
                used.setdefault(node_id, set()).add(port)
        for cable in good:
            adjacency.setdefault(cable.from_, []).append((cable.to, cable))
            adjacency.setdefault(cable.to, []).append((cable.from_, cable))
        for node_id, ports in used.items():
            node = self.nodes[node_id]
            for port in ports:
                for other in _inside(node.type, port, ports):
                    adjacency.setdefault(f"{node_id}.{port}", []).append(
                        (f"{node_id}.{other}", None)
                    )
        for node_id, node in self.nodes.items():
            if node.type != GROUP_TYPE and node_id not in used:
                self.issue("disconnected", f"{node_id} no tiene ningún cable.", node_id)
        return _Net(self.nodes, adjacency)

    # catalogue
    def component(self, node: GraphNode, key: str, kind: type[Any], label: str) -> Any:
        ref = node.values.get(key)
        if not ref:
            self.issue(
                "missing_value", f"{node.id}: falta el {label} del catálogo («{key}»).", node.id
            )
            return None
        try:
            found = self.registry.get(str(ref))
        except UnknownComponentError:
            self.issue("unknown_component", f"{node.id}: «{ref}» no está en el catálogo.", node.id)
            return None
        if not isinstance(found, kind):
            self.issue("unknown_component", f"{node.id}: «{ref}» no es un {label}.", node.id)
            return None
        return found


def _template_item(section: Iterable[Mapping[str, Any]] | None, item_id: str) -> dict[str, Any]:
    for item in section or []:
        if isinstance(item, Mapping) and item.get("id") == item_id:
            return dict(item)
    return {}


def graph_to_spec(
    graph: Graph | Mapping[str, Any],
    template: Mapping[str, Any],
    *,
    registry: ComponentRegistry | None = None,
) -> GraphResult:
    """Build the specification of ``graph`` on ``template`` (see the module docstring)."""
    if not isinstance(graph, Graph):
        try:
            graph = Graph.model_validate(graph)
        except ValidationError as error:
            return GraphResult(
                spec={},
                issues=(
                    GraphIssue(
                        "invalid_graph",
                        f"El dibujo no tiene el formato {FORMAT}: {error.error_count()} error(es); "
                        f"{error.errors()[0]['msg']} en {error.errors()[0]['loc']}.",
                    ),
                ),
            )
    registry = (
        registry
        if registry is not None
        else ComponentRegistry.load(data_path("datasheets/records"))
    )
    b = _Builder(graph, template, registry)
    b.collect()
    net = b.net()
    b.net_cache = net
    spec: dict[str, Any] = {
        key: copy.deepcopy(value)
        for key, value in template.items()
        if key not in ("modules", "inverters", "strings", "circuits", "dc_bos")
    }
    spec["schema_version"] = SCHEMA_VERSION
    spec.setdefault("standards", {"nom_edition": "NOM-001-SEDE-2012", "rulepack": "mx-gd-2026.10"})
    try:
        utility: Utility | None = Utility.model_validate(spec.get("utility"))
    except ValidationError:
        utility = None
        b.issue(
            "missing_value", "La plantilla del proyecto no trae un servicio («utility») válido."
        )
    try:
        site: Site | None = Site.model_validate((spec.get("project") or {}).get("site"))
    except (ValidationError, AttributeError):
        site = None
        b.issue("missing_value", "La plantilla del proyecto no trae el sitio («project.site»).")
    try:
        standards = Standards.model_validate(spec["standards"])
    except ValidationError:
        standards = Standards(nom_edition="NOM-001-SEDE-2012", rulepack="mx-gd-2026.10")
    tables = get_tables(standards.nom_edition)
    phases = SUPPORTED_SYSTEMS.get(utility.system, 1) if utility else 1

    by_type: dict[str, list[GraphNode]] = {}
    for node in b.nodes.values():
        by_type.setdefault(node.type, []).append(node)
    for node_type in WIRING_TYPES:
        for node in by_type.get(node_type, []):
            b.mapped.setdefault(node.id, "wiring")

    # Inverters
    inverters: list[dict[str, Any]] = []
    catalogue_inverters: dict[str, CatalogueInverter] = {}
    for index, node in enumerate(by_type.get("inverter", [])):
        found = b.component(node, "model", CatalogueInverter, "inversor")
        if found is None or utility is None:
            continue
        spec_inverter = adapters.to_spec_inverter(
            found,
            ac_voltage_v=utility.nominal_voltage_v,
            phases=phases,
            optimizers_on_all_modules=bool(node.values.get("optimizers_on_all_modules", False)),
            ocpd_max_a=bos.ac_ocpd_rating_a(found.max_ac_output_current_a, tables),
        )
        inverters.append({**spec_inverter.model_dump(mode="json"), "id": node.id})
        catalogue_inverters[node.id] = found
        b.mapped[node.id] = f"inverters[{index}]"
    spec["inverters"] = inverters

    # Strings and modules
    modules: dict[str, tuple[str, CatalogueModule]] = {}
    strings: list[dict[str, Any]] = []
    string_paths: dict[str, list[GraphCable]] = {}
    on_path: dict[str, set[str]] = {}
    for node in by_type.get("pv_string", []):
        found = b.component(node, "module", CatalogueModule, "módulo FV")
        n_series = node.values.get("n_series")
        if n_series is None:
            b.issue("missing_value", f"{node.id}: falta el número de módulos en serie.", node.id)
        order, parent = net.trace(f"{node.id}.OUT")
        reached = [
            p
            for p in order
            if net.node_of(p).type == "inverter" and p.split(".", 1)[1] in ("A", "B")
        ]
        on_path[node.id] = {
            p.split(".", 1)[0] for p in order if net.node_of(p).type != "inverter"
        } - {node.id}
        targets = sorted(set(reached))
        if not targets:
            b.issue(
                "topology", f"{node.id} no está conectada a ningún MPPT de un inversor.", node.id
            )
            continue
        if len(targets) > 1:
            b.issue(
                "topology",
                f"{node.id} llega a varias entradas a la vez ({', '.join(targets)}).",
                node.id,
            )
            continue
        inverter_id, mppt = targets[0].split(".", 1)
        if found is None or n_series is None:
            continue
        if found.component_id not in modules:
            module_id = f"MOD{len(modules) + 1}"
            spec_module = adapters.to_spec_module(found)
            modules[found.component_id] = (module_id, found)
            spec.setdefault("modules", []).append(
                {**spec_module.model_dump(mode="json"), "id": module_id}
            )
        module_id = modules[found.component_id][0]
        string = {
            "id": node.id,
            "module": module_id,
            "n_series": n_series,
            "inverter": inverter_id,
            "mppt": mppt,
            "tilt_deg": node.values.get("tilt_deg", DEFAULTS.tilt_deg),
            "azimuth_deg": node.values.get("azimuth_deg", DEFAULTS.azimuth_deg),
        }
        for key, default in (
            ("tilt_deg", DEFAULTS.tilt_deg),
            ("azimuth_deg", DEFAULTS.azimuth_deg),
        ):
            if key not in node.values:
                b.fill(f"strings[{len(strings)}].{key}", default, "Valor por omisión del motor.")
        b.mapped[node.id] = f"strings[{len(strings)}]"
        strings.append(string)
        string_paths[node.id] = _path(parent, targets[0])
    spec["strings"] = strings
    spec.setdefault("modules", [])

    # DC box parts and SPDs
    disconnects: list[dict[str, Any]] = []
    spds: list[dict[str, Any]] = []
    first_inverter = inverters[0]["id"] if inverters else "INV1"
    for inverter in inverters:
        if inverter.get("dc_switch_integrated"):
            found = catalogue_inverters[inverter["id"]]
            disconnects.append(
                {
                    "id": "DCD-1",
                    "integrated_in": inverter["id"],
                    "poles": 2,
                    "ue_v": inverter["vdc_max_v"],
                    "ie_a": found.max_input_current_per_mppt_a,
                },
            )
    for node_id, node in b.nodes.items():
        if node.type in ("dc_fuse_holder", "dc_breaker"):
            owners = [s for s, devices in on_path.items() if node_id in devices]
            expected = node_id.split("-", 1)[1] if "-" in node_id else None
            if not owners:
                b.issue("topology", f"{node_id} no está en el camino de ninguna cadena.", node_id)
            elif owners != [expected]:
                b.issue(
                    "topology",
                    f"{node_id} está en el camino de {', '.join(owners)}; su id indica la cadena "
                    f"{expected}.",
                    node_id,
                )
            disconnects.append(_device(node, b, len(disconnects)))
        elif node.type == "dc_switch":
            disconnects.append(_device(node, b, len(disconnects)))
        elif node.type == "dc_spd":
            # "at" comes from the connections (a derived field of the palette, ENG-013).
            entry = {**node.values, "id": node_id, "at": f"{first_inverter}.dc"}
            b.mapped[node_id] = f"dc_bos.spds[{len(spds)}]"
            spds.append(entry)
    spec["dc_bos"] = {"combiners": [], "disconnects": disconnects, "spds": spds}

    # AC side
    _ac_side(b, net, spec, utility, phases, tables)

    # Circuits
    _circuits(b, spec, string_paths, catalogue_inverters, site, standards, phases, tables)

    # Ground electrodes
    electrodes = by_type.get("ground_electrode", [])
    if electrodes:
        grounding = dict(spec.get("grounding") or {})
        grounding["electrodes"] = [
            {k: v for k, v in e.values.items() if k in ("type", "length_m", "qty")}
            for e in electrodes
        ]
        spec["grounding"] = grounding
        for index, e in enumerate(electrodes):
            b.mapped[e.id] = f"grounding.electrodes[{index}]"

    for node_id in b.nodes:
        if node_id not in b.mapped and not any(node_id in i.node_ids for i in b.issues):
            b.issue("topology", f"{node_id} no se pudo ubicar en la especificación.", node_id)
    return GraphResult(
        spec, tuple(b.issues), tuple(b.filled), dict(b.mapped), tuple(b.circuit_cables)
    )


def _device(node: GraphNode, b: _Builder, index: int) -> dict[str, Any]:
    b.mapped[node.id] = f"dc_bos.disconnects[{index}]"
    return {**node.values, "id": node.id, "integrated_in": None}


def _ac_side(
    b: _Builder,
    net: _Net,
    spec: dict[str, Any],
    utility: Utility | None,
    phases: int,
    tables: Any,
) -> None:
    template_ac = b.template.get("ac_bos") or {}
    ac: dict[str, Any] = copy.deepcopy(dict(template_ac))
    ocpds = [dict(o) for o in ac.get("ocpds") or []]
    panels = [dict(p) for p in ac.get("panels") or []]
    mains = [dict(m) for m in ac.get("main_breakers") or []]
    meters = [dict(m) for m in ac.get("meters") or []]
    poc = dict(ac.get("point_of_connection") or {})

    def upsert(items: list[dict[str, Any]], node: GraphNode) -> int:
        base = _template_item(items, node.id)
        merged = {**base, **node.values, "id": node.id}
        for index, item in enumerate(items):
            if item.get("id") == node.id:
                items[index] = merged
                return index
        items.append(merged)
        return len(items) - 1

    breakers = {n.id: n for n in b.nodes.values() if n.type == "ac_breaker"}
    inverter_breakers: list[str] = []
    for node in (n for n in b.nodes.values() if n.type == "inverter"):
        order, _parent = net.trace(f"{node.id}.AC")
        hit = [p.split(".", 1)[0] for p in order if p.split(".", 1)[0] in breakers]
        if not hit:
            b.issue(
                "topology",
                f"La salida de CA de {node.id} no llega a ningún interruptor (ITM).",
                node.id,
            )
            continue
        inverter_breakers.append(hit[0])
        inverter = next((i for i in spec["inverters"] if i["id"] == node.id), None)
        itm = breakers[hit[0]]
        index = upsert(ocpds, itm)
        entry = ocpds[index]
        # role, backfed and at come from the connections (derived fields, ENG-013).
        entry["role"] = "I1"
        entry["backfed"] = True
        entry.setdefault("poles", phases)
        entry.setdefault("voltage_v", AC_BREAKER_VOLTAGE_V)
        if inverter is not None and "rating_a" not in entry:
            rating = bos.ac_ocpd_rating_a(inverter["iac_max_a"], tables)
            choice, _found = bos.select_ac_breaker(
                position=itm.id,
                rating_a=rating,
                poles=entry["poles"],
                voltage_v=AC_BREAKER_VOLTAGE_V,
                fault_ka=utility.available_fault_current_ka if utility else 0.0,
                breakers=b.registry.ac_breakers(),
            )
            entry["rating_a"] = b.fill(
                f"ac_bos.ocpds[{index}].rating_a",
                choice.rating_a if choice else rating,
                "ITM de la salida del inversor: 1.25 × Iac máx, siguiente valor normalizado.",
            )
            if choice is not None and "model" not in entry:
                entry["model"] = b.fill(
                    f"ac_bos.ocpds[{index}].model", choice.device_id, "Interruptor del catálogo."
                )
            if "kaic_ka" not in entry:
                entry["kaic_ka"] = b.fill(
                    f"ac_bos.ocpds[{index}].kaic_ka",
                    choice.interrupting_ka
                    if choice
                    else (utility.available_fault_current_ka if utility else None),
                    "Capacidad interruptiva del interruptor del catálogo.",
                )
        b.mapped[itm.id] = f"ac_bos.ocpds[{index}]"
        # The load centre it feeds is the point of connection.
        order, _parent = net.trace(f"{itm.id}.OUT")
        panel_nodes = [
            b.nodes[p.split(".", 1)[0]] for p in order if net.node_of(p).type == "load_centre"
        ]
        if not panel_nodes:
            b.issue("topology", f"{itm.id} no llega a ningún centro de carga.", itm.id)
            continue
        panel = panel_nodes[0]
        entry["at"] = panel.id
        poc.update({"panel": panel.id, "breaker": itm.id})
        poc.setdefault("id", "PI-1")
        poc.setdefault("type", "load_side")

    for node in (n for n in b.nodes.values() if n.type == "load_centre"):
        index = upsert(panels, node)
        if utility is not None:
            panels[index].setdefault("system", utility.system)
        b.mapped[node.id] = f"ac_bos.panels[{index}]"
        # A breaker between the load centre and the meter or the grid is the service main.
        for port in ("LEFT", "RIGHT"):
            order, _parent = net.trace(f"{node.id}.{port}")
            for p in order:
                other = p.split(".", 1)[0]
                if other in breakers and other not in inverter_breakers and other != node.id:
                    main_index = upsert(mains, breakers[other])
                    mains[main_index]["role"] = "I2"
                    panels[index]["main_ocpd"] = other
                    b.mapped[other] = f"ac_bos.main_breakers[{main_index}]"
    for node in (n for n in b.nodes.values() if n.type == "meter"):
        index = upsert(meters, node)
        b.mapped[node.id] = f"ac_bos.meters[{index}]"
    for node in (n for n in b.nodes.values() if n.type == "interconnection_point"):
        poc["id"] = node.id
        b.mapped[node.id] = "ac_bos.point_of_connection"
    for node in (n for n in b.nodes.values() if n.type == "grid"):
        b.mapped[node.id] = "utility"
    for breaker_id in breakers:
        if breaker_id not in b.mapped:
            b.issue(
                "topology",
                f"{breaker_id}: no está entre el inversor y el centro de carga ni entre el centro "
                "de carga y el medidor o la red.",
                breaker_id,
            )
    ac.update(
        ocpds=ocpds,
        panels=panels,
        main_breakers=mains,
        meters=meters,
        point_of_connection=poc,
    )
    ac.setdefault("disconnects", [])
    ac.setdefault("spds", [])
    spec["ac_bos"] = ac


def _circuits(
    b: _Builder,
    spec: dict[str, Any],
    string_paths: Mapping[str, list[GraphCable]],
    inverters: Mapping[str, CatalogueInverter],
    site: Site | None,
    standards: Standards,
    phases: int,
    tables: Any,
) -> None:
    circuits: list[dict[str, Any]] = []
    strings = spec["strings"]
    modules = {m["id"]: m for m in spec["modules"]}
    t_amb = site.t_amb_max_c if site else None
    # The strings share one DC raceway (owned by C-S1): a size drawn on any of their cables.
    drawn = sorted(
        {
            c.raceway_trade_size_mm
            for string in strings
            for c in string_paths[string["id"]]
            if c.raceway_trade_size_mm is not None
        }
    )
    if len(drawn) > 1:
        b.issue(
            "conflict",
            "Las cadenas comparten la tubería de CD, pero sus cables traen tamaños distintos ("
            + ", ".join(f"{size:g} mm" for size in drawn)
            + f"); se usa el mayor, {drawn[-1]:g} mm.",
            *(s["id"] for s in strings),
        )
    dc_trade_size = drawn[-1] if drawn else None
    for index, string in enumerate(strings):
        path = string_paths[string["id"]]
        cid = f"C-S{index + 1}"
        lengths = [c.length_m for c in path if c.length_m is not None]
        length = (
            sum(lengths)
            if lengths
            else b.fill(
                f"circuits[{len(circuits)}].length_m",
                DEFAULTS.dc_string_length_m,
                "Longitud por omisión del motor (cadena al inversor).",
            )
        )
        ambient = max((c.ambient_c for c in path if c.ambient_c is not None), default=None)
        module = modules[string["module"]]
        n_strings = len(strings)
        size = next((c.size for c in path if c.size), None)
        if size is None and t_amb is not None:
            choice, _issues = bos.size_conductor(
                circuit_id=cid,
                kind="pv_source",
                i_max_a=calc.ISC_SAFETY_FACTOR * module["isc_a"],
                ocpd_a=None,
                current_carrying=2 * n_strings,
                ambient_c=ambient if ambient is not None else t_amb,
                clearance_mm=DEFAULTS.rooftop_clearance_mm,
                length_m=length,
                drop_pct=lambda s, m=module, n=string["n_series"], run=length: (
                    calc.voltage_drop_dc_pct(s, run, m["imp_a"], n * m["vmp_v"], tables)
                ),
                drop_limit_pct=standards.vd_limits_pct.dc,
                drop_rule="VD-001",
                tables=tables,
            )
            if choice is not None:
                size = b.fill(
                    f"circuits[{len(circuits)}].conductors.size",
                    choice.size,
                    "Calibre más chico que cumple ampacidad y caída de tensión.",
                )
        if size is None:
            b.issue("missing_value", f"{cid}: no se pudo determinar el calibre.", string["id"])
            continue
        cable = bos.pv_cable(size, b.registry.cables())
        circuit: dict[str, Any] = {
            "id": cid,
            "kind": "pv_source",
            "from": string["id"],
            "to": f"{string['inverter']}.{string['mppt']}",
            "conductors": {
                "qty": 2,
                "size": size,
                "material": "Cu",
                "insulation": f"PV {cable.rated_voltage_v / 1000:g} kV" if cable else "PV",
                **({"outer_diameter_mm": cable.outer_diameter_mm} if cable else {}),
            },
            "egc": {"size": size, "type": bos.EGC_TYPE},
            "length_m": length,
        }
        if ambient is not None:
            circuit["ambient_c"] = ambient
        if index == 0:
            circuit["raceway"] = {
                "type": "EMT",
                "rooftop_clearance_mm": DEFAULTS.rooftop_clearance_mm,
                "ccc_count": 2 * n_strings,
            }
            b.fill(
                f"circuits[{len(circuits)}].raceway.rooftop_clearance_mm",
                DEFAULTS.rooftop_clearance_mm,
                "Separación sobre el techo por omisión del motor.",
            )
            area = calc.conductor_area_mm2(cable.outer_diameter_mm) if cable else None
            if dc_trade_size is not None:
                circuit["raceway"]["trade_size_mm"] = dc_trade_size
            raceway, _issues = bos.size_raceway(
                circuit_id=cid,
                conductors=2 * n_strings,
                conductor_area_mm2=area,
                egc_size=size,
                given_trade_size_mm=None,
                insulation=circuit["conductors"]["insulation"],
                tables=tables,
                cable=cable,
            )
            if dc_trade_size is None and raceway.trade_size_mm is not None:
                circuit["raceway"]["trade_size_mm"] = b.fill(
                    f"circuits[{len(circuits)}].raceway.trade_size_mm",
                    raceway.trade_size_mm,
                    "EMT más chico que aloja los conductores (Capítulo 10, Tabla 1).",
                )
        else:
            circuit["raceway"] = {"ref": "C-S1"}
        circuits.append(circuit)
        b.circuit_cables.append(
            CircuitCables(cid, "pv_source", circuit["from"], circuit["to"], b.indices(path))
        )

    ac = spec.get("ac_bos") or {}
    itm_id = (ac.get("point_of_connection") or {}).get("breaker")
    itm = _template_item(ac.get("ocpds"), itm_id) if itm_id else {}
    for inverter in spec["inverters"]:
        if not itm:
            break
        found = inverters[inverter["id"]]
        path = _inverter_path(b, inverter["id"], itm_id)
        lengths = [c.length_m for c in path if c.length_m is not None]
        index = len(circuits)
        length = (
            sum(lengths)
            if lengths
            else b.fill(
                f"circuits[{index}].length_m",
                DEFAULTS.ac_output_length_m,
                "Longitud por omisión del motor (inversor al ITM).",
            )
        )
        ambient = max((c.ambient_c for c in path if c.ambient_c is not None), default=None)
        size = next((c.size for c in path if c.size), None)
        current = found.max_ac_output_current_a
        if size is None and t_amb is not None:
            choice, _issues = bos.size_conductor(
                circuit_id="C-INV",
                kind="inverter_output",
                i_max_a=current,
                ocpd_a=itm.get("rating_a"),
                current_carrying=phases + (1 if phases == 1 else 0),
                ambient_c=ambient if ambient is not None else t_amb,
                clearance_mm=None,
                length_m=length,
                drop_pct=lambda s, run=length, i=inverter, amps=current: calc.voltage_drop_ac_pct(
                    s, run, amps, i["vac_v"], phases, tables
                ),
                drop_limit_pct=standards.vd_limits_pct.ac,
                drop_rule="VD-002",
                tables=tables,
            )
            if choice is not None:
                size = b.fill(
                    f"circuits[{index}].conductors.size",
                    choice.size,
                    "Calibre más chico que cumple ampacidad, protección y caída de tensión.",
                )
        if size is None:
            b.issue("missing_value", "C-INV: no se pudo determinar el calibre.", inverter["id"])
            continue
        circuit = {
            "id": "C-INV",
            "kind": "inverter_output",
            "from": f"{inverter['id']}.ac",
            "to": itm_id,
            "conductors": {
                "qty": phases,
                "size": size,
                "material": "Cu",
                "insulation": bos.AC_INSULATION,
            },
            "neutral": {"size": size},
            "egc": {"size": size, "type": bos.EGC_TYPE},
            "raceway": {"type": "EMT", "ccc_count": phases + (1 if phases == 1 else 0)},
            "length_m": length,
        }
        if ambient is not None:
            circuit["ambient_c"] = ambient
        thhw = calc.insulated_area_mm2(size, bos.AC_INSULATION, None, tables)
        ac_drawn = sorted(
            {c.raceway_trade_size_mm for c in path if c.raceway_trade_size_mm is not None}
        )
        if len(ac_drawn) > 1:
            b.issue(
                "conflict",
                "Los cables de C-INV traen tamaños de tubería distintos ("
                + ", ".join(f"{size:g} mm" for size in ac_drawn)
                + f"); se usa el mayor, {ac_drawn[-1]:g} mm.",
                inverter["id"],
            )
        if ac_drawn:
            circuit["raceway"]["trade_size_mm"] = ac_drawn[-1]
        raceway, _issues = bos.size_raceway(
            circuit_id="C-INV",
            conductors=phases + 1,
            conductor_area_mm2=thhw,
            egc_size=size,
            given_trade_size_mm=None,
            insulation=bos.AC_INSULATION,
            tables=tables,
        )
        if not ac_drawn and raceway.trade_size_mm is not None:
            circuit["raceway"]["trade_size_mm"] = b.fill(
                f"circuits[{index}].raceway.trade_size_mm",
                raceway.trade_size_mm,
                "EMT más chico que aloja los conductores (Capítulo 10, Tabla 1).",
            )
        circuits.append(circuit)
        b.circuit_cables.append(
            CircuitCables("C-INV", "inverter_output", circuit["from"], itm_id, b.indices(path))
        )
    spec["circuits"] = circuits


def _inverter_path(b: _Builder, inverter_id: str, itm_id: str) -> list[GraphCable]:
    if b.net_cache is None:
        return []
    _order, parent = b.net_cache.trace(f"{inverter_id}.AC")
    end = next((f"{itm_id}.{p}" for p in ("IN", "OUT") if f"{itm_id}.{p}" in parent), None)
    return _path(parent, end) if end else []


__all__ = [
    "FORMAT",
    "CircuitCables",
    "FilledValue",
    "Graph",
    "GraphCable",
    "GraphIssue",
    "GraphNode",
    "GraphResult",
    "graph_to_spec",
]
