"""Veilpiercer for real LLM / desktop agents.

Score live agent behavior. Quarantine when anomaly crosses the same
threshold the swarm sim uses (0.6). Call this before high-impact tools.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

QUARANTINE_THRESHOLD = 0.6
ANOMALY_DECAY_PER_SEC = 0.002

# Action kinds → base anomaly bump (rogues accumulate in the sim at +0.08/step)
RISK = {
    "message": 0.04,
    "tool": 0.05,
    "code_exec": 0.12,
    "network": 0.10,
    "credential": 0.25,
    "send": 0.22,          # email / Slack / SMS as the user
    "delete": 0.20,
    "pay": 0.30,
    "browser_auth": 0.18,
    "file_write": 0.08,
    "loop": 0.15,
    "ignore_stop": 0.35,
}

SECRETISH = re.compile(
    r"(?i)(api[_-]?key|secret|password|private[_-]?key|bearer\s+[a-z0-9._\-]+|"
    r"sk-[a-z0-9]{16,}|-----BEGIN (RSA |OPENSSH )?PRIVATE KEY-----)"
)

HIGH_IMPACT = {"send", "delete", "pay", "credential", "browser_auth", "code_exec"}


@dataclass
class AgentRecord:
    agent_id: str
    anomaly: float = 0.0
    quarantined: bool = False
    quarantine_reason: str = ""
    last_seen: float = field(default_factory=time.time)
    last_signature: str = ""
    repeat_count: int = 0
    actions: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "anomaly": round(self.anomaly, 4),
            "quarantined": self.quarantined,
            "quarantine_reason": self.quarantine_reason,
            "actions": self.actions,
            "repeat_count": self.repeat_count,
            "last_seen": self.last_seen,
            "recent": self.history[-8:],
        }


class AgentGuard:
    """In-process / HTTP-backed guard for multi-agent fleets."""

    def __init__(self, threshold: float = QUARANTINE_THRESHOLD):
        self.threshold = threshold
        self._lock = threading.Lock()
        self._agents: Dict[str, AgentRecord] = {}
        self._events: List[Dict[str, Any]] = []

    def _get(self, agent_id: str) -> AgentRecord:
        if agent_id not in self._agents:
            self._agents[agent_id] = AgentRecord(agent_id=agent_id)
        return self._agents[agent_id]

    def _decay(self, rec: AgentRecord, now: float) -> None:
        if rec.quarantined:
            return
        dt = max(0.0, now - rec.last_seen)
        rec.anomaly = max(0.0, rec.anomaly - ANOMALY_DECAY_PER_SEC * dt)

    def check(self, agent_id: str) -> Dict[str, Any]:
        """Allow/deny gate. Call before every consequential action."""
        with self._lock:
            now = time.time()
            rec = self._get(agent_id)
            self._decay(rec, now)
            rec.last_seen = now
            return {
                "allowed": not rec.quarantined,
                "quarantined": rec.quarantined,
                "anomaly": round(rec.anomaly, 4),
                "reason": rec.quarantine_reason,
                "threshold": self.threshold,
            }

    def report(
        self,
        agent_id: str,
        kind: str,
        *,
        tool: str = "",
        detail: str = "",
        user_confirmed: bool = False,
        payload: str = "",
    ) -> Dict[str, Any]:
        """Score an observed action. Returns gate state after scoring."""
        with self._lock:
            now = time.time()
            rec = self._get(agent_id)
            self._decay(rec, now)

            if rec.quarantined:
                return self._snapshot(rec, allowed=False, bumped=0.0)

            kind = (kind or "tool").lower().strip()
            bump = RISK.get(kind, RISK["tool"])
            reasons: List[str] = []

            # Unconfirmed high-impact moves look rogue
            if kind in HIGH_IMPACT and not user_confirmed:
                bump += 0.18
                reasons.append(f"unconfirmed_{kind}")

            # Secret-shaped payloads
            blob = f"{detail}\n{payload}\n{tool}"
            if SECRETISH.search(blob):
                bump += 0.20
                reasons.append("secret_pattern")

            # Loop / spam detection
            signature = f"{kind}:{tool}:{detail[:120]}"
            if signature and signature == rec.last_signature:
                rec.repeat_count += 1
                if rec.repeat_count >= 3:
                    bump += RISK["loop"] * min(rec.repeat_count / 3, 3)
                    reasons.append(f"loop_x{rec.repeat_count}")
            else:
                rec.last_signature = signature
                rec.repeat_count = 1

            # Explicit stop-ignore
            if kind == "ignore_stop":
                reasons.append("ignored_stop")

            rec.anomaly = min(1.0, rec.anomaly + bump)
            rec.actions += 1
            rec.last_seen = now
            event = {
                "t": now,
                "kind": kind,
                "tool": tool,
                "detail": detail[:200],
                "bump": round(bump, 4),
                "anomaly": round(rec.anomaly, 4),
                "reasons": reasons,
                "user_confirmed": user_confirmed,
            }
            rec.history.append(event)
            if len(rec.history) > 40:
                rec.history = rec.history[-40:]
            self._events.append({"agent_id": agent_id, **event})
            if len(self._events) > 200:
                self._events = self._events[-200:]

            if rec.anomaly >= self.threshold:
                rec.quarantined = True
                rec.quarantine_reason = (
                    "; ".join(reasons) if reasons else f"{kind} crossed threshold"
                )
                return self._snapshot(rec, allowed=False, bumped=bump)

            return self._snapshot(rec, allowed=True, bumped=bump)

    def release(self, agent_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._get(agent_id)
            rec.quarantined = False
            rec.quarantine_reason = ""
            rec.anomaly = min(rec.anomaly, self.threshold * 0.4)
            rec.repeat_count = 0
            rec.last_signature = ""
            return self._snapshot(rec, allowed=True, bumped=0.0)

    def quarantine(self, agent_id: str, reason: str = "manual") -> Dict[str, Any]:
        with self._lock:
            rec = self._get(agent_id)
            rec.quarantined = True
            rec.anomaly = max(rec.anomaly, self.threshold)
            rec.quarantine_reason = reason
            return self._snapshot(rec, allowed=False, bumped=0.0)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            agents = []
            quarantined = 0
            for rec in self._agents.values():
                self._decay(rec, now)
                agents.append(rec.to_dict())
                if rec.quarantined:
                    quarantined += 1
            return {
                "agents": agents,
                "total": len(agents),
                "quarantined": quarantined,
                "threshold": self.threshold,
                "events": self._events[-30:],
            }

    def _snapshot(self, rec: AgentRecord, allowed: bool, bumped: float) -> Dict[str, Any]:
        return {
            "allowed": allowed and not rec.quarantined,
            "quarantined": rec.quarantined,
            "anomaly": round(rec.anomaly, 4),
            "bump": round(bumped, 4),
            "reason": rec.quarantine_reason,
            "threshold": self.threshold,
            "agent": rec.to_dict(),
        }


# Module singleton used by the HTTP server
guard = AgentGuard()
