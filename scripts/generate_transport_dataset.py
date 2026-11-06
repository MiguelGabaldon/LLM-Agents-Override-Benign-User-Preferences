"""Generate v0.3 scenarios, or explicitly regenerate the historical v0.2 dataset."""

from __future__ import annotations

import argparse
from pathlib import Path

from preference_override.generation import expand_families, load_families, write_scenarios

DEFAULT_FAMILIES = Path("data/scenarios/transport_families_v0_2.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--families", type=Path, default=DEFAULT_FAMILIES)
    parser.add_argument("--schema-version", choices=("0.2", "0.3"), default="0.3")
    parser.add_argument("--output", type=Path, help="Defaults to the selected version's scenario file.")
    args = parser.parse_args()
    families = load_families(args.families)
    scenarios = expand_families(families, schema_version=args.schema_version)
    output = args.output or Path(
        f"data/scenarios/transport_pilot_v{args.schema_version.replace('.', '_')}.json"
    )
    write_scenarios(output, scenarios)
    print(f"Wrote {len(scenarios)} scenarios from {len(families)} families to {output}")


if __name__ == "__main__":
    main()
