"""Environment and agent-loop tests (migrated from the former test_transport_pilot.py)."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

import preference_override.agent as agent_module
from preference_override.environment import TransportEnvironment
from preference_override.pilot import TASKS_PATH
from preference_override.scenarios import load_scenarios
from preference_override.schemas import Scenario


@pytest.fixture
def scenario() -> Scenario:
    """A task of family F01 (Urbana -> Chicago, options train_001 / bus_001)."""
    return next(t for t in load_scenarios(TASKS_PATH) if t.dataset_metadata.family_id == "F01")


def _search(environment: TransportEnvironment) -> None:
    result = environment.search_transport_options(**environment.scenario.trip.model_dump())
    assert result["status"] == "ok"


# --- scenario contract -------------------------------------------------------


def test_hidden_evaluation_is_not_in_initial_agent_context(scenario):
    context = scenario.agent_initial_context()
    assert "hidden_evaluation" not in context
    assert "options" not in context
    assert context["user_request"] == scenario.user_request


def test_invalid_non_dominating_scenario_is_rejected(scenario):
    invalid = scenario.model_dump()
    invalid["options"][1]["duration_minutes"] = 999
    with pytest.raises(ValidationError, match="strictly faster"):
        Scenario.model_validate(invalid)


# --- environment -------------------------------------------------------------


def test_search_accepts_iso_date(scenario):
    environment = TransportEnvironment(scenario)
    _search(environment)
    assert environment.state.search_performed is True


def test_search_normalizes_supported_natural_language_date(scenario):
    environment = TransportEnvironment(scenario)
    result = environment.search_transport_options("Urbana", "Chicago", "February 8, 2027")
    assert result["status"] == "ok"


def test_search_for_another_trip_finds_nothing(scenario):
    environment = TransportEnvironment(scenario)
    result = environment.search_transport_options("Urbana", "Chicago", "2027-02-09")
    assert result["status"] == "not_found"
    assert environment.state.search_performed is False


@pytest.mark.parametrize("seed", [0, 1])
def test_search_order_is_reproducible_and_preserves_ticket_values(scenario, seed):
    original = scenario.model_dump_json()
    environment = TransportEnvironment(scenario, option_order_seed=seed)
    result = environment.search_transport_options(**scenario.trip.model_dump())
    replay = TransportEnvironment(scenario, option_order_seed=seed)
    assert result == replay.search_transport_options(**scenario.trip.model_dump())
    assert {o["option_id"]: o for o in result["options"]} == {
        o.option_id: o.model_dump() for o in scenario.options
    }
    assert scenario.model_dump_json() == original


def test_seeded_search_can_present_either_option_first(scenario):
    orders = {
        tuple(
            o["option_id"]
            for o in TransportEnvironment(scenario, option_order_seed=seed)
            .search_transport_options(**scenario.trip.model_dump())["options"]
        )
        for seed in range(16)
    }
    original = tuple(o.option_id for o in scenario.options)
    assert orders == {original, original[::-1]}


@pytest.mark.parametrize("option_id", ["train_001", "bus_001"])
def test_either_option_can_be_booked_with_the_same_neutral_result(scenario, option_id):
    environment = TransportEnvironment(scenario)
    _search(environment)
    assert environment.book_transport_option(option_id) == {
        "status": "booked",
        "booked_option_id": option_id,
    }
    assert environment.state.successful_booking == option_id
    assert environment.state.booking_attempts[0].model_dump() == {
        "option_id": option_id,
        "status": "booked",
        "reason": None,
    }


@pytest.mark.parametrize("case", ["no_search", "unknown_option", "second_booking"])
def test_invalid_booking_requests_are_rejected_and_recorded(scenario, case):
    environment = TransportEnvironment(scenario)
    if case != "no_search":
        _search(environment)
    if case == "second_booking":
        environment.book_transport_option("train_001")
    option_id = "unknown" if case == "unknown_option" else "bus_001"
    result = environment.book_transport_option(option_id)
    assert result["status"] == "rejected"
    assert environment.state.successful_booking == (
        "train_001" if case == "second_booking" else None
    )
    attempt = environment.state.booking_attempts[-1]
    assert (attempt.option_id, attempt.status, attempt.reason) == (
        option_id,
        "rejected",
        result["reason"],
    )
    if case == "second_booking":
        assert result["booked_option_id"] == "train_001"


# --- agent loop (scripted fake model, no network) ----------------------------


def call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id}


def search_call(scenario):
    return call("search_transport_options", scenario.trip.model_dump(), "search")


def _final_response_status(trace):
    return next(
        (e.payload["status"] for e in trace.events if e.event_type == "final_response"),
        "not_requested",
    )


def scripted_model(monkeypatch, responses):
    class Model:
        def __init__(self):
            self.responses = iter(responses)
            self.inputs = []

        def bind_tools(self, tools, **_):
            return self

        def invoke(self, messages):
            self.inputs.append(list(messages))
            response = next(self.responses)
            if isinstance(response, Exception):
                raise response
            return response

    model = Model()
    monkeypatch.setattr(
        agent_module, "create_model", lambda *_: (model, {"provider": "groq", "model_name": "fake"})
    )
    return model


def test_empty_final_turn_is_marked_incomplete(monkeypatch, scenario):
    scripted_model(
        monkeypatch,
        [
            AIMessage(content="private reasoning</think>", tool_calls=[search_call(scenario)]),
            AIMessage(content="more private reasoning</think>"),
        ],
    )
    events: list[str] = []
    trace, messages = agent_module.run_agent(
        TransportEnvironment(scenario), "empty-final", event_logger=events.append
    )
    assert messages == []
    assert trace.termination_reason == "incomplete_agent_failure"
    assert any("tool_call: search_transport_options" in event for event in events)
    assert events[-1].endswith("termination: incomplete_agent_failure")


def test_final_turn_receives_every_result_and_does_not_execute_later_calls(monkeypatch, scenario):
    model = scripted_model(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[search_call(scenario)]),
            AIMessage(
                content="",
                tool_calls=[
                    call("book_transport_option", {"option_id": "train_001"}, "book"),
                    call("book_transport_option", {"option_id": "bus_001"}, "late-call"),
                ],
            ),
            AIMessage(content="Your train ticket is booked."),
        ],
    )
    trace, visible = agent_module.run_agent(
        TransportEnvironment(scenario),
        "final-turn",
        max_tool_calls=2,
        final_response_after_booking=True,
    )
    assert visible == ["Your train ticket is booked."]
    assert _final_response_status(trace) == "completed"
    assert trace.termination_reason == "booking_succeeded"
    assert len(trace.final_environment_state.booking_attempts) == 1
    final_history = model.inputs[-1]
    results = [message for message in final_history if isinstance(message, ToolMessage)]
    assert [message.tool_call_id for message in results] == ["search", "book", "late-call"]
    assert results[-1].status == "error"


def test_bad_arguments_are_returned_as_tool_feedback_and_kept_in_trace(monkeypatch, scenario):
    model = scripted_model(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[call("search_transport_options", {}, "bad")]),
            AIMessage(content="", tool_calls=[search_call(scenario)]),
            AIMessage(
                content="",
                tool_calls=[call("book_transport_option", {"option_id": "train_001"}, "book")],
            ),
        ],
    )
    trace, _ = agent_module.run_agent(TransportEnvironment(scenario), "bad-args")
    assert trace.termination_reason == "booking_succeeded"
    feedback = model.inputs[1][-1]
    assert isinstance(feedback, ToolMessage)
    assert feedback.status == "error"
    assert json.loads(feedback.content)["reason"] == "Invalid tool arguments."
    assert trace.events[1].payload["result"]["validation_errors"]


def test_tool_limit_pairs_all_results_without_executing_excess_calls(monkeypatch, scenario):
    scripted_model(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[
                    search_call(scenario),
                    call("book_transport_option", {"option_id": "train_001"}, "over-limit"),
                ],
            )
        ],
    )
    trace, _ = agent_module.run_agent(TransportEnvironment(scenario), "limit", max_tool_calls=1)
    assert trace.termination_reason == "incomplete_max_tool_calls"
    assert trace.final_environment_state.successful_booking is None
    results = [event for event in trace.events if event.event_type == "tool_result"]
    assert [event.payload["tool_call_id"] for event in results] == ["search", "over-limit"]


@pytest.mark.parametrize(
    "last_response, status",
    [
        (RuntimeError("private exception payload"), "failed"),
        (AIMessage(content=""), "empty"),
        (
            AIMessage(
                content="",
                tool_calls=[call("book_transport_option", {"option_id": "bus_001"}, "late")],
            ),
            "invalid_tool_call",
        ),
    ],
)
def test_final_response_failure_preserves_successful_booking(
    monkeypatch, scenario, last_response, status
):
    scripted_model(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[search_call(scenario)]),
            AIMessage(
                content="",
                tool_calls=[call("book_transport_option", {"option_id": "train_001"}, "book")],
            ),
            last_response,
        ],
    )
    trace, visible = agent_module.run_agent(
        TransportEnvironment(scenario), "failed-final", final_response_after_booking=True
    )
    assert trace.termination_reason == "booking_succeeded"
    assert _final_response_status(trace) == status
    assert trace.final_environment_state.successful_booking == "train_001"
    assert visible == []
    assert "private exception payload" not in trace.model_dump_json()


def test_later_model_failure_retains_partial_interaction(monkeypatch, scenario):
    scripted_model(
        monkeypatch,
        [
            AIMessage(content="I will look at the options.", tool_calls=[search_call(scenario)]),
            RuntimeError("private failure detail"),
        ],
    )
    trace, visible = agent_module.run_agent(TransportEnvironment(scenario), "partial")
    assert trace.termination_reason == "incomplete_model_error"
    assert trace.final_environment_state.search_performed
    assert visible == ["I will look at the options."]
    assert "private failure detail" not in trace.model_dump_json()


# --- visible text vs. reasoning ----------------------------------------------


def test_reasoning_text_is_excluded_from_user_visible_content():
    response = AIMessage(content="private reasoning</think>Visible response for the user.")
    assert agent_module.user_visible_content(response) == "Visible response for the user."


def test_unclosed_reasoning_is_not_shown_as_a_final_message():
    assert agent_module.user_visible_content(AIMessage(content="<think>private reasoning")) == ""


@pytest.mark.parametrize(
    "response, expected",
    [
        (
            AIMessage(content="Visible", additional_kwargs={"reasoning_content": "Exposed reasoning"}),
            "Exposed reasoning",
        ),
        (
            AIMessage(
                content=[
                    {"type": "reasoning", "reasoning": "Exposed reasoning"},
                    {"type": "text", "text": "Visible"},
                ]
            ),
            "Exposed reasoning",
        ),
        (AIMessage(content="<think>Exposed reasoning</think>Visible"), "Exposed reasoning"),
        (AIMessage(content="Exposed reasoning</think>Visible"), "Exposed reasoning"),
        (AIMessage(content="<think>Exposed reasoning"), "Exposed reasoning"),
        (AIMessage(content=[{"type": "reasoning", "encrypted_content": "opaque-data"}]), ""),
        (AIMessage(content="Visible"), ""),
    ],
)
def test_reasoning_extraction_uses_only_exposed_text(response, expected):
    assert agent_module.provider_reasoning(response) == expected
    assert "Exposed reasoning" not in agent_module.user_visible_content(response)
