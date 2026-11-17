"""Model selection and credentials, separate from the experiment policy."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from pydantic import SecretStr

GROQ_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b")
GOOGLE_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
OPENROUTER_MODELS = (
    "nvidia/nemotron-3.5-lightning:free",
    "cohere/north-mini-code:free",
)
FOUNDRY_MODELS = ("gpt-4.1", "gpt-5.6-luna")
# The Groq SDK appends ``/openai/v1`` to resource paths itself. Supplying that
# prefix here would produce ``/openai/v1/openai/v1/chat/completions``.
GROQ_BASE_URL = "https://api.groq.com"
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
API_PROVIDERS = {
    "groq": ("Groq", "GROQ_API_KEY", "https://console.groq.com/keys", GROQ_MODELS),
    "google": (
        "Google AI",
        "GOOGLEAI_API_KEY",
        "https://aistudio.google.com/api-keys",
        GOOGLE_MODELS,
    ),
    "openrouter": (
        "OpenRouter",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/settings/keys",
        OPENROUTER_MODELS,
    ),
    "foundry": (
        "Microsoft Foundry",
        "FOUNDRY_API_KEY",
        "https://ai.azure.com/",
        FOUNDRY_MODELS,
    ),
}
DEFAULT_MODEL = f"google:{GOOGLE_MODELS[0]}"  # API only; never run Ollama by default
# Shared requested sampling temperature where supported. Gemini 3.5 Flash-Lite
# fixes it at 1.0, and Foundry GPT-5.6 Luna does not accept temperature.
DEFAULT_TEMPERATURE = 1.0
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


class ModelConfigurationError(ValueError):
    """A model selection or credential needs user attention."""


class ModelAPIError(RuntimeError):
    """A remote model request failed; message is safe to show in the UI."""


def resolve_model(selection: str) -> tuple[str, str]:
    """Preserve bare Ollama names/tags; require an explicit prefix for the API."""

    selection = selection.strip()
    prefix, _, name = selection.partition(":")
    if prefix in {"openai", "nvidia", "qwen"}:
        raise ModelConfigurationError(
            "This provider was disabled because only free-access options are enabled. "
            "Choose a model from Models."
        )
    if prefix in API_PROVIDERS:
        label, _, _, names = API_PROVIDERS[prefix]
        if name not in names:
            raise ModelConfigurationError(f"Choose a supported {label} model from Models.")
        return prefix, name
    name = selection.removeprefix("ollama:")
    if not name:
        raise ModelConfigurationError("Choose a model before running a scenario.")
    return "ollama", name


def model_filename(selection: str) -> str:
    """Make model IDs (including provider slashes) safe in run filenames."""

    return re.sub(r"[^a-zA-Z0-9._-]", "_", selection)


def groq_api_key() -> str:
    """Backward-compatible credential accessor for Groq."""
    return provider_api_key("groq")


def env_value(name: str) -> str | None:
    """Read a setting without mutating the process environment."""
    if name in os.environ:
        return os.environ[name]
    return dotenv_values(ENV_PATH, interpolate=False).get(name)


def provider_api_key(provider: str) -> str:
    """Read only the required secret; environment variables take precedence.

    Re-read the project .env for each run so edits work without restarting the UI.
    Do not inject file contents into the process environment or saved run records.
    """

    _, variable, link, _ = API_PROVIDERS[provider]
    key = env_value(variable)
    if not key or not key.strip() or key.strip().startswith("your_"):
        raise ModelConfigurationError(
            f"Missing {variable}. Add it to the project .env file. "
            f"Create a key at {link}, or choose the local Ollama model."
        )
    return key.strip()


def create_model(
    selection: str,
    temperature: float = DEFAULT_TEMPERATURE,
    ollama_base_url: str = "http://localhost:11434",
) -> tuple[BaseChatModel, dict[str, Any]]:
    """Return the adapter and a credential-free configuration for the trace."""

    provider, name = resolve_model(selection)
    metadata: dict[str, Any] = {
        "provider": provider,
        "model_name": name,
        "temperature": temperature,
    }
    if provider == "ollama":
        metadata["base_url"] = ollama_base_url
        return ChatOllama(
            model=name, base_url=ollama_base_url, temperature=temperature, reasoning=True
        ), metadata

    key = provider_api_key(provider)
    if provider == "google":
        return _create_google_model(name, key, metadata)
    if provider == "openrouter":
        return _create_openrouter_model(name, key, metadata)
    if provider == "foundry":
        return _create_foundry_model(name, key, metadata)
    # Keep local inference usable independently of the optional API adapter.
    from langchain_groq import ChatGroq

    metadata.update(
        base_url=GROQ_BASE_URL,
        max_tokens=4096,
        reasoning_effort="medium",
        timeout=60,
        max_retries=0,
    )
    reasoning_kwargs: dict[str, Any]
    if name.startswith("qwen/"):
        metadata["reasoning_format"] = "parsed"
        reasoning_kwargs = {"reasoning_format": "parsed"}
    else:
        metadata["include_reasoning"] = True
        reasoning_kwargs = {"model_kwargs": {"include_reasoning": True}}
    adapter = ChatGroq(
        model=name,
        api_key=SecretStr(key),
        base_url=GROQ_BASE_URL,
        temperature=temperature,
        max_tokens=metadata["max_tokens"],
        reasoning_effort=metadata["reasoning_effort"],
        **reasoning_kwargs,
        timeout=metadata["timeout"],
        max_retries=metadata["max_retries"],
    )
    # ChatGroq normalizes zero to 1e-8. Record what is actually sent as well.
    metadata["requested_temperature"] = temperature
    metadata["temperature"] = adapter.temperature
    return adapter, metadata


def _create_google_model(
    name: str, key: str, metadata: dict[str, Any]
) -> tuple[BaseChatModel, dict[str, Any]]:
    """Native Gemini adapter preserves thought signatures between tool turns."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    metadata.update(
        base_url=GOOGLE_BASE_URL,
        vertexai=False,
        requested_temperature=metadata["temperature"],
        temperature=1.0,
        thinking_level="medium",
        include_thoughts=False,
        max_output_tokens=4096,
        timeout=60,
        max_retries=0,
    )
    adapter = ChatGoogleGenerativeAI(
        model=name,
        api_key=SecretStr(key),
        vertexai=False,
        temperature=metadata["temperature"],
        thinking_level="medium",
        include_thoughts=False,
        max_output_tokens=4096,
        timeout=60,
        max_retries=0,
    )
    return adapter, metadata


