"""Attach completed runs and export two blinded human-annotation packets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from preference_override.annotation import (
    GoldSampleManifest,
    attach_run_records,
    build_blinded_packet,
    write_manifest,
)
from preference_override.experiment import load_experiment_config
from preference_override.scenarios import load_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/annotations/gold_sample_manifest_v0_2.json"),
    )
    parser.add_argument("--runs-root", type=Path, default=Path("runs/pilot_v0_2"))
    parser.add_argument("--config", type=Path, default=Path("experiments/pilot_v0_2.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/annotations/packets"))
    args = parser.parse_args()
    manifest = GoldSampleManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    completed_paths = []
    for path in args.runs_root.rglob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") == "completed":
            completed_paths.append(path)
    attached = attach_run_records(manifest, completed_paths)
    attached_path = args.output_dir / "gold_sample_manifest_attached_v0_2.json"
    write_manifest(attached_path, attached)

    config = load_experiment_config(args.config)
    scenarios = load_scenarios(config.scenario_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for annotator in ("annotator_a", "annotator_b"):
        packet = build_blinded_packet(attached, scenarios, annotator)
        path = args.output_dir / f"{annotator}_packet_v0_2.json"
        path.write_text(
            json.dumps([item.model_dump(mode="json") for item in packet], indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {len(packet)} blinded items to {path}")


if __name__ == "__main__":
    main()
