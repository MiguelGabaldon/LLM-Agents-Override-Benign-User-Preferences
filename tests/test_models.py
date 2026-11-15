from __future__ import annotations

import json

import httpx
import langchain_groq
import pytest

from preference_override import models
from preference_override.agent import run_agent
from preference_override.environment import TransportEnvironment
from preference_override.pilot import TASKS_PATH
from preference_override.scenarios import load_scenarios


def f01_task():
    """First Urbana -> Chicago task (family F01, options train_001 / bus_001)."""
    return next(t for t in load_scenarios(TASKS_PATH) if t.dataset_metadata.family_id == "F01")


@pytest.fixture
def isolated_credentials(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(models, "ENV_PATH", env_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    for variable in (
        "OPENAI_API_KEY",
        "NVIDIA_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_BASE_URL",
        "NVIDIA_BASE_URL",
        "GOOGLEAI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)
    return env_path


def test_credentials_reload_file_and_respect_environment(isolated_credentials, monkeypatch):
    isolated_credentials.write_text('GROQ_API_KEY="test-first"\n', encoding="utf-8")
    assert models.groq_api_key() == "test-first"
    isolated_credentials.write_text("GROQ_API_KEY=test-second\n", encoding="utf-8")
    assert models.groq_api_key() == "test-second"
    monkeypatch.setenv("GROQ_API_KEY", "test-environment")
    assert models.groq_api_key() == "test-environment"


@pytest.mark.parametrize("value", [None, "", "   ", "your_groq_api_key_here"])
def test_missing_key_has_actionable_error(isolated_credentials, value):
    if value is not None:
        isolated_credentials.write_text(f'GROQ_API_KEY="{value}"\n', encoding="utf-8")
    with pytest.raises(models.ModelConfigurationError, match="Missing GROQ_API_KEY"):
        models.create_model("groq:openai/gpt-oss-120b")


def test_local_model_needs_no_credentials_and_retains_settings(isolated_credentials):
    adapter, metadata = models.create_model("qwen3:4b")
    assert adapter.model == "qwen3:4b"
    assert adapter.reasoning is True
    assert adapter.temperature == 0
    assert adapter.base_url == "http://localhost:11434"
    assert metadata == {
        "provider": "ollama",
        "model_name": "qwen3:4b",
        "temperature": 0,
        "base_url": "http://localhost:11434",
    }
    assert models.resolve_model("custom/model:tag") == ("ollama", "custom/model:tag")
    assert models.resolve_model("ollama:qwen3:4b") == ("ollama", "qwen3:4b")


def test_unknown_api_model_does_not_fall_back_to_ollama():
    with pytest.raises(models.ModelConfigurationError, match="supported Groq model"):
        models.resolve_model("groq:unknown")


@pytest.fixture
def mock_groq(monkeypatch, isolated_credentials):
    """Exercise real LangChain + Groq serialization over a fake HTTP transport."""
    isolated_credentials.write_text("GROQ_API_KEY=test-secret\n", encoding="utf-8")
    requests = []
    responses = []

    def handle(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        requests.append(json.loads(request.content))
        return responses.pop(0)

    actual_chat_groq = langchain_groq.ChatGroq
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:

        def factory(**kwargs):
            return actual_chat_groq(http_client=client, **kwargs)

        monkeypatch.setattr(langchain_groq, "ChatGroq", factory)
        yield requests, responses


def tool_response(name, arguments, call_id):
    return httpx.Response(
        200,
        json={
            "id": "test-completion",
            "object": "chat.completion",
            "created": 0,
            "model": "openai/gpt-oss-120b",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning": "Private test reasoning, never a user-visible recommendation.",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                        ],
                    },
                }
            ],
        },
    )


@pytest.mark.parametrize("selection", [f"groq:{name}" for name in models.GROQ_MODELS])
def test_groq_tool_round_trip_and_safe_trace(mock_groq, selection):
    requests, responses = mock_groq
    responses.extend(
        [
            tool_response(
                "search_transport_options",
                {
                    "origin": "Urbana",
                    "destination": "Chicago",
                    "travel_date": "2027-02-08",
                },
                "search-1",
            ),
            tool_response("book_transport_option", {"option_id": "train_001"}, "book-1"),
        ]
    )
    scenario = f01_task()
    trace, visible = run_agent(TransportEnvironment(scenario), "test-api", model_name=selection)
    assert trace.termination_reason == "booking_succeeded"
    assert trace.final_environment_state.successful_booking == "train_001"
    assert visible == []
    assert trace.agent["provider"] == "groq"
    assert trace.agent["model_name"] == selection.removeprefix("groq:")
    assert trace.agent["temperature"] == requests[0]["temperature"]
    assert trace.agent["requested_temperature"] == 0
    assert requests[0]["model"] == trace.agent["model_name"]
    assert requests[0]["reasoning_effort"] == "medium"
    if selection.startswith("groq:qwen/"):
        assert requests[0]["reasoning_format"] == "parsed"
        assert "include_reasoning" not in requests[0]
    else:
        assert requests[0]["include_reasoning"] is True
    assert requests[0]["max_tokens"] == 4096
    assert {tool["function"]["name"] for tool in requests[0]["tools"]} == {
        "search_transport_options",
        "book_transport_option",
    }
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert requests[1]["messages"][-1]["tool_call_id"] == "search-1"
    assert "test-secret" not in trace.model_dump_json()
    assert "Private test reasoning" not in trace.model_dump_json()


@pytest.mark.parametrize("status, expected", [(429, "quota/rate limit"), (401, "rejected access")])
def test_api_errors_are_safe_and_do_not_retry(mock_groq, status, expected):
    requests, responses = mock_groq
    responses.append(httpx.Response(status, json={"error": {"message": "test-secret"}}))
    scenario = f01_task()
    with pytest.raises(models.ModelAPIError, match=expected) as caught:
        run_agent(TransportEnvironment(scenario), "test-error", "groq:openai/gpt-oss-120b")
    assert "test-secret" not in str(caught.value)
    assert len(requests) == 1

