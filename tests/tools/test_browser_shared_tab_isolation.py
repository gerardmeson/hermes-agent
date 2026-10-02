"""Shared-browser tab isolation: sessions on a user CDP browser must be pinned to their own tab."""
import pytest

from tools import browser_tool_lifecycle as lifecycle
from tools import browser_tool_session as session


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_spawn(task_id, session_info, cmd_parts, command, engine, timeout, stdin_payload):
        calls.append(list(cmd_parts))
        return {"success": True, "data": {}}

    monkeypatch.setattr(session, "_spawn_and_collect", fake_spawn)
    monkeypatch.setattr(session, "_agent_browser_argv", lambda _cmd: ["agent-browser"])
    monkeypatch.setattr(session._cdp, "_ensure_cdp_supervisor", lambda _task: None)
    monkeypatch.setattr(session._cloud, "_get_browser_engine", lambda: "auto")
    monkeypatch.setattr(session, "_bot_desktop_attach_port", lambda _info: None)
    return calls


def _dispatch(info, command="open", args=("https://example.test",)):
    return session._dispatch_browser_command("task", info, "agent-browser", command, list(args), 10, None)


def test_user_cdp_override_session_is_pinned_to_its_own_tab(captured):
    _dispatch({"session_name": "cdp_x", "cdp_url": "ws://127.0.0.1:9229/devtools/browser/t",
               "features": {"cdp_override": True}})
    assert "--pin-tab" in captured[0]
    assert captured[0].index("--pin-tab") < captured[0].index("--json")


def test_bot_desktop_shared_browser_is_pinned(captured, monkeypatch):
    monkeypatch.setattr(session, "_bot_desktop_attach_port", lambda _info: 9333)
    _dispatch({"session_name": "local_x", "cdp_url": None, "features": {}})
    assert "--pin-tab" in captured[0]


def test_private_cloud_session_is_not_pinned(captured):
    _dispatch({"session_name": "cloud_x", "cdp_url": "wss://cloud.example/session", "features": {}})
    assert "--pin-tab" not in captured[0]


def test_cleanup_closes_only_the_sessions_own_tab_before_closing_the_daemon(monkeypatch):
    order = []
    info = {"session_name": "cdp_x", "cdp_url": "ws://127.0.0.1:9229/devtools/browser/t",
            "features": {"cdp_override": True}}
    monkeypatch.setattr(lifecycle._cdp, "_stop_cdp_supervisor", lambda _t: None)
    monkeypatch.setattr(lifecycle._bt, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(lifecycle._bt, "_maybe_stop_recording", lambda _t: None)
    monkeypatch.setattr(lifecycle._bt, "_active_sessions", {"task": info})
    monkeypatch.setattr(lifecycle, "_session_has_expired", lambda _i: False)
    monkeypatch.setattr(lifecycle, "_release_session_resources", lambda *_a: None)
    monkeypatch.setattr(lifecycle._session, "_run_browser_command",
                        lambda task, command, args=None, timeout=None, **kw: order.append((command, list(args or []))) or {"success": True})
    lifecycle._cleanup_single_browser_session("task")
    assert order == [("tab", ["close"]), ("close", [])]
