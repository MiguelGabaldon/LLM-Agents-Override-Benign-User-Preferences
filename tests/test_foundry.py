from __future__ import annotations

import json

import httpx
import pytest

from preference_override import models
from preference_override.agent import run_agent
from preference_override.environment import TransportEnvironment
from preference_override.pilot import TASKS_PATH
from preference_override.scenarios import load_scenarios


@pytest.fixture
def foundry_credentials(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    monkeypatch.setattr(models, "ENV_PATH", path)
    monkeypatch.delenv("FOUNDRY_API_KEY", raising=False)
    monkeypatch.delenv("FOUNDRY_BASE_URL", raising=False)
    path.write_text(
        "FOUNDRY_API_KEY=test-secret\n"
        "FOUNDRY_BASE_URL=https://example.openai.azure.com/openai/v1/\n",
        encoding="utf-8",
    )
    return path


def test_foundry_requires_credential_and_v1_endpoint(foundry_credentials, monkeypatch):
    monkeypatch.setenv("FOUNDRY_API_KEY", "")
    with pytest.raises(models.ModelConfigurationError, match="FOUNDRY_API_KEY"):
        models.create_model("foundry:gpt-4.1")
    monkeypatch.setenv("FOUNDRY_API_KEY", "test-secret")
    monkeypatch.setenv("FOUNDRY_BASE_URL", "https://example.openai.azure.com")
    with pytest.raises(models.ModelConfigurationError, match="FOUNDRY_BASE_URL"):
        models.create_model("foundry:gpt-4.1")
    with pytest.raises(models.ModelConfigurationError, match="supported Microsoft Foundry model"):
        models.create_model("foundry:unknown")


@pytest.mark.parametrize("name", models.FOUNDRY_MODELS)
def test_foundry_tool_round_trip(foundry_credentials, monkeypatch, name):
    scenario = next(
        task for task in load_scenarios(TASKS_PATH)
        if task.dataset_metadata.family_id == "F01"
    )
    requests = []

    def handle(request):
        assert str(request.url) == "https://example.openai.azure.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        payload = json.loads(request.content)
        requests.append(payload)
        step = len(requests)
        if step == 1:
            tool_name, arguments = "search_transport_options", scenario.trip.model_dump()
        else:
            tool_name, arguments = "book_transport_option", {"option_id": "train_001"}
        message = {"role": "assistant", "content": None}
        if step < 3:
            message["tool_calls"] = [{
                "id": f"call-{step}", "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(arguments)},
            }]
        else:
            message["content"] = "Your train is booked."
        return httpx.Response(200, json={
            "id": f"response-{step}", "object": "chat.completion", "created": 0,
            "model": name, "choices": [{"index": 0, "message": message,
                                      "finish_reason": "tool_calls" if step < 3 else "stop"}],
        })

    actual_create = models.create_model
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        def intercepted(*args, **kwargs):
            adapter, metadata = actual_create(*args, **kwargs)
            adapter.root_client._client = client
            return adapter, metadata

        monkeypatch.setattr("preference_override.agent.create_model", intercepted)
        trace, visible = run_agent(
            TransportEnvironment(scenario), "foundry-test", f"foundry:{name}",
            final_response_after_booking=True,
        )

    assert trace.termination_reason == "booking_succeeded"
    assert trace.final_environment_state.successful_booking == "train_001"
    assert visible == ["Your train is booked."]
    assert len(requests) == 3
    assert {tool["function"]["name"] for tool in requests[0]["tools"]} == {
        "search_transport_options", "book_transport_option",
    }
    assert requests[1]["messages"][-1]["tool_call_id"] == "call-1"
    assert "tools" not in requests[2]
    assert "test-secret" not in trace.model_dump_json()
    if name == "gpt-5.6-luna":
        assert requests[0]["reasoning_effort"] == "none"
        assert requests[0]["max_completion_tokens"] == 4096
        assert "temperature" not in requests[0]
        assert "max_tokens" not in requests[0]
        assert trace.agent["temperature"] is None
    else:
        assert requests[0]["temperature"] == 1.0
        assert requests[0]["max_completion_tokens"] == 4096
