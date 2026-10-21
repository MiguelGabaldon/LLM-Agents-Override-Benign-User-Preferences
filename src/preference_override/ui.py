"""Local browser interface for running and inspecting pilot trajectories."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any
from uuid import uuid4

import gradio as gr

from .agent import run_agent
from .cli import DEFAULT_SCENARIO_PATH
from .environment import TransportEnvironment
from .models import DEFAULT_MODEL, MODEL_CHOICES, model_filename
from .scenarios import load_scenarios
from .scoring import score_behavior


def scenario_choices(scenarios_path: Path = DEFAULT_SCENARIO_PATH) -> list[str]:
    """Return scenario identifiers for the interface selector."""

    return [scenario.scenario_id for scenario in load_scenarios(scenarios_path)]


def _output_path(scenario_id: str, model_name: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_model_name = model_filename(model_name)
    return Path("runs") / f"{timestamp}_{scenario_id}_{safe_model_name}.json"


def _format_events(events: list[str]) -> str:
    return "\n".join(events) if events else "Waiting for the model to begin..."


def run_selected_scenario(
    scenario_id: str, model_name: str, max_tool_calls: float
) -> Generator[tuple[str, str, str, str, str | None, str], None, None]:
    """Run in a worker thread and yield step-level updates to the interface."""

    scenarios = load_scenarios(DEFAULT_SCENARIO_PATH)
    scenario = next((item for item in scenarios if item.scenario_id == scenario_id), None)
    if scenario is None:
        yield "", "", "", "", None, f"**Error:** unknown scenario `{scenario_id}`."
        return

    progress_queue: Queue[str] = Queue()
    result: dict[str, Any] = {}

    def execute() -> None:
        try:
            environment = TransportEnvironment(scenario)
            trace, assistant_messages = run_agent(
                environment=environment,
                run_id=f"{scenario.scenario_id}_{uuid4().hex[:8]}",
                model_name=model_name,
                max_tool_calls=int(max_tool_calls),
                event_logger=progress_queue.put,
            )
            result["trace"] = trace
            result["assistant_messages"] = assistant_messages
        except Exception as error:  # noqa: BLE001 - Surface model/server errors in the browser.
            result["error"] = f"{type(error).__name__}: {error}"

    worker = Thread(target=execute, daemon=True)
    worker.start()
    events = ["Run started."]
    yield _format_events(events), "", "", "", None, "**Running...**"

    while worker.is_alive() or not progress_queue.empty():
        try:
            events.append(progress_queue.get(timeout=0.25))
            yield _format_events(events), "", "", "", None, "**Running...**"
        except Empty:
            continue

    if "error" in result:
        events.append(f"Run failed: {result['error']}")
        yield _format_events(events), "", "", "", None, f"**Run failed:** {result['error']}"
        return

    trace = result["trace"]
    assistant_messages = result["assistant_messages"]
    labels = score_behavior(
        scenario,
        trace.final_environment_state,
        assistant_messages,
        termination_reason=trace.termination_reason,
    )
    output_path = _output_path(scenario.scenario_id, model_name)
    record = {
        "run_timestamp_utc": datetime.now(UTC).isoformat(),
        "scenario_id": scenario.scenario_id,
        "experimental_conditions": scenario.experimental_conditions.model_dump(),
        "assistant_messages": assistant_messages,
        "behavioral_labels": labels.model_dump(),
        "trace": trace.model_dump(mode="json"),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    events.append(f"Saved run record: {output_path}")
    transcript = "\n\n".join(assistant_messages) or "No user-visible assistant message."
    yield (
        _format_events(events),
        transcript,
        json.dumps(labels.model_dump(), indent=2),
        json.dumps(trace.model_dump(mode="json"), indent=2),
        str(output_path),
        f"**Complete.** Termination: `{trace.termination_reason}`",
    )


def create_demo() -> gr.Blocks:
    """Build the local experiment-runner interface."""

    scenarios = scenario_choices()
    with gr.Blocks(title="Preference Override Pilot") as demo:
        gr.Markdown(
            "# Preference Override Pilot\n"
            "Run one deterministic transport scenario with Ollama locally or an API model. "
            "The progress pane updates after every model or tool step."
        )
        with gr.Row():
            scenario_id = gr.Dropdown(
                choices=scenarios,
                value=scenarios[0],
                label="Scenario",
                info="The scenario controls preference framing, benefit magnitude, and response branch.",
            )
            model_name = gr.Dropdown(
                label="Models",
                choices=MODEL_CHOICES,
                value=DEFAULT_MODEL,
                allow_custom_value=True,
                info="Use Free-tier Google/Groq accounts; quotas apply. "
                "OpenRouter selections use only free endpoints. "
                "You can also enter any installed Ollama model name.",
            )
            max_tool_calls = gr.Slider(
                minimum=1,
                maximum=12,
                value=6,
                step=1,
                label="Maximum tool calls",
            )
        run_button = gr.Button("Run scenario", variant="primary")
        status = gr.Markdown("Choose a scenario and model, then run it.")
        with gr.Row():
            progress = gr.Textbox(label="Live progress", lines=14, interactive=False)
            transcript = gr.Textbox(label="User-visible assistant transcript", lines=14, interactive=False)
        with gr.Row():
            labels = gr.Code(label="Behavioral labels", language="json", interactive=False)
            trace = gr.Code(label="Trace", language="json", interactive=False)
        record_file = gr.File(label="Saved run record")

        run_button.click(
            fn=run_selected_scenario,
            inputs=[scenario_id, model_name, max_tool_calls],
            outputs=[progress, transcript, labels, trace, record_file, status],
            show_progress="minimal",
            concurrency_limit=1,
        )
    return demo


def main() -> None:
    """Launch the local-only browser interface."""

    create_demo().launch(server_name="127.0.0.1", inbrowser=True, show_error=True)


if __name__ == "__main__":
    main()
