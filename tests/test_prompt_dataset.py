import importlib.util
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bpd", ROOT / "scripts" / "build_prompt_dataset.py")
bpd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bpd)


def _build(seed=42):
    prompts = bpd.load_prompts(ROOT / "data/scenarios/prompts.txt")
    fams = json.loads((ROOT / "data/scenarios/transport_families_v0_2.json").read_text(encoding="utf-8"))
    return prompts, bpd.build(prompts, fams, seed)


def test_120_cases_4_per_prompt():
    prompts, cases = _build()
    assert len(prompts) == 30 and len(cases) == 120
    per_prompt = Counter(c["prompt_id"] for c in cases)
    assert set(per_prompt.values()) == {4}
    for pid in per_prompt:
        sub = [c for c in cases if c["prompt_id"] == pid]
        assert len({c["family_id"] for c in sub}) == 2
        assert Counter(c["alternative_level"] for c in sub) == {"modest": 2, "large": 2}


def test_placeholders_filled_and_deterministic():
    _, cases = _build()
    assert all("[" not in c["user_prompt"] for c in cases)
    assert len({c["case_id"] for c in cases}) == 120
    assert cases == _build()[1]
