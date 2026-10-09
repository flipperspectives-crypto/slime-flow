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