def _create_openrouter_model(
    name: str, key: str, metadata: dict[str, Any]
) -> tuple[BaseChatModel, dict[str, Any]]:
    """Use a fixed free model and reject providers with any token/request price."""
    from langchain_openai.chat_models.base import BaseChatOpenAI

    if not name.endswith(":free"):
        raise ModelConfigurationError("OpenRouter only allows explicit :free model IDs.")
    extra_body = {
        "provider": {
            "max_price": {"prompt": 0, "completion": 0, "request": 0},
            "require_parameters": True,
        },
    }
    metadata.update(
        base_url=OPENROUTER_BASE_URL,
        max_tokens=4096,
        extra_body=extra_body,
        timeout=60,
        max_retries=0,
    )
    adapter = BaseChatOpenAI(
        model=name,
        api_key=SecretStr(key),
        base_url=OPENROUTER_BASE_URL,
        temperature=metadata["temperature"],
        max_tokens=4096,
        extra_body=extra_body,
        timeout=60,
        max_retries=0,
        use_responses_api=False,
        organization="",
        openai_proxy="",
    )
    return adapter, metadata


def _create_foundry_model(
    name: str, key: str, metadata: dict[str, Any]
) -> tuple[BaseChatModel, dict[str, Any]]:
    """Use the resource's OpenAI v1 route, with model-specific tool settings."""
    from langchain_openai import ChatOpenAI

    base_url = (env_value("FOUNDRY_BASE_URL") or "").strip().rstrip("/")
    if not base_url.startswith("https://") or not base_url.endswith("/openai/v1"):
        raise ModelConfigurationError(
            "Set FOUNDRY_BASE_URL to the HTTPS Azure OpenAI v1 base URL "
            "shown by Foundry (ending in /openai/v1)."
        )
    metadata.update(base_url=base_url, timeout=60, max_retries=0)
    options: dict[str, Any] = {
        "model": name,
        "api_key": SecretStr(key),
        "base_url": base_url,
        "timeout": 60,
        "max_retries": 0,
        "use_responses_api": False,
    }
    if name == "gpt-5.6-luna":
        # Chat Completions can combine GPT-5.6 tools only with no reasoning.
        # Reasoning models reject temperature and max_tokens.
        metadata.update(temperature=None, reasoning_effort="none", max_completion_tokens=4096)
        options.update(reasoning_effort="none", max_completion_tokens=4096)
    else:
        metadata["max_tokens"] = 4096
        options.update(temperature=metadata["temperature"], max_tokens=4096)
    return ChatOpenAI(**options), metadata


def api_error_message(error: Exception, provider: str = "groq") -> str:
    """Avoid exposing credentials or raw provider payloads in browser errors."""

    # LangChain's Google exceptions keep the HTTP code on the original cause.
    status = None
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "status_code", None) or getattr(current, "code", None)
        if isinstance(code, int):
            status = code
            break
        current = current.__cause__ or current.__context__
    label, variable, _, _ = API_PROVIDERS[provider]
    if status == 429:
        return f"{label} quota/rate limit reached. Wait before retrying or select Ollama locally."
    if status in (401, 403):
        return f"{label} rejected access. Check {variable} and model permissions in your account."
    if status == 404:
        return (
            f"The selected model is unavailable on {label}. Check the endpoint and account access."
        )
    if status == 400:
        return f"{label} rejected the request. Check model, region, endpoint and tool-call support."
    if status is not None and 500 <= status < 600:
        return f"{label} service error (HTTP {status}). Try again later."
    return f"{label} request failed. Check your connection and service/model availability."
