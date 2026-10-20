from __future__ import annotations

from preference_override.cli import main


def test_list_scenarios(capsys) -> None:
    main(["--list-scenarios"])
    output = capsys.readouterr().out
    assert "T01_approve: exploratory, modest, approve" in output
    assert "T04_refuse: habitual, large, refuse" in output
