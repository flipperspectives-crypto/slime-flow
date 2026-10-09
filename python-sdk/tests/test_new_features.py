import pytest
import time
from typing import Callable
from slimeflow.agent_guard import AgentGuard

class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

def test_loop_evasion():
    clock = FakeClock()
    import time
    original_time = time.time
    def fake_time():
        return clock.now
    time.time = fake_time
    try:
        guard = AgentGuard(clock=clock)
        # code_exec unconfirmed = 0.12 + 0.18 = 0.30
        guard.report("test1", "code_exec", user_confirmed=False)
        assert guard.status()["agents"][0]["unconfirmed_hi_60s"] == 1

        clock.advance(30.0)
        # browser_auth confirmed = 0.18
        guard.report("test1", "browser_auth", user_confirmed=True)
        # unconfirmed hi count should still be 1
        assert guard.status()["agents"][0]["unconfirmed_hi_60s"] == 1

        # message unconfirmed = 0.04 (not high impact)
        guard.report("test1", "message", user_confirmed=False)
        assert guard.status()["agents"][0]["unconfirmed_hi_60s"] == 1

        clock.advance(10.0)
        # browser_auth unconfirmed = 0.18 + 0.18 = 0.36 -> total 0.30 - decay + 0.36 = 0.66 > 0.6, will quarantine
        # but wait, let's not quarantine, release it?
        # Actually just let's check unconfirmed_hi_60s after doing another code_exec
        # Wait, if we quarantine, report() won't add to unconfirmed_hi_timestamps.
        # But `unconfirmed_hi_60s` counts what actually got through or blocked?
        # The instructions say "counting unconfirmed high-impact actions in 60s window".
        # It's an extra signal. I added it before quarantine check. Wait!
        # `bump` calculation, then `rec.unconfirmed_hi_timestamps.append(wall)`, then `if self._crossed(rec.anomaly): rec.quarantined = True; return ...`
        # So it DOES get appended if it is the action that CAUSES the quarantine!
    finally:
        time.time = original_time

def test_loop_evasion_2():
    clock = FakeClock()
    import time
    original_time = time.time
    time.time = lambda: clock.now
    try:
        guard = AgentGuard(clock=clock)
        guard.report("test1", "code_exec", user_confirmed=False)
        assert guard.status()["agents"][0]["unconfirmed_hi_60s"] == 1

        clock.advance(30.0)
        guard.report("test1", "code_exec", user_confirmed=False)
        assert guard.status()["agents"][0]["unconfirmed_hi_60s"] == 2

        clock.advance(31.0) # total 61.0 from first
        # Third one causes quarantine, anomaly 0.3+0.3+0.3 = 0.9. decay = 0.002 * 61 = 0.122 => 0.778
        guard.report("test1", "code_exec", user_confirmed=False)
        # The first code_exec was at 1000. Current is 1061. Filter removes 1000.
        # So remaining are 1030, 1061 => len = 2.
        assert guard.status()["agents"][0]["unconfirmed_hi_60s"] == 2
    finally:
        time.time = original_time


def test_quarantined_survives_idle_sweep():
    clock = FakeClock()
    import time
    original_time = time.time
    time.time = lambda: clock.now
    try:
        guard = AgentGuard(clock=clock)
        guard.report("test_q", "pay", user_confirmed=False)
        guard.report("test_q", "pay", user_confirmed=False)

        assert guard.check("test_q")["quarantined"] == True

        guard.report("test_active", "tool")
        guard.report("test_idle", "tool")

        # Advance 25 hours
        clock.advance(90000.0)

        # This will trigger evict
        guard.report("test_active", "tool")

        assert "test_q" in guard._agents
        assert guard._agents["test_q"].quarantined == True
        assert "test_idle" not in guard._agents
        assert "test_active" in guard._agents
    finally:
        time.time = original_time

def test_quarantined_survives_lru_pressure():
    clock = FakeClock()
    import time
    original_time = time.time
    time.time = lambda: clock.now
    try:
        guard = AgentGuard(clock=clock)
        # Quarantine one agent
        guard.report("test_q", "pay", user_confirmed=False)
        guard.report("test_q", "pay", user_confirmed=False)
        assert guard._agents["test_q"].quarantined == True

        # Fill registry past cap (10005 total)
        for i in range(10005):
            guard.report(f"test_fill_{i}", "tool")

        assert "test_q" in guard._agents
        assert guard._agents["test_q"].quarantined == True

        # Total size should be exactly 10000
        assert len(guard._agents) == 10000
    finally:
        time.time = original_time

def test_check_denies_after_eviction_pressure():
    # check() still denies? If it's quarantined, it shouldn't be evicted, so it still denies.
    clock = FakeClock()
    import time
    original_time = time.time
    time.time = lambda: clock.now
    try:
        guard = AgentGuard(clock=clock)
        guard.report("test_q", "pay", user_confirmed=False)
        guard.report("test_q", "pay", user_confirmed=False)
        assert guard.check("test_q")["allowed"] == False

        for i in range(10005):
            guard.check(f"test_check_{i}")

        # test_q shouldn't be evicted, so it still denies
        assert guard.check("test_q")["allowed"] == False
    finally:
        time.time = original_time
