from __future__ import annotations

import json
from pathlib import Path

import httpx
import langchain_groq
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
@pytest.mark.parametrize("with_final_response", [False, True])
def test_native_and_compatible_tool_round_trips(
    credentials, monkeypatch, selection, with_final_response
):
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
            parts = (
                [{"text": "Your train is booked."}]
                if len(requests) == 3
                else [
                    {
                        "functionCall": {"name": tool_name, "args": arguments},
                        "thoughtSignature": "c2lnbmF0dXJl",
                    }
                ]
            )
            if with_final_response:
                parts.insert(0, {"text": "Provider thought summary.", "thought": True})
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": parts,
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "modelVersion": name,
                },
            )
        assert str(request.url) == models.OPENROUTER_BASE_URL + "/chat/completions"
        assert request.headers["authorization"] == "Bearer test-openrouter-secret"
        if len(requests) == 3:
            return httpx.Response(
                200,
                json={
                    "id": "final-response",
                    "object": "chat.completion",
                    "created": 0,
                    "model": name,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "Your train is booked."},
                        }
                    ],
                },
            )
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
        trace, visible = run_agent(
            TransportEnvironment(scenario),
            "test-api",
            selection,
            final_response_after_booking=with_final_response,
            capture_reasoning=with_final_response,
        )

    assert trace.termination_reason == "booking_succeeded"
    assert trace.agent["provider"] == provider
    assert visible == (["Your train is booked."] if with_final_response else [])
    assert "secret" not in trace.model_dump_json()
    assert "Private reasoning" not in trace.model_dump_json()
    assert len(requests) == (3 if with_final_response else 2)
    final_statuses = [e.payload["status"] for e in trace.events if e.event_type == "final_response"]
    assert final_statuses == (["completed"] if with_final_response else [])
    if with_final_response:
        assert "tools" not in requests[2]  # Final text turn cannot execute another booking.
        if provider == "google":
            final_parts = [part for turn in requests[2]["contents"] for part in turn["parts"]]
            assert sum("functionResponse" in part for part in final_parts) == 2
            assert any(part.get("thoughtSignature") == "c2lnbmF0dXJl" for part in final_parts)
        else:
            assert requests[2]["messages"][-1]["role"] == "tool"
            assert requests[2]["messages"][-1]["tool_call_id"] == "call-2"
    if provider == "google":
        config = requests[0]["generationConfig"]
        if name == "gemini-3.5-flash-lite":
            # This model uses provider-fixed sampling and the current SDK removes
            # custom temperature from the serialized request.
            assert "temperature" not in config
            assert trace.agent["requested_temperature"] == 0
        else:
            assert config["temperature"] == trace.agent["temperature"] == 1.0
        assert config["maxOutputTokens"] == 4096
        thinking = config["thinkingConfig"]
        assert (
            thinking.get("includeThoughts", thinking.get("include_thoughts")) is with_final_response
        )
        if "thinkingLevel" in config["thinkingConfig"]:
            assert config["thinkingConfig"]["thinkingLevel"].lower() == "medium"
        assert trace.agent["thinking_level"] == "medium"
        assert trace.agent["include_thoughts"] is with_final_response
        captured = [e.payload["content"] for e in trace.events if e.event_type == "reasoning"]
        assert captured == (["Provider thought summary."] * 3 if with_final_response else [])
        assert "Provider thought summary." not in " ".join(visible)
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


@pytest.mark.parametrize(
    "code, status, expected",
    [
        (400, "INVALID_ARGUMENT", "rejected the request"),
        (401, "UNAUTHENTICATED", "rejected access"),
        (403, "PERMISSION_DENIED", "rejected access"),
        (404, "NOT_FOUND", "unavailable"),
        (429, "RESOURCE_EXHAUSTED", "quota/rate limit"),
        (503, "UNAVAILABLE", "service error (HTTP 503)"),
    ],
)
def test_google_wrapped_http_errors_remain_classified(credentials, code, status, expected):
    credentials.write_text("GOOGLEAI_API_KEY=test-not-a-real-key\n", encoding="utf-8")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            code,
            json={
                "error": {
                    "code": code,
                    "status": status,
                    "message": "private provider payload",
                }
            },
        )

    adapter, _ = models.create_model("google:gemini-3.1-flash-lite")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        adapter.client._api_client._httpx_client = client
        with pytest.raises(Exception) as caught:
            adapter.invoke("Test request")
    message = models.api_error_message(caught.value, "google")
    assert expected in message
    assert "private provider payload" not in message
    assert "test-not-a-real-key" not in message
    assert len(requests) == 1


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


@pytest.mark.parametrize(
    "selection", ["qwen3:4b", *(f"groq:{name}" for name in models.GROQ_MODELS)]
)
def test_final_text_round_trip_preserves_tool_history(monkeypatch, selection):
    scenario = load_scenarios(Path("data/scenarios/transport_pilot_v0_2.json"))[0]
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        step = len(requests)
        if step == 1:
            name, arguments = "search_transport_options", scenario.trip.model_dump()
        else:
            name, arguments = "book_transport_option", {"option_id": "train_001"}
        if selection == "qwen3:4b":
            assert request.url.path == "/api/chat"
            message = {"role": "assistant", "content": ""}
            message["thinking"] = "Provider-only reasoning."
            if step < 3:
                message["tool_calls"] = [{"function": {"name": name, "arguments": arguments}}]
            else:
                message["content"] = "Your train ticket is booked."
            return httpx.Response(
                200,
                json={"model": "qwen3:4b", "message": message, "done": True, "done_reason": "stop"},
            )
        assert request.url.path.endswith("/chat/completions")
        message = {"role": "assistant", "content": None, "reasoning": "Provider-only reasoning."}
        if step < 3:
            message["tool_calls"] = [
                {
                    "id": f"call-{step}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        else:
            message["content"] = "Your train ticket is booked."
        return httpx.Response(
            200,
            json={
                "id": f"response-{step}",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if step < 3 else "stop",
                        "message": message,
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(models, "provider_api_key", lambda _: "test-not-a-real-key")
    with httpx.Client(transport=transport) as client:
        original_ollama = models.ChatOllama
        original_groq = langchain_groq.ChatGroq
        monkeypatch.setattr(
            models,
            "ChatOllama",
            lambda **kwargs: original_ollama(**kwargs, sync_client_kwargs={"transport": transport}),
        )
        monkeypatch.setattr(
            langchain_groq, "ChatGroq", lambda **kwargs: original_groq(**kwargs, http_client=client)
        )
        trace, visible = run_agent(
            TransportEnvironment(scenario),
            "provider-final",
            selection,
            final_response_after_booking=True,
            capture_reasoning=True,
        )
    assert len(requests) == 3
    assert not requests[-1].get("tools")
    assert requests[-1]["messages"][-1]["role"] == "tool"
    assert sum(message["role"] == "user" for message in requests[-1]["messages"]) == 1
    assert visible == ["Your train ticket is booked."]
    assert any(
        e.event_type == "final_response" and e.payload["status"] == "completed"
        for e in trace.events
    )
    assert trace.final_environment_state.successful_booking == "train_001"
    assert [e.payload["content"] for e in trace.events if e.event_type == "reasoning"] == [
        "Provider-only reasoning."
    ] * 3
