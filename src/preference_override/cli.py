"""Command-line entry point for a single local transport-pilot trajectory."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .agent import run_ollama_agent
from .environment import TransportEnvironment
from .scenarios import load_scenarios
from .scoring import score_behavior

DEFAULT_SCENARIO_PATH = Path("data/scenarios/transport_pilot_v0.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one local ground-transport pilot scenario.")
    parser.add_argument(
        "--scenarios-path",
        type=Path,
        default=DEFAULT_SCENARIO_PATH,
        help="Path to the scenario JSON fixture.",
    )
    parser.add_argument("--scenario-id", default="T01_approve", help="Scenario ID to run.")
    parser.add_argument("--model", default="qwen3:4b", help="Local Ollama model name.")
    parser.add_argument("--max-tool-calls", type=int, default=6, help="Maximum tool calls.")
    parser.add_argument("--output", type=Path, help="Optional trace output path.")
    parser.add_argument(
        "--list-scenarios", action="store_true", help="List available scenario IDs and exit."
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    scenarios = load_scenarios(args.scenarios_path)
    if args.list_scenarios:
        for scenario in scenarios:
            conditions = scenario.experimental_conditions
            print(
                f"{scenario.scenario_id}: {conditions.request_framing}, "
                f"{conditions.benefit_magnitude}, {conditions.confirmation_branch}"
            )
        return

    scenario = next((item for item in scenarios if item.scenario_id == args.scenario_id), None)
    if scenario is None:
        available = ", ".join(item.scenario_id for item in scenarios)
        raise SystemExit(f"Unknown scenario ID '{args.scenario_id}'. Available: {available}")

    run_id = f"{scenario.scenario_id}_{uuid4().hex[:8]}"
    environment = TransportEnvironment(scenario)
    trace, assistant_messages = run_ollama_agent(
        environment=environment,
        run_id=run_id,
        model_name=args.model,
        max_tool_calls=args.max_tool_calls,
        event_logger=print,
    )
    labels = score_behavior(
        scenario,
        trace.final_environment_state,
        assistant_messages,
        termination_reason=trace.termination_reason,
    )
    output_path = args.output or (
        Path("runs")
        / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{scenario.scenario_id}_{args.model.replace(':', '_')}.json"
    )
    record = {
        "run_timestamp_utc": datetime.now(UTC).isoformat(),
        "scenario_id": scenario.scenario_id,
        "experimental_conditions": scenario.experimental_conditions.model_dump(),
        "assistant_messages": assistant_messages,
        "behavioral_labels": labels.model_dump(),
        "trace": trace.model_dump(mode="json"),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print(json.dumps({"output_path": str(output_path), **labels.model_dump()}, indent=2))


if __name__ == "__main__":
    main()
