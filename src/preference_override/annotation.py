"""Balanced gold-sample manifests and annotation record I/O."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .evaluation import AnnotationRecord
from .schemas import RunTrace, Scenario


class GoldSampleEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    sample_id: str
    scenario_id: str
    family_id: str
    dataset_split: str
    model_id: str
    run_id: str | None = None
    run_record_path: str | None = None
    assigned_annotators: tuple[str, str] = ("annotator_a", "annotator_b")
    adjudicator: str = "adjudicator"


class GoldSampleManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "0.2"
    entries: tuple[GoldSampleEntry, ...]


class BlindedAnnotationItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    sample_id: str
    run_id: str
    scenario_id: str
    phase: str
    user_request: str
    visible_options: list[dict[str, object]]
    preferred_option_id: str
    alternative_option_id: str
    assistant_messages: list[str]
    interaction_events: list[dict[str, object]]
    termination_reason: str
    annotation: None = None


def build_gold_sample_template(
    scenarios: list[Scenario], model_ids: list[str]
) -> GoldSampleManifest:
    """Select 120 balanced model-scenario slots before model outputs are inspected."""

    if len(model_ids) != 4 or len(set(model_ids)) != 4:
        raise ValueError("pilot gold sampling requires four distinct model IDs")
    by_family: dict[str, list[Scenario]] = {}
    for scenario in scenarios:
        if scenario.dataset_metadata is None:
            raise ValueError("gold sampling requires scenario family metadata")
        by_family.setdefault(scenario.dataset_metadata.family_id, []).append(scenario)
    if len(by_family) != 10 or any(len(items) != 8 for items in by_family.values()):
        raise ValueError("pilot gold sampling requires ten complete eight-variant families")

    selected: list[tuple[Scenario, int]] = []
    model_counts = [0, 0, 0, 0]
    for family_index, family_id in enumerate(sorted(by_family)):
        variants = sorted(by_family[family_id], key=_condition_key)
        for condition_index, scenario in enumerate(variants):
            model_index = (family_index + condition_index) % 4
            selected.append((scenario, model_index))
            model_counts[model_index] += 1

    for family_index, family_id in enumerate(sorted(by_family)):
        variants = sorted(by_family[family_id], key=_condition_key)
        for condition_index, scenario in enumerate(variants):
            if (family_index + condition_index) % 2:
                continue
            base_model = (family_index + condition_index) % 4
            candidates = [index for index in range(4) if index != base_model]
            candidates.sort(
                key=lambda index: (
                    model_counts[index],
                    (index - family_index - condition_index) % 4,
                )
            )
            model_index = candidates[0]
            selected.append((scenario, model_index))
            model_counts[model_index] += 1

    entries = tuple(
        GoldSampleEntry(
            sample_id=f"G{index:03d}",
            scenario_id=scenario.scenario_id,
            family_id=scenario.dataset_metadata.family_id,  # type: ignore[union-attr]
            dataset_split=scenario.dataset_metadata.dataset_split,  # type: ignore[union-attr]
            model_id=model_ids[model_index],
        )
        for index, (scenario, model_index) in enumerate(selected, start=1)
    )
    manifest = GoldSampleManifest(entries=entries)
    _validate_balance(manifest)
    return manifest


def attach_run_records(
    manifest: GoldSampleManifest, run_paths: list[Path]
) -> GoldSampleManifest:
    """Attach one completed record to every preselected slot without inspecting responses."""

    lookup: dict[tuple[str, str], tuple[str, str]] = {}
    for path in run_paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        model_id = _model_id(record["trace"]["agent"])
        key = (record["scenario_id"], model_id)
        if key in lookup:
            raise ValueError(f"multiple run records found for {key}")
        lookup[key] = (record["trace"]["run_id"], str(path))
    attached = []
    for entry in manifest.entries:
        key = (entry.scenario_id, entry.model_id)
        if key not in lookup:
            raise ValueError(f"missing run record for {key}")
        run_id, path = lookup[key]
        attached.append(entry.model_copy(update={"run_id": run_id, "run_record_path": path}))
    return GoldSampleManifest(entries=tuple(attached))


def write_manifest(path: Path, manifest: GoldSampleManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_annotations(path: Path) -> list[AnnotationRecord]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise TypeError("annotation file must contain a JSON list")
    return [AnnotationRecord.model_validate(record) for record in records]


def build_blinded_packet(
    manifest: GoldSampleManifest,
    scenarios: list[Scenario],
    annotator_id: str,
) -> list[BlindedAnnotationItem]:
    """Create a model-identity-free packet after run records have been attached."""

    scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    packet = []
    for entry in manifest.entries:
        if annotator_id not in entry.assigned_annotators:
            continue
        if entry.run_record_path is None or entry.run_id is None:
            raise ValueError("attach run records before exporting annotation packets")
        record = json.loads(Path(entry.run_record_path).read_text(encoding="utf-8"))
        trace = RunTrace.model_validate(record["trace"])
        scenario = scenario_by_id[entry.scenario_id]
        packet.append(
            BlindedAnnotationItem(
                sample_id=entry.sample_id,
                run_id=entry.run_id,
                scenario_id=entry.scenario_id,
                phase=entry.dataset_split,
                user_request=scenario.user_request,
                visible_options=[option.model_dump() for option in scenario.options],
                preferred_option_id=scenario.hidden_evaluation.preferred_option_id,
                alternative_option_id=scenario.hidden_evaluation.alternative_option_id,
                assistant_messages=record["assistant_messages"],
                interaction_events=[
                    event.model_dump(mode="json")
                    for event in trace.events
                    if event.event_type in {"assistant_message", "tool_call", "tool_result"}
                ],
                termination_reason=trace.termination_reason,
            )
        )
    return packet


def _condition_key(scenario: Scenario) -> tuple[str, str, str]:
    conditions = scenario.experimental_conditions
    return (
        conditions.request_framing,
        conditions.benefit_magnitude,
        conditions.confirmation_branch,
    )


def _model_id(agent: dict[str, object]) -> str:
    provider = str(agent["provider"])
    name = str(agent["model_name"])
    return name if provider == "ollama" else f"{provider}:{name}"


def _validate_balance(manifest: GoldSampleManifest) -> None:
    if len(manifest.entries) != 120:
        raise ValueError("gold sample must contain exactly 120 entries")
    family_counts = Counter(entry.family_id for entry in manifest.entries)
    model_counts = Counter(entry.model_id for entry in manifest.entries)
    split_counts = Counter(entry.dataset_split for entry in manifest.entries)
    if set(family_counts.values()) != {12}:
        raise ValueError("gold sample must contain 12 entries per family")
    if set(model_counts.values()) != {30}:
        raise ValueError("gold sample must contain 30 entries per model")
    if split_counts != {"calibration": 36, "validation": 24, "blind_test": 60}:
        raise ValueError("gold sample split allocation is invalid")
