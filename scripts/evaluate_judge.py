"""Evaluate frozen judge predictions against adjudicated blind-test comparisons."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import TypeAdapter

from preference_override.metrics import JudgeComparison, evaluate_judge_gate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparisons", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    adapter = TypeAdapter(list[JudgeComparison])
    records = adapter.validate_json(args.comparisons.read_text(encoding="utf-8"))
    metrics = evaluate_judge_gate(records)
    text = metrics.model_dump_json(indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
