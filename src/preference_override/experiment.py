"""Sequential experiment execution with incremental snapshots and safe resume."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .environment import TransportEnvironment
from .execution import suggest_execution_b
from .models import ModelAPIError, ModelConfigurationError, model_filename
from .schemas import RunTrace, Scenario, TraceEvent
from .storage import atomic_json


def now() -> str:
    return datetime.now(UTC).isoformat()


def message_history(system_prompt: str, user_prompt: str, events: list[TraceEvent]) -> list[dict]:
    messages = [
        {"type": "system", "content": system_prompt},
        {"type": "human", "content": user_prompt},
    ]
    for event in events:
        if event.event_type == "model_message":
            messages.append(event.payload["message"])
        elif event.event_type == "tool_result":
            messages.append({"type": "tool", **event.payload})
    return messages


def token_usage(events: list[TraceEvent]) -> dict[str, Any]:
    """Sum reported usage only; unavailable counts are never invented as zero."""
    usages = [
        e.payload["message"].get("usage_metadata")
        for e in events
        if e.event_type == "model_message"
    ]
    reported = [u for u in usages if u]
    return {
        **{
            key: sum(u.get(key, 0) for u in reported) if reported else None
            for key in ("input_tokens", "output_tokens", "total_tokens")
        },
        "model_responses": len(usages),
        "responses_with_usage": len(reported),
        "complete": bool(usages) and len(reported) == len(usages),
    }


def run_experiment(
    tasks: list[Scenario],
    model: str,
    output_root: Path,
    *,
    trial_id: int = 1,
    resume: bool = False,
    max_tool_calls: int = 6,
    temperature: float = 0,
) -> dict[str, int]:
    from .agent import SYSTEM_PROMPT, build_tools, run_agent
    from .pilot import option_order_seed

    if trial_id < 1 or max_tool_calls < 1:
        raise ValueError("trial_id and max_tool_calls must be positive")
    model = model.strip()
    folder = model_filename(model)
    if folder in {"", ".", ".."}:
        raise ValueError("A model identifier is required")
    directory = output_root / folder
    directory.mkdir(parents=True, exist_ok=True)
    completed = set()
    if resume:
        for path in directory.glob("*.json"):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ValueError(f"Cannot inspect saved run {path}: {error}") from error
            if saved.get("status") == "completed":
                completed.add(saved.get("resume_key"))

    summary = {"completed": 0, "failed": 0, "skipped": 0}
    for task in tasks:
        environment = TransportEnvironment(
            task, option_order_seed=option_order_seed(task.scenario_id)
        )
        config = {
            "model": model,
            "trial_id": trial_id,
            "temperature": temperature,
            "max_tool_calls": max_tool_calls,
            "final_response_after_booking": True,
            "option_order_seed": environment.option_order_seed,
            "system_prompt": SYSTEM_PROMPT,
            "tools": [tool.args_schema.model_json_schema() for tool in build_tools(environment)],
        }
        scenario = task.model_dump(mode="json", exclude_none=True)
        key = hashlib.sha256(json.dumps([scenario, config], sort_keys=True).encode()).hexdigest()
        if resume and key in completed:
            summary["skipped"] += 1
            print(f"{task.scenario_id}: skipped (completed trial {trial_id})")
            continue

        run_id = f"{task.scenario_id}_trial{trial_id}_{uuid4().hex}"
        path = directory / f"{run_id}.json"
        events: list[TraceEvent] = []
        record: dict[str, Any] = {
            "record_version": "1",
            "run_id": run_id,
            "model": model,
            "scenario_id": task.scenario_id,
            "prompt_id": task.experimental_conditions.template_id,
            "trial_id": trial_id,
            "resume_key": key,
            "status": "running",
            "started_at": now(),
            "finished_at": None,
            "config": config,
            "scenario": scenario,
            "user_prompt": task.user_request,
            "errors": [],
            "final_response": None,
        }

        def snapshot(
            trace: RunTrace | None = None,
            *,
            run_id=run_id,
            task=task,
            events=events,
            environment=environment,
            record=record,
            path=path,
        ) -> None:
            current = trace or RunTrace(
                run_id=run_id,
                scenario_id=task.scenario_id,
                agent={"model_selection": model},
                events=events,
                final_environment_state=environment.state,
                termination_reason="incomplete_" + record["status"],
            )
            record["trace"] = current.model_dump(mode="json")
            record["messages"] = message_history(SYSTEM_PROMPT, task.user_request, current.events)
            record["token_usage"] = token_usage(current.events)
            record["assistant_messages"] = [
                e.payload["content"] for e in current.events if e.event_type == "assistant_message"
            ]
            record["booking_attempts"] = [
                a.model_dump(mode="json") for a in environment.state.booking_attempts
            ]
            record["execution_b"] = suggest_execution_b(current)
            atomic_json(path, record)

        def on_event(event: TraceEvent, events=events, snapshot=snapshot) -> None:
            events.append(event)
            snapshot()

        snapshot()
        try:
            trace, _messages = run_agent(
                environment,
                run_id,
                model,
                max_tool_calls=max_tool_calls,
                temperature=temperature,
                final_response_after_booking=True,
                capture_model_messages=True,
                event_callback=on_event,
            )
            final_events = [e for e in trace.events if e.event_type == "final_response"]
            final_status = final_events[-1].payload["status"] if final_events else None
            failed = trace.termination_reason.startswith("incomplete_") or final_status in {
                "empty",
                "failed",
                "invalid_tool_call",
            }
            record["status"] = "failed" if failed else "completed"
            # A pre-booking announcement is not a final response.
            terminal = [
                e.payload["content"]
                for e in trace.events
                if e.event_type == "assistant_message"
                and (
                    e.payload.get("phase") == "final_response"
                    or trace.termination_reason == "agent_finished"
                )
            ]
            record["final_response"] = terminal[-1] if terminal else None
            record["errors"] = [
                e.model_dump(mode="json")
                for e in trace.events
                if e.event_type in {"model_error", "invalid_tool_call"}
                or (e.event_type == "final_response" and e.payload.get("status") != "completed")
                or (
                    e.event_type == "tool_result"
                    and e.payload.get("result", {}).get("status") == "error"
                )
            ]
            record["finished_at"] = now()
            snapshot(trace)
            summary[record["status"]] += 1
        except (KeyboardInterrupt, SystemExit):
            record["status"] = "interrupted"
            record["finished_at"] = now()
            snapshot()
            raise
        except Exception as error:  # noqa: BLE001 - save failed cases and continue the batch.
            record["status"] = "failed"
            detail = (
                str(error)
                if isinstance(error, (ModelAPIError, ModelConfigurationError))
                else ("Unexpected runtime error; provider payload omitted.")
            )
            record["errors"].append({"type": type(error).__name__, "message": detail})
            record["error"] = detail
            record["finished_at"] = now()
            snapshot()
            summary["failed"] += 1
        print(f"{task.scenario_id}: {record['status']} -> {path}")
    return summary
