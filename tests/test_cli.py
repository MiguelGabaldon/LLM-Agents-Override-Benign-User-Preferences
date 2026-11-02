from __future__ import annotations

from preference_override.cli import main


def test_list_scenarios(capsys) -> None:
    main(["--list-scenarios"])
    output = capsys.readouterr().out
    assert "F01_exploratory_modest_approve: exploratory, modest, approve" in output
    assert "F10_habitual_large_refuse: habitual, large, refuse" in output
