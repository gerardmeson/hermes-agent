"""Regression coverage for the no-side-effect source-merge preflight."""

from __future__ import annotations

import json
import subprocess

import pytest

from hermes_cli import update_cmd
import hermes_cli.update_receipt as update_receipt


@pytest.fixture()
def receipt_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    with update_receipt.update_receipt_scope():
        yield home


def test_in_place_merge_preflight_refuses_conflict_before_any_update_side_effect(
    receipt_home, monkeypatch, capsys
):
    """A custom branch conflict must stop before snapshot/restart work begins."""
    calls: list[list[str]] = []

    def fake_git_run(_git_cmd, args, **_kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="CONFLICT (content): Merge conflict in hermes_cli/update_cmd.py\n",
            stderr="",
        )

    monkeypatch.setattr(update_cmd, "_git_run", fake_git_run)
    update_receipt.begin_update_receipt()

    with pytest.raises(SystemExit) as failure:
        update_cmd._preflight_in_place_merge(["git"], "origin/main")

    assert failure.value.code == 1
    assert calls == [["merge-tree", "--write-tree", "HEAD", "origin/main"]]
    out = capsys.readouterr().out
    assert "No snapshot was taken" in out
    assert "source merge conflict" in out.lower()

    receipt_path = update_receipt.finalize_pending_update_receipt(1, "sys.exit(1)")
    assert receipt_path is not None
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["steps"] == [{
        "name": "source_merge_preflight",
        "ok": False,
        "detail": "conflict: hermes_cli/update_cmd.py",
        "at": receipt["steps"][0]["at"],
    }]
