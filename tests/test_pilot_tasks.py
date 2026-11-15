from __future__ import annotations

import json

from preference_override.pilot import TASKS_PATH, build, option_order_seed
from preference_override.scenarios import load_scenarios


def test_task_file_is_current_and_valid(tmp_path):
    fresh = tmp_path / "tasks.json"
    build(fresh)
    assert fresh.read_bytes() == TASKS_PATH.read_bytes(), "run: python -m preference_override.pilot build"
    tasks = load_scenarios(TASKS_PATH)
    assert len(tasks) == 10 * 2 * 9


def test_requests_are_rendered_and_hide_the_conditions():
    for task in load_scenarios(TASKS_PATH):
        assert "[" not in task.user_request
        visible = json.dumps(task.agent_initial_context()).lower()
        assert "modest" not in visible and "large" not in visible
        assert task.experimental_conditions.confirmation_branch is None


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
    assert option_order_seed("F01_large_T03") == option_order_seed("F01_large_T03")
