"""AgentGuard behavior tests.

The first block pins the documented behavior. If one of these fails, the
README and the agent_guard docstring are wrong too.
"""

import json
import os
import stat
import threading
import time
from unittest import mock

import pytest

from conftest import FakeClock
from slimeflow import agent_guard as ag
from slimeflow.agent_guard import (
    ANOMALY_DECAY_PER_SEC,
    HIGH_IMPACT,
    QUARANTINE_THRESHOLD,
    RISK,
    SECRET_BUMP,
    SECRETISH,
    UNCONFIRMED_BUMP,
    AgentGuard,
    redact,
)


def make(**kw):
    clock = FakeClock()
    return AgentGuard(clock=clock, **kw), clock


def unconfirmed(g, agent, kind, i):
    # Distinct tool/detail per call so the loop detector stays out of it.
    return g.report(agent, kind, tool=f"{kind}-tool-{i}", detail=f"call {i}")


# ─── defaults (must not change without Lauren's sign-off) ────────────────

def test_default_values_unchanged():
    assert QUARANTINE_THRESHOLD == 0.6
    assert ANOMALY_DECAY_PER_SEC == 0.002
    assert UNCONFIRMED_BUMP == 0.18
    assert SECRET_BUMP == 0.20
    assert RISK == {
        "message": 0.04, "tool": 0.05, "code_exec": 0.12, "network": 0.10,
        "credential": 0.25, "send": 0.22, "delete": 0.20, "pay": 0.30,
        "browser_auth": 0.18, "file_write": 0.08, "loop": 0.15, "ignore_stop": 0.35,
    }
    assert HIGH_IMPACT == {"send", "delete", "pay", "credential", "browser_auth", "code_exec"}


# ─── locked behavior: deletes ───────────────────────────────────────────

def test_first_unconfirmed_delete_goes_through():
    g, _ = make()
    r = unconfirmed(g, "a", "delete", 1)
    assert r["allowed"] is True
    assert r["anomaly"] == 0.38
    assert r["bump"] == 0.38


@pytest.mark.parametrize("gap", [0.0, 0.001, 1.0, 30.0, 79.9, 80.0])
def test_second_unconfirmed_delete_is_blocked(gap):
    g, clock = make()
    unconfirmed(g, "a", "delete", 1)
    clock.advance(gap)
    r = unconfirmed(g, "a", "delete", 2)
    assert r["allowed"] is False
    assert r["quarantined"] is True
    assert "unconfirmed_delete" in r["reason"]


@pytest.mark.parametrize("gap", [80.01, 300.0])
def test_second_delete_after_80s_is_allowed(gap):
    g, clock = make()
    unconfirmed(g, "a", "delete", 1)
    clock.advance(gap)
    assert unconfirmed(g, "a", "delete", 2)["allowed"] is True


def test_frozen_until_release_no_matter_how_long():
    g, clock = make()
    unconfirmed(g, "a", "delete", 1)
    unconfirmed(g, "a", "delete", 2)
    for _ in range(3):
        clock.advance(86_400)
        assert g.check("a")["allowed"] is False
        assert g.report("a", "message", user_confirmed=True)["allowed"] is False
    released = g.release("a", by="lauren")
    assert released["released"] is True
    assert released["allowed"] is True
    assert g.check("a")["allowed"] is True


def test_confirmed_deletes_score_lower():
    g, _ = make()
    r1 = g.report("a", "delete", tool="rm", detail="1", user_confirmed=True)
    r2 = g.report("a", "delete", tool="rm", detail="2", user_confirmed=True)
    assert r1["anomaly"] == 0.2 and r2["anomaly"] == 0.4
    assert r2["allowed"] is True


# ─── locked behavior: shell commands (code_exec) ────────────────────────

