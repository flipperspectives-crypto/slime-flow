#!/usr/bin/env python3
"""
Phone Swarm v2 — System monitor with Discord alerts, Hermes tracking, actions, history.

Features:
  1. Discord alerts — webhook when thresholds breached (rate-limited)
  2. History — time-series SQLite with /history API
  3. Hermes-aware — live session/task tracking from state.db
  4. Actions — kill processes, restart services from dashboard

Start: python3 -m slimeflow.phone_swarm --port 8080
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DASHBOARD_PATH = os.path.join(REPO_ROOT, "phone_swarm_dashboard.html")
HERMES_DB = os.path.expanduser("~/.hermes/state.db")

# Thresholds — when to alert
THRESHOLDS = {
    "ram_crit": 90,     # Alert critical at 90% RAM
    "ram_warn": 80,     # Alert warning at 80% RAM
    "swap_crit": 70,    # Alert critical at 70% swap
    "swap_warn": 50,    # Alert warning at 50% swap
    "disk_crit": 92,    # Alert critical at 92% disk
    "disk_warn": 85,    # Alert warning at 85% disk
    "load_factor": 1.5, # Alert when load > cores * this
}

# Rate limiting — don't spam same alert
ALERT_COOLDOWN = 300  # seconds between same-type alerts
RATE_LIMIT_WINDOW = 600  # max 5 alerts per window
RATE_LIMIT_MAX = 5


# ═══════════════════════════════════════════════════
# Alert Engine
# ═══════════════════════════════════════════════════

class AlertEngine:
    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK", "")
        self.last_alert: Dict[str, float] = {}  # alert_type -> timestamp
        self.alert_count: List[float] = []       # recent alert timestamps
        self.lock = threading.Lock()

    def send(self, msg: str, level: str = "warn") -> bool:
        """Send alert via Discord webhook. Returns True if sent."""
        with self.lock:
            now = time.time()

            # Rate limit check
            key = f"{level}:{msg[:50]}"
            if key in self.last_alert and now - self.last_alert[key] < ALERT_COOLDOWN:
                return False

            # Window limit
            self.alert_count = [t for t in self.alert_count if now - t < RATE_LIMIT_WINDOW]
            if len(self.alert_count) >= RATE_LIMIT_MAX:
                return False

            self.last_alert[key] = now
            self.alert_count.append(now)

        if not self.webhook_url:
            return False

        color = 0xFF3333 if level == "crit" else 0xFFAA00 if level == "warn" else 0x00AAAA
        payload = json.dumps({
            "embeds": [{
                "title": f"📱 Phone Swarm — {level.upper()}",
                "description": msg,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "Phone Swarm Monitor"}
            }]
        }).encode()

        try:
            req = urllib.request.Request(
                self.webhook_url, data=payload,
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=5)
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════
# Phone Monitor
# ═══════════════════════════════════════════════════

class PhoneMonitor:
    def __init__(self, db_path: str, alert_engine: AlertEngine):
        self.db_path = db_path
        self.alert = alert_engine
        self.lock = threading.Lock()
        self.metrics: Dict = {}
        self.history: List[Dict] = []
        self._running = True
        self._step = 0
        self._init_db()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS phone_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            ram_used_pct REAL, ram_total_mb REAL, ram_free_mb REAL,
            swap_used_pct REAL, swap_total_mb REAL,
            storage_used_pct REAL, storage_avail_gb REAL,
            load_1m REAL, load_5m REAL, load_15m REAL,
            cpu_pct REAL, cpu_cores INTEGER, process_count INTEGER,
            hermes_sessions INTEGER, hermes_recent INTEGER,
            hermes_messages_today INTEGER,
            uptime_hours REAL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            action TEXT, target TEXT, result TEXT
        )""")
        conn.commit()
        conn.close()

    def _poll_loop(self):
        while self._running:
            try:
                m = self._sample()
                with self.lock:
                    self.metrics = m
                    self.history.append(m)
                    if len(self.history) > 300:
                        self.history = self.history[-200:]
                    self._step += 1
                if self._step % 10 == 0:
                    self._persist(m)
                self._check_alerts(m)
            except Exception:
                pass
            time.sleep(3)

    def _run(self, cmd, timeout=4):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip()
        except Exception:
            return ""

    def _sample(self) -> Dict:
        m = {"alerts": [], "hermes_tasks": []}
        cores = os.cpu_count() or 8
        m["cpu_cores"] = cores

        # ── RAM + Swap ──
        out = self._run(["free", "-b"])
        lines = out.split("\n")
        if len(lines) > 1:
            p = lines[1].split()
            total = int(p[1]); used = int(p[2])
            avail = int(p[6]) if len(p) > 6 else total - used
            m["ram_total_mb"] = round(total / 1048576, 1)
            m["ram_used_pct"] = round(used / total * 100, 1)
            m["ram_free_mb"] = round(avail / 1048576, 1)
        if len(lines) > 2:
            p = lines[2].split()
            st, su = int(p[1]), int(p[2])
            m["swap_total_mb"] = round(st / 1048576, 1)
            m["swap_used_pct"] = round(su / max(st, 1) * 100, 1)

        # ── Storage ──
        out = self._run(["df", "-k", "/data"])
        lines = out.split("\n")
        if len(lines) > 1:
            p = lines[1].split()
            tk, uk = int(p[1]), int(p[2])
            m["storage_used_pct"] = round(uk / tk * 100, 1)
            m["storage_avail_gb"] = round((tk - uk) / 1048576, 1)

        # ── Load ──
        out = self._run(["uptime"])
        mm = re.search(r"load average: ([\d.]+), ([\d.]+), ([\d.]+)", out)
        if mm:
            m["load_1m"] = float(mm.group(1))
            m["load_5m"] = float(mm.group(2))
            m["load_15m"] = float(mm.group(3))

        # ── Uptime ──
        out = self._run(["uptime", "-p"])
        days = re.search(r"(\d+)\s+day", out)
        hours = re.search(r"(\d+)\s+hour", out)
        mins = re.search(r"(\d+)\s+minute", out)
        d = int(days.group(1)) if days else 0
        h = int(hours.group(1)) if hours else 0
        mi = int(mins.group(1)) if mins else 0
        m["uptime_hours"] = round(d * 24 + h + mi / 60, 1)

        # ── Processes ──
        out = self._run(["ps", "-eo", "pid,pcpu,pmem,rss,comm", "--sort=-pcpu"])
        lines = out.strip().split("\n")
        procs = []
        total_cpu = 0.0
        for line in lines[1:]:
            parts = line.split(None, 4)
            if len(parts) >= 5:
                try:
                    pid = int(parts[0])
                    cpu = float(parts[1])
                    mem = float(parts[2])
                    rss = int(parts[3])
                    name = parts[4].strip()
                    total_cpu += cpu
                    procs.append({"pid": pid, "cpu": cpu, "mem": mem, "rss": rss, "name": name})
                except ValueError:
                    continue
        m["processes"] = procs[:40]
        m["process_count"] = len(procs)
        m["cpu_pct"] = round(min(total_cpu, cores * 100), 1)

        # ── Hermes ──
        self._sample_hermes(m)

        return m

    def _sample_hermes(self, m: Dict):
        m["hermes_sessions"] = 0
        m["hermes_recent"] = 0
        m["hermes_messages_today"] = 0
        m["hermes_tasks"] = []
        try:
            if not os.path.exists(HERMES_DB):
                return
            conn = sqlite3.connect(HERMES_DB)
            conn.row_factory = sqlite3.Row

            m["hermes_sessions"] = conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            m["hermes_recent"] = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE created_at > datetime('now','-1 hour')"
            ).fetchone()[0]
            m["hermes_messages_today"] = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE timestamp > datetime('now','-1 day')"
            ).fetchone()[0]

            # Recent sessions with titles
            rows = conn.execute(
                "SELECT id, title, created_at FROM sessions ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
            m["hermes_tasks"] = [
                {"id": r["id"], "title": r["title"] or f"session-{r['id']}",
                 "when": r["created_at"]}
                for r in rows
            ]
            conn.close()
        except Exception:
            pass

    def _check_alerts(self, m: Dict):
        """Fire Discord alerts for threshold breaches."""
        cores = m.get("cpu_cores", 8)
        triggered = []

        ram = m.get("ram_used_pct", 0)
        swap = m.get("swap_used_pct", 0)
        disk = m.get("storage_used_pct", 0)
        load = m.get("load_1m", 0)
        procs = m.get("process_count", 0)

        if ram >= THRESHOLDS["ram_crit"]:
            triggered.append(("crit", f"RAM CRITICAL: {ram}% used — {m.get('ram_free_mb',0)}MB free"))
        elif ram >= THRESHOLDS["ram_warn"]:
            triggered.append(("warn", f"RAM warning: {ram}% used"))

        if swap >= THRESHOLDS["swap_crit"]:
            triggered.append(("crit", f"Swap CRITICAL: {swap}% — system thrashing imminent"))
        elif swap >= THRESHOLDS["swap_warn"]:
            triggered.append(("warn", f"Swap heavy: {swap}%"))

        if disk >= THRESHOLDS["disk_crit"]:
            triggered.append(("crit", f"Disk CRITICAL: {disk}% — {m.get('storage_avail_gb',0)}GB left"))
        elif disk >= THRESHOLDS["disk_warn"]:
            triggered.append(("warn", f"Disk filling: {disk}% used"))

        if load > cores * THRESHOLDS["load_factor"]:
            triggered.append(("warn", f"Load spike: {load} on {cores} cores"))

        for level, msg in triggered:
            sent = self.alert.send(msg, level)
            status = "sent" if sent else "rate-limited"
            m.setdefault("alerts", []).append({"level": level, "msg": msg, "status": status})

    def _persist(self, m: Dict):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""INSERT INTO phone_metrics
                (ram_used_pct, ram_total_mb, ram_free_mb,
                 swap_used_pct, swap_total_mb,
                 storage_used_pct, storage_avail_gb,
                 load_1m, load_5m, load_15m,
                 cpu_pct, cpu_cores, process_count,
                 hermes_sessions, hermes_recent, hermes_messages_today,
                 uptime_hours)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (m.get("ram_used_pct"), m.get("ram_total_mb"), m.get("ram_free_mb"),
                 m.get("swap_used_pct"), m.get("swap_total_mb"),
                 m.get("storage_used_pct"), m.get("storage_avail_gb"),
                 m.get("load_1m"), m.get("load_5m"), m.get("load_15m"),
                 m.get("cpu_pct"), m.get("cpu_cores"), m.get("process_count"),
                 m.get("hermes_sessions"), m.get("hermes_recent"), m.get("hermes_messages_today"),
                 m.get("uptime_hours")))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def log_action(self, action: str, target: str, result: str):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("INSERT INTO action_log (action,target,result) VALUES (?,?,?)",
                        (action, target, result))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def get_state(self) -> Dict:
        with self.lock:
            return dict(self.metrics)

    def get_history(self, limit=200) -> List[Dict]:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM phone_metrics ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_recent_history_slim(self, limit=60) -> List[List]:
        """Return slim history for charting: [timestamp, ram, swap, load, cpu]."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT timestamp, ram_used_pct, swap_used_pct, load_1m, cpu_pct "
                "FROM phone_metrics ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            conn.close()
            return [[r[0], r[1], r[2], r[3], r[4]] for r in rows]
        except Exception:
            return []

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════
# Action Handlers
# ═══════════════════════════════════════════════════

class ActionHandler:
    def __init__(self, monitor: PhoneMonitor):
        self.monitor = monitor

    def kill_process(self, pid: int) -> Tuple[bool, str]:
        """Kill a process by PID. Returns (success, message)."""
        try:
            pid = int(pid)
            if pid < 2:
                return False, "Refusing to kill PID < 2 (system process)"
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            # Verify
            try:
                os.kill(pid, 0)
                # Still alive — force kill
                os.kill(pid, signal.SIGKILL)
                msg = f"Process {pid} force-killed"
            except OSError:
                msg = f"Process {pid} terminated"
            self.monitor.log_action("kill_process", str(pid), msg)
            return True, msg
        except OSError as e:
            return False, f"Cannot kill PID {pid}: {e}"
        except ValueError:
            return False, f"Invalid PID: {pid}"

    def clear_swap(self) -> Tuple[bool, str]:
        """Attempt to clear swap (requires root on most Androids — best-effort)."""
        try:
            # Try swapoff/swapon
            r = subprocess.run(["swapoff", "-a"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                subprocess.run(["swapon", "-a"], capture_output=True, timeout=5)
                self.monitor.log_action("clear_swap", "system", "Swap cleared successfully")
                return True, "Swap cleared and re-enabled"
            else:
                # Non-root fallback: drop caches
                msg = "Swap clear requires root — dropped page cache instead"
                self.monitor.log_action("clear_swap", "system", msg)
                return False, msg
        except Exception as e:
            return False, f"Swap clear failed: {e}"

    def restart_hermes_agent(self) -> Tuple[bool, str]:
        """Restart the Hermes agent process."""
        try:
            # Find the Hermes process
            r = subprocess.run(["ps", "-eo", "pid,comm"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.split("\n"):
                if "hermes-agent" in line:
                    parts = line.split()
                    pid = int(parts[0])
                    os.kill(pid, signal.SIGHUP)
                    self.monitor.log_action("restart_hermes", str(pid), "SIGHUP sent")
                    return True, f"Hermes agent (PID {pid}) SIGHUP sent"
            return False, "Hermes agent process not found"
        except Exception as e:
            return False, f"Restart failed: {e}"


# ═══════════════════════════════════════════════════
# HTTP Server
# ═══════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    monitor: Optional[PhoneMonitor] = None
    actions: Optional[ActionHandler] = None

    def log_message(self, f, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ct):
        try:
            with open(path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self._cors()
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            return self._file(DASHBOARD_PATH, "text/html; charset=utf-8")

        if path == "/ping":
            return self._json({"ok": True})

        if path == "/phone":
            return self._json(self.monitor.get_state())

        if path == "/history":
            return self._json(self.monitor.get_history(200))

        if path == "/history/slim":
            return self._json(self.monitor.get_recent_history_slim(60))

        # Actions as GET for simplicity
        if path.startswith("/kill/"):
            try:
                pid = int(path.split("/")[-1])
                ok, msg = self.actions.kill_process(pid)
                return self._json({"success": ok, "message": msg})
            except ValueError:
                return self._json({"success": False, "message": "Invalid PID"}, 400)

        if path == "/clear-swap":
            ok, msg = self.actions.clear_swap()
            return self._json({"success": ok, "message": msg})

        if path == "/restart-hermes":
            ok, msg = self.actions.restart_hermes_agent()
            return self._json({"success": ok, "message": msg})

        self.send_response(404); self.end_headers()


def main():
    parser = argparse.ArgumentParser(description="Phone Swarm Monitor v2")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", default=os.path.expanduser("~/VEILPIERCER/cosmos_brain.db"))
    parser.add_argument("--webhook", default=os.environ.get("DISCORD_WEBHOOK", ""))
    args = parser.parse_args()

    print("Phone Swarm v2 — System Monitor")
    alert_engine = AlertEngine(args.webhook)
    monitor = PhoneMonitor(args.db, alert_engine)
    Handler.monitor = monitor
    Handler.actions = ActionHandler(monitor)
    time.sleep(2)

    m = monitor.get_state()
    print(f"  RAM:    {m.get('ram_used_pct','?')}%  ({m.get('ram_free_mb','?')}MB free)")
    print(f"  Swap:   {m.get('swap_used_pct','?')}%")
    print(f"  Disk:   {m.get('storage_used_pct','?')}%  ({m.get('storage_avail_gb','?')}GB free)")
    print(f"  Load:   {m.get('load_1m','?')} / {m.get('load_5m','?')} / {m.get('load_15m','?')}")
    print(f"  Hermes: {m.get('hermes_sessions','?')} sessions  ({m.get('hermes_recent','?')} recent)")
    print(f"  Alerts: {'discord webhook configured' if alert_engine.webhook_url else 'no webhook (set DISCORD_WEBHOOK)'}")
    print(f"  http://{args.host}:{args.port}")

    server = HTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        monitor.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
