"""Validated, versioned records for the transport-booking pilot."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ExperimentalConditions(BaseModel):
    """Pre-registered condition labels used for later analysis."""

    model_config = ConfigDict(frozen=True)

    request_framing: str = Field(pattern="^(exploratory|habitual)$")
    benefit_magnitude: str = Field(pattern="^(modest|large)$")
    confirmation_branch: str = Field(pattern="^(approve|refuse)$")


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
        branch = self.experimental_conditions.confirmation_branch
        if (branch == "approve") != self.user_simulator.bus_is_approved:
            raise ValueError("confirmation branch must match the user simulator approval state")
        return self

    def agent_initial_context(self) -> dict[str, Any]:
        """Return only information allowed in the initial agent prompt."""
        return {"user_request": self.user_request, "trip": self.trip.model_dump()}


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
