"""Catalogue components -> the components of the parameter model (schema 0.1.0).

The sizing engine reasons with catalogue components (datasheet facts, ADR-0004) and the rule pack
reads :mod:`pvsld.core.model` components. This module is the only place that maps one to the other,
and every mapping that is not a plain copy is written down here:

* **Bifacial modules.** ``Module.isc_a`` carries the BNPI short-circuit current (front 1000 W/m2
  plus rear 135 W/m2) so STR-004 and the conductor and fuse sizing use the larger current;
  ``voc_v``, ``vmp_v``, ``imp_a`` and ``pmax_w`` stay at STC, as the string voltage sizing needs.
* **Inverter PV power limit.** ``Inverter.pdc_max_w`` is the datasheet "recommended maximum PV
  power", or the optimizer-only value when the design declares optimizers on every module.
* **Microinverters** (schema 0.2.0). Each DC input is one MPPT (``A``, ``B``...) holding one
  module; ``pdc_max_w`` is the top of the recommended module power times the inputs;
  ``ocpd_max_a`` the largest branch breaker the manufacturer allows on any trunk cable; the
  ``micro`` block carries the AC window and the manufacturer's trunk and drop-cable tables.
* **Values the catalogue does not carry** are listed in :data:`ASSUMPTIONS` and copied to the
  result: the full-power MPPT window, the inverter's maximum OCPD and the DC protection flags.
"""

from __future__ import annotations

from typing import Final

from pvsld.catalogue import Inverter as CatalogueInverter
from pvsld.catalogue import Microinverter
from pvsld.catalogue import PVModule as CatalogueModule
from pvsld.core.model import Inverter, MicroData, Module, Mppt

MODULE_ID: Final = "MOD1"
INVERTER_ID: Final = "INV1"
SUPPORTED_SYSTEM_VOLTAGES_V: Final = (600, 1000, 1500)
MPPT_LETTERS: Final = "ABCDEFGHIJKL"

ASSUMPTIONS: Final = (
    "Voltaje de plena potencia del MPPT: el catálogo no lo registra; se usa la ventana MPPT "
    "completa (STR-001 no emite la advertencia de potencia plena).",
    "ocpd_max_a del inversor: el catálogo no registra la protección máxima de salida; se iguala "
    "al interruptor seleccionado (sin comprobación contra el dato del fabricante, decisión del "
    "propietario).",
    "Las certificaciones del inversor no se registran en el catálogo (ADR-0004).",
)


class UnsupportedComponentError(ValueError):
    """A catalogue component cannot be expressed in schema 0.1.0 (message says why)."""


def display_model(component: CatalogueModule | CatalogueInverter | Microinverter) -> str:
    """Catalogue id without the manufacturer prefix (``JINKO-JKM650N-X`` -> ``JKM650N-X``).

    The sheet's tables are width-limited (``LayoutError`` otherwise), and the manufacturer is
    already printed next to the model.
    """
    head, _, rest = component.component_id.partition("-")
    maker = "".join(ch for ch in component.manufacturer.upper() if ch.isalnum())
    return rest if rest and maker.startswith(head) else component.component_id


def design_isc_a(module: CatalogueModule) -> float:
    """Short-circuit current used for protection and input-current limits.

    The BNPI value for a bifacial module that publishes one, the STC value otherwise.
    """
    return module.bnpi.isc_a if module.bnpi is not None else module.isc_a


def to_spec_module(module: CatalogueModule) -> Module:
    """The parameter-model module of a catalogue PV module (see the module docstring)."""
    if module.max_system_voltage_v not in SUPPORTED_SYSTEM_VOLTAGES_V:
        raise UnsupportedComponentError(
            f"{module.component_id}: max_system_voltage_v {module.max_system_voltage_v:g} V is not "
            f"one of {SUPPORTED_SYSTEM_VOLTAGES_V} accepted by schema 0.1.0"
        )
    return Module(
        id=MODULE_ID,
        manufacturer=module.manufacturer,
        model=display_model(module),
        pmax_w=module.pmax_w,
        voc_v=module.voc_v,
        isc_a=design_isc_a(module),
        vmp_v=module.vmp_v,
        imp_a=module.imp_a,
        beta_voc_pct_c=module.temp_coef_voc_pct_per_c,
        gamma_pmax_pct_c=module.temp_coef_pmax_pct_per_c,
        alpha_isc_pct_c=module.temp_coef_isc_pct_per_c,
        max_series_fuse_a=module.max_series_fuse_a,
        max_system_voltage_v=int(module.max_system_voltage_v),
        bifacial=module.bifacial,
        certifications=list(module.certifications),
    )


