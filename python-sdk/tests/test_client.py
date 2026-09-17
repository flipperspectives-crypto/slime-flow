"""Tests for Slime Flow Python SDK.

Run with: python -m pytest tests/ -v
"""

import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from slimeflow import SlimeFlow, Frame, Agent, Status


# ─── Sample data ─────────────────────────────────────────────────────────

SAMPLE_FRAME_JSON = {
    "step": 42,
    "w": 64,
    "h": 64,
    "grid": [0.0] * 4096,  # 64*64
    "rogue_grid": [0.0] * 4096,
    "agents": [
        {"x": 0.1, "y": 0.2, "t": 1, "a": 0.0, "q": 0},
        {"x": 0.3, "y": 0.4, "t": 2, "a": 0.0, "q": 0},
        {"x": 0.5, "y": 0.6, "t": 5, "a": 0.8, "q": 1},  # quarantined rogue
        {"x": 0.7, "y": 0.8, "t": 3, "a": 0.0, "q": 2},  # fault-killed
    ],
    "rogue_count": 1,
    "quarantine_count": 1,
    "fault_active": True,
}

SAMPLE_STATUS_JSON = {
    "step": 100,
    "rogue_count": 3,
    "quarantine_count": 1,
    "fault_active": False,
    "gpu": "NVIDIA GeForce RTX 4060",
}


# ─── Model tests ─────────────────────────────────────────────────────────


class TestAgent:
    def test_from_dict(self):
        a = Agent(x=0.5, y=0.6, type=2, anomaly=0.1, quarantine=0)
        assert a.x == 0.5
        assert a.y == 0.6
        assert a.type == 2
        assert a.type_name == "Harvester"
        assert a.is_active
        assert not a.is_rogue
        assert not a.is_quarantined
        assert not a.is_dead

    def test_rogue(self):
        a = Agent(x=0.1, y=0.1, type=5, anomaly=0.9, quarantine=1)
        assert a.is_rogue
        assert a.is_quarantined
        assert a.anomaly_pct == 90.0

    def test_dead(self):
        a = Agent(x=0.5, y=0.5, type=1, anomaly=0.0, quarantine=2)
        assert a.is_dead
        assert not a.is_active

    def test_tuple(self):
        a = Agent(x=0.3, y=0.7, type=4, anomaly=0.2, quarantine=0)
        assert a.as_tuple() == (0.3, 0.7, 4, 0.2, 0)


class TestFrame:
    def test_from_json(self):
        frame = Frame.from_json(SAMPLE_FRAME_JSON)
        assert frame.step == 42
        assert frame.grid_w == 64
        assert frame.grid_h == 64
        assert len(frame.agents) == 4
        assert frame.rogue_count == 1
        assert frame.quarantine_count == 1
        assert frame.fault_active is True

    def test_density_empty_grid(self):
        frame = Frame.from_json(SAMPLE_FRAME_JSON)
        assert frame.density() == 0.0
        assert frame.peak() == 0.0

    def test_density(self):
        data = dict(SAMPLE_FRAME_JSON)
        data["grid"] = [0.5] * 64 * 64
        frame = Frame.from_json(data)
        assert frame.density() == 0.5

    def test_type_counts(self):
        frame = Frame.from_json(SAMPLE_FRAME_JSON)
        counts = frame.type_counts()
        assert counts[1] == 1  # Scout
        assert counts[2] == 1  # Harvester
        assert counts[3] == 0  # Guardian (dead)
        assert counts[5] == 1  # Rogue (quarantined but counted)

    def test_alive_count(self):
        frame = Frame.from_json(SAMPLE_FRAME_JSON)
        assert frame.alive_count() == 3  # 4 agents - 1 fault-killed

    def test_mean_anomaly(self):
        frame = Frame.from_json(SAMPLE_FRAME_JSON)
        assert frame.mean_anomaly() == 0.8  # only 1 rogue

    def test_grid_2d(self):
        frame = Frame.from_json(SAMPLE_FRAME_JSON)
        g2d = frame.grid_2d()
        assert len(g2d) == 64
        assert len(g2d[0]) == 64

    def test_repr(self):
        frame = Frame.from_json(SAMPLE_FRAME_JSON)
        r = repr(frame)
        assert "step=42" in r
        assert "rogues=1" in r

    def test_from_json_string(self):
        raw = json.dumps(SAMPLE_FRAME_JSON)
        frame = Frame.from_json(raw)
        assert frame.step == 42


