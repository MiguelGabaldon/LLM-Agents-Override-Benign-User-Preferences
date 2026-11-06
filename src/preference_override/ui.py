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
from .environment import USER_RESPONSE_POLICY, TransportEnvironment
from .evaluation import evaluate_trajectory
from .models import (
    DEFAULT_MODEL,
    MODEL_CHOICES,
    ModelAPIError,
    ModelConfigurationError,
    model_filename,
    resolve_model,
)
from .scenarios import load_scenarios
from .schemas import RunTrace, TraceEvent
from .scoring import score_behavior


def scenario_choices(scenarios_path: Path = DEFAULT_SCENARIO_PATH) -> list[str]:
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
        elif event.event_type == "tool_call" and payload.get("tool_name") == "ask_user":
            if not payload.get("execution_allowed", True):
                continue
            role, content = "assistant", payload["arguments"].get("question")
        elif event.event_type == "tool_result" and payload.get("tool_name") == "ask_user":
            role, content = "user", payload["result"].get("response")
        else:
            continue
        if isinstance(content, str) and content.strip():
            display_content = content.replace("\\r\\n", "\n").replace("\\n", "\n")
            messages.append({"role": role, "content": display_content})
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


def run_selected_scenario(
    scenario_id: str,
    model_name: str,
    max_tool_calls: float,
    continue_after_question: bool = False,
) -> Generator[tuple[str, list[dict[str, str]], str, str, str | None, str, str], None, None]:
    """Run in a worker thread and yield step-level updates to the interface."""

    scenarios = load_scenarios(DEFAULT_SCENARIO_PATH)
    scenario = next((item for item in scenarios if item.scenario_id == scenario_id), None)
    if scenario is None:
        yield "", [], "", "", None, "", f"**Error:** unknown scenario `{scenario_id}`."
        return

    progress_queue: Queue[str | TraceEvent] = Queue()
    result: dict[str, Any] = {}
    environment = TransportEnvironment(scenario, continue_after_question=continue_after_question)
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
    yield _format_events(events), conversation, "", "", None, reasoning, "**Running...**"

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
        yield _format_events(events), conversation, "", "", None, reasoning, "**Running...**"

    if "error" in result:
        events.append(f"Run failed: {result['error']}")
        if not received_events:
            yield (
                _format_events(events),
                conversation,
                "",
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
            agent={
                "provider": provider,
                "model_name": name,
                "configuration_incomplete": True,
                "continue_after_question": continue_after_question,
                "user_response_policy": USER_RESPONSE_POLICY,
            },
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
    labels = score_behavior(
        scenario,
        trace.final_environment_state,
        assistant_messages,
        termination_reason=trace.termination_reason,
    )
    evaluation = evaluate_trajectory(scenario, trace, assistant_messages)
    output_path = _output_path(scenario.scenario_id, model_name)
    record = {
        "run_timestamp_utc": datetime.now(UTC).isoformat(),
        "scenario_id": scenario.scenario_id,
        "scenario_schema_version": scenario.schema_version,
        "dataset_metadata": (
            scenario.dataset_metadata.model_dump(mode="json") if scenario.dataset_metadata else None
        ),
        "experimental_conditions": scenario.experimental_conditions.model_dump(),
        "assistant_messages": assistant_messages,
        "behavioral_labels": labels.model_dump(),
        "evaluation": evaluation.model_dump(mode="json"),
        "trace": trace.model_dump(mode="json"),
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
    elif trace.termination_reason == "awaiting_user_response":
        status = "**Awaiting user response.** Question saved; no simulated reply was sent."
    elif final_status in {"empty", "failed", "invalid_tool_call"}:
        status = f"**Booking completed.** Final response: `{final_status}`. See Trace."
    else:
        status = f"**Complete.** Termination: `{trace.termination_reason}`"
    if save_error:
        status += f" **{save_error}**"
    yield (
        _format_events(events),
        conversation,
        json.dumps(evaluation.model_dump(mode="json"), indent=2),
        json.dumps(trace.model_dump(mode="json"), indent=2),
        saved_path,
        _reasoning(trace.events),
        status,
    )


def family_choices(scenarios_path: Path | None = None) -> list[tuple[str, str]]:
    """Show one trip per family instead of all scenario combinations."""
    families = {
        scenario.dataset_metadata.family_id: scenario.trip
        for scenario in load_scenarios(scenarios_path or DEFAULT_SCENARIO_PATH)
        if scenario.dataset_metadata is not None
    }
    return [
        (f"{family}: {trip.origin} -> {trip.destination} ({trip.travel_date})", family)
        for family, trip in sorted(families.items())
    ]


def run_selected_combination(
    request_framing: str,
    benefit_magnitude: str,
    family_id: str,
    model_name: str,
    max_tool_calls: float,
    continue_after_question: bool = False,
) -> Generator[tuple[str, list[dict[str, str]], str, str, str | None, str, str], None, None]:
    """Resolve the form selection and reuse the shared scenario runner."""
    scenario_id = f"{family_id}_{request_framing}_{benefit_magnitude}"
    yield from run_selected_scenario(
        scenario_id, model_name, max_tool_calls, continue_after_question
    )


def create_demo() -> gr.Blocks:
    """Build the local experiment-runner interface."""

    families = family_choices()
    with gr.Blocks(title="Preference Override Pilot") as demo:
        gr.Markdown(
            "# Preference Override Pilot\n"
            "Run one deterministic transport scenario with Ollama locally or an API model. "
            "The progress pane updates after every model or tool step."
        )
        with gr.Row():
            request_framing = gr.Dropdown(
                choices=[
                    ("Direct train request", "direct"),
                    ("Train preference, open to trade-offs", "flexible"),
                    ("Usual train travel, mode secondary", "habit_secondary"),
                ],
                value="direct",
                label="User request",
            )
            benefit_magnitude = gr.Dropdown(
                choices=[("Small benefit", "modest"), ("Large benefit", "large")],
                value="modest",
                label="Benefit magnitude",
            )
            family_id = gr.Dropdown(
                choices=families,
                value=families[0][1],
                label="Trip scenario",
            )
        with gr.Row():
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
        continue_after_question = gr.Checkbox(
            value=False,
            label="Continue after asking the user",
            info='Off: stop at the first question. On: reply "Okay, that sounds good to me." and continue.',
        )
        run_button = gr.Button("Run scenario", variant="primary")
        status = gr.Markdown("Choose a scenario and model, then run it.")
        with gr.Row():
            progress = gr.Textbox(label="Live progress", lines=14, interactive=False)
            conversation = gr.Chatbot(
                label="Conversation (simulated user)",
                height=450,
                allow_tags=False,
                group_consecutive_messages=False,
            )
        with gr.Row():
            labels = gr.Code(label="Behavioral labels", language="json", interactive=False)
            trace = gr.Code(label="Trace", language="json", interactive=False)
        record_file = gr.File(label="Saved run record")
        reasoning = gr.Textbox(
            label="Provider reasoning / thought summary (if available)",
            info="Only text exposed by the provider. It may be a summary rather than full reasoning.",
            lines=12,
            interactive=False,
        )

        run_button.click(
            fn=run_selected_combination,
            inputs=[
                request_framing, benefit_magnitude, family_id,
                model_name, max_tool_calls, continue_after_question,
            ],
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
