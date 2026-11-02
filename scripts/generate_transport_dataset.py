"""Generate the reviewed-shape v0.2 scenario file from matched base families."""

from __future__ import annotations

import argparse
from pathlib import Path

from preference_override.generation import expand_families, load_families, write_scenarios

DEFAULT_FAMILIES = Path("data/scenarios/transport_families_v0_2.json")
DEFAULT_OUTPUT = Path("data/scenarios/transport_pilot_v0_2.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--families", type=Path, default=DEFAULT_FAMILIES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    families = load_families(args.families)
    scenarios = expand_families(families)
    write_scenarios(args.output, scenarios)
    print(f"Wrote {len(scenarios)} scenarios from {len(families)} families to {args.output}")


if __name__ == "__main__":
    main()
