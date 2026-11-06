"""Reproducible sequential batch execution for a frozen experiment configuration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .agent import run_agent
from .environment import TransportEnvironment
from .evaluation import evaluate_trajectory
from .models import ModelAPIError, ModelConfigurationError, model_filename
from .scenarios import load_scenarios
from .schemas import ReviewStatus


class HumanGoldConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int = 120
    calibration: int = 36
    validation: int = 24
    blind_test: int = 60
    annotations_per_trajectory: int = 2
    adjudicate_disagreements: bool = True


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    scenario_path: Path
    evaluated_models: tuple[str, ...]
    scenario_generator_model: str
    semantic_judge_model: str
    temperature: float = 0
    runs_per_model_scenario: int = Field(default=1, ge=1)
    max_tool_calls: int = Field(default=6, ge=1)
    continue_after_question: bool = False
    human_gold: HumanGoldConfig | None = None
    judge_gate: dict[str, float]


def load_experiment_config(path: Path) -> ExperimentConfig:
    return ExperimentConfig.model_validate_json(path.read_text(encoding="utf-8"))


def preflight_experiment(config: ExperimentConfig) -> dict[str, object]:
    scenarios = load_scenarios(config.scenario_path)
    pending = [
        scenario.scenario_id
        for scenario in scenarios
        if scenario.dataset_metadata is None
        or scenario.dataset_metadata.review_status != ReviewStatus.APPROVED
    ]
    return {
        "experiment_id": config.experiment_id,
        "scenario_count": len(scenarios),
        "model_count": len(config.evaluated_models),
        "planned_runs": len(scenarios) * len(config.evaluated_models),
        "unapproved_scenario_count": len(pending),
        "ready": (
            bool(scenarios)
            and bool(config.evaluated_models)
            and len(set(config.evaluated_models)) == len(config.evaluated_models)
            and config.runs_per_model_scenario == 1
            and not pending
        ),
    }


def run_batch(
    config: ExperimentConfig,
    output_root: Path,
    selected_models: set[str] | None = None,
    resume: bool = False,
) -> dict[str, int]:
    """Run each configured model once per scenario, with no fallback or silent retry."""

    if config.runs_per_model_scenario != 1:
        raise ValueError("exactly one run per model-scenario is supported")
    scenarios = load_scenarios(config.scenario_path)
    if not scenarios or not config.evaluated_models:
        raise ValueError("experiments require scenarios and evaluated models")
    if len(set(config.evaluated_models)) != len(config.evaluated_models):
        raise ValueError("evaluated model identifiers must be unique")
    unapproved = [
        scenario.scenario_id
        for scenario in scenarios
        if scenario.dataset_metadata is None
        or scenario.dataset_metadata.review_status != ReviewStatus.APPROVED
    ]
    if unapproved:
        raise ValueError(
            f"refusing to run {len(unapproved)} scenarios without human approval; "
            "review the ten family records and regenerate the dataset"
        )
    models = [
        model for model in config.evaluated_models if selected_models is None or model in selected_models
    ]
    if selected_models and set(models) != selected_models:
        unknown = sorted(selected_models - set(models))
        raise ValueError(f"models are not in the frozen roster: {unknown}")

    summary = {"completed": 0, "failed": 0, "skipped": 0}
    for model in models:
        model_directory = output_root / model_filename(model)
        for scenario in scenarios:
            output_path = model_directory / f"{scenario.scenario_id}.json"
            if output_path.exists():
                if resume:
                    summary["skipped"] += 1
                    continue
                raise FileExistsError(
                    f"run record already exists: {output_path}; use resume or a new output root"
                )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(UTC).isoformat()
            try:
                environment = TransportEnvironment(
                    scenario, continue_after_question=config.continue_after_question
                )
                trace, assistant_messages = run_agent(
                    environment=environment,
                    run_id=f"{scenario.scenario_id}_{uuid4().hex[:8]}",
                    model_name=model,
                    max_tool_calls=config.max_tool_calls,
                    temperature=config.temperature,
                )
                evaluation = evaluate_trajectory(scenario, trace, assistant_messages)
                status = (
                    "failed" if trace.termination_reason.startswith("incomplete_") else "completed"
                )
                record = {
                    "record_schema_version": "0.2",
                    "experiment_id": config.experiment_id,
                    "status": status,
                    "run_timestamp_utc": timestamp,
                    "scenario_id": scenario.scenario_id,
                    "assistant_messages": assistant_messages,
                    "evaluation": evaluation.model_dump(mode="json"),
                    "trace": trace.model_dump(mode="json"),
                }
                summary[status] += 1
            except (ModelConfigurationError, ModelAPIError, OSError) as error:
                record = {
                    "record_schema_version": "0.2",
                    "experiment_id": config.experiment_id,
                    "status": "failed",
                    "run_timestamp_utc": timestamp,
                    "scenario_id": scenario.scenario_id,
                    "model_selection": model,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
                summary["failed"] += 1
            except Exception as error:  # noqa: BLE001 - preserve a batch failure without secrets.
                record = {
                    "record_schema_version": "0.2",
                    "experiment_id": config.experiment_id,
                    "status": "failed",
                    "run_timestamp_utc": timestamp,
                    "scenario_id": scenario.scenario_id,
                    "model_selection": model,
                    "error_type": type(error).__name__,
                    "error": "Unexpected model/runtime error; details omitted.",
                }
                summary["failed"] += 1
            record["scenario_schema_version"] = scenario.schema_version
            record["dataset_metadata"] = scenario.dataset_metadata.model_dump(mode="json")
            record["experimental_conditions"] = scenario.experimental_conditions.model_dump()
            output_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return summary
