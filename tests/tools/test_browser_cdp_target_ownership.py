"""browser_cdp on a shared browser may only act on tabs the calling task created itself."""
import json

import pytest

from tools import browser_cdp_tool as cdp_tool


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    async def fake_cdp_call(endpoint, method, params, target_id, timeout):
        recorded.append((method, dict(params), target_id))
        if method == "Target.createTarget":
            return {"targetId": f"created-{len([c for c in recorded if c[0] == 'Target.createTarget'])}"}
        if method == "Target.closeTarget":
            return {"success": True}
        return {}

    monkeypatch.setattr(cdp_tool, "_cdp_call", fake_cdp_call)
    monkeypatch.setattr(cdp_tool, "_resolve_cdp_endpoint", lambda: "ws://127.0.0.1:9229/devtools/browser/x")
    monkeypatch.setattr(cdp_tool, "_WS_AVAILABLE", True)
    monkeypatch.setattr(cdp_tool, "_browser_cdp_private_guard", lambda **_kw: None)
    cdp_tool._OWNED_TARGETS.clear()
    yield recorded
    cdp_tool._OWNED_TARGETS.clear()


def _run(method, params=None, target_id=None, task="task-a"):
    return json.loads(cdp_tool.browser_cdp(method, params, target_id=target_id, task_id=task))


def test_page_level_call_on_an_unowned_tab_is_refused(calls):
    out = _run("Page.navigate", {"url": "https://example.test"}, target_id="pinned-1")
    assert "error" in out and "Target.createTarget" in out["error"]
    assert calls == []


def test_a_tab_created_by_the_task_can_be_used_and_closed(calls):
    owned = _run("Target.createTarget", {"url": "about:blank"})["result"]["targetId"]
    assert _run("Page.navigate", {"url": "https://example.test"}, target_id=owned).get("success") is True
    assert _run("Target.closeTarget", {"targetId": owned}).get("success") is True
    after = _run("Page.navigate", {"url": "https://example.test"}, target_id=owned)
    assert "error" in after


def test_another_tasks_tab_is_refused(calls):
    owned = _run("Target.createTarget", {"url": "about:blank"}, task="task-a")["result"]["targetId"]
    out = _run("Page.navigate", {"url": "https://example.test"}, target_id=owned, task="task-b")
    assert "error" in out


@pytest.mark.parametrize("method", ["Target.closeTarget", "Target.activateTarget", "Target.attachToTarget"])
def test_browser_level_target_methods_refuse_unowned_tabs(calls, method):
    out = _run(method, {"targetId": "pinned-1"})
    assert "error" in out
    assert calls == []


def test_listing_tabs_stays_available(calls):
    assert _run("Target.getTargets").get("success") is True


def test_frame_routing_through_the_supervisor_is_refused_on_a_shared_browser(calls, monkeypatch):
    monkeypatch.setattr("tools.browser_tool_cdp._get_cdp_override_raw", lambda: "http://127.0.0.1:9229")
    out = json.loads(cdp_tool.browser_cdp("Runtime.evaluate", {"expression": "1"}, frame_id="f1", task_id="task-a"))
    assert "error" in out
    assert calls == []
