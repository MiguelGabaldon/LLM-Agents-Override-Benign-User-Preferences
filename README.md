# Preference Override Pilot

This repository contains a deterministic ground-transport booking pilot for
studying how tool-using agents handle an explicit benign preference when a
non-preferred alternative is cheaper and faster on displayed metrics.

## Setup

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

## Verify the deterministic environment

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

The scenario fixtures live in `data/scenarios/transport_pilot_v0.json`. They
are synthetic and do not represent real transport schedules or prices.

`src/preference_override/agent.py` contains the optional LangChain/Ollama
adapter. Before a model run, download the default open-weight model once:

```powershell
ollama pull qwen3:4b
```

The model runs locally and requires no API key. The deterministic tests do not
require Ollama or downloaded model weights.

## Run one pilot scenario

```powershell
.\.venv\Scripts\run-transport-pilot.exe --scenario-id T01_approve
```

The command prints live model/tool trajectory steps, then behavioral labels, and
saves the complete trace to `runs/`. Model reasoning is excluded from the
user-visible transcript and from text-based behavioral labels. An empty final
model response or a tool-call limit is recorded as an incomplete agent failure,
not as a preference decision. Use `--list-scenarios` to view the eight
available scenario IDs.

## Run the local experiment interface

Launch the local-only browser interface from the project directory:

```powershell
.\.venv\Scripts\run-transport-pilot-ui.exe
```

Choose a scenario and local Ollama model, then select **Run scenario**. The
interface shows step-level model/tool progress, the user-visible transcript,
behavioral labels, the complete trace, and a download link for the saved record.
