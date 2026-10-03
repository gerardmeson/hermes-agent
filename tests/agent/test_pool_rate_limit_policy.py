"""Opt-in early pool rotation, exercised through the real conversation retry loop."""
import time
from types import SimpleNamespace

import pytest

import agent.credential_pool as pool_mod
from tests.agent.test_run_agent import agent, _mock_plugin_discovery, _mock_response


class RateLimitError(Exception):
    status_code = 429
    response = SimpleNamespace(headers={"retry-after": "600"})
    body = {"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}}


def configure(agent, monkeypatch, *, policy="rotate_first", count=2):
    monkeypatch.setattr(pool_mod, "_load_config_safe", lambda: {
        "credential_pool_rate_limit_policies": {"anthropic": policy}
    })
    monkeypatch.setattr(pool_mod, "_seed_from_singletons", lambda *_: (False, set()))
    monkeypatch.setattr(pool_mod, "_seed_from_env", lambda *_: (False, set()))
    entries = [pool_mod.PooledCredential(
        id=f"synthetic-{i}", label=f"synthetic-{i}", provider="anthropic",
        auth_type="api_key", priority=i, source="manual",
        access_token=f"synthetic-not-a-secret-{i}", last_status=pool_mod.STATUS_OK,
    ) for i in range(count)]
    pool = pool_mod.CredentialPool("anthropic", entries)
    monkeypatch.setattr(pool, "_persist", lambda **_: None)
    agent.provider = "anthropic"
    agent.base_url = "https://api.anthropic.com"
    agent.model = "synthetic-model"
    agent._credential_pool = pool
    first = pool.select(model=agent.model)
    assert first is not None
    agent.api_key = first.runtime_api_key
    agent._credential_pool_entry_id = first.id
    def swap(entry):
        agent.api_key = entry.runtime_api_key
        agent._credential_pool_entry_id = entry.id
        return True
    monkeypatch.setattr(agent, "_swap_credential", swap)
    monkeypatch.setattr(agent, "_persist_session", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_save_trajectory", lambda *a, **kw: None)
    return pool


def drive(agent, monkeypatch, *, failures=1):
    calls, waits = [], []
    def request(kwargs):
        calls.append(agent._credential_pool_entry_id)
        if len(calls) <= failures:
            raise RateLimitError("Rate limit exceeded")
        return _mock_response(content="RECOVERED")
    monkeypatch.setattr(agent, "_interruptible_api_call", request)
    # Exercise the real backoff decision, but never sleep in a test.
    def sleep(_agent, seconds, *_args, **_kwargs):
        waits.append(seconds)
        return None
    monkeypatch.setattr("agent.turn_api_error.interruptible_backoff_sleep", sleep)
    result = agent.run_conversation("Reply with RECOVERED")
    return result, calls, waits


def test_usable_sibling_is_tried_before_600_second_backoff(agent, monkeypatch):
    configure(agent, monkeypatch)
    result, calls, waits = drive(agent, monkeypatch)
    assert result["completed"] and result["final_response"] == "RECOVERED"
    assert calls == ["synthetic-0", "synthetic-1"]
    assert waits == []


def test_no_sibling_preserves_first_retry_without_benching(agent, monkeypatch):
    pool = configure(agent, monkeypatch, count=1)
    recovered, retried = agent._recover_with_credential_pool(
        status_code=429, has_retried_429=False,
        error_context={"message": "Rate limit exceeded", "reset_at": time.time() + 600},
    )
    assert (recovered, retried) == (False, True)
    assert pool_mod.model_cooldown_until(pool.entries()[0], agent.model) is None
    result, calls, waits = drive(agent, monkeypatch)
    assert result["completed"]
    assert calls == ["synthetic-0", "synthetic-0"]
    assert waits == [600.0]


def test_unverifiable_sibling_falls_back_to_default(agent, monkeypatch):
    pool = configure(agent, monkeypatch)
    def unavailable(**kwargs):
        raise RuntimeError("synthetic availability failure")
    monkeypatch.setattr(pool, "has_alternative", unavailable)
    result, calls, waits = drive(agent, monkeypatch)
    assert result["completed"]
    assert calls == ["synthetic-0", "synthetic-0"]
    assert waits == [600.0]


def test_policy_is_a_supported_config_key():
    from hermes_cli.config import _validate_config_key
    assert _validate_config_key("credential_pool_rate_limit_policies.anthropic") == (True, None)


def test_unknown_failure_identity_does_not_trigger_early_rotation(agent, monkeypatch):
    pool = configure(agent, monkeypatch)
    assert not pool.has_alternative(credential_id="not-in-pool", api_key_hint="not-in-pool",
                                    model=agent.model, base_url=agent.base_url)


def test_alias_is_not_an_independent_account_without_key_hint(agent, monkeypatch):
    pool = configure(agent, monkeypatch)
    pool.entries()[1].access_token = pool.entries()[0].access_token
    assert not pool.has_alternative(credential_id="synthetic-0", model=agent.model,
                                    base_url=agent.base_url)


@pytest.mark.parametrize("policy", ["retry_once", "invalid", ""])
def test_default_and_invalid_policies_keep_same_account_retry(agent, monkeypatch, policy):
    configure(agent, monkeypatch, policy=policy)
    result, calls, waits = drive(agent, monkeypatch)
    assert result["completed"]
    assert calls == ["synthetic-0", "synthetic-0"]
    assert waits == [600.0]


def test_second_account_rate_limit_preserves_bounded_backoff(agent, monkeypatch):
    configure(agent, monkeypatch)
    result, calls, waits = drive(agent, monkeypatch, failures=2)
    assert result["completed"]
    assert calls == ["synthetic-0", "synthetic-1", "synthetic-1"]
    assert waits == [600.0]


@pytest.mark.parametrize("blocked", ["dead", "exhausted", "model", "endpoint", "empty"])
def test_ineligible_sibling_preserves_first_retry(agent, monkeypatch, blocked):
    pool = configure(agent, monkeypatch)
    sibling = pool.entries()[1]
    if blocked == "dead":
        sibling.last_status = pool_mod.STATUS_DEAD
    elif blocked == "exhausted":
        sibling.last_status = pool_mod.STATUS_EXHAUSTED
        sibling.last_status_at = time.time()
    elif blocked == "model":
        sibling.model_cooldowns = {agent.model: time.time() + 600}
    elif blocked == "endpoint":
        sibling.base_url = "https://other.example.invalid"
    elif blocked == "empty":
        sibling.access_token = ""
    result, calls, waits = drive(agent, monkeypatch)
    assert result["completed"]
    assert calls == ["synthetic-0", "synthetic-0"]
    assert waits == [600.0]


@pytest.mark.parametrize("config", [{}, {"credential_pool_rate_limit_policies": None},
                                     {"credential_pool_rate_limit_policies": []},
                                     {"credential_pool_rate_limit_policies": {"other": "rotate_first"}}])
def test_absent_malformed_and_other_provider_settings_keep_default(monkeypatch, config):
    monkeypatch.setattr(pool_mod, "_load_config_safe", lambda: config)
    assert pool_mod.get_pool_rate_limit_policy("anthropic") == "retry_once"





