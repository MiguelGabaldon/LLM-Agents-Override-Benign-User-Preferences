from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from preference_override import models
from preference_override.agent import run_agent
from preference_override.environment import TransportEnvironment
from preference_override.scenarios import load_scenarios

SELECTIONS = [
    *(f"google:{name}" for name in models.GOOGLE_MODELS),
    *(f"openrouter:{name}" for name in models.OPENROUTER_MODELS),
]


@pytest.fixture
def credentials(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    monkeypatch.setattr(models, "ENV_PATH", path)
    for variable in (
        "GROQ_API_KEY",
        "GOOGLEAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
    ):
        monkeypatch.delenv(variable, raising=False)
    path.write_text("GROQ_API_KEY=groq-must-not-be-used\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("selection", SELECTIONS)
def test_credentials_do_not_fall_back_to_another_provider(credentials, selection):
    provider = selection.split(":", 1)[0]
    variable = models.API_PROVIDERS[provider][1]
    with pytest.raises(models.ModelConfigurationError, match=f"Missing {variable}"):
        models.create_model(selection)


@pytest.mark.parametrize("selection", SELECTIONS)
def test_native_and_compatible_tool_round_trips(credentials, monkeypatch, selection):
    """Run real serializers over mocked HTTP, including Gemini thought signatures."""
    provider, name = models.resolve_model(selection)
    variable = models.API_PROVIDERS[provider][1]
    credentials.write_text(f"{variable}=test-{provider}-secret\n", encoding="utf-8")
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            tool_name = "search_transport_options"
            arguments = {"origin": "Urbana", "destination": "Chicago", "travel_date": "2026-11-15"}
        else:
            tool_name = "book_transport_option"
            arguments = {"option_id": "train_001"}
        if provider == "google":
            assert request.url.host == "generativelanguage.googleapis.com"
            assert name in request.url.path
            assert request.headers["x-goog-api-key"] == "test-google-secret"
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {
                                        "functionCall": {"name": tool_name, "args": arguments},
                                        "thoughtSignature": "c2lnbmF0dXJl",
                                    }
                                ],
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "modelVersion": name,
                },
            )
        assert str(request.url) == models.OPENROUTER_BASE_URL + "/chat/completions"
        assert request.headers["authorization"] == "Bearer test-openrouter-secret"
        return httpx.Response(
            200,
            json={
                "id": "test-response",
                "object": "chat.completion",
                "created": 0,
                "model": name,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning": "Private reasoning should not affect scoring.",
                            "tool_calls": [
                                {
                                    "id": f"call-{len(requests)}",
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        )

    actual_create_model = models.create_model
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:

        def create_intercepted(*args, **kwargs):
            adapter, metadata = actual_create_model(*args, **kwargs)
            if provider == "google":
                adapter.client._api_client._httpx_client = client
            else:
                adapter.root_client._client = client
            return adapter, metadata

        monkeypatch.setattr("preference_override.agent.create_model", create_intercepted)
        scenario = load_scenarios(Path("data/scenarios/transport_pilot_v0.json"))[0]
        trace, visible = run_agent(TransportEnvironment(scenario), "test-api", selection)

    assert trace.termination_reason == "booking_succeeded"
    assert trace.agent["provider"] == provider
    assert visible == []
    assert "secret" not in trace.model_dump_json()
    assert "Private reasoning" not in trace.model_dump_json()
    assert len(requests) == 2
    if provider == "google":
        config = requests[0]["generationConfig"]
        assert config["temperature"] == trace.agent["temperature"] == 1.0
        assert config["maxOutputTokens"] == 4096
        assert config["thinkingConfig"]["thinkingLevel"].lower() == "medium"
        assert config["thinkingConfig"]["includeThoughts"] is False
        parts = [part for turn in requests[1]["contents"] for part in turn["parts"]]
        assert any(part.get("thoughtSignature") == "c2lnbmF0dXJl" for part in parts)
        assert any("functionResponse" in part for part in parts)
        assert trace.agent["vertexai"] is False
    else:
        assert requests[0]["model"] == name
        assert requests[0]["max_tokens"] == 4096
        assert requests[0]["provider"]["max_price"] == {"prompt": 0, "completion": 0, "request": 0}
        assert requests[0]["provider"]["require_parameters"] is True
        assert "models" not in requests[0]  # No alternative model / paid fallback.
        assert requests[1]["messages"][-1]["tool_call_id"] == "call-1"


@pytest.mark.parametrize(
    "selection",
    [
        "openai:gpt-5.4-mini-2026-03-17",
        "nvidia:nvidia/nemotron-3-nano-30b-a3b",
        "qwen:qwen-plus",
        "openrouter:openai/gpt-oss-120b",
        "openrouter:openrouter/auto",
        "google:gemini-4-argon",
    ],
)
def test_non_free_options_rejected_before_credentials_or_network(credentials, selection):
    with pytest.raises(models.ModelConfigurationError):
        models.create_model(selection)


@pytest.mark.parametrize("provider", ["google", "openrouter"])
def test_error_uses_correct_key_name_and_hides_secret(provider):
    class AuthenticationError(Exception):
        status_code = 401

    message = models.api_error_message(AuthenticationError("private-secret"), provider)
    assert models.API_PROVIDERS[provider][1] in message
    assert "private-secret" not in message


def test_google_native_quota_error():
    class QuotaError(Exception):
        code = 429

    assert "quota/rate limit" in models.api_error_message(QuotaError("secret"), "google")


def test_new_credential_names_reload_and_environment_override(credentials, monkeypatch):
    credentials.write_text(
        "GOOGLEAI_API_KEY=file-first\nOPENROUTER_API_KEY=router-key\n", encoding="utf-8"
    )
    assert models.provider_api_key("google") == "file-first"
    assert models.provider_api_key("openrouter") == "router-key"
    credentials.write_text("GOOGLEAI_API_KEY=file-updated\n", encoding="utf-8")
    assert models.provider_api_key("google") == "file-updated"
    monkeypatch.setenv("GOOGLEAI_API_KEY", "env-google")
    assert models.provider_api_key("google") == "env-google"
