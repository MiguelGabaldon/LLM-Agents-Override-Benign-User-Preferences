"""Scenario loading for version-controlled JSON pilot fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from .schemas import Scenario


def load_scenarios(path: Path) -> list[Scenario]:
    """Load and validate a JSON list of scenario records."""
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise TypeError("scenario file must contain a JSON list")
    scenarios = [Scenario.model_validate(record) for record in records]
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario identifiers must be unique")
    return scenarios
