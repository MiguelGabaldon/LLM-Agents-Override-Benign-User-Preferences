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
from .environment import TransportEnvironment
from .execution import suggest_execution_b
from .models import (
    DEFAULT_MODEL,
    GOOGLE_MODELS,
    GROQ_MODELS,
    OPENROUTER_MODELS,
    ModelAPIError,
    ModelConfigurationError,
    model_filename,
    resolve_model,
)
from .pilot import TASKS_PATH, option_order_seed
from .scenarios import load_scenarios
from .schemas import RunTrace, Scenario, TraceEvent

# API models only: the interface never offers local Ollama models.
MODEL_CHOICES = [
    ("Gemini 3.5 Flash-Lite (Google AI · Free tier)", f"google:{GOOGLE_MODELS[0]}"),
    ("Gemini 3.1 Flash-Lite (Google AI · Free tier)", f"google:{GOOGLE_MODELS[1]}"),
    ("GPT-OSS 120B (Groq API)", f"groq:{GROQ_MODELS[0]}"),
    ("GPT-OSS 20B (Groq API)", f"groq:{GROQ_MODELS[1]}"),
    ("Qwen3.8 27B (Groq API · Free tier)", f"groq:{GROQ_MODELS[2]}"),
    ("Nemotron 3.5 Lightning (OpenRouter · free)", f"openrouter:{OPENROUTER_MODELS[0]}"),
    ("Cohere North Mini Code (OpenRouter · free)", f"openrouter:{OPENROUTER_MODELS[1]}"),
]


def scenario_choices(scenarios_path: Path = TASKS_PATH) -> list[str]:
    """Return scenario identifiers for the interface selector."""

    return [scenario.scenario_id for scenario in load_scenarios(scenarios_path)]


def _output_path(scenario_id: str, model_name: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    safe_model_name = model_filename(model_name)
    return Path("runs") / f"{timestamp}_{scenario_id}_{safe_model_name}.json"


def _format_events(events: list[str]) -> str:
    return "\n".join(events) if events else "Waiting for the model to begin..."


def _conversation(user_request: str, events: list[TraceEvent]) -> list[dict[str, str]]:
    messages = [{"role": "user", "content": user_request}]
    for event in events:
        payload = event.payload
        if event.event_type == "assistant_message":
            role, content = "assistant", payload.get("content")
        else:
            continue
        if isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content})
    return messages


def _reasoning(events: list[TraceEvent]) -> str:
    return (
        "\n\n".join(
            f"Step {event.step} ({event.payload.get('phase', 'action')})\n{event.payload['content']}"
            for event in events
            if event.event_type == "reasoning"
        )
        or "No reasoning text was returned by this model/provider."
    )


def _case_details(scenario: Scenario) -> str:
    """Conditions and options for the human annotator; never sent to the agent."""
    metadata = scenario.dataset_metadata
    return json.dumps(
        {
            "scenario_id": scenario.scenario_id,
            "experimental_conditions": scenario.experimental_conditions.model_dump(
                exclude_none=True
            ),
            "options": [option.model_dump() for option in scenario.options],
            "hidden_evaluation": scenario.hidden_evaluation.model_dump(),
            "dataset_split": metadata.dataset_split if metadata else None,
        },
        indent=2,
    )


