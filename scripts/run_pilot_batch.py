"""Execute the configured scenario roster (external calls require --execute)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from preference_override.experiment import (
    load_experiment_config,
    preflight_experiment,
    run_batch,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("experiments/pilot_v0_3.json"))
    parser.add_argument("--output-root", type=Path, default=Path("runs/pilot_v0_3"))
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Required confirmation because the roster includes external API calls.",
    )
    args = parser.parse_args()
    config = load_experiment_config(args.config)
    preflight = preflight_experiment(config)
    if not args.execute:
        print(json.dumps(preflight, indent=2))
        print(
            "Dry configuration check complete. Approve all families, then add --execute to "
            "perform local/API model calls."
        )
        return
    summary = run_batch(
        config,
        args.output_root,
        selected_models=set(args.models) if args.models else None,
        resume=args.resume,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
