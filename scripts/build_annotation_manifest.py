"""Create the pre-response, balanced 120-trajectory annotation sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from preference_override.annotation import build_gold_sample_template, write_manifest
from preference_override.scenarios import load_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("experiments/pilot_v0_2.json"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/annotations/gold_sample_manifest_v0_2.json"),
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    scenarios = load_scenarios(Path(config["scenario_path"]))
    manifest = build_gold_sample_template(scenarios, config["evaluated_models"])
    write_manifest(args.output, manifest)
    print(f"Wrote {len(manifest.entries)} preselected annotation slots to {args.output}")


if __name__ == "__main__":
    main()
