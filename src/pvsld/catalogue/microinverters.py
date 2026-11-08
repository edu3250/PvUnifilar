"""Microinverter records: one family (values common to the models) with one variant per model.

A microinverter takes one PV module per DC input and joins other microinverters on an AC trunk
cable (an AC branch). It is a separate record type from the string inverter so that code written
for strings, MPPT windows and DC boxes never picks a microinverter by mistake.

Besides the electrical ratings the record keeps two installation tables the manufacturer prints
in the installation manual (Growatt NEO M-X2, section 4.4):

* ``trunk_cables`` (family) and ``max_units_per_trunk`` (variant): how many units one AC trunk
  cable of a given size may carry and the largest branch breaker the manufacturer allows. The
  engine applies the NOM (690-8(b), 240-4(d)) and uses this table only as an extra cap.
* ``max_drop_cable_length_m`` (variant): the longest cable of each size from the AC sub connector.
  The engine computes the voltage drop with the real length and uses this as a cap.

Conductor sizes are written as in the rest of the engine (``"12 AWG"``).

Owner criteria for datasheets (2026-11-08): a missing real power is the apparent power when the
default power factor is at least 0.99 (noted in the record); a missing largest breaker is left out
(the NOM 240-4(d) limit then applies alone); caps that change with the AC voltage keep the 220 V
column (the 2F-3H service in Mexico).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from pvsld.catalogue.common import (
    ComponentId,
    Percent,
    PositiveFloat,
    PositiveInt,
    Provenance,
    Range,
    Strict,
    check_range,
    duplicates,
    raise_problems,
)
from pvsld.catalogue.inverters import (
    AC_CURRENT_TOLERANCE,
    AscendingRange,
    InverterProtection,
    PowerFactorRange,
)

ConductorSize = Annotated[str, Field(pattern=r"^\d+ AWG$", examples=["12 AWG"])]


def _ascending_or_equal(value: Range) -> Range:
    problem = check_range("range", value, allow_equal=True)
    if problem:
        raise ValueError(problem)
    return value


class TrunkCable(Strict):
    """One AC trunk cable size the manufacturer allows and its largest branch breaker."""

    size: ConductorSize
    max_ocpd_a: PositiveFloat | None = Field(
        default=None,
        description="Largest branch breaker the manufacturer allows on this cable; omitted when "
        "the datasheet gives none",
    )


class MicroinverterVariant(Strict):
    """One microinverter model of the family (one column of the datasheet table)."""

    component_id: ComponentId
    recommended_module_power_wp: Annotated[Range, AfterValidator(_ascending_or_equal)] = Field(
        description="Recommended STC power of the module on each input (low, high)"
    )
    rated_ac_power_w: PositiveFloat
    max_apparent_power_va: PositiveFloat
    max_ac_output_current_a: PositiveFloat = Field(
        description="Continuous output current: the 690-8(a)(3) maximum circuit current"
    )
    max_efficiency_pct: Percent
    max_input_current_per_mppt_a: PositiveFloat | None = Field(
        default=None,
        description="Per-model value when it differs by model; omit it when the family defines it",
    )
    max_units_per_trunk: dict[ConductorSize, PositiveInt] = Field(
        min_length=1, description="Manufacturer cap of units per AC branch, by trunk cable size"
    )
    max_drop_cable_length_m: dict[ConductorSize, PositiveFloat] = Field(
        default_factory=dict,
        description="Longest cable from the AC sub connector, by size (manufacturer cap)",
    )

    @model_validator(mode="after")
    def _plausible(self) -> MicroinverterVariant:
        if self.rated_ac_power_w > self.max_apparent_power_va:
            raise ValueError(
                f"variant {self.component_id}: rated_ac_power_w ({self.rated_ac_power_w:g}) "
                f"exceeds max_apparent_power_va ({self.max_apparent_power_va:g})"
            )
        return self


class MicroinverterSpec(Strict):
    """Values every model of the family shares (field group, not used on its own)."""

    manufacturer: str = Field(min_length=1)
    model_family: str = Field(min_length=1, description="Series name as printed on the datasheet")
    mppt_count: PositiveInt = Field(description="DC inputs (one MPPT each)")
    inputs_per_mppt: Literal[1] = Field(description="One PV module per input")
    max_input_voltage_v: PositiveFloat
    startup_voltage_v: PositiveFloat | None = Field(
        default=None, description="Omitted when the datasheet does not print it"
    )
    operating_input_voltage_range_v: AscendingRange | None = None
    mppt_voltage_range_v: AscendingRange
    max_input_current_per_mppt_a: PositiveFloat | None = Field(
        default=None,
        description="Operating limit (module current above it is clipped); omit it when it "
        "differs by model and set it on each variant",
    )
    max_short_circuit_current_per_mppt_a: PositiveFloat = Field(
        description="Hard limit for the module Isc on each input"
    )
    mppt_efficiency_pct: Percent | None = None
    grid: Literal["single_phase"]
    rated_ac_voltage_v: list[PositiveFloat] = Field(min_length=1)
    ac_voltage_range_v: AscendingRange = Field(
        description="AC voltage window the unit operates in; the service must fall inside it"
    )
    max_ac_current_reference_voltage_v: PositiveFloat = Field(
        description="AC voltage at which max_ac_output_current_a is printed"
    )
    frequency_hz: list[PositiveFloat] = Field(min_length=1)
    power_factor_range: PowerFactorRange
    thd_max_pct: PositiveFloat | None = None
    protection: InverterProtection | None = None
    topology: str | None = Field(default=None, description="As printed on the datasheet")
    night_power_w: PositiveFloat | None = None
    dc_connector: str | None = None
    ac_connection: str | None = None
    communication: str | None = None
    operating_temp_c: AscendingRange | None = None
    altitude_m: PositiveFloat | None = None
    ip_rating: str | None = None
    weight_kg: PositiveFloat | None = None
    dimensions_mm: tuple[PositiveFloat, PositiveFloat, PositiveFloat] | None = None
    trunk_cables: list[TrunkCable] = Field(min_length=1)
    drop_cable_size: ConductorSize | None = Field(
        default=None, description="Cable size the manufacturer recommends from the sub connector"
    )

    @model_validator(mode="after")
    def _plausible_shared(self) -> MicroinverterSpec:
        problems: list[str] = []
        current = self.max_input_current_per_mppt_a
        if current is not None and current > self.max_short_circuit_current_per_mppt_a:
            problems.append(
                f"max_input_current_per_mppt_a ({current:g}) exceeds "
                f"max_short_circuit_current_per_mppt_a "
                f"({self.max_short_circuit_current_per_mppt_a:g})"
            )
        high = self.mppt_voltage_range_v[1]
        if high > self.max_input_voltage_v:
            problems.append(
                f"mppt_voltage_range_v upper limit ({high:g}) exceeds max_input_voltage_v "
                f"({self.max_input_voltage_v:g})"
            )
        if self.startup_voltage_v is not None and self.startup_voltage_v > high:
            problems.append(
                f"startup_voltage_v ({self.startup_voltage_v:g}) is above the MPPT window "
                f"upper limit ({high:g})"
            )
        ac_low, ac_high = self.ac_voltage_range_v
        problems += [
            f"rated_ac_voltage_v {volts:g} is outside ac_voltage_range_v ({ac_low:g}, {ac_high:g})"
            for volts in [*self.rated_ac_voltage_v, self.max_ac_current_reference_voltage_v]
            if not ac_low <= volts <= ac_high
        ]
        problems += [
            f"duplicate trunk cable size {size!r}"
            for size in duplicates([cable.size for cable in self.trunk_cables])
        ]
        raise_problems(problems)
        return self


class MicroinverterFamily(MicroinverterSpec):
    """A datasheet family of microinverters: shared values once, one variant per model."""

    component_type: Literal["microinverter"]
    family_id: ComponentId
    variants: list[MicroinverterVariant] = Field(min_length=1)
    source: Provenance

    @model_validator(mode="after")
    def _plausible_variants(self) -> MicroinverterFamily:
        problems = [
            f"duplicate variant component_id {name!r}"
            for name in duplicates([v.component_id for v in self.variants])
        ]
        sizes = {cable.size for cable in self.trunk_cables}
        reference = self.max_ac_current_reference_voltage_v
        for variant in self.variants:
            who = f"variant {variant.component_id}"
            levels = [self.max_input_current_per_mppt_a, variant.max_input_current_per_mppt_a]
            if all(level is None for level in levels) or None not in levels:
                problems.append(
                    f"{who}: max_input_current_per_mppt_a must be defined at exactly one level "
                    "(family or variant)"
                )
            elif (
                variant.max_input_current_per_mppt_a is not None
                and variant.max_input_current_per_mppt_a > self.max_short_circuit_current_per_mppt_a
            ):
                problems.append(
                    f"{who}: max_input_current_per_mppt_a exceeds "
                    "max_short_circuit_current_per_mppt_a"
                )
            unknown = sorted(set(variant.max_units_per_trunk) - sizes)
            missing = sorted(sizes - set(variant.max_units_per_trunk))
            if unknown:
                problems.append(
                    f"{who}: max_units_per_trunk names sizes not in trunk_cables {unknown}"
                )
            if missing:
                problems.append(f"{who}: max_units_per_trunk lacks trunk cable sizes {missing}")
            apparent = variant.max_apparent_power_va
            mismatch = abs(reference * variant.max_ac_output_current_a - apparent) / apparent
            if mismatch > AC_CURRENT_TOLERANCE:
                problems.append(
                    f"{who}: max_ac_output_current_a ({variant.max_ac_output_current_a:g}) does "
                    f"not match max_apparent_power_va ({apparent:g}) at "
                    f"max_ac_current_reference_voltage_v ({reference:g}) within "
                    f"{AC_CURRENT_TOLERANCE:.0%}"
                )
        raise_problems(problems)
        return self

    def expand(self) -> list[Microinverter]:
        """One standalone ``Microinverter`` per variant (family values, variant values on top)."""
        shared = {name: getattr(self, name) for name in MicroinverterSpec.model_fields}
        expanded = []
        for variant in self.variants:
            values = {**shared}
            for name in MicroinverterVariant.model_fields:
                value = getattr(variant, name)
                if value is not None or name not in values:
                    values[name] = value
            expanded.append(
                Microinverter(
                    component_type=self.component_type,
                    family_id=self.family_id,
                    source=self.source,
                    **values,
                )
            )
        return expanded


class Microinverter(MicroinverterSpec, MicroinverterVariant):
    """A single microinverter model as the engine sees it (no family/variant split)."""

    component_type: Literal["microinverter"]
    family_id: ComponentId
    source: Provenance
    max_input_current_per_mppt_a: PositiveFloat  # resolved: never None here

    @property
    def reviewed(self) -> bool:
        """True once the owner has approved the record this microinverter came from."""
        return self.source.reviewed

    def trunk_cable(self, size: str) -> TrunkCable | None:
        """The manufacturer's data for an AC trunk cable size, or ``None`` if it is not allowed."""
        return next((cable for cable in self.trunk_cables if cable.size == size), None)
