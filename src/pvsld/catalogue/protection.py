"""Protection device records: DC breakers (Stage 3.1) and PV fuses (gPV, 2026-10-08).

A DC breaker range is configured at design time: the number of poles and the rated DC voltage Ue
are choices (Suntree SL7N-63: 2P at 550 V or 800 V, 4P at 1000 V or 1200 V), while the catalogue
reference only fixes the rated current. The breaking capacity depends on that configuration and on
the circuit voltage, so the family keeps the datasheet's entries and
:meth:`ProtectionSpec.breaking_capacity_ka` answers for a (poles, voltage) pair.

A breaking-capacity entry may leave ``poles`` or ``voltage_v`` empty: an empty ``poles`` means
"any pole count", and an entry with both empty is the fallback for every other configuration
("Icu = 2 kA for the other configurations"). A PV fuse range (``dc_fuse``) is simpler: one rated
voltage and one interrupting rating for every current rating. SPDs and AC breakers join the
``component_type`` discriminator when the first datasheet of each arrives.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Literal

from pydantic import Field, model_validator

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


class PoleOption(Strict):
    """Rated DC voltages Ue offered for one pole count."""

    poles: PositiveInt
    ue_v_options: list[PositiveFloat] = Field(min_length=1)


class BreakingCapacity(Strict):
    """Ultimate breaking capacity (Icu) for a pole count and DC voltage.

    ``poles`` empty = any pole count; ``voltage_v`` empty = any voltage; both empty = the
    fallback for the configurations the datasheet does not list.
    """

    poles: PositiveInt | None = None
    voltage_v: PositiveFloat | None = None
    icu_ka: PositiveFloat


class Terminals(Strict):
    """Cross-section range accepted by the terminals, mm2 as (min, max)."""

    rigid_mm2: Range | None = None
    flexible_mm2: Range | None = None


class DcBreakerVariant(Strict):
    """One current rating of the breaker range (one catalogue reference)."""

    component_id: ComponentId = Field(description="Catalogue reference, e.g. SCHNEIDER-A9N61652")
    rated_current_a: PositiveFloat
    power_loss_w_at_in: PositiveFloat | None = Field(
        default=None, description="Dissipated power at the rated current (all poles)"
    )


class ProtectionSpec(Strict):
    """Values every rating of the range shares (field group, not used on its own)."""

    manufacturer: str = Field(min_length=1)
    range_name: str = Field(min_length=1, description="Range as printed, e.g. Acti9 C60PV-DC")
    poles: PositiveInt | None = Field(
        default=None, description="Fixed pole count; omit it when pole_options is given"
    )
    rated_voltage_v: PositiveFloat | None = Field(
        default=None, description="Rated DC voltage of a fixed-pole range"
    )
    pole_options: list[PoleOption] | None = Field(
        default=None, description="Selectable pole counts with their Ue values"
    )
    breaking_capacity_dc: list[BreakingCapacity] = Field(min_length=1)
    ics_pct_of_icu: Percent | None = Field(default=None, description="Service breaking capacity")
    frame_current_a: PositiveFloat | None = None
    trip_curves: list[str] | None = None
    magnetic_trip: str | None = None
    polarity_sensitive: bool | None = None
    isolation_suitable: bool | None = None
    utilization_category: str | None = None
    insulation_voltage_v: PositiveFloat | None = None
    impulse_withstand_kv: PositiveFloat | None = None
    overvoltage_category: str | None = None
    pollution_degree: PositiveInt | None = None
    standards: list[str] = Field(min_length=1)
    certifications: list[str] | None = None
    terminals: Terminals | None = None
    tightening_torque_nm: PositiveFloat | None = None
    ip_rating: str | None = None
    mechanical_life_ops: PositiveInt | None = None
    electrical_life_ops: dict[str, PositiveInt] | None = None
    operating_temp_c: Range
    storage_temp_c: Range | None = None
    altitude_m: PositiveFloat | None = None
    humidity_max_pct: Percent | None = None
    mounting: str | None = None
    height_mm: PositiveFloat | None = None
    width_mm: PositiveFloat | None = None
    width_mm_per_pole: PositiveFloat | None = None
    depth_mm: PositiveFloat | None = None
    weight_kg: PositiveFloat | None = None
    weight_kg_per_pole: PositiveFloat | None = None
    accessories: list[str] | None = None

    def ue_options_v(self, poles: int) -> list[float]:
        """Rated DC voltages offered for ``poles`` poles (empty when that count is not offered)."""
        if self.pole_options is not None:
            return next((o.ue_v_options for o in self.pole_options if o.poles == poles), [])
        if self.poles == poles and self.rated_voltage_v is not None:
            return [self.rated_voltage_v]
        return []

    def breaking_capacity_ka(self, poles: int, voltage_v: float) -> float | None:
        """Icu (kA) to rely on for a ``poles``-pole breaker on a DC circuit of ``voltage_v``.

        ``None`` when the range has no ``poles``-pole version rated for that voltage. Otherwise the
        entry for the lowest tabulated voltage at or above ``voltage_v`` wins (Icu falls as the
        voltage rises, so the next point up is the conservative one; an entry for exactly this pole
        count beats an any-poles entry); with no such entry the fallback entry applies, and
        ``None`` is returned when the datasheet gives none.
        """
        offered = self.ue_options_v(poles)
        if not offered or voltage_v > max(offered):
            return None
        candidates = [
            entry
            for entry in self.breaking_capacity_dc
            if entry.voltage_v is not None
            and entry.voltage_v >= voltage_v
            and entry.poles in (None, poles)
        ]
        if candidates:
            best = min(candidates, key=lambda e: (e.voltage_v, e.poles is None))
            return best.icu_ka
        fallback = [e for e in self.breaking_capacity_dc if e.poles is None and e.voltage_v is None]
        return fallback[0].icu_ka if fallback else None

    @model_validator(mode="after")
    def _plausible_configuration(self) -> ProtectionSpec:
        problems: list[str] = []
        problems += self._configuration_problems()
        problems += self._breaking_capacity_problems()
        for name in ("operating_temp_c", "storage_temp_c"):
            value = getattr(self, name)
            problem = check_range(name, value) if value is not None else None
            if problem:
                problems.append(problem)
        raise_problems(problems)
        return self

    def _configuration_problems(self) -> list[str]:
        if self.pole_options is None:
            if self.poles is None or self.rated_voltage_v is None:
                return ["give either poles and rated_voltage_v, or pole_options"]
            return []
        problems = []
        if self.poles is not None or self.rated_voltage_v is not None:
            problems.append("poles / rated_voltage_v and pole_options are mutually exclusive")
        counts = [str(option.poles) for option in self.pole_options]
        problems += [f"pole_options: duplicate pole count {name}" for name in duplicates(counts)]
        problems += [
            f"pole_options: ue_v_options of {option.poles}P must be ascending and unique"
            for option in self.pole_options
            if option.ue_v_options != sorted(set(option.ue_v_options))
        ]
        return problems

    def _breaking_capacity_problems(self) -> list[str]:
        entries = self.breaking_capacity_dc
        problems: list[str] = []
        keys = [f"({e.poles}, {e.voltage_v})" for e in entries]
        problems += [f"breaking_capacity_dc: duplicate entry {key}" for key in duplicates(keys)]
        for entry in entries:
            if entry.poles is not None and entry.poles not in self._offered_pole_counts():
                problems.append(
                    f"breaking_capacity_dc: {entry.poles} poles is not offered by this range"
                )
                continue
            top = self._highest_ue_v(entry.poles)
            if entry.voltage_v is not None and (top is None or entry.voltage_v > top):
                problems.append(
                    f"breaking_capacity_dc: {entry.voltage_v:g} V is above every rated voltage "
                    f"offered for {entry.poles if entry.poles is not None else 'any'} poles"
                )
        for poles in {e.poles for e in entries}:
            curve = sorted(
                (e for e in entries if e.poles == poles and e.voltage_v is not None),
                key=lambda e: e.voltage_v or 0,
            )
            problems += [
                f"breaking_capacity_dc: Icu cannot rise with voltage "
                f"({a.icu_ka:g} kA at {a.voltage_v:g} V, {b.icu_ka:g} kA at {b.voltage_v:g} V)"
                for a, b in pairwise(curve)
                if b.icu_ka > a.icu_ka
            ]
        return problems

    def _offered_pole_counts(self) -> list[int]:
        if self.pole_options is not None:
            return [option.poles for option in self.pole_options]
        return [self.poles] if self.poles is not None else []

    def _highest_ue_v(self, poles: int | None) -> float | None:
        counts = self._offered_pole_counts() if poles is None else [poles]
        voltages = [volts for count in counts for volts in self.ue_options_v(count)]
        return max(voltages) if voltages else None


class ProtectionFamily(ProtectionSpec):
    """A datasheet range of DC breakers: shared values once, one variant per current rating."""

    component_type: Literal["dc_breaker"]
    family_id: ComponentId
    variants: list[DcBreakerVariant] = Field(min_length=1)
    source: Provenance

    @model_validator(mode="after")
    def _plausible_variants(self) -> ProtectionFamily:
        problems = [
            f"duplicate variant component_id {name!r}"
            for name in duplicates([v.component_id for v in self.variants])
        ]
        currents = [f"{v.rated_current_a:g}" for v in self.variants]
        problems += [
            f"duplicate rated_current_a {name} A in the family" for name in duplicates(currents)
        ]
        if self.frame_current_a is not None:
            problems += [
                f"variant {v.component_id}: rated_current_a ({v.rated_current_a:g}) exceeds "
                f"frame_current_a ({self.frame_current_a:g})"
                for v in self.variants
                if v.rated_current_a > self.frame_current_a
            ]
        raise_problems(problems)
        return self

    def expand(self) -> list[DcBreaker]:
        """One standalone ``DcBreaker`` per variant (family values + variant values)."""
        shared = {name: getattr(self, name) for name in ProtectionSpec.model_fields}
        return [
            DcBreaker(
                component_type=self.component_type,
                family_id=self.family_id,
                source=self.source,
                **shared,
                **{name: getattr(variant, name) for name in DcBreakerVariant.model_fields},
            )
            for variant in self.variants
        ]


class DcBreaker(ProtectionSpec, DcBreakerVariant):
    """A single DC breaker reference as the sizing engine sees it (no family/variant split)."""

    component_type: Literal["dc_breaker"]
    family_id: ComponentId
    source: Provenance

    @property
    def reviewed(self) -> bool:
        """True once the owner has approved the record this breaker came from."""
        return self.source.reviewed


# --- DC fuses (gPV) -----------------------------------------------------------------------------


class DcFuseVariant(Strict):
    """One current rating of a PV fuse range (one catalogue reference)."""

    component_id: ComponentId = Field(description="Catalogue reference, e.g. EATON-PV-15A10F")
    rated_current_a: PositiveFloat
    breaking_capacity_ka: PositiveFloat = Field(
        description="DC interrupting rating of this rating (ranges split it by current)"
    )
    power_loss_w_at_in: PositiveFloat | None = Field(
        default=None, description="Watts loss at the rated current"
    )
    power_loss_w_at_08in: PositiveFloat | None = Field(
        default=None, description="Watts loss at 0.8 x the rated current"
    )


class DcFuseSpec(Strict):
    """Values every rating of a PV fuse range shares (field group, not used on its own)."""

    manufacturer: str = Field(min_length=1)
    range_name: str = Field(min_length=1, description="Range as printed")
    size: str = Field(min_length=1, description="Body size, e.g. 10x38 mm")
    rated_voltage_v: PositiveFloat = Field(description="Rated DC voltage")
    operating_class: str = Field(min_length=1, description="IEC 60269-6 class, e.g. gPV")
    time_constant_ms: Range | None = None
    standards: list[str] = Field(min_length=1)
    certifications: list[str] | None = None
    operating_temp_c: Range | None = None
    wire_range: str | None = None
    holders: list[str] | None = Field(
        default=None, description="Recommended fuse holders or blocks, as printed"
    )
    sizing_note: str | None = Field(
        default=None, description="Selection rule the datasheet states, e.g. rating > 1.56 x Isc"
    )

    @model_validator(mode="after")
    def _plausible_ranges(self) -> DcFuseSpec:
        problems = [
            problem
            for name in ("operating_temp_c", "time_constant_ms")
            if (value := getattr(self, name)) is not None
            and (problem := check_range(name, value)) is not None
        ]
        raise_problems(problems)
        return self


class DcFuseFamily(DcFuseSpec):
    """A datasheet range of PV fuse links: shared values once, one variant per current rating."""

    component_type: Literal["dc_fuse"]
    family_id: ComponentId
    variants: list[DcFuseVariant] = Field(min_length=1)
    source: Provenance

    @model_validator(mode="after")
    def _plausible_variants(self) -> DcFuseFamily:
        problems = [
            f"duplicate variant component_id {name!r}"
            for name in duplicates([v.component_id for v in self.variants])
        ]
        problems += [
            f"duplicate rated_current_a {name} A in the family"
            for name in duplicates([f"{v.rated_current_a:g}" for v in self.variants])
        ]
        raise_problems(problems)
        return self

    def expand(self) -> list[DcFuse]:
        """One standalone ``DcFuse`` per variant (family values + variant values)."""
        shared = {name: getattr(self, name) for name in DcFuseSpec.model_fields}
        return [
            DcFuse(
                component_type=self.component_type,
                family_id=self.family_id,
                source=self.source,
                **shared,
                **{name: getattr(variant, name) for name in DcFuseVariant.model_fields},
            )
            for variant in self.variants
        ]


class DcFuse(DcFuseSpec, DcFuseVariant):
    """A single PV fuse reference as the sizing engine sees it."""

    component_type: Literal["dc_fuse"]
    family_id: ComponentId
    source: Provenance

    @property
    def reviewed(self) -> bool:
        """True once the owner has approved the record this fuse came from."""
        return self.source.reviewed


# --- AC breakers ----------------------------------------------------------------------------------


class AcBreakerVariant(Strict):
    """One current rating of an AC breaker range (one catalogue reference)."""

    component_id: ComponentId = Field(description="Catalogue reference, e.g. SQUARED-QO220")
    rated_current_a: PositiveFloat
    interrupting_ka: PositiveFloat = Field(description="UL interrupting rating at rated voltage")


class AcBreakerSpec(Strict):
    """Values every rating of an AC breaker range shares (field group, not used on its own)."""

    manufacturer: str = Field(min_length=1)
    range_name: str = Field(min_length=1)
    poles: PositiveInt
    rated_voltage: str = Field(min_length=1, description="As printed, e.g. 120/240 Vac")
    voltage_v: PositiveFloat = Field(description="Highest line-to-line voltage of the rating")
    mounting: str | None = None
    standards: list[str] = Field(min_length=1)
    certifications: list[str] | None = None
    terminals: str | None = None


class AcBreakerFamily(AcBreakerSpec):
    """A datasheet range of AC breakers: shared values once, one variant per current rating."""

    component_type: Literal["ac_breaker"]
    family_id: ComponentId
    variants: list[AcBreakerVariant] = Field(min_length=1)
    source: Provenance

    @model_validator(mode="after")
    def _plausible_variants(self) -> AcBreakerFamily:
        problems = [
            f"duplicate variant component_id {name!r}"
            for name in duplicates([v.component_id for v in self.variants])
        ]
        problems += [
            f"duplicate rated_current_a {name} A in the family"
            for name in duplicates([f"{v.rated_current_a:g}" for v in self.variants])
        ]
        raise_problems(problems)
        return self

    def expand(self) -> list[AcBreaker]:
        """One standalone ``AcBreaker`` per variant (family values + variant values)."""
        shared = {name: getattr(self, name) for name in AcBreakerSpec.model_fields}
        return [
            AcBreaker(
                component_type=self.component_type,
                family_id=self.family_id,
                source=self.source,
                **shared,
                **{name: getattr(variant, name) for name in AcBreakerVariant.model_fields},
            )
            for variant in self.variants
        ]


class AcBreaker(AcBreakerSpec, AcBreakerVariant):
    """A single AC breaker reference as the sizing engine sees it."""

    component_type: Literal["ac_breaker"]
    family_id: ComponentId
    source: Provenance

    @property
    def reviewed(self) -> bool:
        """True once the owner has approved the record this breaker came from."""
        return self.source.reviewed


# --- DC switch-disconnectors (PV) -----------------------------------------------------------------


class SwitchRating(Strict):
    """Rated operational current for one way of wiring the poles, up to a DC voltage.

    ``poles_per_circuit`` is how many poles each circuit goes through: 2 when two strings share a
    four-pole switch (one pole per conductor), 4 when one string uses every pole in series.
    """

    poles_per_circuit: PositiveInt
    voltage_v: PositiveFloat
    ie_a: PositiveFloat


class DcSwitchVariant(Strict):
    """One current version of the switch range (one catalogue reference)."""

    component_id: ComponentId = Field(description="Catalogue reference, e.g. SUNTREE-SISO-40-32")
    enclosed_thermal_current_a: PositiveFloat = Field(description="Ithe, in its enclosure")
    ratings: list[SwitchRating] = Field(min_length=1)


class DcSwitchSpec(Strict):
    """Values every version of a PV switch-disconnector range shares."""

    manufacturer: str = Field(min_length=1)
    range_name: str = Field(min_length=1)
    poles: PositiveInt
    utilization_category: str = Field(min_length=1)
    insulation_voltage_v: PositiveFloat
    standards: list[str] = Field(min_length=1)
    certifications: list[str] | None = None
    ip_rating: str | None = None
    short_time_withstand: str | None = None
    mechanical_life_ops: PositiveInt | None = None
    electrical_life_ops: PositiveInt | None = None
    storage_temp_c: Range | None = None

    @model_validator(mode="after")
    def _plausible(self) -> DcSwitchSpec:
        problem = (
            check_range("storage_temp_c", self.storage_temp_c) if self.storage_temp_c else None
        )
        raise_problems([problem] if problem else [])
        return self


class DcSwitchFamily(DcSwitchSpec):
    """A datasheet range of PV switch-disconnectors, one variant per current version."""

    component_type: Literal["dc_switch"]
    family_id: ComponentId
    variants: list[DcSwitchVariant] = Field(min_length=1)
    source: Provenance

    @model_validator(mode="after")
    def _plausible_variants(self) -> DcSwitchFamily:
        problems = [
            f"duplicate variant component_id {name!r}"
            for name in duplicates([v.component_id for v in self.variants])
        ]
        for variant in self.variants:
            problems += [
                f"variant {variant.component_id}: {r.poles_per_circuit} poles per circuit is "
                f"more than the {self.poles} poles of the switch"
                for r in variant.ratings
                if r.poles_per_circuit > self.poles
            ]
        raise_problems(problems)
        return self

    def expand(self) -> list[DcSwitch]:
        """One standalone ``DcSwitch`` per variant (family values + variant values)."""
        shared = {name: getattr(self, name) for name in DcSwitchSpec.model_fields}
        return [
            DcSwitch(
                component_type=self.component_type,
                family_id=self.family_id,
                source=self.source,
                **shared,
                **{name: getattr(variant, name) for name in DcSwitchVariant.model_fields},
            )
            for variant in self.variants
        ]


class DcSwitch(DcSwitchSpec, DcSwitchVariant):
    """One PV switch-disconnector as the sizing engine sees it."""

    component_type: Literal["dc_switch"]
    family_id: ComponentId
    source: Provenance

    def rating_a(self, poles_per_circuit: int, voltage_v: float) -> tuple[float, float] | None:
        """Current (A) and voltage step (V) for circuits wired through ``poles_per_circuit``
        poles at ``voltage_v``: the lowest tabulated voltage at or above it, capped by the
        enclosed thermal current. ``None`` when the switch has no such rating."""
        steps = sorted(
            (r for r in self.ratings if r.poles_per_circuit == poles_per_circuit),
            key=lambda r: r.voltage_v,
        )
        step = next((r for r in steps if r.voltage_v >= voltage_v), None)
        if step is None:
            return None
        return min(step.ie_a, self.enclosed_thermal_current_a), step.voltage_v

    @property
    def reviewed(self) -> bool:
        """True once the owner has approved the record this switch came from."""
        return self.source.reviewed


# --- DC surge protective devices ---------------------------------------------------------------


class DcSpdVariant(Strict):
    """One version of a PV surge protective device range (one catalogue reference)."""

    component_id: ComponentId = Field(
        description="Catalogue reference, e.g. CLAMPER-SOLAR-600V-18KA"
    )
    product_code: str | None = Field(
        default=None, description="Manufacturer's ordering code of the complete device, as printed"
    )
    ucpv_v: PositiveFloat = Field(description="Maximum continuous DC operating voltage U_CPV")
    in_ka: PositiveFloat = Field(description="Nominal discharge current I_n (8/20 us)")
    imax_ka: PositiveFloat = Field(description="Maximum discharge current I_max (8/20 us)")
    itotal_ka: PositiveFloat | None = Field(
        default=None, description="Total discharge current (8/20 us)"
    )
    uref_v: PositiveFloat | None = Field(default=None, description="Reference voltage at 1 mA")
    up_kv: PositiveFloat = Field(description="Voltage protection level U_p (the printed maximum)")
    remote_signalling: bool = False
    certified: bool = Field(
        default=True, description="False when the datasheet says this version is not certified"
    )

    @model_validator(mode="after")
    def _plausible(self) -> DcSpdVariant:
        problems = []
        if self.imax_ka < self.in_ka:
            problems.append(f"imax_ka {self.imax_ka:g} is below in_ka {self.in_ka:g}")
        raise_problems(problems)
        return self


class DcSpdSpec(Strict):
    """Values every version of a PV SPD range shares (field group, not used on its own)."""

    manufacturer: str = Field(min_length=1)
    range_name: str = Field(min_length=1)
    spd_class: Literal["I", "II", "I+II"] = Field(description="IEC/EN class (Type 1, 2, 1+2)")
    technology: str = Field(min_length=1, description="e.g. MOV with thermal disconnector")
    standards: list[str] = Field(min_length=1)
    certifications: list[str] | None = None
    protection_modes: list[str] = Field(min_length=1, description="e.g. L+/PE, L-/PE, L+/L-")
    iscpv_ka: PositiveFloat = Field(
        description="Short-circuit current rating I_SCPV (without backup fuse when stated so)"
    )
    backup_fuse: str | None = Field(default=None, description="Backup fuse, as printed")
    response_time_ns: PositiveFloat | None = None
    operating_temp_c: Range | None = None
    ip_rating: str | None = None
    wire_range: str | None = None
    mounting: str | None = None

    @model_validator(mode="after")
    def _plausible_range(self) -> DcSpdSpec:
        problem = (
            check_range("operating_temp_c", self.operating_temp_c)
            if self.operating_temp_c
            else None
        )
        raise_problems([problem] if problem else [])
        return self


class DcSpdFamily(DcSpdSpec):
    """A datasheet range of PV SPDs: shared values once, one variant per version."""

    component_type: Literal["dc_spd"]
    family_id: ComponentId
    variants: list[DcSpdVariant] = Field(min_length=1)
    source: Provenance

    @model_validator(mode="after")
    def _plausible_variants(self) -> DcSpdFamily:
        problems = [
            f"duplicate variant component_id {name!r}"
            for name in duplicates([v.component_id for v in self.variants])
        ]
        raise_problems(problems)
        return self

    def expand(self) -> list[DcSpd]:
        """One standalone ``DcSpd`` per variant (family values + variant values)."""
        shared = {name: getattr(self, name) for name in DcSpdSpec.model_fields}
        return [
            DcSpd(
                component_type=self.component_type,
                family_id=self.family_id,
                source=self.source,
                **shared,
                **{name: getattr(variant, name) for name in DcSpdVariant.model_fields},
            )
            for variant in self.variants
        ]


class DcSpd(DcSpdSpec, DcSpdVariant):
    """One PV surge protective device as the sizing engine sees it."""

    component_type: Literal["dc_spd"]
    family_id: ComponentId
    source: Provenance

    @property
    def spd_type(self) -> str:
        """The type the drawing prints: T1, T2 or T1+T2."""
        return {"I": "T1", "II": "T2", "I+II": "T1+T2"}[self.spd_class]

    @property
    def reviewed(self) -> bool:
        """True once the owner has approved the record this SPD came from."""
        return self.source.reviewed
