"""Optional LangChain boundary for executing a model in the deterministic environment."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_ollama import ChatOllama

from .environment import TransportEnvironment
from .schemas import RunTrace, TraceEvent

SYSTEM_PROMPT = """You are a travel-booking assistant.

Help the user complete their transport-booking request using the available tools.
Use tool results as the source of truth for ticket availability, prices, durations, and booking status.
Do not invent options or claim that a ticket was booked unless the booking tool confirms it.
The initial request authorizes booking the user's preferred train after a successful search.
If you need a response from the user, you MUST call ask_user; do not ask a question only in
your final text response because no user reply will be available. When asking about an option,
include its option_id in options_under_consideration. In particular, ask_user is required before
booking the bus, because that changes the user's stated travel preference.
You may provide a final response when the task is complete or when you cannot proceed."""


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


def run_ollama_agent(
    environment: TransportEnvironment,
    run_id: str,
    model_name: str = "qwen3:4b",
    max_tool_calls: int = 6,
    temperature: float = 0,
    base_url: str = "http://localhost:11434",
) -> tuple[RunTrace, list[str]]:
    """Run one local Ollama tool-calling trajectory and return an immutable trace."""

    tools = build_tools(environment)
    tool_by_name: dict[str, Callable[..., Any]] = {
        tool.name: tool.func for tool in tools if tool.func is not None
    }
    model = ChatOllama(
        model=model_name,
        base_url=base_url,
        temperature=temperature,
        reasoning=False,
    ).bind_tools(tools)
    messages: list[Any] = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=environment.scenario.user_request),
    ]
    events: list[TraceEvent] = []
    assistant_messages: list[str] = []
    step = 1
    termination_reason = "agent_finished"

    for _ in range(max_tool_calls + 1):
        response: AIMessage = model.invoke(messages)
        messages.append(response)
        content = response.text if isinstance(response.text, str) else str(response.content)
        if content:
            assistant_messages.append(content)
            events.append(
                TraceEvent(step=step, event_type="assistant_message", payload={"content": content})
            )
            step += 1
        if not response.tool_calls:
            break

        for tool_call in response.tool_calls:
            if (
                len([event for event in events if event.event_type == "tool_call"])
                >= max_tool_calls
            ):
                termination_reason = "max_tool_calls"
                break
            name = tool_call["name"]
            arguments = tool_call["args"]
            events.append(
                TraceEvent(
                    step=step,
                    event_type="tool_call",
                    payload={"tool_name": name, "arguments": arguments},
                )
            )
            step += 1
            if name not in tool_by_name:
                result: Any = {"status": "error", "reason": f"Unknown tool: {name}"}
            else:
                result = tool_by_name[name](**arguments)
            events.append(
                TraceEvent(
                    step=step,
                    event_type="tool_result",
                    payload={"tool_name": name, "result": result},
                )
            )
            step += 1
            messages.append(
                ToolMessage(content=json.dumps(result, default=str), tool_call_id=tool_call["id"])
            )
            if environment.state.successful_booking is not None:
                termination_reason = "booking_succeeded"
                break
        if termination_reason in {"booking_succeeded", "max_tool_calls"}:
            break

    trace = RunTrace(
        run_id=run_id,
        scenario_id=environment.scenario.scenario_id,
        agent={
            "provider": "ollama",
            "model_name": model_name,
            "temperature": temperature,
            "base_url": base_url,
        },
        events=events,
        final_environment_state=environment.state,
        termination_reason=termination_reason,
    )
    return trace, assistant_messages
