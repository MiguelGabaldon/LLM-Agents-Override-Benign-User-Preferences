"""Build the prompt x transport-family dataset.

For each of the prompts in data/scenarios/prompts.txt, sample 2 distinct
families from transport_families_v0_2.json (seeded) and create one case for
the modest alternative and one for the large alternative -> 4 cases/prompt.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCEN = ROOT / "data" / "scenarios"


def load_prompts(path: Path) -> list[str]:
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [l[1:-1] if l.startswith('"') and l.endswith('"') else l for l in lines]


def fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def fill(template: str, fam: dict) -> str:
    return (template.replace("[Origin]", fam["origin"])
            .replace("[Destination]", fam["destination"])
            .replace("[Date]", fmt_date(fam["travel_date"])))


def build(prompts: list[str], families: list[dict], seed: int, per_prompt: int = 2) -> list[dict]:
    rng = random.Random(seed)
    cases = []
    for p_idx, template in enumerate(prompts, start=1):
        for fam in rng.sample(families, per_prompt):
            for level in ("modest", "large"):
                alt = fam[f"{level}_alternative"]
                cases.append({
                    "case_id": f"P{p_idx:02d}-{fam['family_id']}-{level}",
                    "prompt_id": f"P{p_idx:02d}",
                    "family_id": fam["family_id"],
                    "alternative_level": level,
                    "prompt_template": template,
                    "user_prompt": fill(template, fam),
                    "origin": fam["origin"],
                    "destination": fam["destination"],
                    "travel_date": fam["travel_date"],
                    "preferred": {"mode": "train", **fam["preferred"]},
                    "alternative": {"mode": "bus", **alt},
                    "dataset_split": fam["dataset_split"],
                })
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(SCEN / "prompt_cases_v0_3.json"))
    args = ap.parse_args()
    prompts = load_prompts(SCEN / "prompts.txt")
    families = json.loads((SCEN / "transport_families_v0_2.json").read_text(encoding="utf-8"))
    cases = build(prompts, families, args.seed)
    out = Path(args.out)
    out.write_text(json.dumps({"seed": args.seed, "n_cases": len(cases), "cases": cases},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(prompts)} prompts -> {len(cases)} cases -> {out}")


if __name__ == "__main__":
    main()
