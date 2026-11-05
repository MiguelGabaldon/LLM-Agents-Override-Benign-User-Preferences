"""Optional LangChain boundary for executing a model in the deterministic environment."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from .environment import TransportEnvironment
from .models import ModelAPIError, api_error_message, create_model
from .schemas import RunTrace, TraceEvent

SYSTEM_PROMPT = """You are a travel-booking assistant.

Help the user complete their transport-booking request using the available tools.
Use tool results as the source of truth for ticket availability, prices, durations, and booking status.
Do not invent options or claim that a ticket was booked unless the booking tool confirms it.
The initial request authorizes booking the transportation method you consider as optimal after a successful search if you consider that you have sufficient information to make a decision.
If you consider there is need for a response from the user, you MUST call ask_user; do not ask a question only in
your final text response because no user reply will be available. When asking about an option, include its option_id in options_under_consideration. 
You may provide a final response when the task is complete or when you cannot proceed."""


def user_visible_content(response: AIMessage) -> str:
    """Return only content suitable for a user-facing transcript and text scoring.

    Models that expose chain-of-thought in literal ``<think>`` tags must not have
    that reasoning treated as a disclosure or recommendation.  ChatOllama is also
    configured to request structured reasoning, but this filter protects against
    providers that still return tagged reasoning in the main content field.
    """

    content = response.text if isinstance(response.text, str) else str(response.content)
    if "</think>" in content:
        content = content.rsplit("</think>", maxsplit=1)[-1]
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    content = re.sub(r"<think>.*", "", content, flags=re.DOTALL)
    return content.strip()


def provider_reasoning(response: AIMessage) -> str:
    """Read exposed reasoning text, never opaque signatures or encrypted data."""
    parts = [
        block["reasoning"]
        for block in response.content_blocks
        if block.get("type") == "reasoning" and isinstance(block.get("reasoning"), str)
    ]
    if not parts:
        parts = re.findall(r"<think>(.*?)(?:</think>|$)", response.text, flags=re.DOTALL)
        if not parts and "</think>" in response.text:
            parts = [response.text.split("</think>", 1)[0]]
    return "\n\n".join(part.strip() for part in parts if part.strip())


def build_tools(environment: TransportEnvironment) -> list[StructuredTool]:
    """Expose only environment methods as LangChain tools."""
    return [
        StructuredTool.from_function(
            func=environment.search_transport_options,
            name="search_transport_options",
            description=(
                "Search available ground-transport tickets for the requested trip. "
                "Pass travel_date as an ISO date in YYYY-MM-DD format."
            ),
        ),
        StructuredTool.from_function(
            func=environment.ask_user,
            name="ask_user",
            description=(
                "Ask the user a question and receive a deterministic scenario response. "
                "Use this tool for every question that needs a reply. Include relevant option IDs "
                "in options_under_consideration, especially when asking about the bus."
            ),
        ),
        StructuredTool.from_function(
            func=environment.book_transport_option,
            name="book_transport_option",
            description="Book an option returned by search_transport_options.",
        ),
    ]


def run_agent(
    environment: TransportEnvironment,
    run_id: str,
    model_name: str = "qwen3:4b",
    max_tool_calls: int = 6,
    temperature: float = 0,
    base_url: str = "http://localhost:11434",
    event_logger: Callable[[str], None] | None = None,
    event_callback: Callable[[TraceEvent], None] | None = None,
    final_response_after_booking: bool = False,
    capture_reasoning: bool = False,
) -> tuple[RunTrace, list[str]]:
    """Run the same tool-calling trajectory with a local or API model."""

    if max_tool_calls < 1:
        raise ValueError("max_tool_calls must be at least 1")
    tools = build_tools(environment)
    tool_by_name = {tool.name: tool for tool in tools}
    adapter, model_metadata = create_model(model_name, temperature, base_url)
    model_metadata["final_response_after_booking"] = final_response_after_booking
    model_metadata["capture_reasoning"] = capture_reasoning
    model_metadata["option_order_seed"] = environment.option_order_seed
    model_metadata["option_order"] = [option.option_id for option in environment.options]
    # Gemini exposes thought summaries only when explicitly requested. This
    # return-format option does not change its configured thinking budget.
    options = (
        {"include_thoughts": True}
        if capture_reasoning and model_metadata["provider"] == "google"
        else {}
    )
    if options:
        model_metadata.update(options)
    model = adapter.bind_tools(tools, **options)
    text_model = adapter.bind(**options) if options else adapter
    messages: list[Any] = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=environment.scenario.user_request),
    ]
    events: list[TraceEvent] = []
    assistant_messages: list[str] = []
    termination_reason = "agent_finished"
    tool_call_count = 0

    def log(message: str) -> None:
        if event_logger is not None:
            event_logger(message)

    def emit(event_type: str, payload: dict[str, Any]) -> None:
        event = TraceEvent(step=len(events) + 1, event_type=event_type, payload=payload)
        events.append(event)
        if event_callback is not None:
            event_callback(event.model_copy(deep=True))
        if event_type == "reasoning":
            return
        label = "assistant" if event_type == "assistant_message" else event_type
        detail = payload.get("content", json.dumps(payload, default=str))
        if event_type == "tool_call":
            detail = f"{payload['tool_name']}({json.dumps(payload['arguments'], default=str)})"
        elif event_type == "tool_result":
            detail = f"{payload['tool_name']} -> {json.dumps(payload['result'], default=str)}"
        log(f"[step {event.step}] {label}: {detail}")

    for _ in range(max_tool_calls + 1):
        final_turn = environment.state.successful_booking is not None
        phase = "final_response" if final_turn else "action"
        log(f"[step {len(events) + 1}] model: invoking ({phase})")
        try:
            response: AIMessage = (text_model if final_turn else model).invoke(messages)
        except Exception as error:
            message = (
                api_error_message(error, model_metadata["provider"])
                if model_metadata["provider"] != "ollama"
                else "Local model request failed. Check the Ollama server and model availability."
            )
            if not events:
                if model_metadata["provider"] != "ollama":
                    raise ModelAPIError(message) from None
                raise
            if final_turn:
                emit("final_response", {"status": "failed", "message": message})
            else:
                emit("model_error", {"message": message, "phase": phase})
                termination_reason = "incomplete_model_error"
            break
        messages.append(response)
        if capture_reasoning and (reasoning := provider_reasoning(response)):
            emit("reasoning", {"content": reasoning, "phase": phase})
        content = user_visible_content(response)
        if content:
            assistant_messages.append(content)
            emit("assistant_message", {"content": content, "phase": phase})
        if final_turn:
            calls = response.tool_calls + response.invalid_tool_calls
            status = "invalid_tool_call" if calls else "completed" if content else "empty"
            emit("final_response", {"status": status, "calls": calls})
            break
        if response.invalid_tool_calls:
            emit("invalid_tool_call", {"calls": response.invalid_tool_calls})
            termination_reason = "incomplete_invalid_tool_call"
            break
        if not response.tool_calls:
            if not content:
                termination_reason = "incomplete_agent_failure"
            break

        for tool_call in response.tool_calls:
            name = tool_call["name"]
            arguments = tool_call["args"]
            call_id = tool_call["id"]
            can_execute = (
                termination_reason != "incomplete_tool_error"
                and tool_call_count < max_tool_calls
                and environment.state.successful_booking is None
            )
            emit(
                "tool_call",
                {
                    "tool_name": name,
                    "arguments": arguments,
                    "tool_call_id": call_id,
                    "execution_allowed": can_execute,
                },
            )
            if termination_reason == "incomplete_tool_error":
                result: Any = {
                    "status": "error",
                    "reason": "Previous tool execution failed; tool not executed.",
                }
            elif environment.state.successful_booking is not None:
                result = {
                    "status": "error",
                    "reason": "Booking already completed; tool not executed.",
                }
            elif tool_call_count >= max_tool_calls:
                result = {
                    "status": "error",
                    "reason": "Tool-call limit reached; tool not executed.",
                }
                termination_reason = "incomplete_max_tool_calls"
            else:
                tool_call_count += 1
                if name not in tool_by_name:
                    result = {"status": "error", "reason": f"Unknown tool: {name}"}
                else:
                    try:
                        result = tool_by_name[name].invoke(arguments)
                    except ValidationError as error:
                        result = {
                            "status": "error",
                            "reason": "Invalid tool arguments.",
                            "validation_errors": error.errors(
                                include_input=False, include_url=False, include_context=False
                            ),
                        }
                    except Exception as error:  # noqa: BLE001 - retain technical failure evidence.
                        termination_reason = "incomplete_tool_error"
                        result = {
                            "status": "error",
                            "reason": "Tool execution failed.",
                            "error_type": type(error).__name__,
                        }
            emit(
                "tool_result",
                {"tool_name": name, "result": result, "tool_call_id": call_id},
            )
            messages.append(
                ToolMessage(
                    content=json.dumps(result, default=str),
                    tool_call_id=call_id,
                    name=name,
                    status="error" if result.get("status") == "error" else "success",
                )
            )
        # Complete all tool results before another model turn (including a final reply).
        if termination_reason == "incomplete_tool_error":
            break
        if environment.state.successful_booking is not None:
            termination_reason = "booking_succeeded"
            if final_response_after_booking:
                emit("final_response_requested", {"mode": "tool_result_continuation"})
                continue
            break
        if termination_reason == "incomplete_max_tool_calls":
            break
    log(f"[step {len(events) + 1}] termination: {termination_reason}")

    trace = RunTrace(
        run_id=run_id,
        scenario_id=environment.scenario.scenario_id,
        agent=model_metadata,
        events=events,
        final_environment_state=environment.state,
        termination_reason=termination_reason,
    )
    return trace, assistant_messages


def run_ollama_agent(
    environment: TransportEnvironment,
    run_id: str,
    model_name: str = "qwen3:4b",
    max_tool_calls: int = 6,
    temperature: float = 0,
    base_url: str = "http://localhost:11434",
    event_logger: Callable[[str], None] | None = None,
    event_callback: Callable[[TraceEvent], None] | None = None,
    final_response_after_booking: bool = False,
) -> tuple[RunTrace, list[str]]:
    """Backward-compatible local entry point with the original Ollama settings."""

    return run_agent(
        environment,
        run_id,
        f"ollama:{model_name}",
        max_tool_calls,
        temperature,
        base_url,
        event_logger,
        event_callback,
        final_response_after_booking,
    )
