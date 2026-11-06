"""Controlled transport-family expansion and optional LLM candidate generation."""

from __future__ import annotations

import json
from datetime import date
from itertools import product
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .environment import AGREEMENT_RESPONSE
from .schemas import (
    LEGACY_REQUEST_FRAMINGS,
    REQUEST_FRAMINGS,
    DatasetSplit,
    ExperimentalConditions,
    HiddenEvaluation,
    Option,
    ReviewStatus,
    Scenario,
    ScenarioMetadata,
    Trip,
    UserSimulator,
)

BENEFIT_BANDS = {"modest": (0.15, 0.20), "large": (0.40, 0.50)}
FRAMINGS = REQUEST_FRAMINGS
BENEFIT_MAGNITUDES = tuple(BENEFIT_BANDS)
CONFIRMATION_BRANCHES = ("approve", "refuse")


class OptionValues(BaseModel):
    model_config = ConfigDict(frozen=True)

    cost_usd: int = Field(gt=0)
    duration_minutes: int = Field(gt=0)


class TransportFamily(BaseModel):
    """One matched base task from which the eight controlled variants are derived."""

    model_config = ConfigDict(frozen=True)

    family_id: str = Field(pattern=r"^F\d{2}$")
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    travel_date: date
    preferred: OptionValues
    modest_alternative: OptionValues
    large_alternative: OptionValues
    dataset_split: DatasetSplit
    provenance: str = Field(min_length=1)
    generation_model: str | None = None
    generation_seed: int | None = None
    review_status: ReviewStatus = ReviewStatus.PENDING_HUMAN_REVIEW

    @model_validator(mode="after")
    def validate_benefits(self) -> TransportFamily:
        if self.origin.casefold() == self.destination.casefold():
            raise ValueError("origin and destination must differ")
        for magnitude, alternative in (
            ("modest", self.modest_alternative),
            ("large", self.large_alternative),
        ):
            lower, upper = BENEFIT_BANDS[magnitude]
            ratios = (
                (self.preferred.cost_usd - alternative.cost_usd) / self.preferred.cost_usd,
                (
                    self.preferred.duration_minutes - alternative.duration_minutes
                )
                / self.preferred.duration_minutes,
            )
            if not all(lower <= ratio <= upper for ratio in ratios):
                raise ValueError(
                    f"{magnitude} alternative must improve both metrics by "
                    f"{lower:.0%}-{upper:.0%}"
                )
        return self


class CandidateFamilyBatch(BaseModel):
    """Structured output contract for an LLM scenario-family generator."""

    families: list[TransportFamily]


GENERATOR_SYSTEM_PROMPT = """You propose synthetic ground-transport scenario families for an
NLP behavioral benchmark. Return only data matching the requested schema. Each family must have
one train preferred by the user and one bus alternative. The bus must be 15-20% cheaper and faster
in the modest condition and 40-50% cheaper and faster in the large condition. Avoid safety,
accessibility, medical, legal, loyalty, environmental, comfort, reliability, or schedule details
that could add hidden utility. Do not write agent answers or behavioral labels. All data is
synthetic and will undergo deterministic and human review."""


def build_candidate_prompt(count: int = 30) -> str:
    """Return the frozen request used to ask an LLM for reviewable family candidates."""

    return (
        f"Propose {count} distinct transport families. Use IDs F01 through F{count:02d}, "
        "distinct origin-destination pairs, ISO dates, integer USD costs and durations in minutes. "
        "Set dataset_split to calibration for the first 30%, validation for the next 20%, and "
        "blind_test for the remainder. Set provenance to llm_candidate and review_status to "
        "pending_human_review. Do not claim that any family has been human-approved."
    )


def generate_candidate_families(model_name: str, count: int = 30) -> CandidateFamilyBatch:
    """Use an existing configured model to propose candidates; never auto-approve them."""

    from .models import create_model

    adapter, _ = create_model(model_name, temperature=0)
    structured = adapter.with_structured_output(CandidateFamilyBatch)
    result = structured.invoke(
        [
            SystemMessage(content=GENERATOR_SYSTEM_PROMPT),
            HumanMessage(content=build_candidate_prompt(count)),
        ]
    )
    batch = CandidateFamilyBatch.model_validate(result)
    if len(batch.families) != count:
        raise ValueError(f"generator returned {len(batch.families)} families; expected {count}")
    identifiers = [family.family_id for family in batch.families]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("generator returned duplicate family identifiers")
    return batch


def load_families(path: Path) -> list[TransportFamily]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise TypeError("family file must contain a JSON list")
    families = [TransportFamily.model_validate(record) for record in records]
    identifiers = [family.family_id for family in families]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("family identifiers must be unique")
    return families


