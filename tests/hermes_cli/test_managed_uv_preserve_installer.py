"""An update may preserve an admitted installer without skipping runtime repair."""
from hermes_cli import managed_uv


def test_scoped_preserve_skips_self_update_but_keeps_repair(monkeypatch):
    calls = []
    monkeypatch.setenv('HERMES_UPDATE_SKIP_UV_SELF_UPDATE', '1')
    monkeypatch.setattr(managed_uv, 'resolve_uv', lambda: '/approved/uv')
    monkeypatch.setattr(managed_uv, '_uv_self_update_is_fresh', lambda: False)
    monkeypatch.setattr(managed_uv.subprocess, 'run', lambda *a, **kw: calls.append('self_update'))
    monkeypatch.setattr(managed_uv, '_run_runtime_repair', lambda *a, **kw: calls.append('repair'))
    assert managed_uv.update_managed_uv() == '/approved/uv'
    assert calls == ['repair']
