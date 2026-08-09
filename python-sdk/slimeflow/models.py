"""Data models for Slime Flow frames and agents."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # numpy is optional


@dataclass
class Agent:
    """A single agent in the swarm.

    Attributes:
        x: Normalized x position (0–1)
        y: Normalized y position (0–1)
        type: Agent type (1=Scout, 2=Harvester, 3=Guardian, 4=Emergent, 5=Rogue)
        anomaly: Anomaly score (0–1), rogues accumulate +0.08/step
        quarantine: Status (0=active, 1=quarantined, 2=fault-killed)
    """

    x: float
    y: float
    type: int
    anomaly: float
    quarantine: int

    # Human-readable type names
    TYPE_NAMES = {
        1: "Scout",
        2: "Harvester",
        3: "Guardian",
        4: "Emergent",
        5: "Rogue",
    }

    @property
    def type_name(self) -> str:
        """Human-readable agent type name."""
        return self.TYPE_NAMES.get(self.type, f"Unknown({self.type})")

    @property
    def is_rogue(self) -> bool:
        return self.type == 5

    @property
    def is_quarantined(self) -> bool:
        return self.quarantine == 1

    @property
    def is_dead(self) -> bool:
        return self.quarantine == 2

    @property
    def is_active(self) -> bool:
        return self.quarantine == 0

    @property
    def anomaly_pct(self) -> float:
        """Anomaly as percentage (0–100)."""
        return self.anomaly * 100.0

    def as_tuple(self) -> Tuple[float, float, int, float, int]:
        """Return (x, y, type, anomaly, quarantine)."""
        return (self.x, self.y, self.type, self.anomaly, self.quarantine)


@dataclass
class Frame:
    """A single simulation frame from the GPU server.

    Attributes:
        step: Simulation step number
        grid_w: Grid width (typically 64, downsampled from 128)
        grid_h: Grid height
        pheromone: Flat list of pheromone values (0–1), length grid_w * grid_h
        rogue_pheromone: Flat list of rogue pheromone values (0–1)
        agents: List of Agent objects (typically 512)
        rogue_count: Number of rogue agents
        quarantine_count: Number of quarantined agents
        fault_active: Whether a fault zone is active
        raw: Raw JSON dict for advanced use
    """

    step: int
    grid_w: int
    grid_h: int
    pheromone: List[float]
    rogue_pheromone: List[float]
    agents: List[Agent]
    rogue_count: int
    quarantine_count: int
    fault_active: bool
    raw: dict = field(repr=False)

    # Agent type counts, computed on access
    _type_counts: Optional[dict] = field(default=None, repr=False, init=False)

    @classmethod
    def from_json(cls, data: dict | str) -> "Frame":
        """Parse a frame from JSON dict or string."""
        if isinstance(data, str):
            data = json.loads(data)

        agents = [
            Agent(
                x=a["x"],
                y=a["y"],
                type=a["t"],
                anomaly=a["a"],
                quarantine=a["q"],
            )
            for a in data["agents"]
        ]

        return cls(
            step=data["step"],
            grid_w=data["w"],
            grid_h=data["h"],
            pheromone=data["grid"],
            rogue_pheromone=data["rogue_grid"],
            agents=agents,
            rogue_count=data["rogue_count"],
            quarantine_count=data["quarantine_count"],
            fault_active=data["fault_active"],
            raw=data,
        )

    def density(self) -> float:
        """Average pheromone density across the grid (0–1)."""
        n = len(self.pheromone)
        if n == 0:
            return 0.0
        return sum(self.pheromone) / n

    def rogue_density(self) -> float:
        """Average rogue pheromone density across the grid (0–1)."""
        n = len(self.rogue_pheromone)
        if n == 0:
            return 0.0
        return sum(self.rogue_pheromone) / n

    def peak(self) -> float:
        """Maximum pheromone value in the grid."""
        return max(self.pheromone) if self.pheromone else 0.0

    def type_counts(self) -> dict:
        """Count agents by type. Returns {1: count, 2: count, ...}."""
        if self._type_counts is None:
            counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
            for ag in self.agents:
                if ag.quarantine != 2:  # don't count dead agents
                    counts[ag.type] = counts.get(ag.type, 0) + 1
            self._type_counts = counts
        return self._type_counts

    def alive_count(self) -> int:
        """Number of non-fault-killed agents."""
        return sum(1 for a in self.agents if a.quarantine != 2)

    def mean_anomaly(self) -> float:
        """Mean anomaly score across rogues."""
        rogues = [a for a in self.agents if a.type == 5]
        if not rogues:
            return 0.0
        return sum(a.anomaly for a in rogues) / len(rogues)

    def integrity(self) -> float:
        """Swarm integrity score (100 = pristine, 0 = fully compromised)."""
        rdens = self.rogue_density()
        leak = min(rdens * 450, 100)  # matches browser: rspread * 1.5 capped
        return max(100 - leak * 0.8, 0)

    def grid_2d(self) -> List[List[float]]:
        """Return pheromone grid as 2D list of rows [grid_h][grid_w]."""
        w, h = self.grid_w, self.grid_h
        return [self.pheromone[i * w : (i + 1) * w] for i in range(h)]

    def rogue_grid_2d(self) -> List[List[float]]:
        """Return rogue pheromone grid as 2D list of rows."""
        w, h = self.grid_w, self.grid_h
        return [self.rogue_pheromone[i * w : (i + 1) * w] for i in range(h)]

    # ─── numpy helpers (optional) ─────────────────────────────────────────
    def grid_np(self):
        """Return pheromone grid as numpy (grid_h, grid_w) float32 array.
        Requires ``numpy`` to be installed.
        """
        import numpy as np
        return np.array(self.pheromone, dtype=np.float32).reshape(
            self.grid_h, self.grid_w
        )

    def rogue_grid_np(self):
        """Return rogue pheromone grid as numpy (grid_h, grid_w) float32 array."""
        import numpy as np
        return np.array(self.rogue_pheromone, dtype=np.float32).reshape(
            self.grid_h, self.grid_w
        )

    def agents_np(self):
        """Return agent data as numpy structured array or dict of arrays.
        Returns dict with keys: x, y, type, anomaly, quarantine — each a 1D array.
        """
        import numpy as np
        n = len(self.agents)
        return {
            "x": np.array([a.x for a in self.agents], dtype=np.float32),
            "y": np.array([a.y for a in self.agents], dtype=np.float32),
            "type": np.array([a.type for a in self.agents], dtype=np.int32),
            "anomaly": np.array([a.anomaly for a in self.agents], dtype=np.float32),
            "quarantine": np.array([a.quarantine for a in self.agents], dtype=np.int32),
        }

    def __repr__(self) -> str:
        return (
            f"Frame(step={self.step}, agents={len(self.agents)}, "
            f"rogues={self.rogue_count}, quarantined={self.quarantine_count}, "
            f"density={self.density():.3f}, integrity={self.integrity():.1f}%)"
        )


@dataclass
class Status:
    """Server status information."""

    step: int
    rogue_count: int
    quarantine_count: int
    fault_active: bool
    gpu: str

    @classmethod
    def from_json(cls, data: dict | str) -> "Status":
        """Parse status from JSON dict or string."""
        if isinstance(data, str):
            data = json.loads(data)
        return cls(
            step=data["step"],
            rogue_count=data["rogue_count"],
            quarantine_count=data["quarantine_count"],
            fault_active=data["fault_active"],
            gpu=data.get("gpu", "unknown"),
        )

    def __repr__(self) -> str:
        return (
            f"Status(step={self.step}, gpu={self.gpu}, "
            f"rogues={self.rogue_count}, fault={'ACTIVE' if self.fault_active else 'CLEAR'})"
        )
