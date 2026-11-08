"""Inputs and outputs of the sizing engine.

``SizingRequest`` is what a person (or Claude, through the future MCP tool) provides. Everything the
engine returns is a frozen dataclass with a ``to_dict`` that produces plain JSON types.

The request has no site, utility or title-block fields of its own: it carries a ``template``, a
mapping in the format of the parameter model (:mod:`pvsld.core.model`) with everything that is not
sizing (project, client, site, utility, grounding, title block, layout, panel and point of
connection). The site temperatures and the service voltage are read from that template so there is a
single source of truth. A complete specification is also a valid template: the engine replaces its
``modules``, ``inverters``, ``strings``, ``circuits``, ``dc_bos.disconnects`` and ``ac_bos.ocpds``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pvsld.core.calc import DELTA_T_CELL_C
from pvsld.core.model import ConductorSize, Site, Utility
from pvsld.core.policy import DEFAULT_DC_AC_POLICY, DcAcPolicy
from pvsld.core.rules import Finding
from pvsld.core.severity import Severity

# Sections the template must provide; the engine cannot invent them.
REQUIRED_TEMPLATE_KEYS = (
    "project",
    "utility",
    "ac_bos",
    "grounding",
    "storage",
    "title_block",
    "layout",
)
SUPPORTED_SYSTEMS = {"1F-2H": 1, "2F-3H": 2}
"""Utility systems of this version (single phase, 127 V line-neutral or 220 V line-line)."""


class SizingInputError(ValueError):
    """The request cannot be sized (bad template, unknown component, unsupported service)."""


class Routing(BaseModel):
    """Installation data the sizing needs and no catalogue provides (defaults follow the sample)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dc_string_length_m: float = Field(default=25, gt=0, lt=1000, description="One-way string run")
    ac_output_length_m: float = Field(default=30, gt=0, lt=1000, description="Inverter to ITM run")
    dc_ambient_c: float | None = Field(default=None, description="Default: site maximum ambient")
    ac_ambient_c: float | None = Field(default=None, description="Default: site maximum ambient")
    rooftop_clearance_mm: float = Field(
        default=25, ge=0, description="Sunlit DC raceway above the roof (Table 310-15(b)(3)(c))"
    )
    dc_raceway_trade_size_mm: float | None = Field(
        default=None, gt=0, description="EMT designation (mm); default: the smallest that fits"
    )
    ac_raceway_trade_size_mm: float | None = Field(
        default=None, gt=0, description="EMT designation (mm); default: the smallest that fits"
    )
    tilt_deg: float = Field(default=20, ge=0, le=90)
    azimuth_deg: float = Field(default=180, ge=0, le=360)
    micro_spacing_m: float | None = Field(
        default=None,
        gt=0,
        lt=100,
        description="Trunk length between two microinverters; default 1.2 m per connected module",
    )


