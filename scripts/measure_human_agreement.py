"""Measure independent annotation agreement before adjudication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from preference_override.annotation import load_annotations
from preference_override.metrics import SEMANTIC_BOOLEAN_FIELDS, agreement_summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annotator_a", type=Path)
    parser.add_argument("annotator_b", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    left = {record.run_id: record for record in load_annotations(args.annotator_a)}
    right = {record.run_id: record for record in load_annotations(args.annotator_b)}
    if set(left) != set(right):
        raise SystemExit("Annotation files must contain the same run IDs.")
    run_ids = sorted(left)
    results = {
        "orientation": agreement_summary(
            [left[run_id].orientation for run_id in run_ids],
            [right[run_id].orientation for run_id in run_ids],
        ),
        "autonomy": agreement_summary(
            [left[run_id].autonomy for run_id in run_ids],
            [right[run_id].autonomy for run_id in run_ids],
        ),
        "behavioral_category": agreement_summary(
            [left[run_id].behavioral_category for run_id in run_ids],
            [right[run_id].behavioral_category for run_id in run_ids],
        ),
        "semantic": {
            field: agreement_summary(
                [getattr(left[run_id].semantic, field) for run_id in run_ids],
                [getattr(right[run_id].semantic, field) for run_id in run_ids],
            )
            for field in SEMANTIC_BOOLEAN_FIELDS
        },
    }
    results["meets_targets"] = {
        "primary": all(
            results[field]["cohen_kappa"] >= 0.80
            for field in ("orientation", "autonomy", "behavioral_category")
        ),
        "semantic": all(
            value["cohen_kappa"] >= 0.70 for value in results["semantic"].values()
        ),
    }
    text = json.dumps(results, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
