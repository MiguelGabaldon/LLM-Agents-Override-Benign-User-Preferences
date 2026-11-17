"""Interface tests with a scripted agent: no API calls, no Ollama."""

from __future__ import annotations

import json
from pathlib import Path

from preference_override import ui
from preference_override.pilot import TASKS_PATH
from preference_override.scenarios import load_scenarios
from preference_override.schemas import RunTrace, TraceEvent

CASE_ID = load_scenarios(TASKS_PATH)[0].scenario_id


def test_selectors_list_all_cases_and_only_api_models():
    demo = ui.create_demo()
    components = demo.config["components"]
    case = next(c for c in components if c["props"].get("label") == "Case")
    model = next(c for c in components if c["props"].get("label") == "Models")
    assert len(case["props"]["choices"]) == 120
    values = [value for _, value in model["props"]["choices"]]
    assert model["props"]["value"] == "google:gemini-3.5-flash-lite"
    assert all(value.split(":")[0] in {"google", "groq", "openrouter", "foundry"} for value in values)
    assert {"foundry:gpt-4.1", "foundry:gpt-5.6-luna"}.issubset(values)
    assert model["props"]["allow_custom_value"] is False


def test_conversation_shows_user_request_and_assistant_text_only():
    events = [
        TraceEvent(step=1, event_type="tool_call", payload={"tool_name": "search_transport_options"}),
        TraceEvent(step=2, event_type="assistant_message", payload={"content": "Booked the train."}),
    ]
    assert ui._conversation("Book me a ticket.", events) == [
        {"role": "user", "content": "Book me a ticket."},
        {"role": "assistant", "content": "Booked the train."},
    ]


def _fake_run_agent(environment, run_id, event_callback, **_):
    event = TraceEvent(step=1, event_type="assistant_message", payload={"content": "Done."})
    event_callback(event)
    trace = RunTrace(
        run_id=run_id,
        scenario_id=environment.scenario.scenario_id,
        agent={"provider": "google", "model_name": "fake"},
        events=[event],
        final_environment_state=environment.state,
        termination_reason="agent_finished",
    )
    return trace, ["Done."]


def test_run_saves_record_in_pilot_layout(monkeypatch, tmp_path):
    output = tmp_path / "run.json"
    monkeypatch.setattr(ui, "run_agent", _fake_run_agent)
    monkeypatch.setattr(ui, "_output_path", lambda *_: output)
    last = list(ui.run_selected_scenario(CASE_ID, "google:gemini-3.5-flash-lite", 6))[-1]
    _, conversation, details, _, saved, _, status = last
    assert saved == str(output)
    assert conversation[-1] == {"role": "assistant", "content": "Done."}
    assert json.loads(details)["scenario_id"] == CASE_ID
    assert "Complete" in status
    record = json.loads(output.read_text(encoding="utf-8"))
    assert set(record) >= {"scenario", "trace", "assistant_messages"}
    assert record["scenario"]["scenario_id"] == CASE_ID
    assert record["assistant_messages"] == ["Done."]
    assert record["execution_b"]["suggested_b"] == 0
    assert record["execution_b"]["review_status"] == "pending_human_review"
    assert "Suggested execution B: **0**" in status


def test_failure_keeps_partial_trace_without_leaking_error_details(monkeypatch, tmp_path):
    def failing(environment, run_id, event_callback, **_):
        event_callback(
            TraceEvent(step=1, event_type="assistant_message", payload={"content": "Checking."})
        )
        raise RuntimeError("private provider payload")

    output = tmp_path / "partial.json"
    monkeypatch.setattr(ui, "run_agent", failing)
    monkeypatch.setattr(ui, "_output_path", lambda *_: output)
    last = list(ui.run_selected_scenario(CASE_ID, "google:gemini-3.5-flash-lite", 6))[-1]
    assert "Run failed" in last[-1]
    text = output.read_text(encoding="utf-8")
    assert json.loads(text)["trace"]["termination_reason"] == "incomplete_runtime_error"
    assert json.loads(text)["execution_b"]["suggested_b"] is None
    assert "Suggested execution B: **needs review**" in last[-1]
    assert "private provider payload" not in text


def test_output_path_is_safe():
    path = ui._output_path(CASE_ID, "google:gemini-3.5-flash-lite")
    assert path.parent == Path("runs")
    assert ":" not in path.name
