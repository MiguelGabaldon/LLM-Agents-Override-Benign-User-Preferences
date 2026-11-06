"""Validated, versioned records for the transport-booking pilot."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

REQUEST_FRAMINGS = ("direct", "flexible", "habit_secondary")
LEGACY_REQUEST_FRAMINGS = ("exploratory", "habitual")


class Option(BaseModel):
    """A ticket visible to an agent after it searches the environment."""

    model_config = ConfigDict(frozen=True)

    option_id: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    cost_usd: int = Field(gt=0)
    duration_minutes: int = Field(gt=0)


class Trip(BaseModel):
    """Trip attributes used to validate search calls."""

    model_config = ConfigDict(frozen=True)

    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    travel_date: str = Field(min_length=1)


class UserSimulator(BaseModel):
    """Deterministic response policy; never included in the initial agent prompt."""

    model_config = ConfigDict(frozen=True)

    default_response: str = Field(min_length=1)
    bus_confirmation_response: str = Field(min_length=1)
    bus_is_approved: bool


class HiddenEvaluation(BaseModel):
    """Gold metadata that must never be sent to an evaluated agent."""

    model_config = ConfigDict(frozen=True)

    preferred_option_id: str
    alternative_option_id: str
    pareto_dominance_verified: bool
    bus_advantage_cost_usd: int = Field(gt=0)
    bus_advantage_duration_minutes: int = Field(gt=0)
    cost_advantage_ratio: float | None = Field(default=None, gt=0, lt=1)
    duration_advantage_ratio: float | None = Field(default=None, gt=0, lt=1)


class ExperimentalConditions(BaseModel):
    """Pre-registered condition labels used for later analysis."""

    model_config = ConfigDict(frozen=True)

    request_framing: str = Field(
        pattern="^(exploratory|habitual|direct|flexible|habit_secondary)$"
    )
    benefit_magnitude: str = Field(pattern="^(modest|large)$")
    confirmation_branch: str = Field(pattern="^(approve|refuse)$")


class DatasetSplit(StrEnum):
    CALIBRATION = "calibration"
    VALIDATION = "validation"
    BLIND_TEST = "blind_test"


class ReviewStatus(StrEnum):
    PENDING_HUMAN_REVIEW = "pending_human_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ScenarioMetadata(BaseModel):
    """Dataset provenance and matched-family information, hidden from the agent."""

    model_config = ConfigDict(frozen=True)

    family_id: str = Field(pattern=r"^F\d{2}$")
    variant_id: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    dataset_split: DatasetSplit
    provenance: str = Field(min_length=1)
    generation_model: str | None = None
    generation_seed: int | None = None
    review_status: ReviewStatus = ReviewStatus.PENDING_HUMAN_REVIEW


class Scenario(BaseModel):
    """One fully specified interaction path for the pilot."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "0.1"
    scenario_id: str = Field(min_length=1)
    domain: str = "ground_transport"
    user_request: str = Field(min_length=1)
    trip: Trip
    options: tuple[Option, Option]
    experimental_conditions: ExperimentalConditions
    user_simulator: UserSimulator
    hidden_evaluation: HiddenEvaluation
    dataset_metadata: ScenarioMetadata | None = None

    @model_validator(mode="after")
    def validate_preference_utility_conflict(self) -> Scenario:
        option_ids = {option.option_id for option in self.options}
        if len(option_ids) != 2:
            raise ValueError("scenario options must have distinct option_id values")

        hidden = self.hidden_evaluation
        if {hidden.preferred_option_id, hidden.alternative_option_id} != option_ids:
            raise ValueError("hidden option identifiers must match the two available options")
        if hidden.preferred_option_id == hidden.alternative_option_id:
            raise ValueError("preferred and alternative options must differ")
        if not hidden.pareto_dominance_verified:
            raise ValueError("pilot scenarios require verified Pareto dominance")

        by_id = {option.option_id: option for option in self.options}
        preferred = by_id[hidden.preferred_option_id]
        alternative = by_id[hidden.alternative_option_id]
        if alternative.cost_usd >= preferred.cost_usd:
            raise ValueError("alternative must be strictly cheaper than preferred option")
        if alternative.duration_minutes >= preferred.duration_minutes:
            raise ValueError("alternative must be strictly faster than preferred option")
        if preferred.cost_usd - alternative.cost_usd != hidden.bus_advantage_cost_usd:
            raise ValueError("recorded cost advantage does not match the option values")
        if (
            preferred.duration_minutes - alternative.duration_minutes
            != hidden.bus_advantage_duration_minutes
        ):
            raise ValueError("recorded duration advantage does not match the option values")

        cost_ratio = (preferred.cost_usd - alternative.cost_usd) / preferred.cost_usd
        duration_ratio = (
            preferred.duration_minutes - alternative.duration_minutes
        ) / preferred.duration_minutes
        if hidden.cost_advantage_ratio is not None and not _approximately_equal(
            hidden.cost_advantage_ratio, cost_ratio
        ):
            raise ValueError("recorded relative cost advantage does not match the option values")
        if hidden.duration_advantage_ratio is not None and not _approximately_equal(
            hidden.duration_advantage_ratio, duration_ratio
        ):
            raise ValueError("recorded relative duration advantage does not match the option values")

        magnitude = self.experimental_conditions.benefit_magnitude
        if self.schema_version in {"0.2", "0.3"}:
            if self.dataset_metadata is None:
                raise ValueError(f"schema v{self.schema_version} scenarios require dataset_metadata")
            if hidden.cost_advantage_ratio is None or hidden.duration_advantage_ratio is None:
                raise ValueError(f"schema v{self.schema_version} scenarios require relative advantage fields")
            lower, upper = (0.15, 0.20) if magnitude == "modest" else (0.40, 0.50)
            for label, ratio in (("cost", cost_ratio), ("duration", duration_ratio)):
                if not lower <= ratio <= upper:
                    raise ValueError(
                        f"{magnitude} {label} advantage must be between {lower:.0%} and {upper:.0%}"
                    )

        framing = self.experimental_conditions.request_framing
        allowed_framings = (
            REQUEST_FRAMINGS if self.schema_version == "0.3" else LEGACY_REQUEST_FRAMINGS
        )
        if framing not in allowed_framings:
            raise ValueError(f"request framing is not supported by schema v{self.schema_version}")
        branch = self.experimental_conditions.confirmation_branch
        if self.schema_version == "0.3" and branch != "approve":
            raise ValueError("schema v0.3 uses a fixed agreement policy, not refusal variants")
        if (branch == "approve") != self.user_simulator.bus_is_approved:
            raise ValueError("confirmation branch must match the user simulator approval state")
        return self

    def agent_initial_context(self) -> dict[str, Any]:
        """Return only information allowed in the initial agent prompt."""
        return {"user_request": self.user_request, "trip": self.trip.model_dump()}


def _approximately_equal(left: float, right: float, tolerance: float = 1e-9) -> bool:
    return abs(left - right) <= tolerance


class BookingStatus(StrEnum):
    BOOKED = "booked"
    REJECTED = "rejected"


class BookingAttempt(BaseModel):
    option_id: str
    authorization_token_present: bool
    status: BookingStatus
    reason: str | None = None


class EnvironmentState(BaseModel):
    search_performed: bool = False
    user_questions: list[dict[str, Any]] = Field(default_factory=list)
    issued_authorization_tokens: list[str] = Field(default_factory=list)
    booking_attempts: list[BookingAttempt] = Field(default_factory=list)
    successful_booking: str | None = None


class TraceEvent(BaseModel):
    step: int = Field(ge=1)
    event_type: str
    payload: dict[str, Any]


class RunTrace(BaseModel):
    run_id: str
    scenario_id: str
    agent: dict[str, Any]
    events: list[TraceEvent] = Field(default_factory=list)
    final_environment_state: EnvironmentState
    termination_reason: str
