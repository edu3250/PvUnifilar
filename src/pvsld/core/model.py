"""Pydantic v2 parameter model (schema_version 0.1.0) of a photovoltaic installation.

This is the single source of truth of the pipeline (ADR-0001, layer L2): inputs only. Anything
computable (maximum string voltage, design currents, voltage drop, ...) is derived in
:mod:`pvsld.core.calc`, never typed by the user. Field names carry their unit (``_v``, ``_a``,
``_w``, ``_m``, ``_c``, ``_pct``), as in the vault note "PV SLD Parameter Model".

The model is strict on purpose: unknown fields are rejected, so a typo made by an LLM client is an
error that names the field instead of a silently ignored input. Cross references (strings to
modules and inverters, circuits to components, ...) are checked here, so the rule pack can rely
on them.

Example::

    spec = parse_spec(yaml.safe_load(text))
    schema = export_json_schema()  # JSON Schema 2020-12, published as an MCP resource
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.1.0"
RULEPACK_ID = "mx-gd-2026.10"
NOM_EDITION = "NOM-001-SEDE-2012"

# NOM-001-SEDE-2012 conductor sizes covered by the ampacity tables of this version.
AWG_PATTERN = r"^(14|12|10|8|6|4|3|2|1|1/0|2/0|3/0|4/0) AWG$"
# CFE standard service voltages (L-L for 2F/3F systems, L-N for 1F), low and medium voltage.
NOMINAL_VOLTAGES_V = (127, 220, 240, 480, 13200, 13800, 23000, 34500)

ConductorSize = Annotated[str, Field(pattern=AWG_PATTERN, examples=["10 AWG"])]
PositiveFloat = Annotated[float, Field(gt=0)]
PositiveInt = Annotated[int, Field(gt=0)]
DeviceRole = Literal["I1", "I2", "other"]


class _Strict(BaseModel):
    """Base of every model: no unknown fields, immutable, aliases accepted by name too."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# Personal data (owner, address, coordinates, service identifiers, responsible engineer) is
# optional (Stage 3.6.2): what the user gives is drawn and reported, what is missing stays a blank
# line to fill by hand. Nothing is ever invented. Its format is the user's business (owner decision
# 2026-10-09): any text is accepted as written, with no pattern or range check.
OptionalText = Annotated[str | None, Field(min_length=1)]
Coordinate = float | Annotated[str, Field(min_length=1)] | None


class Client(_Strict):
    name: OptionalText = None
    phone: OptionalText = None
    email: OptionalText = None


class Address(_Strict):
    street: OptionalText = None
    number: OptionalText = None
    colonia: OptionalText = None
    municipio: OptionalText = None
    estado: OptionalText = None
    cp: OptionalText = None


def address_text(address: Address) -> str:
    """The address on one line from the parts given ("" when none is)."""
    street = " ".join(part for part in (address.street, address.number) if part)
    parts = [street, address.colonia, address.municipio, address.estado]
    if address.cp:
        parts.append(f"C.P. {address.cp}")
    return ", ".join(part for part in parts if part)


class Site(_Strict):
    address: Address = Address()
    lat: Coordinate = Field(default=None, description="Degrees, or the text the user wrote")
    lon: Coordinate = Field(default=None, description="Degrees, or the text the user wrote")
    occupancy: Literal[
        "vivienda_unifamiliar",
        "vivienda_bifamiliar",
        "comercial",
        "industrial",
        "lugar_concentracion_publica",
    ]
    array_on_building: bool = True
    t_min_c: float = Field(ge=-40, le=25, description="Lowest expected ambient (design), degC")
    t_min_source: str = Field(min_length=1, description="Where t_min_c comes from")
    t_amb_max_c: float = Field(ge=25, le=55, description="Highest expected ambient, degC")
    ground_flash_density_ng: float | None = Field(default=None, gt=0)


