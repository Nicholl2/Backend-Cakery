import json
import sys

import pytest

from app import db_reconcile


def test_apply_is_disabled_before_any_database_connection(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["db_reconcile", "apply", "--confirm-database", "toti", "--confirm-apply"],
    )
    monkeypatch.setattr(db_reconcile, "engine", None)
    with pytest.raises(SystemExit) as exc:
        db_reconcile.main()
    assert exc.value.code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "APPLY_DISABLED"
    assert output["database_connected"] is False


def test_apply_requires_explicit_operator_confirmation(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["db_reconcile", "apply", "--confirm-database", "toti"])
    with pytest.raises(SystemExit) as exc:
        db_reconcile.main()
    assert exc.value.code == 2