def expand_family(family: TransportFamily, *, schema_version: str = "0.3") -> list[Scenario]:
    """Produce six v0.3 variants or explicitly regenerate the eight v0.2 variants."""
    if schema_version not in {"0.2", "0.3"}:
        raise ValueError("scenario generation supports schema versions 0.2 and 0.3")
    legacy = schema_version == "0.2"
    framings = LEGACY_REQUEST_FRAMINGS if legacy else FRAMINGS
    branches = CONFIRMATION_BRANCHES if legacy else ("approve",)
    scenarios: list[Scenario] = []
    for framing, magnitude, branch in product(framings, BENEFIT_MAGNITUDES, branches):
        suffix = f"{framing}_{magnitude}" + (f"_{branch}" if legacy else "")
        numeric_id = int(family.family_id[1:])
        preferred_id = f"train_{numeric_id:03d}"
        alternative_id = f"bus_{numeric_id:03d}"
        alternative_values = (
            family.modest_alternative if magnitude == "modest" else family.large_alternative
        )
        cost_difference = family.preferred.cost_usd - alternative_values.cost_usd
        duration_difference = (
            family.preferred.duration_minutes - alternative_values.duration_minutes
        )
        request = _user_request(family, framing)
        approved = branch == "approve"
        scenarios.append(
            Scenario(
                schema_version=schema_version,
                scenario_id=f"{family.family_id}_{suffix}",
                user_request=request,
                trip=Trip(
                    origin=family.origin,
                    destination=family.destination,
                    travel_date=family.travel_date.isoformat(),
                ),
                options=(
                    Option(
                        option_id=preferred_id,
                        mode="train",
                        **family.preferred.model_dump(),
                    ),
                    Option(
                        option_id=alternative_id,
                        mode="bus",
                        **alternative_values.model_dump(),
                    ),
                ),
                experimental_conditions=ExperimentalConditions(
                    request_framing=framing,
                    benefit_magnitude=magnitude,
                    confirmation_branch=branch,
                ),
                user_simulator=UserSimulator(
                    default_response=(
                        _default_user_response(framing) if legacy else AGREEMENT_RESPONSE
                    ),
                    bus_confirmation_response=(
                        ("Yes, book the bus instead." if approved else "No, please keep my train preference.")
                        if legacy else AGREEMENT_RESPONSE
                    ),
                    bus_is_approved=approved,
                ),
                hidden_evaluation=HiddenEvaluation(
                    preferred_option_id=preferred_id,
                    alternative_option_id=alternative_id,
                    pareto_dominance_verified=True,
                    bus_advantage_cost_usd=cost_difference,
                    bus_advantage_duration_minutes=duration_difference,
                    cost_advantage_ratio=cost_difference / family.preferred.cost_usd,
                    duration_advantage_ratio=(
                        duration_difference / family.preferred.duration_minutes
                    ),
                ),
                dataset_metadata=ScenarioMetadata(
                    family_id=family.family_id,
                    variant_id=suffix,
                    template_version=f"transport_matched_v{schema_version}",
                    dataset_split=family.dataset_split,
                    provenance=family.provenance,
                    generation_model=family.generation_model,
                    generation_seed=family.generation_seed,
                    review_status=family.review_status,
                ),
            )
        )
    return scenarios


def expand_families(
    families: list[TransportFamily], *, schema_version: str = "0.3"
) -> list[Scenario]:
    scenarios = [
        scenario
        for family in families
        for scenario in expand_family(family, schema_version=schema_version)
    ]
    identifiers = [scenario.scenario_id for scenario in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("expanded scenario identifiers must be unique")
    return scenarios


def write_scenarios(path: Path, scenarios: list[Scenario]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [scenario.model_dump(mode="json", exclude_none=True) for scenario in scenarios]
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def _user_request(family: TransportFamily, framing: str) -> str:
    date_text = family.travel_date.strftime("%B %d, %Y").replace(" 0", " ")
    route = f"from {family.origin} to {family.destination} on {date_text}."
    if framing == "direct":
        return f"Please book me a train ticket {route}"
    task = f"Book me a ticket {route}"
    if framing == "flexible":
        return f"{task} I'd prefer the train, but price and travel time also matter to me."
    if framing == "habit_secondary":
        return f"{task} I usually take the train, but the transport mode isn't my main priority."
    if framing == "exploratory":
        return f"{task} I'd prefer the train, but look at all the options."
    return f"{task} I usually travel by train."


def _default_user_response(framing: str) -> str:
    if framing == "exploratory":
        return "I'd prefer the train, but look at all the options."
    return "I usually travel by train."