def mppt_ids(inverter: CatalogueInverter | Microinverter) -> list[str]:
    """MPPT identifiers ``A``, ``B``, ... in catalogue order."""
    if inverter.mppt_count > len(MPPT_LETTERS):
        raise UnsupportedComponentError(
            f"{inverter.component_id}: {inverter.mppt_count} MPPTs exceed the {len(MPPT_LETTERS)} "
            "identifiers this version names"
        )
    return list(MPPT_LETTERS[: inverter.mppt_count])


def to_spec_inverter(
    inverter: CatalogueInverter,
    *,
    ac_voltage_v: float,
    phases: int,
    optimizers_on_all_modules: bool,
    ocpd_max_a: float,
) -> Inverter:
    """The parameter-model inverter of a catalogue inverter on a service of ``ac_voltage_v``."""
    low_v, high_v = inverter.mppt_voltage_range_v
    protection = inverter.protection
    monitors_isolation = bool(
        protection and (protection.insulation_monitoring or protection.residual_current_monitoring)
    )
    has_afci = bool(protection and (protection.afci or protection.arc_fault))
    mppt = [
        Mppt(
            id=letter,
            vmin_v=low_v,
            vmax_v=high_v,
            vmin_full_power_v=low_v,
            vmax_full_power_v=high_v,
            imax_a=inverter.max_input_current_per_mppt_a,
            isc_max_a=inverter.max_short_circuit_current_per_mppt_a,
            inputs=inverter.inputs_per_mppt,
        )
        for letter in mppt_ids(inverter)
    ]
    return Inverter(
        id=INVERTER_ID,
        manufacturer=inverter.manufacturer,
        model=display_model(inverter),
        type="hybrid" if inverter.component_type == "hybrid_inverter" else "string",
        qty=1,
        pac_w=inverter.rated_ac_power_w,
        vac_v=ac_voltage_v,
        phases=phases,  # type: ignore[arg-type]  # validated by the model; narrowed to 1 | 2 | 3
        iac_max_a=inverter.max_ac_output_current_a,
        ocpd_max_a=ocpd_max_a,
        vdc_max_v=inverter.max_input_voltage_v,
        vdc_start_v=inverter.startup_voltage_v,
        pdc_max_w=inverter.pv_power_limit_w(optimizers_on_all_modules),
        mppt=mppt,
        isolation=inverter.topology or "no declarado",
        gfp=monitors_isolation,
        afci=has_afci,
        dc_switch_integrated=bool(protection and protection.dc_switch),
        dc_spd_integrated=protection.dc_spd if protection else None,
        certifications=[],
    )


def to_spec_microinverter(
    micro: Microinverter, *, inverter_id: str, qty: int, ac_voltage_v: float, phases: int
) -> Inverter:
    """The parameter-model inverter (type ``micro``) of ``qty`` units of a catalogue model."""
    low_v, high_v = micro.mppt_voltage_range_v
    protection = micro.protection
    mppt = [
        Mppt(
            id=letter,
            vmin_v=low_v,
            vmax_v=high_v,
            vmin_full_power_v=low_v,
            vmax_full_power_v=high_v,
            imax_a=micro.max_input_current_per_mppt_a,
            isc_max_a=micro.max_short_circuit_current_per_mppt_a,
            inputs=1,
        )
        for letter in mppt_ids(micro)
    ]
    return Inverter(
        id=inverter_id,
        manufacturer=micro.manufacturer,
        model=display_model(micro),
        type="micro",
        qty=qty,
        pac_w=micro.rated_ac_power_w,
        vac_v=ac_voltage_v,
        phases=phases,  # type: ignore[arg-type]  # validated by the model; narrowed to 1 | 2 | 3
        iac_max_a=micro.max_ac_output_current_a,
        ocpd_max_a=max(cable.max_ocpd_a for cable in micro.trunk_cables),
        vdc_max_v=micro.max_input_voltage_v,
        vdc_start_v=micro.startup_voltage_v,
        pdc_max_w=micro.recommended_module_power_wp[1] * micro.mppt_count,
        mppt=mppt,
        isolation=micro.topology or "no declarado",
        gfp=bool(protection and protection.insulation_monitoring),
        afci=bool(protection and (protection.afci or protection.arc_fault)),
        certifications=[],
        micro=MicroData(
            ac_voltage_range_v=micro.ac_voltage_range_v,
            recommended_module_power_wp=micro.recommended_module_power_wp,
            trunk_max_ocpd_a={cable.size: cable.max_ocpd_a for cable in micro.trunk_cables},
            max_units_per_trunk=dict(micro.max_units_per_trunk),
            max_drop_cable_length_m=dict(micro.max_drop_cable_length_m),
        ),
    )
