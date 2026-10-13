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

The command prints behavioral labels and saves the complete trace to `runs/`.
Use `--list-scenarios` to view the eight available scenario IDs.
