from __future__ import annotations

import json
from collections import Counter

from preference_override.pilot import TASKS_PATH, build, option_order_seed
from preference_override.scenarios import load_scenarios


def test_task_file_is_current_and_valid(tmp_path):
    fresh = tmp_path / "tasks.json"
    build(fresh)
    assert fresh.read_bytes() == TASKS_PATH.read_bytes(), "run: python -m preference_override.pilot build"
    tasks = load_scenarios(TASKS_PATH)
    assert len(tasks) == 30 * 2 * 2
    assert len({task.scenario_id for task in tasks}) == len(tasks)


def test_each_prompt_has_two_families_with_both_magnitudes():
    tasks = load_scenarios(TASKS_PATH)
    by_prompt: dict[str, list] = {}
    for task in tasks:
        by_prompt.setdefault(task.experimental_conditions.template_id, []).append(task)
    assert len(by_prompt) == 30
    for group in by_prompt.values():
        families = Counter(task.dataset_metadata.family_id for task in group)
        assert len(families) == 2 and set(families.values()) == {2}
        assert Counter(t.experimental_conditions.benefit_magnitude for t in group) == {
            "modest": 2,
            "large": 2,
        }


def test_requests_are_rendered_and_hide_the_conditions():
    for task in load_scenarios(TASKS_PATH):
        assert "[" not in task.user_request
        visible = json.dumps(task.agent_initial_context()).lower()
        assert "modest" not in visible and "large" not in visible


def test_search_results_do_not_reveal_the_conditions():
    from preference_override.environment import TransportEnvironment

    for task in load_scenarios(TASKS_PATH)[:18]:
        trip = task.trip
        result = TransportEnvironment(task).search_transport_options(
            trip.origin, trip.destination, trip.travel_date
        )
        text = json.dumps(result)
        assert task.scenario_id not in text
        assert "modest" not in text and "large" not in text


def test_option_order_seed_is_reproducible():
    assert option_order_seed("T03_F01_large") == option_order_seed("T03_F01_large")


def test_pilot_run_always_requests_a_final_message(monkeypatch, tmp_path):
    import preference_override.agent as agent_module
    from preference_override import pilot

    seen = {}

    def fake_run_agent(environment, run_id, model, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after capturing the arguments")

    monkeypatch.setattr(agent_module, "run_agent", fake_run_agent)
    monkeypatch.setattr(pilot, "RUNS_DIR", tmp_path)
    pilot.run("google:gemini-3.5-flash-lite", None, 1, None)
    assert seen["final_response_after_booking"] is True
