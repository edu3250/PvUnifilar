"""Component catalogue: datasheet facts as validated records (ADR-0004, Stage 3.1).

One YAML record per datasheet family lives in ``datasheets/records/<type>/``. Every power level or
model is its own variant with its own ``component_id``; :class:`ComponentRegistry` expands the
variants into standalone components (:class:`PVModule`, :class:`Inverter`, :class:`DcBreaker`,
:class:`DcFuse`) so the sizing engine and the rule pack never see the family/variant split.

Example::

    registry = ComponentRegistry.load("datasheets/records")
    inverter = registry.get("HUAWEI-SUN2000-6KTL-L1")
    limit_w = inverter.pv_power_limit_w(optimizers_on_all_modules=False)  # STR-007
"""

from pvsld.catalogue.common import Provenance
from pvsld.catalogue.conductors import Cable, CableFamily, CableVariant
from pvsld.catalogue.errors import CatalogueError, UnknownComponentError
from pvsld.catalogue.inverters import (
    BatteryPort,
    Inverter,
    InverterFamily,
    InverterProtection,
    InverterVariant,
)
from pvsld.catalogue.modules import ElectricalPoint, ModuleVariant, PVModule, PVModuleFamily
from pvsld.catalogue.protection import (
    AcBreaker,
    AcBreakerFamily,
    AcBreakerVariant,
    BreakingCapacity,
    DcBreaker,
    DcBreakerVariant,
    DcFuse,
    DcFuseFamily,
    DcFuseVariant,
    DcSpd,
    DcSpdFamily,
    DcSpdVariant,
    DcSwitch,
    DcSwitchFamily,
    DcSwitchVariant,
    PoleOption,
    ProtectionFamily,
    SwitchRating,
    Terminals,
)
from pvsld.catalogue.registry import (
    Component,
    ComponentRegistry,
    Family,
    LoadedRecord,
    load_records,
    read_record,
)

__all__ = [
    "AcBreaker",
    "AcBreakerFamily",
    "AcBreakerVariant",
    "BatteryPort",
    "BreakingCapacity",
    "Cable",
    "CableFamily",
    "CableVariant",
    "CatalogueError",
    "Component",
    "ComponentRegistry",
    "DcBreaker",
    "DcBreakerVariant",
    "DcFuse",
    "DcFuseFamily",
    "DcFuseVariant",
    "DcSpd",
    "DcSpdFamily",
    "DcSpdVariant",
    "DcSwitch",
    "DcSwitchFamily",
    "DcSwitchVariant",
    "ElectricalPoint",
    "Family",
    "Inverter",
    "InverterFamily",
    "InverterProtection",
    "InverterVariant",
    "LoadedRecord",
    "ModuleVariant",
    "PVModule",
    "PVModuleFamily",
    "PoleOption",
    "ProtectionFamily",
    "Provenance",
    "SwitchRating",
    "Terminals",
    "UnknownComponentError",
    "load_records",
    "read_record",
]
