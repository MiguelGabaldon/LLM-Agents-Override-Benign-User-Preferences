"""Export JSON Schemas for scenario, annotation, evaluation, generator and judge records."""

from __future__ import annotations

import json
from pathlib import Path

from preference_override.evaluation import AnnotationRecord, EvaluationRecord, SemanticLabels
from preference_override.generation import CandidateFamilyBatch, TransportFamily
from preference_override.schemas import Scenario


def main() -> None:
    output = Path("data/schemas")
    output.mkdir(parents=True, exist_ok=True)
    models = {
        "scenario_v0_2": Scenario,
        "transport_family_v0_2": TransportFamily,
        "candidate_family_batch_v0_2": CandidateFamilyBatch,
        "semantic_labels_v0_2": SemanticLabels,
        "annotation_record_v0_2": AnnotationRecord,
        "evaluation_record_v0_2": EvaluationRecord,
    }
    for filename, model in models.items():
        path = output / f"{filename}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(models)} schemas to {output}")


if __name__ == "__main__":
    main()