class Project(_Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    client: Client = Client()
    site: Site


class VdLimits(_Strict):
    dc: float = Field(default=1.5, gt=0, le=5)
    ac: float = Field(default=2.0, gt=0, le=5)
    total: float = Field(default=3.0, gt=0, le=5)


class Standards(_Strict):
    nom_edition: Literal["NOM-001-SEDE-2012"]
    rulepack: Literal["mx-gd-2026.10"]
    voc_method: Literal["coefficient", "table_690_7", "iec_default_1_2"] = "coefficient"
    vd_limits_pct: VdLimits = VdLimits()


class ServiceMain(_Strict):
    rating_a: PositiveFloat
    poles: PositiveInt
    kaic_ka: PositiveFloat


class Utility(_Strict):
    supplier: str = Field(min_length=1)
    rpu: OptionalText = Field(default=None, description="CFE RPU, as the user wrote it")
    service_number: OptionalText = None
    meter_number: OptionalText = None
    tariff: str = Field(min_length=1)
    voltage_level: Literal["BT", "MT"]
    system: Literal["1F-2H", "2F-3H", "3F-4H", "MT-3F-3H", "MT-3F-4H"]
    nominal_voltage_v: int
    available_fault_current_ka: PositiveFloat
    regime: Literal["medicion_neta", "facturacion_neta", "venta_total"]
    interconnection_scheme: int = Field(ge=1, le=9)
    service_main: ServiceMain

    @model_validator(mode="after")
    def _standard_voltage(self) -> Utility:
        if self.nominal_voltage_v not in NOMINAL_VOLTAGES_V:
            raise ValueError(
                f"nominal_voltage_v {self.nominal_voltage_v} is not a CFE standard value "
                f"{NOMINAL_VOLTAGES_V}"
            )
        return self


class Module(_Strict):
    id: str = Field(min_length=1)
    manufacturer: str = Field(min_length=1)
    model: str = Field(min_length=1)
    pmax_w: PositiveFloat
    voc_v: PositiveFloat
    isc_a: PositiveFloat
    vmp_v: PositiveFloat
    imp_a: PositiveFloat
    beta_voc_pct_c: float | None = Field(default=None, gt=-0.6, lt=0)
    gamma_pmax_pct_c: float = Field(gt=-0.6, lt=0)
    alpha_isc_pct_c: float | None = None
    max_series_fuse_a: PositiveFloat
    max_system_voltage_v: Literal[600, 1000, 1500]
    bifacial: bool = False
    certifications: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _operating_point_below_open_circuit(self) -> Module:
        if self.vmp_v >= self.voc_v:
            raise ValueError(f"module {self.id}: vmp_v must be lower than voc_v")
        if self.imp_a >= self.isc_a:
            raise ValueError(f"module {self.id}: imp_a must be lower than isc_a")
        return self


class Mppt(_Strict):
    id: str = Field(min_length=1)
    vmin_v: PositiveFloat
    vmax_v: PositiveFloat
    vmin_full_power_v: PositiveFloat
    vmax_full_power_v: PositiveFloat
    imax_a: PositiveFloat
    isc_max_a: PositiveFloat
    inputs: PositiveInt = 1


class Inverter(_Strict):
    id: str = Field(min_length=1)
    manufacturer: str = Field(min_length=1)
    model: str = Field(min_length=1)
    type: Literal["string", "central", "micro", "hybrid", "optimizer_string"]
    qty: PositiveInt = 1
    pac_w: PositiveFloat
    vac_v: PositiveFloat
    phases: Literal[1, 2, 3] = Field(description="Energized conductors: 1 (L-N), 2 (L-L), 3")
    iac_max_a: PositiveFloat
    ocpd_max_a: PositiveFloat
    vdc_max_v: PositiveFloat
    vdc_start_v: PositiveFloat
    pdc_max_w: PositiveFloat
    mppt: list[Mppt] = Field(min_length=1)
    isolation: str = Field(min_length=1)
    gfp: bool
    afci: bool
    dc_switch_integrated: bool = False
    dc_spd_integrated: str | None = None
    certifications: list[str] = Field(default_factory=list)


class StringSpec(_Strict):
    id: str = Field(min_length=1)
    module: str
    n_series: PositiveInt
    inverter: str
    mppt: str
    tilt_deg: float = Field(ge=0, le=90)
    azimuth_deg: float = Field(ge=0, le=360)


class Combiner(_Strict):
    id: str = Field(min_length=1)
    inputs: PositiveInt
    fuse_rating_a: PositiveFloat | None = None
    fuse_voltage_v: PositiveFloat | None = None


class DcDisconnect(_Strict):
    id: str = Field(min_length=1)
    integrated_in: str | None = None
    poles: PositiveInt
    ue_v: PositiveFloat
    ie_a: PositiveFloat
    model: str | None = Field(
        default=None, description="Catalogue reference of the device (component_id)"
    )


class Spd(_Strict):
    id: str = Field(min_length=1)
    at: str = Field(min_length=1)
    spd_type: str = Field(min_length=1)
    ucpv_v: PositiveFloat | None = None
    uc_v: PositiveFloat | None = None
    in_ka: PositiveFloat
    iscpv_a: PositiveFloat | None = None
    model: str | None = Field(
        default=None, description="Catalogue reference of the device (component_id)"
    )


class DcBos(_Strict):
    combiners: list[Combiner] = Field(default_factory=list)
    disconnects: list[DcDisconnect] = Field(default_factory=list)
    spds: list[Spd] = Field(default_factory=list)


class Ocpd(_Strict):
    id: str = Field(min_length=1)
    role: DeviceRole
    poles: PositiveInt
    rating_a: PositiveFloat
    voltage_v: PositiveFloat
    kaic_ka: PositiveFloat
    backfed: bool = False
    at: str | None = None
    model: str | None = Field(
        default=None, description="Catalogue reference of the device (component_id)"
    )
    manual: bool | None = Field(default=None, description="Manually operated (DIS-003)")
    lockable: bool | None = Field(default=None, description="Can be locked open (DIS-003)")


class AcDisconnect(_Strict):
    id: str = Field(min_length=1)
    role: DeviceRole = "other"
    poles: PositiveInt
    rating_a: PositiveFloat
    visible_break: bool | None = None
    lockable: bool | None = None


class Panel(_Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    bus_a: PositiveFloat
    main_ocpd: str
    system: Literal["1F-2H", "2F-3H", "3F-4H"]
    kaic_ka: PositiveFloat


class MainBreaker(_Strict):
    id: str = Field(min_length=1)
    role: DeviceRole
    poles: PositiveInt
    rating_a: PositiveFloat
    bidirectional: bool = False
    kaic_ka: PositiveFloat | None = Field(
        default=None, description="Interrupting rating, when the breaker is known"
    )
    model: str | None = Field(
        default=None, description="Catalogue reference of the device (component_id)"
    )


class PointOfConnection(_Strict):
    id: str = Field(min_length=1)
    type: Literal["supply_side", "load_side"]
    panel: str
    breaker: str
    position: Literal["opposite_end", "other"] = "opposite_end"


class Meter(_Strict):
    id: str = Field(min_length=1)
    role: Literal["MF", "MCE"]
    bidirectional: bool
    owner: str = Field(min_length=1)
    meter_no: OptionalText = None


class AcBos(_Strict):
    ocpds: list[Ocpd] = Field(default_factory=list)
    disconnects: list[AcDisconnect] = Field(default_factory=list)
    spds: list[Spd] = Field(default_factory=list)
    panels: list[Panel] = Field(min_length=1)
    main_breakers: list[MainBreaker] = Field(default_factory=list)
    point_of_connection: PointOfConnection
    meters: list[Meter] = Field(default_factory=list)


class Conductors(_Strict):
    qty: PositiveInt
    size: ConductorSize
    material: Literal["Cu"] = Field(description="Aluminium tables are not part of schema 0.1.0")
    insulation: str = Field(min_length=1)
    outer_diameter_mm: PositiveFloat | None = Field(
        default=None,
        description="Overall diameter with insulation, from the cable datasheet; without it the "
        "conduit fill (CON-006) uses NOM Chapter 10, Table 5 for TW/THW/THHW insulation",
    )


class Neutral(_Strict):
    size: ConductorSize


class Egc(_Strict):
    size: ConductorSize
    type: str = Field(min_length=1)


class Raceway(_Strict):
    type: str | None = None
    trade_size_mm: PositiveFloat | None = None
    rooftop_clearance_mm: float | None = Field(default=None, ge=0)
    ccc_count: PositiveInt | None = Field(
        default=None, description="Current-carrying conductors in the raceway"
    )
    ref: str | None = Field(default=None, description="Share the raceway of another circuit")


class Circuit(_Strict):
    id: str = Field(min_length=1)
    kind: Literal["pv_source", "pv_output", "inverter_output", "feeder", "battery", "mt_feeder"]
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    conductors: Conductors
    neutral: Neutral | None = None
    egc: Egc
    raceway: Raceway
    length_m: float = Field(gt=0, lt=1000)
    ambient_c: float | None = None


class Electrode(_Strict):
    type: str = Field(min_length=1)
    length_m: PositiveFloat
    qty: PositiveInt = 1


class Grounding(_Strict):
    dc_system: Literal["grounded", "ungrounded_690_35"]
    electrodes: list[Electrode] = Field(min_length=1)
    design_resistance_ohm: PositiveFloat = 25
    gec_ac: ConductorSize
    gec_dc: ConductorSize
    dc_ac_bond_method: str = Field(min_length=1)


class Storage(_Strict):
    coupling: Literal["none", "dc", "ac"] = "none"


class Monitoring(_Strict):
    gateway: str | None = None
    consumption_ct: bool = False


class Responsible(_Strict):
    name: OptionalText = None
    cedula_profesional: OptionalText = None
    company: OptionalText = None


class Revision(_Strict):
    rev: str = Field(min_length=1)
    date: date
    description: str = Field(min_length=1)
    by: str = Field(min_length=1)
    approved_by: str | None = None


class TitleBlock(_Strict):
    drawing_no: str = Field(min_length=1)
    sheet: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    date: date
    responsible: Responsible = Responsible()
    uvie: str | None = None
    drawn_by: str = Field(min_length=1)
    checked_by: OptionalText = None
    approved_by: str | None = None
    revisions: list[Revision] = Field(min_length=1)


class Layout(_Strict):
    template: Literal["bt_string_residential_v1", "a3_plantilla_v1"]
    """``a3_plantilla_v1`` places the schematic on the owner's sheet template (``pvsld.sheets``)."""
    sheet: Literal["A3"]
    flow: Literal["left_to_right"] = "left_to_right"
    symbol_style: Literal["IEC", "ANSI"] = "IEC"
    language: Literal["es"] = "es"


def _base_id(endpoint: str) -> str:
    """``INV1.A`` -> ``INV1``; an endpoint without a port is returned unchanged."""
    return endpoint.split(".", maxsplit=1)[0]


def _duplicates(ids: Iterable[str]) -> list[str]:
    return sorted(name for name, count in Counter(ids).items() if count > 1)


class PvSystemSpec(_Strict):
    """Root of the parameter model: one PV installation and the sheet that documents it."""

    schema_version: Literal["0.1.0"]
    project: Project
    standards: Standards
    utility: Utility
    modules: list[Module] = Field(min_length=1)
    inverters: list[Inverter] = Field(min_length=1)
    strings: list[StringSpec] = Field(min_length=1)
    dc_bos: DcBos
    ac_bos: AcBos
    circuits: list[Circuit] = Field(min_length=1)
    grounding: Grounding
    storage: Storage
    monitoring: Monitoring | None = None
    title_block: TitleBlock
    layout: Layout
    # Medium-voltage sections are accepted but not modelled in schema 0.1.0 (queued for ADR-0002).
    transformer: dict[str, Any] | None = None
    mt_protection: dict[str, Any] | None = None

    def component_ids(self) -> list[str]:
        """Every identifier that can be a circuit endpoint or a drawing component."""
        ids = [m.id for m in self.modules]
        ids += [i.id for i in self.inverters]
        ids += [s.id for s in self.strings]
        ids += [c.id for c in self.dc_bos.combiners]
        ids += [d.id for d in self.dc_bos.disconnects]
        ids += [s.id for s in self.dc_bos.spds]
        ids += [o.id for o in self.ac_bos.ocpds]
        ids += [d.id for d in self.ac_bos.disconnects]
        ids += [s.id for s in self.ac_bos.spds]
        ids += [p.id for p in self.ac_bos.panels]
        ids += [b.id for b in self.ac_bos.main_breakers]
        ids += [m.id for m in self.ac_bos.meters]
        ids.append(self.ac_bos.point_of_connection.id)
        return ids

    @model_validator(mode="after")
    def _check_references(self) -> PvSystemSpec:
        problems: list[str] = []
        known = set(self.component_ids())

        duplicated = _duplicates(self.component_ids())
        if duplicated:
            problems.append(f"duplicate component ids: {', '.join(duplicated)}")
        duplicated_circuits = _duplicates(c.id for c in self.circuits)
        if duplicated_circuits:
            problems.append(f"duplicate circuit ids: {', '.join(duplicated_circuits)}")

        modules = {m.id for m in self.modules}
        inverters = {i.id: i for i in self.inverters}
        for string in self.strings:
            if string.module not in modules:
                problems.append(f"string {string.id}: unknown module {string.module!r}")
            inverter = inverters.get(string.inverter)
            if inverter is None:
                problems.append(f"string {string.id}: unknown inverter {string.inverter!r}")
            elif string.mppt not in {m.id for m in inverter.mppt}:
                problems.append(
                    f"string {string.id}: inverter {inverter.id} has no MPPT {string.mppt!r}"
                )

        for circuit in self.circuits:
            for endpoint in (circuit.from_, circuit.to):
                if _base_id(endpoint) not in known:
                    problems.append(
                        f"circuit {circuit.id}: unknown endpoint {endpoint!r} "
                        f"({_base_id(endpoint)!r} is not a component id)"
                    )
        circuit_ids = {c.id for c in self.circuits}
        for circuit in self.circuits:
            ref = circuit.raceway.ref
            if ref is not None and ref not in circuit_ids:
                problems.append(f"circuit {circuit.id}: raceway ref to unknown circuit {ref!r}")

        panels = {p.id for p in self.ac_bos.panels}
        breakers = {b.id for b in self.ac_bos.main_breakers} | {o.id for o in self.ac_bos.ocpds}
        for panel in self.ac_bos.panels:
            if panel.main_ocpd not in breakers:
                problems.append(f"panel {panel.id}: unknown main_ocpd {panel.main_ocpd!r}")
        poc = self.ac_bos.point_of_connection
        if poc.panel not in panels:
            problems.append(f"point_of_connection: unknown panel {poc.panel!r}")
        if poc.breaker not in breakers:
            problems.append(f"point_of_connection: unknown breaker {poc.breaker!r}")
        for ocpd in self.ac_bos.ocpds:
            if ocpd.at is not None and _base_id(ocpd.at) not in known:
                problems.append(f"ocpd {ocpd.id}: unknown location {ocpd.at!r}")
        for disconnect in self.dc_bos.disconnects:
            if disconnect.integrated_in is not None and disconnect.integrated_in not in inverters:
                problems.append(
                    f"disconnect {disconnect.id}: unknown inverter {disconnect.integrated_in!r}"
                )
        for spd in [*self.dc_bos.spds, *self.ac_bos.spds]:
            if _base_id(spd.at) not in known:
                problems.append(f"spd {spd.id}: unknown location {spd.at!r}")

        if problems:
            raise ValueError("; ".join(problems))
        return self


def parse_spec(data: Mapping[str, Any]) -> PvSystemSpec:
    """Validate ``data`` (parsed YAML or JSON) and return the immutable model.

    Raises:
        pydantic.ValidationError: with one entry per offending field or cross reference.
    """
    return PvSystemSpec.model_validate(data)


def export_json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) of :class:`PvSystemSpec`, keyed by field aliases (``from``)."""
    schema = PvSystemSpec.model_json_schema(by_alias=True)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
