"""Regression guards for update actions escaping a non-canonical Hermes home."""
from __future__ import annotations

import hermes_cli.gateway as gateway
import hermes_cli.profiles as profiles
import hermes_cli.update_cmd_maint as maintenance


class _Profile:
    def __init__(self, name: str, is_default: bool = False):
        self.name = name
        self.is_default = is_default


def test_sandboxed_home_has_no_launchd_restart_targets(monkeypatch, tmp_path):
    """A fixture or side-by-side checkout must never claim shared launchd labels."""
    canonical_root = tmp_path / "canonical-hermes"
    sandbox_home = tmp_path / "rehearsal-home"
    # The profile root follows HERMES_HOME, but launchd ownership must remain
    # anchored to the account's native Hermes root.
    monkeypatch.setattr(gateway, "get_default_hermes_root", lambda: sandbox_home)
    monkeypatch.setattr(
        gateway, "_get_platform_default_hermes_home", lambda: canonical_root, raising=False
    )
    monkeypatch.setattr(gateway, "get_hermes_home", lambda: sandbox_home)
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_Profile("default", is_default=True)])

    assert gateway.launchd_gateway_labels_for_install() == []


def test_sandboxed_home_does_not_adopt_default_launchd_service_pid(monkeypatch, tmp_path):
    """A non-canonical rehearsal must not stop the account's real launchd gateway PID."""
    canonical_root = tmp_path / "canonical-hermes"
    sandbox_home = tmp_path / "rehearsal-home"
    monkeypatch.setattr(gateway, "get_default_hermes_root", lambda: sandbox_home)
    monkeypatch.setattr(
        gateway, "_get_platform_default_hermes_home", lambda: canonical_root, raising=False
    )
    monkeypatch.setattr(gateway, "get_hermes_home", lambda: sandbox_home)
    monkeypatch.setattr(gateway, "_get_service_pids", lambda all_profiles=False: [4242])
    monkeypatch.setattr(gateway, "_scan_gateway_pids", lambda *args, **kwargs: [])

    assert gateway.find_gateway_pids(all_profiles=True) == []


def test_canonical_home_keeps_its_launchd_restart_targets(monkeypatch, tmp_path):
    canonical_root = tmp_path / "canonical-hermes"
    monkeypatch.setattr(gateway, "get_default_hermes_root", lambda: canonical_root)
    monkeypatch.setattr(
        gateway, "_get_platform_default_hermes_home", lambda: canonical_root, raising=False
    )
    monkeypatch.setattr(gateway, "get_hermes_home", lambda: canonical_root)
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_Profile("default", is_default=True)])

    assert gateway.launchd_gateway_labels_for_install() == ["ai.hermes.gateway"]


def test_sandboxed_home_ignores_unscoped_default_gateway(monkeypatch, tmp_path):
    canonical_root = tmp_path / "canonical-hermes"
    sandbox_home = tmp_path / "rehearsal-home"
    monkeypatch.setattr(gateway, "get_default_hermes_root", lambda: sandbox_home)
    monkeypatch.setattr(
        gateway, "_get_platform_default_hermes_home", lambda: canonical_root, raising=False
    )
    monkeypatch.setattr(gateway, "get_hermes_home", lambda: sandbox_home)
    monkeypatch.setattr(gateway, "_get_ancestor_pids", lambda: set())
    monkeypatch.setattr(gateway, "is_windows", lambda: False)
    monkeypatch.setattr(gateway.os.path, "isdir", lambda path: False)
    monkeypatch.setattr(
        gateway.subprocess,
        "run",
        lambda *args, **kwargs: gateway.subprocess.CompletedProcess(
            args=args, returncode=0, stdout="4242 /fixture/python -m gateway run\n", stderr=""
        ),
    )
    import gateway.status as gateway_status
    monkeypatch.setattr(gateway_status, "looks_like_gateway_command_line", lambda command: True)

    assert gateway._scan_gateway_pids(set()) == []
    assert gateway._scan_gateway_pids(set(), all_profiles=True) == []


def test_sandboxed_home_skips_the_direct_launchd_restart(monkeypatch):
    import hermes_cli.update_cmd_fleet as fleet

    calls: list[object] = []
    monkeypatch.setattr(gateway, "launchd_gateway_labels_for_install", lambda: [])
    monkeypatch.setattr(fleet, "_restart_launchd_gateway_after_update", lambda **kwargs: calls.append(kwargs))

    fleet._restart_macos_launchd_gateways([], [], drain_budget=0.0)

    assert calls == []


def test_cua_refresh_requires_explicit_opt_in(monkeypatch):
    """An updater must not mutate a global CuaDriver installation by default."""
    calls: list[dict] = []
    monkeypatch.setattr(maintenance.sys, "platform", "darwin")
    monkeypatch.setattr(maintenance.shutil, "which", lambda name: "/fixture/cua-driver")
    monkeypatch.setattr("hermes_cli.tools_config.install_cua_driver", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(maintenance, "_load_updates_cfg", lambda: {})

    maintenance._refresh_cua_driver_after_update()

    assert calls == []


def test_cua_refresh_runs_only_after_explicit_opt_in(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(maintenance.sys, "platform", "darwin")
    monkeypatch.setattr(maintenance.shutil, "which", lambda name: "/fixture/cua-driver")
    monkeypatch.setattr("hermes_cli.tools_config.install_cua_driver", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(maintenance, "_load_updates_cfg", lambda: {"refresh_cua_driver": True})

    maintenance._refresh_cua_driver_after_update()

    assert calls == [{"upgrade": True, "require_confirmed_update": True, "show_installer_progress": False}]