class SizingRequest(BaseModel):
    """One sizing job: which module and inverters, what to reach, and the non-sizing template."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    template: dict[str, Any]
    module: str = Field(min_length=1, description="Catalogue component_id of the PV module")
    inverters: list[str] | Literal["auto"] = Field(
        default="auto", description="Catalogue inverter ids to evaluate, or 'auto' for all"
    )
    target_dc_power_w: float | None = Field(default=None, gt=0)
    module_count_min: int | None = Field(default=None, gt=0, description="Total modules, lower")
    module_count_max: int | None = Field(default=None, gt=0, description="Total modules, upper")
    optimizers_on_all_modules: bool = False
    microinverters: list[str] | Literal["auto"] | None = Field(
        default=None,
        description="Design with microinverters: catalogue ids to evaluate or 'auto' for all; "
        "None (default) designs with string inverters (owner: micros only when asked for)",
    )
    micro_trunk_size: ConductorSize | None = Field(
        default=None,
        description="Microinverter designs: fix the trunk cable size of every branch (checked by "
        "MIC-011/013, not chosen; professional mode)",
    )
    ac_combiner: bool = Field(
        default=False,
        description="Microinverter designs: put the branch breakers in an AC combiner box with "
        "one main breaker to the load centre (owner: only on request)",
    )
    n_strings: int | None = Field(
        default=None, gt=0, description="Fix the number of strings (professional mode)"
    )
    n_series: int | None = Field(
        default=None, gt=0, description="Fix the modules per string (professional mode)"
    )
    dc_conductor_size: ConductorSize | None = Field(
        default=None, description="Fix the string conductor size; checked, not chosen"
    )
    ac_conductor_size: ConductorSize | None = Field(
        default=None, description="Fix the inverter-output conductor size; checked, not chosen"
    )
    main_breaker: str | None = Field(
        default=None,
        description="Catalogue AC breaker id of the service main (ITM-P); default: the catalogue "
        "device of its rating",
    )
    dc_ocpd: Literal["auto", "always"] = Field(
        default="always",
        description="'always' (default, owner decision 2026-10-08): one protection per string in "
        "the DC protection box; 'auto': only where NOM 690-9(a) needs one",
    )
    dc_ocpd_device: Literal["fuse", "breaker"] = Field(
        default="fuse",
        description="String protection of the box: 'fuse' (default, owner decision 2026-10-08), a "
        "gPV fuse in a fuse-disconnector, falling back to a breaker when no catalogue fuse "
        "qualifies; 'breaker': a DC breaker",
    )
    dc_cable: str | Literal["auto"] = Field(
        default="auto",
        description="Catalogue family id of the PV cable of the strings (its diameter fills the "
        "raceway), or 'auto' for the first PV cable family",
    )
    ac_breakers: list[str] | Literal["auto"] = Field(
        default="auto",
        description="Catalogue AC breaker ids for ITM-1 and ITM-P, or 'auto' for all",
    )
    dc_switches: list[str] | Literal["auto"] = Field(
        default="auto",
        description="Catalogue PV switch-disconnector ids for the box disconnect, or 'auto'",
    )
    dc_fuses: list[str] | Literal["auto"] = Field(
        default="auto", description="Catalogue gPV fuse ids to choose from, or 'auto' for all"
    )
    dc_fuse_fallback: bool = Field(
        default=True,
        description="When no listed gPV fuse fits, protect the strings with a DC breaker "
        "(default); false: reject the configuration instead (the user fixed the fuse)",
    )
    dc_breakers: list[str] | Literal["auto"] = Field(
        default="auto", description="Catalogue DC breaker ids to choose from, or 'auto' for all"
    )
    cell_temp_rise_c: float = Field(
        default=DELTA_T_CELL_C,
        gt=0,
        description="Cell temperature rise over the maximum ambient (rooftop array); the rule pack "
        "uses the default, so a different value can make the rule-pack gate reject candidates",
    )
    dc_ac_policy: DcAcPolicy = DEFAULT_DC_AC_POLICY
    routing: Routing = Routing()
    max_candidates: int = Field(default=10, ge=1, le=50)
    layout_template: Literal["a3_plantilla_v1", "bt_string_residential_v1"] = Field(
        default="a3_plantilla_v1",
        description="Layout template of the specifications produced (default the owner's sheet)",
    )

    @model_validator(mode="after")
    def _check_template_and_range(self) -> SizingRequest:
        missing = [key for key in REQUIRED_TEMPLATE_KEYS if key not in self.template]
        if missing:
            raise ValueError(
                f"template lacks {', '.join(missing)}; give the non-sizing sections of the "
                "parameter model (a complete specification also works)"
            )
        try:
            utility = Utility.model_validate(self.template["utility"])
            Site.model_validate(self.template["project"]["site"])
        except (ValidationError, KeyError, TypeError) as error:
            raise ValueError(f"template: invalid project.site or utility: {error}") from error
        if utility.system not in SUPPORTED_SYSTEMS:
            raise ValueError(
                f"template: utility.system {utility.system!r} is not supported; this version sizes "
                f"single-phase services {sorted(SUPPORTED_SYSTEMS)}"
            )
        low, high = self.module_count_min, self.module_count_max
        if low is not None and high is not None and low > high:
            raise ValueError(f"module_count_min ({low}) is above module_count_max ({high})")
        return self

    @property
    def site(self) -> Site:
        return Site.model_validate(self.template["project"]["site"])

    @property
    def utility(self) -> Utility:
        return Utility.model_validate(self.template["utility"])


# --- Results -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """One finding of the engine: rule id (vault catalogue), severity, Spanish message."""

    rule_id: str
    severity: Severity
    message_es: str
    subject: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "subject": self.subject,
            "message_es": self.message_es,
        }

    @classmethod
    def from_finding(cls, finding: Finding) -> Issue:
        return cls(finding.rule_id, finding.severity, finding.message_es, finding.subject)


@dataclass(frozen=True)
class StringConfig:
    """``n_strings`` identical strings of ``n_series`` modules on one inverter."""

    inverter_id: str
    n_strings: int
    n_series: int

    @property
    def label(self) -> str:
        return f"{self.n_strings}×{self.n_series}"

    @property
    def n_modules(self) -> int:
        return self.n_strings * self.n_series

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "label": self.label, "n_modules": self.n_modules}


@dataclass(frozen=True)
class StringMetrics:
    """Electrical values of a configuration (the numbers behind each check)."""

    p_dc_w: float
    dc_ac_ratio: float
    voc_cold_string_v: float
    vmp_hot_string_v: float
    vmp_cold_string_v: float
    isc_design_a: float
    isc_input_a: float
    imp_a: float
    strings_per_mppt: int
    clipping_pct: float
    deliverable_dc_w: float

    def to_dict(self) -> dict[str, Any]:
        return {key: round(value, 3) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class Rejection:
    """A configuration (or a whole inverter, when ``config.n_strings`` is 0) the engine refuses."""

    config: StringConfig
    issues: tuple[Issue, ...]
    stage: Literal["inverter", "strings", "bos", "rule_pack"] = "strings"
    metrics: StringMetrics | None = None

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def rule_id(self) -> str:
        """The first (primary) rule that rejects the configuration."""
        return self.errors[0].rule_id

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(i.rule_id for i in self.errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "inverter": self.config.inverter_id,
            "config": self.config.label if self.config.n_strings else None,
            "stage": self.stage,
            "rule_ids": list(self.rule_ids),
            "reasons": [i.message_es for i in self.errors],
        }


@dataclass(frozen=True)
class DcOcpdChoice:
    """String overcurrent protection: needed or not, and the catalogue device chosen.

    ``device`` says what ``device_id`` is: a DC breaker, or a gPV fuse in a fuse-disconnector (the
    default of the DC protection box, owner decision 2026-10-08).
    """

    required: bool
    reason_es: str
    device_id: str | None = None
    device: Literal["breaker", "fuse"] = "breaker"
    rating_a: float | None = None
    ue_v: float | None = None
    poles: int = 2
    icu_ka: float | None = None
    minimum_rating_a: float | None = None
    rejected: tuple[tuple[str, str], ...] = ()

    def to_dict(self, *, with_alternatives: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not with_alternatives:
            data.pop("rejected")
        else:
            data["rejected"] = [{"id": name, "reason": why} for name, why in self.rejected]
        return data


@dataclass(frozen=True)
class ConductorChoice:
    """Conductor size of one circuit type and the numbers that decided it."""

    circuit_id: str
    kind: str
    size: str
    i_max_a: float
    ocpd_a: float | None
    ampacity_75_a: float
    ampacity_corrected_a: float
    vd_pct: float
    vd_limit_pct: float
    length_m: float

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass(frozen=True)
class DeviceChoice:
    """A catalogue device chosen for one position (ITM-1, ITM-P, DCD-CD1)."""

    position: str
    device_id: str
    rating_a: float
    voltage_v: float
    interrupting_ka: float | None = None
    note_es: str = ""
    poles: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RacewayChoice:
    """The EMT raceway of one circuit type: what it holds and the designation chosen."""

    circuit_id: str
    type: str
    trade_size_mm: float | None
    trade_size_in: str | None
    conductors: int
    area_mm2: float | None
    fill_pct: float | None
    fill_limit_pct: float
    insulation: str
    cable_id: str | None = None
    outer_diameter_mm: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass(frozen=True)
class BosSpec:
    """Balance of system of one candidate: string OCPD, AC OCPD, conductors and raceways."""

    dc_ocpd: DcOcpdChoice
    ac_ocpd_a: float
    conductors: tuple[ConductorChoice, ...]
    todo: tuple[str, ...] = ()
    raceways: tuple[RacewayChoice, ...] = ()
    devices: tuple[DeviceChoice, ...] = ()
    """Catalogue devices for ITM-1, ITM-P and the box disconnect (absent when none fits)."""

    def device(self, position: str) -> DeviceChoice | None:
        return next((d for d in self.devices if d.position == position), None)

    def to_dict(self, *, with_alternatives: bool = False) -> dict[str, Any]:
        return {
            "dc_ocpd": self.dc_ocpd.to_dict(with_alternatives=with_alternatives),
            "ac_ocpd_a": self.ac_ocpd_a,
            "conductors": [c.to_dict() for c in self.conductors],
            "raceways": [r.to_dict() for r in self.raceways],
            "devices": [d.to_dict() for d in self.devices],
            "todo": list(self.todo),
        }


@dataclass(frozen=True)
class Candidate:
    """A configuration that passed every check, with its BOS and its full specification."""

    rank: int
    config: StringConfig
    metrics: StringMetrics
    issues: tuple[Issue, ...]
    bos: BosSpec
    findings: tuple[Finding, ...]
    spec: dict[str, Any] = field(repr=False)
    explanation_es: str = ""
    """Why this inverter, in one or two plain Spanish sentences (shown to the user)."""

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    def to_dict(self, *, include_spec: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "rank": self.rank,
            "inverter": self.config.inverter_id,
            "explanation_es": self.explanation_es,
            "config": self.config.to_dict(),
            "metrics": self.metrics.to_dict(),
            "issues": [i.to_dict() for i in self.issues],
            "bos": self.bos.to_dict(with_alternatives=include_spec),
            "rule_pack": {
                "errors": 0,
                "warnings": sum(1 for f in self.findings if f.severity is Severity.WARNING),
                "findings": [f.to_dict() for f in self.findings],
            },
        }
        if include_spec:
            # dates parsed from YAML become ISO strings, which the model accepts back
            data["spec"] = json.loads(json.dumps(self.spec, default=str))
        return data


@dataclass(frozen=True)
class SizingResult:
    """Ranked candidates (best first), every rejection with its rule, and the selected design."""

    module_id: str
    objective: str
    target_dc_power_w: float | None
    candidates: tuple[Any, ...]
    """``Candidate`` (string inverters) or ``MicroCandidate`` (microinverters), best first."""
    rejected: tuple[Rejection, ...]
    assumptions: tuple[str, ...]
    inverters_evaluated: tuple[str, ...]

    @property
    def selected(self) -> Any:
        return self.candidates[0] if self.candidates else None

    @property
    def ok(self) -> bool:
        return self.selected is not None

    @property
    def spec(self) -> dict[str, Any] | None:
        """Full parameter specification of the selected design (``validate_pv_design`` input)."""
        return self.selected.spec if self.selected else None

    def to_dict(self, *, include_spec: bool = True) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "module": self.module_id,
            "objective": self.objective,
            "target_dc_power_w": self.target_dc_power_w,
            "inverters_evaluated": list(self.inverters_evaluated),
            "selected": self.selected.to_dict(include_spec=include_spec) if self.selected else None,
            "alternatives": [c.to_dict() for c in self.candidates[1:]],
            "rejected": [r.to_dict() for r in self.rejected],
            "assumptions": list(self.assumptions),
        }