@pytest.mark.parametrize("gap", [0.000001, 0.001, 1.0, 74.9])
def test_two_unconfirmed_shell_commands_allowed_third_freezes(gap):
    g, clock = make()
    r1 = unconfirmed(g, "a", "code_exec", 1)
    assert r1["bump"] == 0.3 and r1["allowed"] is True
    clock.advance(gap)
    r2 = unconfirmed(g, "a", "code_exec", 2)
    assert r2["allowed"] is True
    clock.advance(gap)
    r3 = unconfirmed(g, "a", "code_exec", 3)
    assert r3["allowed"] is False
    assert r3["quarantined"] is True


def test_two_shell_commands_one_second_apart_score_0598():
    g, clock = make()
    unconfirmed(g, "a", "code_exec", 1)
    clock.advance(1.0)
    r2 = unconfirmed(g, "a", "code_exec", 2)
    assert r2["anomaly"] == 0.598
    assert r2["allowed"] is True


@pytest.mark.parametrize("gap1,gap2,frozen", [
    (75.0, 74.99, True),     # span 149.99 s
    (75.0, 75.0, True),      # span 150 s: exactly 0.60
    (75.0, 75.01, False),    # span 150.01 s
    (10.0, 140.0, True),     # uneven gaps, span 150 s
    (10.0, 140.01, False),   # uneven gaps, span 150.01 s
    (149.0, 0.5, True),
])
def test_third_shell_command_bound_is_150s_span(gap1, gap2, frozen):
    g, clock = make()
    unconfirmed(g, "a", "code_exec", 1)
    clock.advance(gap1)
    assert unconfirmed(g, "a", "code_exec", 2)["allowed"] is True
    clock.advance(gap2)
    assert unconfirmed(g, "a", "code_exec", 3)["quarantined"] is frozen


def test_identical_shell_command_repeats_freeze_sooner():
    g, clock = make()
    for _ in range(2):
        assert g.report("a", "code_exec", tool="bash", detail="pkill node")["allowed"]
        clock.advance(100)
    # span 200 s would not freeze distinct commands; the loop penalty does
    r = g.report("a", "code_exec", tool="bash", detail="pkill node")
    assert r["quarantined"] is True
    assert "loop_x3" in r["reason"]


# ─── scope: the guard only sees reports, and counts no money ────────────

def test_pay_amount_is_not_counted():
    g, _ = make()
    small = g.report("a", "pay", tool="stripe", detail="$1", user_confirmed=True)
    g2, _ = make()
    big = g2.report("a", "pay", tool="stripe", detail="$1000000", user_confirmed=True)
    assert small["bump"] == big["bump"] == 0.3


def test_deletes_add_up_across_different_paths():
    g, _ = make()
    g.report("a", "delete", tool="rm", detail="/tmp/one")
    r = g.report("a", "delete", tool="rm", detail="/srv/two")
    assert r["quarantined"] is True


# ─── threshold semantics and float safety ───────────────────────────────

def test_float_error_does_not_decide_quarantine():
    # 0.30 + 0.30 - 0.15 + 0.30 - 0.15 is 0.5999999999999999 in floating
    # point. The exact value is 0.60, so the third command must freeze.
    g, clock = make()
    unconfirmed(g, "a", "code_exec", 1)
    clock.advance(75)
    unconfirmed(g, "a", "code_exec", 2)
    clock.advance(75)
    assert unconfirmed(g, "a", "code_exec", 3)["quarantined"] is True


def test_same_clock_reading_two_shell_commands_freeze():
    # Documented edge case: with zero elapsed time the score is exactly 0.60.
    g, _ = make()
    unconfirmed(g, "a", "code_exec", 1)
    r = unconfirmed(g, "a", "code_exec", 2)
    assert r["anomaly"] == 0.6
    assert r["quarantined"] is True


def test_unconfirmed_send_with_secret_freezes_on_first_call():
    g, _ = make()
    r = g.report("a", "send", tool="gmail.send", payload="api_key=abc123")
    assert r["anomaly"] == 0.6
    assert r["quarantined"] is True


