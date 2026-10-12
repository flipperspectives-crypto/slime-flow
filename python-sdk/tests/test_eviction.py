import pytest
import time
from slimeflow.agent_guard import AgentGuard

class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

def test_quarantine_ttl_eviction():
    clock = FakeClock()
    original_time = time.time
    time.time = lambda: clock.now
    try:
        guard = AgentGuard(clock=clock)
        guard.report("agent_1", "pay", user_confirmed=False)
        guard.report("agent_1", "pay", user_confirmed=False)
        assert guard.check("agent_1")["quarantined"] == True

        # Advance 24 hours + 1 second
        clock.advance(86401.0)

        # Trigger eviction
        guard.report("agent_2", "tool")

        assert "agent_1" not in guard._agents
    finally:
        time.time = original_time

def test_quarantine_cap_flood():
    clock = FakeClock()
    original_time = time.time

    # We must patch time.time for wall clock, since eviction is based on last_seen (wall)
    time.time = lambda: clock.now

    try:
        guard = AgentGuard(clock=clock)

        for i in range(10005):
            clock.advance(0.01) # ensure distinct last_seen
            guard.report(f"q_{i}", "pay", user_confirmed=False)
            guard.report(f"q_{i}", "pay", user_confirmed=False)

        clock.advance(300.0) # wait for eviction cycle
        guard.report("agent_trigger", "tool")

        # Cap is 10000. The oldest ones should be gone.
        assert len([r for r in guard._agents.values() if r.quarantined]) == 10000
    finally:
        time.time = original_time

def test_restart_no_resurrection(tmp_path):
    clock = FakeClock()
    original_time = time.time
    time.time = lambda: clock.now
    try:
        path = tmp_path / "state.json"
        guard = AgentGuard(clock=clock, state_path=path)
        guard.report("q_agent", "pay", user_confirmed=False)
        guard.report("q_agent", "pay", user_confirmed=False)

        # Advance 24 hours + 1 second
        clock.advance(86401.0)

        # Create new guard pointing to same state
        guard2 = AgentGuard(clock=clock, state_path=path)
        assert "q_agent" not in guard2._agents
    finally:
        time.time = original_time
