"""Mocked end-to-end experiment, interruption/resume, and annotation tests."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from preference_override import agent
from preference_override.annotations import load_scores, save_score
from preference_override.experiment import run_experiment
from preference_override.pilot import TASKS_PATH
from preference_override.scenarios import load_scenarios


def fake_models(monkeypatch, interrupt_on=None):
    seen = []

    def create(*args):
        number = len(seen) + 1
        seen.append(number)

        class Model:
            step = 0

            def bind_tools(self, tools, **kwargs):
                return self

            def invoke(self, messages):
                self.step += 1
                if interrupt_on == number and self.step == 2:
                    raise KeyboardInterrupt
                # Extract only the actual user prompt; each fresh run starts with two messages.
                if self.step == 1:
                    assert len(messages) == 2
                    task = next(
                        t
                        for t in load_scenarios(TASKS_PATH)
                        if t.user_request == messages[1].content
                    )
                    calls = [
                        {
                            "name": "search_transport_options",
                            "args": task.trip.model_dump(),
                            "id": "search",
                        }
                    ]
                    content = "I will search."
                elif self.step == 2:
                    options = json.loads(messages[-1].content)["options"]
                    calls = [
                        {
                            "name": "book_transport_option",
                            "args": {"option_id": options[0]["option_id"]},
                            "id": "book",
                        }
                    ]
                    content = ""
                else:
                    calls, content = [], "Your ticket is booked."
                return AIMessage(
                    content=content,
                    tool_calls=calls,
                    usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                )

        return Model(), {"provider": "ollama", "model_name": "fake"}

    monkeypatch.setattr(agent, "create_model", create)
    return seen


def records(root):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.rglob("*.json"))]


def test_run_resume_unique_trials_and_complete_trajectory(monkeypatch, tmp_path):
    tasks = load_scenarios(TASKS_PATH)[:2]
    seen = fake_models(monkeypatch)
    assert run_experiment(tasks, "mock:model", tmp_path) == {
        "completed": 2,
        "failed": 0,
        "skipped": 0,
    }
    originals = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert len(list((tmp_path / "mock_model").glob("*.json"))) == 2
    record = records(tmp_path)[0]
    assert record["final_response"] == "Your ticket is booked."
    assert [m["type"] for m in record["messages"]] == [
        "system",
        "human",
        "ai",
        "tool",
        "ai",
        "tool",
        "ai",
    ]
    assert record["messages"][4]["tool_calls"][0]["name"] == "book_transport_option"
    assert record["token_usage"]["total_tokens"] == 45
    assert record["token_usage"]["complete"] is True
    assert len(record["booking_attempts"]) == 1
    assert record["execution_b"]["suggested_b"] == 1
    assert record["config"]["system_prompt"] == agent.SYSTEM_PROMPT
    assert run_experiment(tasks, "mock:model", tmp_path, resume=True)["skipped"] == 2
    assert len(seen) == 2
    assert all(p.read_bytes() == content for p, content in originals.items())
    assert (
        run_experiment(tasks[:1], "mock:model", tmp_path, resume=True, trial_id=2)["completed"] == 1
    )
    saved = records(tmp_path)
    assert len({r["run_id"] for r in saved}) == 3
    assert {r["trial_id"] for r in saved} == {1, 2}


def test_interrupt_preserves_partial_run_and_resume_skips_only_completed(monkeypatch, tmp_path):
    tasks = load_scenarios(TASKS_PATH)[:3]
    fake_models(monkeypatch, interrupt_on=2)
    with pytest.raises(KeyboardInterrupt):
        run_experiment(tasks, "mock:model", tmp_path)
    before = records(tmp_path)
    assert sorted(r["status"] for r in before) == ["completed", "interrupted"]
    partial = next(r for r in before if r["status"] == "interrupted")
    assert any(e["event_type"] == "tool_result" for e in partial["trace"]["events"])
    assert partial["token_usage"]["total_tokens"] == 15
    assert partial["execution_b"]["suggested_b"] is None
    fake_models(monkeypatch)
    assert run_experiment(tasks, "mock:model", tmp_path, resume=True) == {
        "completed": 2,
        "failed": 0,
        "skipped": 1,
    }
    assert len(records(tmp_path)) == 4  # retain interrupted attempt rather than overwrite it


def test_non_booking_answer_is_completed_and_unknown_usage_is_null(monkeypatch, tmp_path):
    class Model:
        def bind_tools(self, tools, **kwargs):
            return self

        def invoke(self, messages):
            return AIMessage(content="Would you like the train or bus?")

    monkeypatch.setattr(
        agent,
        "create_model",
        lambda *args: (
            Model(),
            {"provider": "ollama", "model_name": "fake"},
        ),
    )
    task = load_scenarios(TASKS_PATH)[:1]
    assert run_experiment(task, "mock:model", tmp_path)["completed"] == 1
    record = records(tmp_path)[0]
    assert record["execution_b"]["suggested_b"] == 0
    assert record["final_response"] == "Would you like the train or bus?"
    assert record["token_usage"]["total_tokens"] is None
    assert record["token_usage"]["complete"] is False
    assert run_experiment(task, "mock:model", tmp_path, resume=True)["skipped"] == 1
    assert (
        run_experiment(task, "mock:model", tmp_path, resume=True, max_tool_calls=5)["completed"]
        == 1
    )


def test_csv_edits_replace_only_annotation_and_preserve_trajectories(monkeypatch, tmp_path):
    fake_models(monkeypatch)
    root = tmp_path / "runs"
    run_experiment(load_scenarios(TASKS_PATH)[:2], "mock:model", root)
    paths = sorted(root.rglob("*.json"))
    original = paths[0].read_bytes()
    record = json.loads(original)
    csv_path = tmp_path / "annotations/scores.csv"
    save_score(record, paths[0], 3, 2, 1, False, "Evidence, with\na newline", csv_path)
    assert load_scores(csv_path)[record["run_id"]]["autonomy"] == "5"
    save_score(record, paths[0], 1, 0, 1, False, "Corrected", csv_path)
    rows = load_scores(csv_path)
    assert len(rows) == 1
    assert rows[record["run_id"]]["autonomy"] == "3"
    save_score(record, paths[0], None, None, 1, True, "Unclear preference", csv_path)
    assert load_scores(csv_path)[record["run_id"]]["autonomy"] == ""
    assert paths[0].read_bytes() == original
    with pytest.raises(ValueError):
        save_score(record, paths[0], 8, 0, 1, False, "", csv_path)


def test_streamlit_save_edit_navigate_and_restart(monkeypatch, tmp_path):
    import shutil

    from streamlit.testing.v1 import AppTest

    from preference_override import annotate

    fake_models(monkeypatch)
    run_experiment(load_scenarios(TASKS_PATH)[:2], "mock:model", tmp_path / "runs")
    (tmp_path / "docs").mkdir()
    shutil.copyfile(Path("docs/rubric.md"), tmp_path / "docs/rubric.md")
    monkeypatch.setattr(annotate, "ROOT", tmp_path)
    app = AppTest.from_string("from preference_override.annotate import main\nmain()")
    app.run(timeout=30)
    assert not app.exception

    def select(app, label):
        return next(widget for widget in app.selectbox if widget.label == label)

    assert select(app, "Model").value == "mock:model"
    select(app, "Preference Orientation (0–4)").set_value(3)
    select(app, "Decision Authority A (0–2)").set_value(2)
    app.run()
    assert app.metric[0].value == "5"
    next(b for b in app.button if b.label == "Save scores").click().run()
    assert not app.exception
    csv_path = tmp_path / "annotations/scores.csv"
    assert len(load_scores(csv_path)) == 1
    select(app, "Preference Orientation (0–4)").set_value(1).run()
    next(b for b in app.button if b.label == "Save scores").click().run()
    assert len(load_scores(csv_path)) == 1
    assert next(iter(load_scores(csv_path).values()))["preference_orientation"] == "1"
    next(b for b in app.button if b.label == "Next").click().run()
    selected = select(app, "Run").value
    restarted = AppTest.from_string("from preference_override.annotate import main\nmain()")
    restarted.run(timeout=30)
    assert not restarted.exception
    assert select(restarted, "Run").value == selected
    next(b for b in restarted.button if b.label == "Previous").click().run()
    assert select(restarted, "Preference Orientation (0–4)").value == 1
