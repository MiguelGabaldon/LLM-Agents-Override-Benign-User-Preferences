"""Render the 3 x 3 framework from human/adjudicated or validated-judge evaluations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from preference_override.analysis import plot_counts, render_plot_svg
from preference_override.evaluation import EvaluationRecord, LabelSource


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=Path("analysis/framework_plot.svg"))
    parser.add_argument("--allow-heuristic", action="store_true")
    args = parser.parse_args()
    records = []
    for path in args.inputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            candidates = payload
        elif "evaluation" in payload:
            candidates = [payload["evaluation"]]
        else:
            candidates = [payload]
        records.extend(EvaluationRecord.model_validate(item) for item in candidates)
    if not args.allow_heuristic and any(
        record.semantic_source == LabelSource.HEURISTIC for record in records
    ):
        raise SystemExit(
            "Refusing to present heuristic semantics as final results; use adjudicated or "
            "validated-judge evaluations."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_plot_svg(records), encoding="utf-8")
    summary_path = args.output.with_suffix(".json")
    summary_path.write_text(json.dumps(plot_counts(records), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} and {summary_path}")


if __name__ == "__main__":
    main()