def test_threshold_validation():
    for bad in (0, -1, 1, 5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            AgentGuard(threshold=bad)


def test_tool_calls_one_second_apart_freeze_on_the_thirteenth():
    # 0.05 per call minus 0.002 per second: 13 calls in 12 s reach 0.626.
    g, clock = make()
    for i in range(12):
        assert g.report("a", "tool", tool="search", detail=str(i), user_confirmed=True)["allowed"]
        clock.advance(1)
    assert g.report("a", "tool", tool="search", detail="13", user_confirmed=True)["quarantined"]


# ─── release / quarantine ───────────────────────────────────────────────

def test_release_caps_score_and_gives_no_decay_credit_for_frozen_time():
    g, clock = make()
    unconfirmed(g, "a", "delete", 1)
    unconfirmed(g, "a", "delete", 2)
    clock.advance(600)
    r = g.release("a")
    assert r["anomaly"] == 0.24
    # 0.24 + 0.38 = 0.62: an unconfirmed delete right after release refreezes
    assert unconfirmed(g, "a", "delete", 3)["quarantined"] is True


def test_release_unknown_agent_is_a_noop():
    g, _ = make()
    r = g.release("ghost")
    assert r["released"] is False
    assert g.status()["total"] == 0


def test_release_is_audited():
    g, _ = make()
    g.quarantine("a", "manual test")
    g.release("a", by="lauren", note="checked logs")
    last = g.status()["agents"][0]["recent"][-1]
    assert last["kind"] == "release"
    assert last["reasons"] == ["released_by:lauren"]
    assert last["detail"] == "checked logs"


def test_manual_quarantine_blocks():
    g, _ = make()
    g.report("a", "message")
    r = g.quarantine("a", "looks wrong")
    assert r["allowed"] is False
    assert g.check("a")["reason"] == "looks wrong"


def test_blocked_attempts_are_logged():
    g, _ = make()
    g.quarantine("a")
    r = g.report("a", "pay", tool="stripe", detail="charge card")
    assert r["allowed"] is False and r["bump"] == 0.0
    rec = g.status()["agents"][0]
    assert rec["blocked"] == 1 and rec["actions"] == 0
    assert rec["recent"][-1]["reasons"] == ["blocked_quarantined"]


# ─── inputs ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["DELETE", " delete ", "Delete"])
def test_kind_is_case_and_space_insensitive(kind):
    g, _ = make()
    assert g.report("a", kind)["bump"] == 0.38


def test_unknown_kind_scores_as_tool_and_is_flagged():
    g, _ = make()
    r = g.report("a", "shell")
    assert r["bump"] == 0.05
    assert r["agent"]["recent"][-1]["reasons"] == ["unknown_kind"]


def test_strict_kinds_rejects_unknown():
    g, _ = make(strict_kinds=True)
    with pytest.raises(ValueError):
        g.report("a", "shell")


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_user_confirmed_must_be_bool(value):
    g, _ = make()
    with pytest.raises(TypeError):
        g.report("a", "delete", user_confirmed=value)


@pytest.mark.parametrize("agent_id", ["", "   ", "a" * 201, "bad\nid"])
def test_bad_agent_ids_rejected(agent_id):
    g, _ = make()
    with pytest.raises(ValueError):
        g.report(agent_id, "message")


def test_check_does_not_create_records():
    g, _ = make()
    assert g.check("never-reported")["allowed"] is True
    assert g.status()["total"] == 0


# ─── time source ────────────────────────────────────────────────────────

def test_wall_clock_jump_does_not_decay_score():
    g, _ = make()
    unconfirmed(g, "a", "delete", 1)
    real = time.time()
    with mock.patch.object(ag.time, "time", lambda: real + 3600):
        r = unconfirmed(g, "a", "delete", 2)
    assert r["quarantined"] is True


def test_default_clock_is_monotonic():
    assert AgentGuard()._clock is time.monotonic


# ─── secrets ────────────────────────────────────────────────────────────

SECRETS = {
    "legacy openai": "sk-abcdefghijklmnop1234",
    "openai project": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234",
    "anthropic": "sk-ant-api03-AbCdEfGhIjKlMnOpQrSt",
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "github classic": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "github fine-grained": "github_pat_11ABCDEFG0123456789_abcdefghijkl",
    "slack": "xoxb-123456789012-abcdefghijkl",
    "pem rsa": "-----BEGIN RSA PRIVATE KEY-----",
    "pem ec": "-----BEGIN EC PRIVATE KEY-----",
    "pem pkcs8": "-----BEGIN PRIVATE KEY-----",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijk",
    "bearer": "Authorization: Bearer abc.def-123",
    "keyword": "api_key=hunter2",
}


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_secret_patterns_detected(name):
    assert SECRETISH.search(SECRETS[name])
    g, _ = make()
    r = g.report("a", "network", detail=SECRETS[name], user_confirmed=True)
    assert r["bump"] == pytest.approx(RISK["network"] + SECRET_BUMP)


@pytest.mark.parametrize("text", ["task-management-system-v2", "ask-the-user-before-sending", "plain text"])
def test_secret_pattern_false_positives_avoided(text):
    assert not SECRETISH.search(text)


def test_secrets_redacted_from_history_and_status():
    g, _ = make()
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    g.report("a", "network", tool="curl", detail=f"curl -H 'Authorization: Bearer {token}' password=hunter2",
             payload="sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234", user_confirmed=True)
    dumped = json.dumps(g.status())
    assert token not in dumped
    assert "hunter2" not in dumped
    assert "sk-proj" not in dumped
    assert "[redacted]" in dumped


def test_redact_keeps_plain_text():
    assert redact("reset password page") == "reset password page"
    assert redact("password=hunter2") == "password=[redacted]"


def test_large_payload_scan_is_fast():
    g, _ = make()
    t0 = time.perf_counter()
    g.report("a", "tool", payload="bearer " + "a" * 2_000_000, user_confirmed=True)
    assert time.perf_counter() - t0 < 5


# ─── concurrency ────────────────────────────────────────────────────────

def test_concurrent_reports_are_counted_exactly():
    g = AgentGuard()
    n_threads, per = 8, 300

    def work(t):
        for i in range(per):
            g.report(f"agent-{t % 2}", "message", detail=f"{t}-{i}", user_confirmed=True)

    threads = [threading.Thread(target=work, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    agents = {a["agent_id"]: a for a in g.status()["agents"]}
    total = sum(a["actions"] + a["blocked"] for a in agents.values())
    assert total == n_threads * per
    for a in agents.values():
        assert 0.0 <= a["anomaly"] <= 1.0


def test_concurrent_deletes_freeze_exactly_once():
    g = AgentGuard()
    barrier = threading.Barrier(10)
    results = []

    def work(i):
        barrier.wait()
        results.append(g.report("a", "delete", tool="rm", detail=str(i))["allowed"])

    threads = [threading.Thread(target=work, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1  # only the first delete gets through


# ─── persistence ────────────────────────────────────────────────────────

def test_quarantine_survives_restart(tmp_path):
    path = tmp_path / "state.json"
    g1 = AgentGuard(state_path=path)
    g1.report("a", "delete", tool="rm", detail="1")
    g1.report("a", "delete", tool="rm", detail="2")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    g2 = AgentGuard(state_path=path)
    assert g2.check("a")["allowed"] is False
    g2.release("a", by="lauren")
    g3 = AgentGuard(state_path=path)
    assert g3.check("a")["allowed"] is True


def test_damaged_state_file_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(ValueError):
        AgentGuard(state_path=path)


def test_no_state_file_without_state_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    g = AgentGuard()
    g.quarantine("a")
    assert list(tmp_path.iterdir()) == []
