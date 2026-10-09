"""AgentGuard: score the actions an agent's harness reports, and freeze the
agent when its score reaches a threshold (0.6 by default).

Built by Lauren Flipo.

What the guard does and does not do:

* It only sees actions the harness passes to ``report()``. Anything the agent
  does without a ``report()`` call is invisible to it.
* It trusts the harness for ``kind`` and ``user_confirmed``. If the agent can
  set those values itself, it can understate its own risk.
* It does not count money. A ``pay`` action scores the same for $1 or $1M.
* State lives in memory unless you pass ``state_path`` (or call
  ``attach_state``). Without it, restarting the process clears quarantines.

Scoring, with the default values below and a score that starts at 0:

* Each report adds ``RISK[kind]``. Kinds not in ``RISK`` score as ``tool``.
* High-impact kinds (``HIGH_IMPACT``) add ``UNCONFIRMED_BUMP`` (0.18) when
  ``user_confirmed`` is False. An unconfirmed delete is 0.20 + 0.18 = 0.38;
  an unconfirmed ``code_exec`` is 0.12 + 0.18 = 0.30.
* A secret-shaped string in ``tool``, ``detail`` or ``payload`` adds 0.20.
* The third and later identical reports in a row (same kind, tool and first
  120 characters of detail) add a loop penalty.
* The score decays by 0.002 per second of monotonic time while the agent is
  not quarantined.
* When the score reaches the threshold, the agent is quarantined. Every
  later ``check()`` and ``report()`` returns ``allowed: False`` until someone
  calls ``release()``. Decay does not run while quarantined.

What that means in practice:

* The first unconfirmed delete goes through (0.38). A second unconfirmed
  delete 80 s or less later is blocked (0.76 - 0.002 * gap >= 0.6) and the
  agent stays frozen until ``release()``.
* Two unconfirmed shell commands (``code_exec``, 0.30 each) are allowed:
  the second brings the score to 0.60 minus decay, just under the line. A
  third freezes the agent if the time from the first to the third is 150 s
  or less (0.90 - 0.002 * span >= 0.6), which is 75 s per gap when the gaps
  are even. Repeating the identical command adds the loop penalty on the
  third call, so identical repeats freeze sooner.
* Note that the registry does NOT fix ID rotation. The harness must bind agent_id to something the agent itself cannot rotate.
* Edge case: the two-command allowance relies on the clock moving between
  the calls. If both land on the same clock reading the score is exactly
  0.60 and the second one freezes the agent. ``time.monotonic`` has
  sub-microsecond resolution on Linux and macOS and on Windows from Python
  3.13; older Python on Windows ticks about every 15.6 ms.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from collections import OrderedDict

QUARANTINE_THRESHOLD = 0.6
ANOMALY_DECAY_PER_SEC = 0.002
UNCONFIRMED_BUMP = 0.18
SECRET_BUMP = 0.20
LOOP_REPEAT_START = 3
MAX_AGENT_ID_LEN = 200

# Scores are compared after rounding to this many decimal places, so float
# error never decides whether an agent is frozen. Example: three unconfirmed
# shell commands 75 s apart compute to 0.5999999999999999 in floating point;
# the exact value is 0.60, which freezes.
_SCORE_DECIMALS = 12

# Action kind -> base score added per report.
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

HIGH_IMPACT = {"send", "delete", "pay", "credential", "browser_auth", "code_exec"}

# Detection: any match adds SECRET_BUMP. Includes plain keywords such as
# "password", so it will also fire on text like "reset password page".
SECRETISH = re.compile(
    r"(?i)("
    r"api[_-]?key|secret|password|private[_-]?key"
    r"|bearer\s+[a-z0-9._~+/\-]+=*"
    r"|\bsk-[a-z0-9][a-z0-9_\-]{15,}"
    r"|-----BEGIN (?:[A-Z]+ ){0,3}PRIVATE KEY-----"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[a-z0-9]{36,}"
    r"|\bgithub_pat_[a-z0-9_]{22,}"
    r"|\bxox[abposr]-[a-z0-9\-]{10,}"
    r"|\beyJ[a-z0-9_\-]{8,}\.eyJ[a-z0-9_\-]{8,}\.[a-z0-9_\-]{8,}"
    r")"
)

# Redaction for text the guard stores and serves back (history, events,
# GET /agents). Removes token-shaped values and the value after
# "password=", "api_key: " and similar. Keywords alone are left in place.
_REDACT_TOKENS = re.compile(
    r"(?i)("
    r"(?<=bearer )[a-z0-9._~+/\-]+=*"
    r"|\bsk-[a-z0-9][a-z0-9_\-]{15,}"
    r"|-----BEGIN (?:[A-Z]+ ){0,3}PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z]+ ){0,3}PRIVATE KEY-----|$)"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[a-z0-9]{36,}"
    r"|\bgithub_pat_[a-z0-9_]{22,}"
    r"|\bxox[abposr]-[a-z0-9\-]{10,}"
    r"|\beyJ[a-z0-9_\-]{8,}\.eyJ[a-z0-9_\-]{8,}\.[a-z0-9_\-]{8,}"
    r")"
)
_REDACT_KV = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|token|private[_-]?key)"
    r"(\s*[:=]\s*['\"]?)([^\s'\",;&]{3,})"
)
REDACTED = "[redacted]"


def redact(text: str) -> str:
    """Remove secret-shaped values from ``text`` before it is stored."""
    text = _REDACT_TOKENS.sub(REDACTED, text)
    return _REDACT_KV.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)


def _validate_agent_id(agent_id: Any) -> str:
    if not isinstance(agent_id, str):
        raise TypeError("agent_id must be a str")
    agent_id = agent_id.strip()
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    if len(agent_id) > MAX_AGENT_ID_LEN:
        raise ValueError(f"agent_id longer than {MAX_AGENT_ID_LEN} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in agent_id):
        raise ValueError("agent_id contains control characters")
    return agent_id


@dataclass
class AgentRecord:
    agent_id: str
    anomaly: float = 0.0
    quarantined: bool = False
    quarantine_reason: str = ""
    last_seen: float = field(default_factory=time.time)  # wall clock, display only
    last_tick: float = 0.0                               # guard clock, decay math
    last_signature: str = ""
    unconfirmed_hi_timestamps: List[float] = field(default_factory=list)
    repeat_count: int = 0
    actions: int = 0
    blocked: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self, now: float) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "anomaly": round(self.anomaly, 4),
            "quarantined": self.quarantined,
            "quarantine_reason": self.quarantine_reason,
            "actions": self.actions,
            "blocked": self.blocked,
            "unconfirmed_hi_60s": len([t for t in self.unconfirmed_hi_timestamps if now - t <= 60.0]),
            "repeat_count": self.repeat_count,
            "last_seen": self.last_seen,
            "recent": self.history[-8:],
        }


class AgentGuard:
    """Thread-safe in-process guard. One instance can track many agents.

    Args:
        threshold: freeze an agent when its score reaches this value.
            Must be greater than 0 and less than 1.
        clock: monotonic time source used for decay. Defaults to
            ``time.monotonic`` so wall-clock changes (NTP, sleep/resume,
            manual clock edits) do not change scores.
        strict_kinds: raise ``ValueError`` for kinds not in ``RISK`` instead
            of scoring them as ``tool``.
        state_path: JSON file used to keep quarantines across restarts.
    """

    def __init__(
        self,
        threshold: float = QUARANTINE_THRESHOLD,
        *,
        clock: Callable[[], float] = time.monotonic,
        strict_kinds: bool = False,
        state_path: Optional[Union[str, Path]] = None,
    ):
        threshold = float(threshold)
        if not math.isfinite(threshold) or not 0.0 < threshold < 1.0:
            raise ValueError("threshold must be greater than 0 and less than 1")
        self.threshold = threshold
        self.strict_kinds = strict_kinds
        self._clock = clock
        self._lock = threading.Lock()
        self._agents: OrderedDict[str, AgentRecord] = OrderedDict()
        self._events: List[Dict[str, Any]] = []
        self._state_path: Optional[Path] = None
        if state_path is not None:
            self.attach_state(state_path)

    # ─── persistence ──────────────────────────────────────────────────────

    def attach_state(self, path: Union[str, Path]) -> int:
        """Load quarantines from ``path`` and save every later change there.

        Returns the number of quarantined agents loaded. Raises ``ValueError``
        if the file exists but cannot be read, so a damaged file never
        silently unfreezes agents.
        """
        path = Path(path)
        loaded = 0
        with self._lock:
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    rows = data["quarantined"]
                    if not isinstance(rows, dict):
                        raise TypeError("'quarantined' must be an object")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise ValueError(f"cannot read guard state file {path}: {exc}") from exc
                now = self._clock()
                wall = time.time()
                for agent_id, row in rows.items():
                    rec = self._get(_validate_agent_id(agent_id), now, wall)
                    rec.quarantined = True
                    rec.quarantine_reason = str(row.get("reason", "restored"))
                    anomaly = float(row.get("anomaly", self.threshold))
                    rec.anomaly = anomaly if math.isfinite(anomaly) else self.threshold
                    rec.last_tick = now
                    rec.last_seen = wall
                    loaded += 1
            self._state_path = path
            self._save_locked()
        return loaded

    def _save_locked(self) -> None:
        if self._state_path is None:
            return
        payload = {
            "version": 1,
            "quarantined": {
                r.agent_id: {
                    "reason": r.quarantine_reason,
                    "anomaly": round(r.anomaly, 6),
                    "last_seen": r.last_seen,
                }
                for r in self._agents.values()
                if r.quarantined
            },
        }
        path = self._state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".guard-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ─── internals ────────────────────────────────────────────────────────

    def _evict_agents(self, wall: float) -> None:
        """Evict agents idle for >24h, and cap total size to 10000 using LRU order."""
        if len(self._agents) <= 10000 and wall - getattr(self, '_last_evict_time', 0.0) < 300.0:
            return
        self._last_evict_time = wall

        # Evict >24h idle
        expired = []
        for aid, rec in self._agents.items():
            if wall - rec.last_seen > 86400.0:
                if not rec.quarantined:
                    expired.append(aid)
            else:
                break
        for aid in expired:
            del self._agents[aid]

    def _get(self, agent_id: str, now: float, wall: float) -> AgentRecord:
        self._evict_agents(wall)
        rec = self._agents.get(agent_id)
        if rec is None:
            rec = AgentRecord(agent_id=agent_id, last_tick=now)
            self._agents[agent_id] = rec
        self._agents.move_to_end(agent_id)
        # Cap at 10000
        if len(self._agents) > 10000:
            to_remove = []
            for aid, r in self._agents.items():
                if len(self._agents) - len(to_remove) <= 10000:
                    break
                if not r.quarantined:
                    to_remove.append(aid)
            for aid in to_remove:
                del self._agents[aid]
        return rec

    def _decay(self, rec: AgentRecord, now: float) -> None:
        if rec.quarantined:
            return
        dt = max(0.0, now - rec.last_tick)
        rec.anomaly = max(0.0, rec.anomaly - ANOMALY_DECAY_PER_SEC * dt)
        rec.last_tick = now

    def _crossed(self, anomaly: float) -> bool:
        return round(anomaly, _SCORE_DECIMALS) >= self.threshold

    def _log(self, rec: AgentRecord, event: Dict[str, Any]) -> None:
        rec.history.append(event)
        if len(rec.history) > 40:
            del rec.history[:-40]
        self._events.append({"agent_id": rec.agent_id, **event})
        if len(self._events) > 200:
            del self._events[:-200]

    # ─── public API ───────────────────────────────────────────────────────

    def check(self, agent_id: str) -> Dict[str, Any]:
        """Gate only, no scoring. ``allowed`` is False while quarantined.

        Unknown agents are reported as allowed with a score of 0 and are not
        added to the fleet list.
        """
        agent_id = _validate_agent_id(agent_id)
        with self._lock:
            now = self._clock()
            wall = time.time()
            rec = self._agents.get(agent_id)
            if rec is not None:
                self._agents.move_to_end(agent_id)
                rec.last_seen = wall
            if rec is None:
                return {
                    "allowed": True,
                    "quarantined": False,
                    "anomaly": 0.0,
                    "reason": "",
                    "threshold": self.threshold,
                }
            self._decay(rec, now)
            rec.last_seen = wall
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
        """Score an action and return the gate state after scoring.

        Call this before running the action, and run it only if the result
        has ``allowed: True``. Calling it after the action has already run
        still freezes the agent, but cannot undo that action.
        """
        agent_id = _validate_agent_id(agent_id)
        if kind is not None and not isinstance(kind, str):
            raise TypeError("kind must be a str")
        if not isinstance(user_confirmed, bool):
            raise TypeError("user_confirmed must be a bool")
        kind = (kind or "tool").strip().lower() or "tool"
        known = kind in RISK
        if not known and self.strict_kinds:
            raise ValueError(f"unknown action kind {kind!r}; expected one of {sorted(RISK)}")
        tool, detail, payload = str(tool), str(detail), str(payload)

        # Regex work happens before taking the lock so a large payload does
        # not stall other agents.
        secret_hit = SECRETISH.search(f"{detail}\n{payload}\n{tool}") is not None
        stored_tool = redact(tool)[:200]
        stored_detail = redact(detail)[:200]

        with self._lock:
            now = self._clock()
            wall = time.time()
            rec = self._get(agent_id, now, wall)
            self._decay(rec, now)
            rec.last_seen = wall

            if rec.quarantined:
                rec.blocked += 1
                self._log(rec, {
                    "t": wall,
                    "kind": kind,
                    "tool": stored_tool,
                    "detail": stored_detail,
                    "bump": 0.0,
                    "anomaly": round(rec.anomaly, 4),
                    "reasons": ["blocked_quarantined"],
                    "user_confirmed": user_confirmed,
                })
                return self._snapshot(rec, allowed=False, bumped=0.0, now=now)

            bump = RISK.get(kind, RISK["tool"])
            reasons: List[str] = []
            if not known:
                reasons.append("unknown_kind")

            if kind in HIGH_IMPACT and not user_confirmed:
                bump += UNCONFIRMED_BUMP
                reasons.append(f"unconfirmed_{kind}")

                rec.unconfirmed_hi_timestamps.append(now)

            cutoff = now - 60.0
            rec.unconfirmed_hi_timestamps = [t for t in rec.unconfirmed_hi_timestamps if t >= cutoff]

            if secret_hit:
                bump += SECRET_BUMP
                reasons.append("secret_pattern")

            signature = f"{kind}:{tool}:{detail[:120]}"
            if signature == rec.last_signature:
                rec.repeat_count += 1
                if rec.repeat_count >= LOOP_REPEAT_START:
                    bump += RISK["loop"] * min(rec.repeat_count / LOOP_REPEAT_START, 3)
                    reasons.append(f"loop_x{rec.repeat_count}")
            else:
                rec.last_signature = signature
                rec.repeat_count = 1

            if kind == "ignore_stop":
                reasons.append("ignored_stop")

            rec.anomaly = min(1.0, rec.anomaly + bump)
            rec.actions += 1
            self._log(rec, {
                "t": wall,
                "kind": kind,
                "tool": stored_tool,
                "detail": stored_detail,
                "bump": round(bump, 4),
                "anomaly": round(rec.anomaly, 4),
                "reasons": reasons,
                "user_confirmed": user_confirmed,
            })

            if self._crossed(rec.anomaly):
                rec.quarantined = True
                rec.quarantine_reason = (
                    "; ".join(reasons) if reasons else f"{kind} crossed threshold"
                )
                self._save_locked()
                return self._snapshot(rec, allowed=False, bumped=bump, now=now)

            return self._snapshot(rec, allowed=True, bumped=bump, now=now)

    def release(self, agent_id: str, *, by: str = "", note: str = "") -> Dict[str, Any]:
        """Unfreeze an agent. Only a human should call this.

        The score is capped at 40% of the threshold (0.24 by default), and
        decay restarts from now, so time spent frozen is not credited.
        ``by`` and ``note`` are written to the agent's history for audit.
        The result has ``released: True`` only if the agent was quarantined.
        """
        agent_id = _validate_agent_id(agent_id)
        with self._lock:
            now = self._clock()
            wall = time.time()
            rec = self._agents.get(agent_id)
            if rec is not None:
                self._agents.move_to_end(agent_id)
                rec.last_seen = wall
            if rec is None:
                return {
                    "allowed": True,
                    "quarantined": False,
                    "released": False,
                    "anomaly": 0.0,
                    "bump": 0.0,
                    "reason": "",
                    "threshold": self.threshold,
                    "agent": None,
                }
            was_quarantined = rec.quarantined
            rec.quarantined = False
            rec.quarantine_reason = ""
            rec.anomaly = min(rec.anomaly, self.threshold * 0.4)
            rec.repeat_count = 0
            rec.last_signature = ""
            rec.last_tick = now
            rec.last_seen = wall
            self._log(rec, {
                "t": wall,
                "kind": "release",
                "tool": "",
                "detail": redact(str(note))[:200],
                "bump": 0.0,
                "anomaly": round(rec.anomaly, 4),
                "reasons": [f"released_by:{str(by)[:80]}" if by else "released"],
                "user_confirmed": True,
            })
            if was_quarantined:
                self._save_locked()
            out = self._snapshot(rec, allowed=True, bumped=0.0, now=now)
            out["released"] = was_quarantined
            return out

    def quarantine(self, agent_id: str, reason: str = "manual", *, by: str = "") -> Dict[str, Any]:
        """Freeze an agent by hand."""
        agent_id = _validate_agent_id(agent_id)
        reason = str(reason)[:200]
        with self._lock:
            now = self._clock()
            wall = time.time()
            rec = self._get(agent_id, now, wall)
            self._decay(rec, now)
            rec.quarantined = True
            rec.anomaly = max(rec.anomaly, self.threshold)
            rec.quarantine_reason = reason
            rec.last_seen = wall
            self._log(rec, {
                "t": wall,
                "kind": "quarantine",
                "tool": "",
                "detail": redact(reason),
                "bump": 0.0,
                "anomaly": round(rec.anomaly, 4),
                "reasons": [f"manual_by:{str(by)[:80]}" if by else "manual"],
                "user_confirmed": True,
            })
            self._save_locked()
            return self._snapshot(rec, allowed=False, bumped=0.0, now=now)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            now = self._clock()
            agents = []
            quarantined = 0
            for rec in self._agents.values():
                self._decay(rec, now)
                agents.append(rec.to_dict(now))
                if rec.quarantined:
                    quarantined += 1
            return {
                "agents": agents,
                "total": len(agents),
                "quarantined": quarantined,
                "threshold": self.threshold,
                "events": self._events[-30:],
            }

    def _snapshot(self, rec: AgentRecord, allowed: bool, bumped: float, now: float) -> Dict[str, Any]:
        return {
            "allowed": allowed and not rec.quarantined,
            "quarantined": rec.quarantined,
            "anomaly": round(rec.anomaly, 4),
            "bump": round(bumped, 4),
            "reason": rec.quarantine_reason,
            "threshold": self.threshold,
            "agent": rec.to_dict(now),
        }


# Module singleton used by the HTTP server and the README quickstart.
guard = AgentGuard()
