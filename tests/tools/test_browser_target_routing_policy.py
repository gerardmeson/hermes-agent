from tools import browser_use_cli


def test_shared_cdp_profile_uses_the_target_owned_builtin_route(monkeypatch):
    monkeypatch.setattr(browser_use_cli, "_camofox_active", lambda: False)
    monkeypatch.setattr(browser_use_cli, "get_browser_backend", lambda: "")
    monkeypatch.setattr(browser_use_cli, "is_legacy_browser_use_cloud_config", lambda _cfg: False)
    monkeypatch.setattr(browser_use_cli, "_read_browser_cfg", lambda: {})
    monkeypatch.setattr(browser_use_cli, "_find_cli", lambda: ["python", "-m", "browser_harness.run"])
    monkeypatch.setattr("tools.browser_tool_cdp._get_cdp_override_raw", lambda: "http://127.0.0.1:9229")

    assert browser_use_cli.is_browser_use_cli_mode() is False
