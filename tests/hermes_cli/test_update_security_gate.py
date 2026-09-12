"""Real metadata probes in disposable venv layouts, never the operator's home."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from hermes_cli import update_cmd_deps

USE_REAL_DEPENDENCY_SECURITY_GATE = True


@pytest.fixture
def target(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    venv = root / "venv"
    (venv / "bin").mkdir(parents=True)
    # CI's managed venv launcher may be relocatable. A target test must not
    # move it, because that makes Python derive a bogus stdlib prefix. The
    # macOS/Linux system interpreter is stable under this disposable link;
    # Windows retains its native runner.
    interpreter = Path("/usr/bin/python3") if os.name != "nt" else Path(sys.executable)
    if not interpreter.exists():
        interpreter = Path(sys.executable)
    launcher = venv / "bin" / "python"
    if os.name == "nt":
        launcher.symlink_to(interpreter)
    else:
        # macOS derives runtime paths from an executable's location even for
        # a system-python symlink. A one-line exec wrapper retains the stable
        # executable path while receiving the same ``-I -S`` probe arguments.
        launcher.write_text(f"#!/bin/sh\nexec {interpreter} \"$@\"\n")
        launcher.chmod(0o755)
    version = subprocess.check_output(
        [str(interpreter), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        text=True,
    ).strip()
    site = venv / "lib" / f"python{version}" / "site-packages"
    site.mkdir(parents=True)
    for name, version in {"mcp": "2.0.0", "httpx2": "2.12.0", "httpcore2": "2.12.0"}.items():
        dist = site / f"{name}-{version}.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
    recorded = []
    monkeypatch.setattr("hermes_cli.update_cmd._m", lambda: SimpleNamespace(PROJECT_ROOT=root, _is_windows=lambda: False))
    monkeypatch.setattr("hermes_cli.update_cmd._record_update_step", lambda *a: recorded.append(a))
    # Parent metadata is irrelevant: a fresh child must inspect the target.
    monkeypatch.setattr("hermes_cli.security_advisories.update_blocking_hits", lambda: [])
    return site, recorded


def test_corrupt_target_metadata_blocks_even_with_clean_parent(target):
    site, recorded = target
    (site / "httpx2-2.12.0.dist-info" / "METADATA").write_text("Name: httpx2\n")
    with pytest.raises(RuntimeError, match="dependency security"):
        update_cmd_deps._enforce_post_dependency_security_gate()
    assert recorded[-1][0:2] == ("dependency_security", False)


def test_clear_target_passes_and_site_hooks_never_run(target, tmp_path):
    site, recorded = target
    sentinel = tmp_path / "hook-executed"
    (site / "hostile.pth").write_text(f"import pathlib; pathlib.Path({str(sentinel)!r}).touch()\n")
    update_cmd_deps._enforce_post_dependency_security_gate()
    assert recorded[-1][0:2] == ("dependency_security", True)
    assert not sentinel.exists()


@pytest.mark.parametrize("fault", ["missing", "duplicate", "vulnerable", "prerelease"])
def test_unknown_or_bad_target_is_rejected(target, fault):
    site, recorded = target
    metadata = site / "httpx2-2.12.0.dist-info" / "METADATA"
    if fault == "missing":
        metadata.unlink()
    elif fault == "duplicate":
        duplicate = site / "httpx2-2.11.0.dist-info"
        duplicate.mkdir()
        (duplicate / "METADATA").write_text("Name: httpx2\nVersion: 2.11.0\n")
    else:
        version = "2.11.0" if fault == "vulnerable" else "2.12.0rc1"
        metadata.write_text(f"Name: httpx2\nVersion: {version}\n")
    with pytest.raises(RuntimeError, match="dependency security"):
        update_cmd_deps._enforce_post_dependency_security_gate()
    assert not recorded[-1][1]


def test_already_current_cannot_bypass_gate(target, monkeypatch):
    from hermes_cli import update_cmd as command
    site, recorded = target
    (site / "httpx2-2.12.0.dist-info" / "METADATA").write_text("Name: httpx2\nVersion: 2.7.0\n")
    events = []
    monkeypatch.setattr(command, "_invalidate_update_cache", lambda: None)
    monkeypatch.setattr(command, "_repair_current_checkout", lambda **kw: True)
    monkeypatch.setattr(command, "_apply_pending_fleet_restart_catchup", lambda: events.append("restart"))
    old = command._m()
    old._resume_windows_gateways_after_update = lambda *a: None
    monkeypatch.setattr(command, "_m", lambda: old)
    plan = SimpleNamespace(auto_stash_ref=None, parked_branch_switched=False, upstream_checked=True)
    with pytest.raises(RuntimeError, match="dependency security"):
        command._finish_already_up_to_date([], "main", "main", plan,
            assume_yes=False, gateway_mode=False, gw_input_fn=None,
            pre_update_snapshot_id=None, desktop_dir=site, had_desktop_app_before_update=False,
            active_lazy_features=[], active_tool_dependencies=[], _windows_gateway_resume=[])
    assert not events


@pytest.mark.parametrize("bad", [True, False])
def test_real_post_pull_caller_orders_gate_before_restart(target, monkeypatch, bad):
    from hermes_cli import update_cmd as command
    site, recorded = target
    if bad:
        (site / "httpx2-2.12.0.dist-info" / "METADATA").write_text("Name: httpx2\nVersion: 2.7.0\n")
    events = []
    for name in ("_invalidate_update_cache", "_write_fleet_restart_pending_marker",
                 "_sweep_bytecode_after_update", "_resume_windows_gateways_and_merge_outcome", "_verify_fleet_after_update"):
        monkeypatch.setattr(command, name, lambda *a, **kw: None)
    monkeypatch.setattr(command, "_verify_head_after_pull", lambda *a, **kw: "fixture-revision")
    monkeypatch.setattr(command, "_sync_python_dependencies_after_pull", lambda *a, **kw: update_cmd_deps._enforce_post_dependency_security_gate())
    monkeypatch.setattr(command, "_update_node_dependencies", lambda: [])
    monkeypatch.setattr(command, "_rebuild_desktop_after_update", lambda *a, **kw: True)
    monkeypatch.setattr(command, "_branch_head_suffix", lambda *a: "fixture")
    monkeypatch.setattr(command, "_run_post_update_maintenance", lambda **kw: True)
    monkeypatch.setattr(command, "_restart_gateway_fleet_after_update", lambda *a: events.append("restart"))
    old = command._m()
    old._build_web_ui = lambda *a: None
    monkeypatch.setattr(command, "_m", lambda: old)
    def run():
        command._apply_pulled_update([], "main", "fixture-base", SimpleNamespace(in_place_update=True),
            SimpleNamespace(active_lazy_features=[], active_tool_dependencies=[], assume_yes=False, pre_update_version="fixture"),
            gateway_mode=False, is_fork=False, desktop_dir=site, had_desktop_app_before_update=False,
            pre_update_snapshot_id=None, _pre_update_plan=None, _windows_gateway_resume=[])
    if bad:
        with pytest.raises(RuntimeError, match="dependency security"):
            run()
        assert not events
    else:
        run()
        assert events == ["restart"]
        assert recorded[-1][1]


def test_failed_gate_persists_a_failed_stage_receipt(target, monkeypatch, tmp_path):
    from hermes_cli import update_cmd as command, update_receipt as receipts
    site, _ = target
    (site / "httpx2-2.12.0.dist-info" / "METADATA").write_text("Name: httpx2\nVersion: 2.7.0\n")
    monkeypatch.setattr(receipts, "_code_identity", lambda **kw: {"fixture": True})
    monkeypatch.setattr(receipts, "_receipt_dir", lambda: tmp_path / "receipts")
    monkeypatch.setattr(command, "_record_update_step", receipts.record_step)
    receipts.begin_update_receipt()
    with pytest.raises(RuntimeError):
        update_cmd_deps._enforce_post_dependency_security_gate()
    path = receipts.finalize_update_receipt("failed", fleet=[])
    assert path is not None
    saved = json.loads(path.read_text())
    assert saved["outcome"] == "failed"
    assert saved["steps"][-1]["name"] == "dependency_security"
    assert saved["steps"][-1]["ok"] is False