class TestStatus:
    def test_from_json(self):
        s = Status.from_json(SAMPLE_STATUS_JSON)
        assert s.step == 100
        assert s.rogue_count == 3
        assert s.gpu == "NVIDIA GeForce RTX 4060"
        assert s.fault_active is False

    def test_repr(self):
        s = Status.from_json(SAMPLE_STATUS_JSON)
        r = repr(s)
        assert "RTX 4060" in r
        assert "step=100" in r


# ─── Client tests (offline — model parsing) ─────────────────────────────


class TestClientOffline:
    """Tests that don't require a running server."""

    def test_repr(self):
        sf = SlimeFlow("192.168.1.50", 9090)
        r = repr(sf)
        assert "192.168.1.50" in r
        assert "9090" in r

    def test_default_host(self):
        sf = SlimeFlow()
        assert sf.host == "localhost"
        assert sf.port == 8080


# ─── Integration tests (require running server) ─────────────────────────


class TestClientLive:
    """Tests that require a running Slime Flow server at localhost:8080."""

    @staticmethod
    def _require_server():
        """Skip if server is not available."""
        sf = SlimeFlow(timeout=2)
        if not sf.ping():
            pytest.skip("Slime Flow server not available at localhost:8080")

    def test_ping(self):
        self._require_server()
        sf = SlimeFlow()
        assert sf.ping() is True

    def test_status(self):
        self._require_server()
        sf = SlimeFlow()
        s = sf.status()
        assert isinstance(s, Status)
        assert s.step >= 0
        assert len(s.gpu) > 0

    def test_frame(self):
        self._require_server()
        sf = SlimeFlow()
        f = sf.frame()
        assert isinstance(f, Frame)
        assert f.step > 0
        assert len(f.agents) > 0
        assert len(f.pheromone) == f.grid_w * f.grid_h

    def test_reset(self):
        self._require_server()
        sf = SlimeFlow()
        sf.reset()
        f = sf.frame()
        assert f.step > 0  # First step after reset

    def test_spawn_rogues(self):
        self._require_server()
        sf = SlimeFlow()
        sf.reset()
        sf.spawn_rogues()
        f = None
        # After spawning, step a few frames for rogues to appear
        for _ in range(5):
            f = sf.frame()
        assert f is not None
        assert f.rogue_count > 0

    def test_inject_fault(self):
        self._require_server()
        sf = SlimeFlow()
        sf.reset()
        sf.inject_fault(0.5, 0.5)
        f = sf.frame()
        assert f.fault_active is True

    def test_clear_fault(self):
        self._require_server()
        sf = SlimeFlow()
        sf.inject_fault(0.5, 0.5)
        sf.clear_fault()
        f = sf.frame()
        assert f.fault_active is False

    def test_stream(self):
        self._require_server()
        sf = SlimeFlow()
        sf.reset()
        frames = list(sf.stream(max_frames=5, interval=0.01))
        assert len(frames) == 5
        assert frames[0].step < frames[-1].step  # steps increment

    def test_stream_callback(self):
        self._require_server()
        sf = SlimeFlow()
        sf.reset()
        collected = []

        def cb(f):
            collected.append(f.step)

        list(sf.stream(max_frames=3, interval=0.01, on_frame=cb))
        assert len(collected) == 3
        assert collected[0] < collected[-1]


# ─── Run ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest

    # Run offline tests + optionally live tests
    pytest.main([__file__, "-v", "--tb=short"])