def run_selected_scenario(
    scenario_id: str, model_name: str, max_tool_calls: float
) -> Generator[tuple[str, list[dict[str, str]], str, str, str | None, str, str], None, None]:
    """Run in a worker thread and yield step-level updates to the interface."""

    scenarios = load_scenarios(TASKS_PATH)
    scenario = next((item for item in scenarios if item.scenario_id == scenario_id), None)
    if scenario is None:
        yield "", [], "", "", None, "", f"**Error:** unknown scenario `{scenario_id}`."
        return

    progress_queue: Queue[str | TraceEvent] = Queue()
    result: dict[str, Any] = {}
    # Same option display order as `pilot run` uses for this case.
    environment = TransportEnvironment(
        scenario, option_order_seed=option_order_seed(scenario.scenario_id)
    )
    details = _case_details(scenario)
    run_id = f"{scenario.scenario_id}_{uuid4().hex[:8]}"

    def execute() -> None:
        try:
            trace, assistant_messages = run_agent(
                environment=environment,
                run_id=run_id,
                model_name=model_name,
                max_tool_calls=int(max_tool_calls),
                event_logger=progress_queue.put,
                event_callback=progress_queue.put,
                final_response_after_booking=True,
                capture_reasoning=True,
            )
            result["trace"] = trace
            result["assistant_messages"] = assistant_messages
        except (ModelAPIError, ModelConfigurationError) as error:
            result["error"] = f"{type(error).__name__}: {error}"
        except Exception as error:  # noqa: BLE001 - keep unexpected error payloads out of the UI.
            result["error"] = f"{type(error).__name__}: Unexpected runtime error."

    worker = Thread(target=execute, daemon=True)
    worker.start()
    events = ["Run started."]
    received_events: list[TraceEvent] = []
    conversation = _conversation(scenario.user_request, received_events)
    reasoning = "Waiting for provider reasoning..."
    yield _format_events(events), conversation, details, "", None, reasoning, "**Running...**"

    while worker.is_alive() or not progress_queue.empty():
        try:
            event = progress_queue.get(timeout=0.5)
            if isinstance(event, TraceEvent):
                received_events.append(event)
                conversation = _conversation(scenario.user_request, received_events)
                if event.event_type == "reasoning":
                    reasoning = _reasoning(received_events)
            else:
                events.append(event)
        except Empty:
            pass
        # Keep the conversation on screen while a model/tool is working; the
        # generator also stays responsive when a model has not returned yet.
        yield _format_events(events), conversation, details, "", None, reasoning, "**Running...**"

    if "error" in result:
        events.append(f"Run failed: {result['error']}")
        if not received_events:
            yield (
                _format_events(events),
                conversation,
                details,
                "",
                None,
                _reasoning(received_events),
                f"**Run failed:** {result['error']}",
            )
            return
        provider, name = resolve_model(model_name)
        result["trace"] = RunTrace(
            run_id=run_id,
            scenario_id=scenario.scenario_id,
            agent={"provider": provider, "model_name": name, "configuration_incomplete": True},
            events=received_events,
            final_environment_state=environment.state,
            termination_reason="incomplete_runtime_error",
        )
        result["assistant_messages"] = [
            event.payload["content"]
            for event in received_events
            if event.event_type == "assistant_message"
        ]

    trace = result["trace"]
    assistant_messages = result["assistant_messages"]
    output_path = _output_path(scenario.scenario_id, model_name)
    # Same record layout as `pilot run`, plus how and when the run was launched.
    record = {
        "scenario": scenario.model_dump(mode="json", exclude_none=True),
        "trace": trace.model_dump(mode="json"),
        "assistant_messages": assistant_messages,
        "execution_b": suggest_execution_b(trace),
        "launched_from": "ui",
        "run_timestamp_utc": datetime.now(UTC).isoformat(),
    }
    if "error" in result:
        record["error"] = result["error"]
    saved_path = None
    save_error = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        saved_path = str(output_path)
        events.append(f"Saved run record: {output_path}")
    except OSError as error:
        save_error = f"Could not save run record ({type(error).__name__})."
        events.append(save_error)
    conversation = _conversation(scenario.user_request, trace.events)
    final_status = next(
        (event.payload["status"] for event in trace.events if event.event_type == "final_response"),
        "not_requested",
    )
    if "error" in result:
        status = f"**Run failed:** {result['error']}. Partial trace retained."
    elif trace.termination_reason.startswith("incomplete_"):
        status = f"**Incomplete.** Termination: `{trace.termination_reason}`"
    elif final_status in {"empty", "failed", "invalid_tool_call"}:
        status = f"**Booking completed.** Final response: `{final_status}`. See Trace."
    else:
        status = f"**Complete.** Termination: `{trace.termination_reason}`"
    if save_error:
        status += f" **{save_error}**"
    suggestion = record["execution_b"]
    score = suggestion["suggested_b"]
    status += (
        "\n\nSuggested execution B: **needs review** (trace incomplete)."
        if score is None
        else f"\n\nSuggested execution B: **{score}** (pending human review)."
    )
    yield (
        _format_events(events),
        conversation,
        details,
        json.dumps(trace.model_dump(mode="json"), indent=2),
        saved_path,
        _reasoning(trace.events),
        status,
    )


def create_demo() -> gr.Blocks:
    """Build the local experiment-runner interface."""

    scenarios = scenario_choices()
    with gr.Blocks(title="Preference Override Pilot") as demo:
        gr.Markdown(
            "# Preference Override Pilot\n"
            "Run one case from `prompt_cases_v0_3.json` with an API model. "
            "The progress pane updates after every model or tool step. "
            "Execution B is suggested from tool calls for human review; "
            "annotate the remaining scores with `docs/rubric.md`."
        )
        with gr.Row():
            scenario_id = gr.Dropdown(
                choices=scenarios,
                value=scenarios[0],
                label="Case",
                info="ID = prompt template _ trip family _ benefit magnitude (modest/large).",
            )
            model_name = gr.Dropdown(
                label="Models",
                choices=MODEL_CHOICES,
                value=DEFAULT_MODEL,
                allow_custom_value=False,
                info="Use Free-tier Google/Groq accounts; quotas apply. "
                "OpenRouter selections use only free endpoints.",
            )
            max_tool_calls = gr.Slider(
                minimum=1,
                maximum=12,
                value=6,
                step=1,
                label="Maximum tool calls",
            )
        run_button = gr.Button("Run case", variant="primary")
        status = gr.Markdown("Choose a case and model, then run it.")
        with gr.Row():
            progress = gr.Textbox(label="Live progress", lines=14, interactive=False)
            conversation = gr.Chatbot(
                label="Conversation (what the user sees)",
                height=450,
                allow_tags=False,
                group_consecutive_messages=False,
            )
        with gr.Row():
            labels = gr.Code(
                label="Case details (hidden from the agent)", language="json", interactive=False
            )
            trace = gr.Code(label="Trace", language="json", interactive=False)
        record_file = gr.File(label="Saved run record")
        reasoning = gr.Textbox(
            label="Provider reasoning / thought summary (if available)",
            info="Only text exposed by the provider. It may be a summary rather than full reasoning.",
            lines=12,
            interactive=False,
        )

        run_button.click(
            fn=run_selected_scenario,
            inputs=[scenario_id, model_name, max_tool_calls],
            outputs=[progress, conversation, labels, trace, record_file, reasoning, status],
            show_progress="minimal",
            concurrency_limit=1,
        )
    return demo


def main() -> None:
    """Launch the local-only browser interface."""

    create_demo().launch(server_name="127.0.0.1", inbrowser=True, show_error=True)


if __name__ == "__main__":
    main()
