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

MAX_INVERTERS: int | None = None
"""No limit on string inverters per design (owner 2026-11-13; they share one breaker)."""
MAX_STRINGS = 4
"""Strings per string inverter: one on each MPPT input, A to D (owner 2026-11-14)."""
SUPPORTED_INVERTER_TYPES = frozenset({"string", "hybrid", "micro"})
MAX_MICRO_MODELS = 1
INVERTER_DC_PORTS = ("A", "B", "C", "D")
SUPPORTED_SYSTEMS = ("1F-2H", "2F-3H", "3F-4H")

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
        "Cualquier cantidad de inversores de cadena, de modelos iguales o distintos, con sus "
        "salidas unidas a un solo ITM compartido (1.25 × la suma de sus corrientes). El "
        "dimensionamiento automático y el plano A3 todavía usan un inversor.",
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
        f"Hasta {MAX_STRINGS} cadenas por inversor, una por MPPT (A y B a la izquierda, C y D "
        "arriba); dos cadenas en paralelo en una entrada están pendientes. El dimensionamiento "
        "automático y el plano A3 todavía usan las entradas A y B.",
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
        "supported",
        "La plantilla A3 dibuja la caja por cadena: fusible, DPS y seccionador de 2 polos por "
        "cadena.",
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
        "supported",
        "ITM por rama, DPS de CA e ITM general en una caja antes del centro de carga "
        "(MIC-020, MIC-021); sin caja, un ITM por rama en el centro de carga.",
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
        "supported",
        "Plantilla A3 (a3_plantilla_v1): cada rama en un renglón con sus módulos, troncal y caja "
        "de conexiones, ITM de rama y caja combinadora; hasta 8 microinversores en 3 ramas.",
    ),
    Capability(
        "inverters.breakers",
        "Protección de CA de los inversores",
        "supported",
        "ITM compartido (caja de unión), un ITM por inversor en el centro de carga (cada uno es "
        "I1) o tablero combinador con un ITM por inversor y un ITM general (I1, CMB-001).",
    ),
    Capability(
        "drawing.inverter_breakers",
        "Plano con un ITM por inversor o tablero combinador",
        "pending",
        "La plantilla A3 todavía dibuja solo el ITM compartido.",
    ),
    Capability(
        "service.no_load_centre",
        "Conexión sin centro de carga",
        "supported",
        "Inversor, I1, interruptor principal I2 (punto de interconexión), medidor y red, sin "
        "centro de carga dibujado (esquemas de la RES/142/2017); un solo ITM puede hacer de I1 e "
        "I2 con aviso DIS-005. Solo con inversor de cadena.",
    ),
    Capability(
        "drawing.no_load_centre",
        "Plano sin centro de carga",
        "pending",
        "La plantilla A3 todavía dibuja siempre el centro de carga.",
    ),
    Capability(
        "utility.system",
        "Servicio",
        "supported",
        f"Baja tensión: {', '.join(SUPPORTED_SYSTEMS)} (3F-4H 220/127 V con inversores "
        "trifásicos, o monofásicos entre dos fases con aviso de desbalance, SRV-001). El "
        "dimensionamiento automático y el plano A3 todavía usan servicios monofásicos; "
        "microinversores en 3F-4H pendientes.",
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
            "max_strings": None,
            "max_strings_per_inverter": MAX_STRINGS,
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
