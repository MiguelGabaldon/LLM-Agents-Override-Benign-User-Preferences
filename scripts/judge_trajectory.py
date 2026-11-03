"""Apply the configured blinded semantic judge to one completed trajectory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from preference_override.evaluation import LabelSource, evaluate_trajectory
from preference_override.experiment import load_experiment_config
from preference_override.judge import judge_semantics
from preference_override.scenarios import load_scenarios
from preference_override.schemas import RunTrace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_record", type=Path)
    parser.add_argument("--config", type=Path, default=Path("experiments/pilot_v0_2.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("Add --execute to call the configured external judge model.")

    config = load_experiment_config(args.config)
    record = json.loads(args.run_record.read_text(encoding="utf-8"))
    if record.get("status", "completed") != "completed":
        raise SystemExit("Only completed run records can be judged.")
    scenario = next(
        item
        for item in load_scenarios(config.scenario_path)
        if item.scenario_id == record["scenario_id"]
    )
    trace = RunTrace.model_validate(record["trace"])
    messages = record["assistant_messages"]
    judgment = judge_semantics(
        scenario,
        trace,
        messages,
        judge_model=config.semantic_judge_model,
    )
    evaluation = evaluate_trajectory(
        scenario,
        trace,
        messages,
        semantic=judgment.semantic,
        semantic_source=LabelSource.LLM_JUDGE,
    )
    output = args.output or args.run_record.with_suffix(".judge.json")
    payload = {
        "run_record": str(args.run_record),
        "judge": judgment.model_dump(mode="json"),
        "evaluation": evaluation.model_dump(mode="json"),
    }
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
