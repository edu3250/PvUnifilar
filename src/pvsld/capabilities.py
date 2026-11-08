"""What the engine calculates today: the capability matrix (ENG-003, Stage 3.8.5).

One versioned document (``pvsld.capabilities/1``) lists each topology feature with its status:
``supported`` (the engine calculates and draws it) or ``pending`` (planned). The editor reads it to
warn before a user draws something the engine cannot calculate yet, and the draft evaluation
(:mod:`pvsld.core.draft`) takes its limits from here, so there is one source of truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

FORMAT = "pvsld.capabilities/1"

MAX_INVERTERS = 1
MAX_STRINGS = 2
SUPPORTED_INVERTER_TYPES = frozenset({"string", "hybrid", "micro"})
MAX_MICRO_MODELS = 1
INVERTER_DC_PORTS = ("A", "B")
SUPPORTED_SYSTEMS = ("1F-2H", "2F-3H")

Status = Literal["supported", "pending"]


@dataclass(frozen=True)
class Capability:
    """One feature of a design and whether the engine covers it."""

    id: str
    title_es: str
    status: Status
    detail_es: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "inverters.count",
        "Número de inversores",
        "supported",
        f"Hasta {MAX_INVERTERS} inversor por diseño; varios inversores están pendientes.",
    ),
    Capability(
        "inverters.type",
        "Tipo de inversor",
        "supported",
        "Inversores de cadena, híbridos y microinversores; centrales y con optimizadores "
        "por cadena están pendientes.",
    ),
    Capability(
        "inverters.qty",
        "Cantidad por modelo",
        "supported",
        "Un equipo por inversor dibujado (qty 1).",
    ),
    Capability(
        "strings.count",
        "Número de cadenas",
        "supported",
        f"Hasta {MAX_STRINGS} cadenas; más cadenas están pendientes.",
    ),
    Capability(
        "strings.mppt",
        "Entradas MPPT",
        "supported",
        f"Cada cadena llega a la entrada {' o '.join(INVERTER_DC_PORTS)} del inversor.",
    ),
    Capability(
        "dc_box",
        "Caja de protecciones CD",
        "supported",
        "Fusible gPV (o ITM de CD) por cadena, DPS de CD y seccionador, armada por el motor.",
    ),
    Capability(
        "dc_box.per_string",
        "Caja de protecciones CD por cadena",
        "supported",
        "Cada cadena con su fusible, su seccionador de 2 polos y su DPS (solo se comparte la "
        "tierra): caja, dibujo del editor y revisiones.",
    ),
    Capability(
        "dc_box.combined",
        "Caja 2 entradas → 1 salida",
        "pending",
        "Dos cadenas unidas en paralelo después de sus fusibles hacia una sola entrada MPPT "
        "(ENG-016): requiere inversores con entradas de más corriente (comerciales, trifásicos).",
    ),
    Capability(
        "drawing.dc_box_per_string",
        "Plano de la caja por cadena",
        "pending",
        "La plantilla del plano (plugin) dibuja solo la caja compartida.",
    ),
    Capability(
        "microinverters",
        "Microinversores",
        "supported",
        f"Ramas de CA de microinversores ({MAX_MICRO_MODELS} modelo por diseño, sin límite de "
        "ramas), un ITM por rama en el centro de carga; revisiones MIC-001 a MIC-014 y caída de "
        "tensión del troncal.",
    ),
    Capability(
        "microinverters.ac_combiner",
        "Caja combinadora de CA",
        "pending",
        "ITM por rama, DPS de CA e ITM general en una caja antes del centro de carga.",
    ),
    Capability(
        "microinverters.mixed",
        "Microinversores con inversor de cadena",
        "pending",
        "Un mismo diseño con microinversores y un inversor de cadena.",
    ),
    Capability(
        "drawing.microinverters",
        "Plano con microinversores",
        "pending",
        "La plantilla del plano (plugin) dibuja solo inversores de cadena.",
    ),
    Capability(
        "utility.system",
        "Servicio",
        "supported",
        f"Monofásico en baja tensión ({', '.join(SUPPORTED_SYSTEMS)}); trifásico pendiente.",
    ),
    Capability(
        "utility.voltage_level",
        "Media tensión",
        "pending",
        "Servicios en media tensión, transformador y su protección.",
    ),
    Capability(
        "storage",
        "Almacenamiento",
        "pending",
        "Baterías acopladas en CD o en CA.",
    ),
    Capability(
        "conductors.material",
        "Material de conductores",
        "supported",
        "Cobre; aluminio pendiente.",
    ),
    Capability(
        "optimizers",
        "Optimizadores",
        "pending",
        "Límites de tensión y corriente de optimizadores por módulo (STR-009).",
    ),
)


def capabilities() -> dict[str, Any]:
    """The capability matrix as plain data."""
    return {
        "format": FORMAT,
        "limits": {
            "max_inverters": MAX_INVERTERS,
            "max_micro_models": MAX_MICRO_MODELS,
            "max_strings": MAX_STRINGS,
            "inverter_types": sorted(SUPPORTED_INVERTER_TYPES),
            "inverter_dc_ports": list(INVERTER_DC_PORTS),
            "utility_systems": list(SUPPORTED_SYSTEMS),
        },
        "capabilities": [c.to_dict() for c in CAPABILITIES],
    }


__all__ = [
    "CAPABILITIES",
    "FORMAT",
    "INVERTER_DC_PORTS",
    "MAX_INVERTERS",
    "MAX_MICRO_MODELS",
    "MAX_STRINGS",
    "SUPPORTED_INVERTER_TYPES",
    "SUPPORTED_SYSTEMS",
    "Capability",
    "capabilities",
]
