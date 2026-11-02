# Pilot v0.2 workflow

## 1. Review and freeze scenarios

The generated file contains 80 structurally valid candidates, all marked
`pending_human_review`. Review the ten base families in
`data/scenarios/transport_families_v0_2.json`. After approval, change only the family records to
`approved` and regenerate the variants:

```powershell
.\.venv\Scripts\python.exe scripts\generate_transport_dataset.py
```

Do not edit individual variants; that would break the matched-family design.

## 2. Calibrate annotators

Read `docs/evaluation_spec_v0_2.md`, then discuss the 24 authored examples. These are constructed
examples rather than model findings.

The gold-sample manifest is fixed before runs exist:

```powershell
.\.venv\Scripts\python.exe scripts\build_annotation_manifest.py
```

## 3. Run the pilot

Inspect `experiments/pilot_v0_2.json` and verify provider access. A configuration check does not call
models:

```powershell
.\.venv\Scripts\python.exe scripts\run_pilot_batch.py
```

The complete experiment requires explicit confirmation because it performs 320 local/API calls:

```powershell
.\.venv\Scripts\python.exe scripts\run_pilot_batch.py --execute --resume
```

Use `--model MODEL_ID` for a single frozen-roster model. Failures are recorded without provider or
paid fallback.

## 4. Human annotation and judge validation

Attach completed run paths to the preselected manifest, export two blinded annotation packets, and
store adjudication as a third record. Never expose model IDs to annotators. Develop the judge prompt
only on calibration, tune only on validation, then run the frozen judge once on blind test.

Report human agreement, per-label confusion, judge abstention, the strict acceptance gate, and all
subgroup results. Do not fill missing annotations or failed runs with synthetic labels.

## 5. Reporting

Generate plot counts from adjudicated or validated-judge `EvaluationRecord` objects. Always report
off-grid failures separately. Preserve scenarios, experiment configuration, full traces, annotation
records, judge prompt/model metadata, and rubric version with every result.

The final categorical SVG and its machine-readable count matrix can be rendered from one or more
evaluation JSON files:

```powershell
.\.venv\Scripts\python.exe scripts\render_framework_plot.py EVALUATION_FILES...
```

The command rejects preliminary lexical-heuristic labels unless explicitly overridden for local
debugging.
