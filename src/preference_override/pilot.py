"""Minimal pilot: build the task file from the families, then run a model over it.

    python -m preference_override.pilot build
    python -m preference_override.pilot run --model qwen3:4b --split calibration --limit 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .generation import TransportFamily, load_families
from .schemas import (
    ExperimentalConditions,
    HiddenEvaluation,
    Option,
    Scenario,
    ScenarioMetadata,
    Trip,
)

FAMILIES_PATH = Path("data/scenarios/transport_families_v0_2.json")
PROMPTS_PATH = Path("data/scenarios/prompts.txt")
TASKS_PATH = Path("data/scenarios/transport_tasks_v0_3.json")
RUNS_DIR = Path("runs")
SEED = 20261114

# prompts.txt line number -> preference strength. PROPOSAL, pending team review.
# Excluded on purpose: wordings that tell the agent whether to ask/book (T20-T24) and
# wordings that add an explicit cost/time goal (T11-T19, T22, T26-T28, T30).
SELECTED_TEMPLATES = {
    "T02": "weak", "T04": "weak", "T08": "weak",
    "T01": "moderate", "T05": "moderate", "T07": "moderate",
    "T03": "strong", "T06": "strong", "T29": "strong",
}


def load_prompt_templates(path: Path = PROMPTS_PATH) -> dict[str, str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    texts = [line.strip('"') for line in lines if line]
    return {f"T{index:02d}": text for index, text in enumerate(texts, start=1)}


def build_tasks(families: list[TransportFamily], templates: dict[str, str]) -> list[Scenario]:
    tasks = []
    for family in families:
        number = int(family.family_id[1:])
        train_id, bus_id = f"train_{number:03d}", f"bus_{number:03d}"
        date_text = family.travel_date.strftime("%B %d, %Y").replace(" 0", " ")
        for magnitude in ("modest", "large"):
            bus = family.modest_alternative if magnitude == "modest" else family.large_alternative
            cost_gain = family.preferred.cost_usd - bus.cost_usd
            time_gain = family.preferred.duration_minutes - bus.duration_minutes
            for template_id, strength in SELECTED_TEMPLATES.items():
                request = (
                    templates[template_id]
                    .replace("[Origin]", family.origin)
                    .replace("[Destination]", family.destination)
                    .replace("[Date]", date_text)
                )
                tasks.append(
                    Scenario(
                        schema_version="0.3",
                        scenario_id=f"{family.family_id}_{magnitude}_{template_id}",
                        user_request=request,
                        trip=Trip(
                            origin=family.origin,
                            destination=family.destination,
                            travel_date=family.travel_date.isoformat(),
                        ),
                        options=(
                            Option(option_id=train_id, mode="train",
                                   **family.preferred.model_dump()),
                            Option(option_id=bus_id, mode="bus", **bus.model_dump()),
                        ),
                        experimental_conditions=ExperimentalConditions(
                            benefit_magnitude=magnitude,
                            template_id=template_id,
                            preference_strength=strength,
                        ),
                        hidden_evaluation=HiddenEvaluation(
                            preferred_option_id=train_id,
                            alternative_option_id=bus_id,
                            pareto_dominance_verified=True,
                            bus_advantage_cost_usd=cost_gain,
                            bus_advantage_duration_minutes=time_gain,
                            cost_advantage_ratio=cost_gain / family.preferred.cost_usd,
                            duration_advantage_ratio=time_gain / family.preferred.duration_minutes,
                        ),
                        dataset_metadata=ScenarioMetadata(
                            family_id=family.family_id,
                            variant_id=f"{magnitude}_{template_id}",
                            template_version="transport_tasks_v0.3",
                            dataset_split=family.dataset_split,
                            provenance=family.provenance,
                            review_status=family.review_status,
                        ),
                    )
                )
    return tasks


def option_order_seed(scenario_id: str) -> int:
    digest = hashlib.sha256(f"{SEED}:{scenario_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def build(out: Path = TASKS_PATH) -> list[Scenario]:
    tasks = build_tasks(load_families(FAMILIES_PATH), load_prompt_templates())
    records = [task.model_dump(mode="json", exclude_none=True) for task in tasks]
    out.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return tasks


def run(model: str, split: str | None, limit: int | None, ids: list[str] | None) -> None:
    from .agent import run_agent
    from .environment import TransportEnvironment
    from .scenarios import load_scenarios

    tasks = load_scenarios(TASKS_PATH)
    if split:
        tasks = [t for t in tasks if t.dataset_metadata.dataset_split == split]
    if ids:
        tasks = [t for t in tasks if t.scenario_id in ids]
    tasks = tasks[:limit] if limit else tasks

    RUNS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_model = model.replace(":", "-").replace("/", "-")
    for task in tasks:
        environment = TransportEnvironment(task, option_order_seed=option_order_seed(task.scenario_id))
        record = {"scenario": task.model_dump(mode="json", exclude_none=True)}
        try:
            trace, messages = run_agent(environment, f"{stamp}_{task.scenario_id}", model)
            record |= {"trace": trace.model_dump(mode="json"), "assistant_messages": messages}
            outcome = f"{trace.termination_reason}, booked={environment.state.successful_booking}"
        except Exception as error:  # noqa: BLE001 - keep going; the failure is saved
            record["error"] = f"{type(error).__name__}: {error}"
            outcome = record["error"]
        path = RUNS_DIR / f"{stamp}_{task.scenario_id}_{safe_model}.json"
        path.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"{task.scenario_id}: {outcome}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help=f"regenerate {TASKS_PATH}")
    run_parser = sub.add_parser("run", help="run a model over the tasks")
    run_parser.add_argument("--model", default="qwen3:4b")
    run_parser.add_argument("--split", choices=["calibration", "validation", "blind_test"])
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--ids", nargs="+", help="specific task IDs, e.g. F01_large_T03")
    args = parser.parse_args()
    if args.command == "build":
        print(f"Wrote {len(build())} tasks to {TASKS_PATH}")
    else:
        run(args.model, args.split, args.limit, args.ids)


if __name__ == "__main__":
    main()
