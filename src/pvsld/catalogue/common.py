"""Building blocks shared by every record type: strict base model, provenance, small validators.

Units are stored as printed on the datasheet and named in the field (``_v``, ``_a``, ``_w``,
``_wp``, ``_va``, ``_mm``, ``_kg``, ``_c``, ``_pct``, ``_ka``); the sizing engine converts them
where needed (for example temperature coefficients stay in %/degC).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Plausibility tolerances of ADR-0004.
POWER_TOLERANCE = 0.02  # |Vmp x Imp - Pmax| / Pmax
EFFICIENCY_TOLERANCE_PT = 0.3  # |datasheet efficiency - Pmax / area| in percentage points

PositiveFloat = Annotated[float, Field(gt=0)]
PositiveInt = Annotated[int, Field(gt=0)]
Percent = Annotated[float, Field(gt=0, le=100)]
# UPPERCASE ids are URL-safe and match the symbol naming of ADR-0003 (open question 2 of ADR-0004).
ComponentId = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9._-]*$", examples=["JINKO-JKM650N"])]
Range = tuple[float, float]


class Strict(BaseModel):
    """Base of every record model: unknown fields are errors, instances are immutable."""

    # ``protected_namespaces=()`` because datasheets have a ``model_family`` field.
    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())


class Provenance(Strict):
    """Where a record comes from and who approved it (ADR-0004, driver D5).

    The owner's PDF is never committed; the record keeps its SHA-256 so the extraction can be
    audited against the original file. ``reviewed_by`` stays ``None`` until the owner has compared
    every value with the datasheet; the registry loads only reviewed records by default.
    """

    filename: str = Field(min_length=1)
    url: str | None = Field(
        default=None,
        pattern=r"^https://\S+$",
        description="Official download address of the datasheet (required in catalogue PRs)",
    )
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="SHA-256 of the owner's PDF")
    title: str = Field(min_length=1, description="Title, revision and date as printed")
    pages: list[PositiveInt] = Field(min_length=1)
    extraction_method: Literal["markitdown", "rendered_page", "pdf_text_layer"]
    extraction_date: date
    reviewed_by: str | None = Field(description="None until the owner approves the record")
    review_date: date | None = Field(description="None until the owner approves the record")
    notes: str | None = None

    @model_validator(mode="after")
    def _review_is_complete(self) -> Provenance:
        if (self.reviewed_by is None) != (self.review_date is None):
            raise ValueError("reviewed_by and review_date must be both set or both null")
        if self.review_date is not None and self.review_date < self.extraction_date:
            raise ValueError(
                f"review_date {self.review_date} is before extraction_date {self.extraction_date}"
            )
        return self

    @property
    def reviewed(self) -> bool:
        """True once the owner has approved the record."""
        return self.reviewed_by is not None


def check_range(name: str, value: Range, *, allow_equal: bool = False) -> str | None:
    """Return a problem text unless ``value`` is an ordered (low, high) pair."""
    low, high = value
    if low > high or (low == high and not allow_equal):
        return f"{name} must be an ascending (low, high) pair, got ({low:g}, {high:g})"
    return None


def raise_problems(problems: list[str]) -> None:
    """Raise one ``ValueError`` that lists every problem (Pydantic wraps it with the location)."""
    if problems:
        raise ValueError("; ".join(problems))


def duplicates(values: list[str]) -> list[str]:
    """Return the values that appear more than once, in first-seen order."""
    seen: set[str] = set()
    repeated: dict[str, None] = {}
    for value in values:
        if value in seen:
            repeated[value] = None
        seen.add(value)
    return list(repeated)
