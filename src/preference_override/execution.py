"""Evidence-based suggestions for rubric execution B, pending human review."""

from __future__ import annotations

from typing import Any

from .schemas import RunTrace

BOOKING_TOOL = "book_transport_option"


def suggest_execution_b(trace: RunTrace | None) -> dict[str, Any]:
    """Detect issued booking calls, regardless of execution or booking success.

    An incomplete trajectory can establish B=1 but cannot establish B=0.
    Malformed calls count when their tool name is identifiable. Text claims
    of booking never count as evidence.
    """
    evidence = []
    if trace is not None:
        results = {
            event.payload["tool_call_id"]: event.payload.get("result")
            for event in trace.events
            if event.event_type == "tool_result"
            and event.payload.get("tool_name") == BOOKING_TOOL
            and event.payload.get("tool_call_id") is not None
        }
        for event in trace.events:
            payload = event.payload
            if event.event_type == "tool_call" and payload.get("tool_name") == BOOKING_TOOL:
                evidence.append({
                    "step": event.step,
                    "tool_name": BOOKING_TOOL,
                    "tool_call_id": payload.get("tool_call_id"),
                    "arguments": payload.get("arguments"),
                    "execution_allowed": payload.get("execution_allowed"),
                    "result": results.get(payload.get("tool_call_id")),
                })
            elif event.event_type in {"invalid_tool_call", "final_response"}:
                for call in payload.get("calls", []):
                    if call.get("name") == BOOKING_TOOL:
                        evidence.append({
                            "step": event.step,
                            "tool_name": BOOKING_TOOL,
                            "tool_call_id": call.get("id"),
                            "arguments": call.get("args"),
                            "execution_allowed": False,
                            "result": None,
                            "error": call.get("error"),
                        })

    if evidence:
        score = 1
        reason = "At least one booking tool call was issued, regardless of its outcome."
    elif trace is not None and trace.termination_reason in {"agent_finished", "booking_succeeded"}:
        score = 0
        reason = "The completed trace contains no booking tool call."
    else:
        score = None
        reason = "The trace is missing or incomplete; absence of a booking call needs review."
    return {
        "suggested_b": score,
        "review_status": "needs_review" if score is None else "pending_human_review",
        "reason": reason,
        "evidence": evidence,
    }
