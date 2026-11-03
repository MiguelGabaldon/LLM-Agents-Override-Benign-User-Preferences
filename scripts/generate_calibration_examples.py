"""Write the 24 authored rubric-calibration trajectories."""

from __future__ import annotations

from pathlib import Path

from preference_override.calibration import write_canonical_trajectories

if __name__ == "__main__":
    output = Path("data/annotations/canonical_trajectories_v0_2.json")
    write_canonical_trajectories(output)
    print(f"Wrote 24 authored calibration examples to {output}")
