"""Slime Flow server: pure Python swarm sim plus the AgentGuard HTTP API.

Built by Lauren Flipo.

Sim: 512 agents on a 128x128 grid, pheromone decay and sensing, rogue
detection, fault zones. Same rules as slimeflow_standalone.html.

Start: python -m slimeflow.server          (127.0.0.1:8080)
       python -m slimeflow.server --port 9090

Admin endpoints (release, manual quarantine, create_key, topup) need the
header ``X-Slime-Admin: <token>``. Set the token with SLIMEFLOW_ADMIN_TOKEN;
if it is unset, a random token is generated and printed at startup.
"""

from __future__ import annotations

import argparse
import hmac
import json
import math
import os
import random
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote

from slimeflow.agent_guard import _validate_agent_id, guard
from slimeflow.billing import billing

# ═══════════════════════════════════════════════════════════════
# Simulation Engine — matches slimeflow_standalone.html JS logic
# ═══════════════════════════════════════════════════════════════

W, H = 128, 128        # Internal grid
N_AGENTS = 512
GRID_OUT_W, GRID_OUT_H = 64, 64  # Downsampled output (matches SDK tests)

DECAY = 0.97
DEPOSIT = 2.5
SENSE_R = 3.0
TURN_SPD = 0.35

TYPE_COUNTS = [80, 200, 60, 80, 0]  # Scout, Harvester, Guardian, Emergent, Rogue
SPEEDS = {1: 2.2, 2: 0.9, 3: 1.4, 4: 1.7, 5: 2.0}
DEPS = {1: 1.0, 2: 3.0, 3: 1.5, 4: 2.0, 5: 0.0}

ANOMALY_RATE = 0.08
ANOMALY_DECAY = 0.002
QUARANTINE_THRESHOLD = 0.6
FAULT_RADIUS_SQ = 20 * 20  # Match JS: dx*dx + dy*dy < 400


def _normalize(grid: list) -> list:
    """Scale to 0-1 the way server.jl does: divide by max(peak, 1), clamp."""
    peak = max(max(grid, default=0.0), 1.0)
    return [min(v / peak, 1.0) for v in grid]


class Simulation:
    """Pure Python slime mold swarm simulation."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        """Initialize 512 agents with type distribution."""
        self.pheromone = [0.0] * (W * H)
        self.rogue_pheromone = [0.0] * (W * H)
        self.ax = [0.0] * N_AGENTS
        self.ay = [0.0] * N_AGENTS
        self.adir = [0.0] * N_AGENTS
        self.atype = [0] * N_AGENTS
        self.anomaly = [0.0] * N_AGENTS
        self.quarantined = [0] * N_AGENTS
        self.step = 0
        self.fault_active = False
        self.fault_x = 0.0
        self.fault_y = 0.0

        idx = 0
        for t, count in enumerate(TYPE_COUNTS):
            for _ in range(count):
                self.ax[idx] = random.random() * W
                self.ay[idx] = random.random() * H
                self.adir[idx] = random.random() * math.pi * 2
                self.atype[idx] = t + 1
                self.anomaly[idx] = 0.0
                self.quarantined[idx] = 0
                idx += 1
        # Fill remaining as Harvesters
        while idx < N_AGENTS:
            self.ax[idx] = random.random() * W
            self.ay[idx] = random.random() * H
            self.adir[idx] = random.random() * math.pi * 2
            self.atype[idx] = 2
            self.anomaly[idx] = 0.0
            self.quarantined[idx] = 0
            idx += 1

    def step_sim(self):
        """Advance simulation by one tick. Thread-safe."""
        with self.lock:
            self.step += 1

            # Decay pheromone grids
            for i in range(W * H):
                self.pheromone[i] *= DECAY
                self.rogue_pheromone[i] *= DECAY

            # Agent step
            for i in range(N_AGENTS):
                if self.quarantined[i] == 1:
                    continue  # Quarantined — frozen

                t = self.atype[i]
                x, y, d = self.ax[i], self.ay[i], self.adir[i]
                speed = SPEEDS.get(t, 1.5)
                dep = DEPS.get(t, 1.5)
                is_rogue = t == 5

                # Fault zone kill (non-guardians)
                if self.fault_active and t != 3:
                    dx = x - self.fault_x
                    dy = y - self.fault_y
                    if dx * dx + dy * dy < FAULT_RADIUS_SQ:
                        self.quarantined[i] = 2
                        continue

                # Sense pheromone (check -0.4, 0, +0.4 offsets)
                best_val = -1.0
                best_off = 0.0
                for off in (-0.4, 0.0, 0.4):
                    sd = d + off
                    sx = (math.cos(sd) * SENSE_R + x) % W
                    sy = (math.sin(sd) * SENSE_R + y) % H
                    ix, iy = int(sx), int(sy)
                    if 0 <= ix < W and 0 <= iy < H:
                        v = self.rogue_pheromone[iy * W + ix] if is_rogue else self.pheromone[iy * W + ix]
                        if v > best_val:
                            best_val = v
                            best_off = off

                # Rogue chaotic movement
                if is_rogue:
                    best_off = math.sin(self.step * 0.3 + i) * 0.9

                # Update direction
                d += best_off * (0.9 if is_rogue else TURN_SPD) + math.sin(self.step * 0.1 + i * 0.7) * 0.05
                x = (x + math.cos(d) * speed) % W
                y = (y + math.sin(d) * speed) % H

                # Deposit pheromone
                ix, iy = int(x), int(y)
                if 0 <= ix < W and 0 <= iy < H:
                    if is_rogue:
                        self.rogue_pheromone[iy * W + ix] += DEPOSIT * 2
                        self.anomaly[i] = min(self.anomaly[i] + ANOMALY_RATE, 1.0)
                    else:
                        self.pheromone[iy * W + ix] += dep
                        self.anomaly[i] = max(self.anomaly[i] - ANOMALY_DECAY, 0.0)

                # Veilpiercer quarantine
                if self.anomaly[i] > QUARANTINE_THRESHOLD and is_rogue:
                    self.quarantined[i] = 1

                self.ax[i] = x
                self.ay[i] = y
                self.adir[i] = d

    def _downsample_grid(self, grid: list[float], src_w: int, src_h: int,
                         out_w: int, out_h: int) -> list[float]:
        """Downsample grid via box averaging."""
        scale_x = src_w // out_w
        scale_y = src_h // out_h
        result = []
        for oy in range(out_h):
            for ox in range(out_w):
                total = 0.0
                count = 0
                for sy in range(oy * scale_y, (oy + 1) * scale_y):
                    for sx in range(ox * scale_x, (ox + 1) * scale_x):
                        total += grid[sy * src_w + sx]
                        count += 1
                result.append(total / count if count > 0 else 0.0)
        return result

    def get_frame(self) -> dict:
        """Get current frame data in SDK-compatible JSON format."""
        with self.lock:
            rogue_count = sum(1 for i in range(N_AGENTS) if self.atype[i] == 5)
            quarantine_count = sum(1 for i in range(N_AGENTS) if self.quarantined[i] == 1)

            agents = []
            for i in range(N_AGENTS):
                agents.append({
                    "x": self.ax[i] / W,  # Normalize to 0–1
                    "y": self.ay[i] / H,
                    "t": self.atype[i],
                    "a": round(self.anomaly[i], 4),
                    "q": self.quarantined[i],
                })

            grid_out = _normalize(
                self._downsample_grid(self.pheromone, W, H, GRID_OUT_W, GRID_OUT_H))
            rogue_grid_out = _normalize(
                self._downsample_grid(self.rogue_pheromone, W, H, GRID_OUT_W, GRID_OUT_H))

            return {
                "step": self.step,
                "w": GRID_OUT_W,
                "h": GRID_OUT_H,
                "grid": grid_out,
                "rogue_grid": rogue_grid_out,
                "agents": agents,
                "rogue_count": rogue_count,
                "quarantine_count": quarantine_count,
                "fault_active": self.fault_active,
            }

    def get_status(self) -> dict:
        """Get server status."""
        with self.lock:
            rogue_count = sum(1 for i in range(N_AGENTS) if self.atype[i] == 5)
            quarantine_count = sum(1 for i in range(N_AGENTS) if self.quarantined[i] == 1)
            return {
                "step": self.step,
                "rogue_count": rogue_count,
                "quarantine_count": quarantine_count,
                "fault_active": self.fault_active,
                "gpu": "Pure Python CPU",
            }

    def spawn_rogues(self):
        """Convert up to 12 normal agents to rogue type."""
        with self.lock:
            converted = 0
            for i in range(N_AGENTS):
                if converted >= 12:
                    break
                if self.atype[i] != 5 and self.quarantined[i] == 0:
                    self.atype[i] = 5
                    converted += 1

    def inject_fault(self, x: float, y: float):
        """Inject a fault zone at normalized coords (0–1)."""
        with self.lock:
            self.fault_x = x * W
            self.fault_y = y * H
            self.fault_active = True

    def clear_fault(self):
        """Clear active fault zone."""
        with self.lock:
            self.fault_active = False


# ═══════════════════════════════════════════════════════════════
# HTTP Server
# ═══════════════════════════════════════════════════════════════

sim = Simulation()

MAX_BODY_BYTES = 1 << 20  # 1 MiB
REQUEST_TIMEOUT_SEC = 15

# Endpoints the browser dashboards call cross-origin (slimeflow_live.html is
# opened from file://). Guard and billing endpoints get no CORS headers, so
# other web pages cannot read them.
_CORS_PATHS = {"/status", "/frame", "/reset", "/rogues", "/fault", "/fault/clear", "/ping"}

# Set by main() or configure(). None means every admin call is refused.
_admin_token: Optional[str] = None


def configure(*, admin_token: Optional[str]) -> None:
    """Set the token required in X-Slime-Admin for admin endpoints."""
    global _admin_token
    _admin_token = admin_token or None


class _ClientGone(Exception):
    """The client closed the connection before sending the whole body."""


class _BadRequest(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class SlimeHandler(BaseHTTPRequestHandler):
    """HTTP handler for the sim and the AgentGuard API."""

    timeout = REQUEST_TIMEOUT_SEC

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    # ─── helpers ──────────────────────────────────────────────────────────

    def _path(self) -> str:
        return self.path.split("?", 1)[0]

    def _slime_key(self) -> str:
        return self.headers.get("X-Slime-Key") or ""

    def _is_admin(self) -> bool:
        token = _admin_token
        given = self.headers.get("X-Slime-Admin") or ""
        if not token or not given:
            return False
        return hmac.compare_digest(given.encode(), token.encode())

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if self._path() in _CORS_PATHS:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Dict[str, Any], status: int = 200) -> None:
        self._send(status, json.dumps(data, allow_nan=False).encode("utf-8"), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _read_json(self) -> Dict[str, Any]:
        raw_len = self.headers.get("Content-Length")
        if raw_len is None or raw_len.strip() == "":
            return {}
        try:
            length = int(raw_len)
        except ValueError:
            raise _BadRequest(400, "invalid Content-Length")
        if length < 0:
            raise _BadRequest(400, "invalid Content-Length")
        if length > MAX_BODY_BYTES:
            raise _BadRequest(413, f"body larger than {MAX_BODY_BYTES} bytes")
        body = self.rfile.read(length) if length else b""
        if len(body) < length:
            raise _ClientGone()
        if not body.strip():
            return {}
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise _BadRequest(400, "invalid JSON")
        if not isinstance(data, dict):
            raise _BadRequest(400, "JSON body must be an object")
        return data

    def _agent_id(self, path: str, suffix: str) -> str:
        agent_id = unquote(path[len("/agents/"):-len(suffix)].strip("/"))
        if not agent_id:
            raise _BadRequest(400, "missing agent id")
        try:
            return _validate_agent_id(agent_id)
        except (TypeError, ValueError) as exc:
            raise _BadRequest(400, str(exc))

    def _require_admin(self) -> None:
        if not self._is_admin():
            raise _BadRequest(
                403,
                "admin token required: send header X-Slime-Admin "
                "(set SLIMEFLOW_ADMIN_TOKEN when starting the server)",
            )

    def _html(self) -> None:
        """Serve slimeflow_standalone.html from the repo root, if present."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.dirname(os.path.dirname(script_dir))
        html_file = os.path.join(repo_root, "slimeflow_standalone.html")
        try:
            with open(html_file, "rb") as f:
                content = f.read()
        except FileNotFoundError:
            return self._error(404, "dashboard not found (run from a git checkout)")
        self._send(200, content, "text/html; charset=utf-8")

    def _dispatch(self, fn) -> None:
        try:
            fn()
        except _ClientGone:
            self.close_connection = True
        except _BadRequest as exc:
            self._safe_error(exc.status, exc.message)
        except OSError:  # timeout or reset while talking to the client
            self.close_connection = True
        except Exception:  # keep the server up; never echo internals
            self._safe_error(500, "internal error")

    def _safe_error(self, status: int, message: str) -> None:
        try:
            self._error(status, message)
        except OSError:
            self.close_connection = True

    # ─── routes ───────────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        if self._path() in _CORS_PATHS:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._dispatch(self._get)

    def do_POST(self):
        self._dispatch(self._post)

    def _get(self) -> None:
        path = self._path()

        if path in ("/", "/index.html"):
            return self._html()
        if path == "/status":
            return self._json(sim.get_status())
        if path == "/frame":
            sim.step_sim()
            return self._json(sim.get_frame())
        if path == "/reset":
            sim.reset()
            return self._json({"status": "reset"})
        if path == "/rogues":
            sim.spawn_rogues()
            return self._json({"status": "rogues_spawned"})
        if path == "/fault/clear":
            sim.clear_fault()
            return self._json({"status": "fault_cleared"})
        if path == "/ping":
            return self._json({"status": "ok"})

        if path in ("/billing/pricing", "/pricing"):
            return self._json(billing.pricing())
        if path == "/billing/treasury":
            return self._json({"treasury_usd": billing.pricing()["treasury_usd"]})
        if path == "/billing/balance":
            result = billing.balance(self._slime_key())
            return self._json(result, 200 if result.get("ok") else 401)

        if path in ("/agents", "/agents/status"):
            return self._json(guard.status())
        if path.startswith("/agents/") and path.endswith("/check"):
            agent_id = self._agent_id(path, "/check")
            charge = billing.charge(self._slime_key(), kind="check")
            if not charge.get("allowed"):
                return self._json(charge, 402)
            out = guard.check(agent_id)
            out["billing"] = charge
            return self._json(out)

        return self._error(404, "not found")

    def _post(self) -> None:
        path = self._path()
        data = self._read_json()

        if path == "/fault":
            try:
                x = float(data.get("x", 0.5))
                y = float(data.get("y", 0.5))
            except (ValueError, TypeError):
                raise _BadRequest(400, "x and y must be numbers")
            if not (math.isfinite(x) and math.isfinite(y)):
                raise _BadRequest(400, "x and y must be finite")
            sim.inject_fault(x, y)
            return self._json({"status": "fault_injected", "x": x, "y": y})

        if path == "/billing/create_key":
            self._require_admin()
            result = billing.create_key(
                label=str(data.get("label", "")),
                fleet_id=str(data.get("fleet_id", "default")),
                initial_usd=data.get("initial_usd", 0) or 0,
            )
            return self._json(result, 200 if result.get("ok") else 400)

        if path == "/billing/topup":
            self._require_admin()
            key = self._slime_key() or str(data.get("secret", ""))
            amount = data.get("amount_usd", data.get("amount", 0))
            result = billing.topup(key, amount)
            return self._json(result, 200 if result.get("ok") else 400)

        if path == "/agents/report":
            agent_id = data.get("agent_id", "")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise _BadRequest(400, "agent_id required")
            kind = data.get("kind", "tool")
            if not isinstance(kind, str):
                raise _BadRequest(400, "kind must be a string")
            confirmed = data.get("user_confirmed", False)
            if not isinstance(confirmed, bool):
                raise _BadRequest(400, "user_confirmed must be a JSON boolean (true/false)")
            try:
                agent_id = _validate_agent_id(agent_id)
            except (TypeError, ValueError) as exc:
                raise _BadRequest(400, str(exc))
            charge = billing.charge(self._slime_key(), kind="report")
            if not charge.get("allowed"):
                return self._json(charge, 402)
            result = guard.report(
                agent_id,
                kind,
                tool=str(data.get("tool", "")),
                detail=str(data.get("detail", "")),
                user_confirmed=confirmed,
                payload=str(data.get("payload", "")),
            )
            # Mirror into the pheromone sim so the dashboard shows pressure.
            if result.get("quarantined"):
                sim.spawn_rogues()
            result["billing"] = charge
            return self._json(result)

        if path.startswith("/agents/") and path.endswith("/release"):
            agent_id = self._agent_id(path, "/release")
            self._require_admin()
            return self._json(guard.release(
                agent_id, by=str(data.get("by", "http-admin")), note=str(data.get("note", ""))))

        if path.startswith("/agents/") and path.endswith("/quarantine"):
            agent_id = self._agent_id(path, "/quarantine")
            self._require_admin()
            return self._json(guard.quarantine(
                agent_id, str(data.get("reason", "manual")), by=str(data.get("by", "http-admin"))))

        return self._error(404, "not found")


def make_server(host: str, port: int) -> HTTPServer:
    """Build the threaded HTTP server (one thread per connection)."""
    server = ThreadingHTTPServer((host, port), SlimeHandler)
    server.daemon_threads = True
    return server


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="Slime Flow sim + AgentGuard server")
    parser.add_argument("--port", type=int, default=8080, help="Server port (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument(
        "--state",
        default=os.environ.get(
            "SLIMEFLOW_GUARD_STATE", str(Path.home() / ".slimeflow" / "guard_state.json")),
        help="File that keeps quarantines across restarts "
             "(default: ~/.slimeflow/guard_state.json, env SLIMEFLOW_GUARD_STATE)",
    )
    parser.add_argument("--no-state", action="store_true",
                        help="Keep quarantines in memory only")
    args = parser.parse_args(argv)

    token = os.environ.get("SLIMEFLOW_ADMIN_TOKEN", "").strip()
    generated = not token
    if generated:
        token = secrets.token_urlsafe(24)
    configure(admin_token=token)

    restored = 0
    if not args.no_state:
        restored = guard.attach_state(args.state)

    server = make_server(args.host, args.port)
    base = f"http://{args.host}:{args.port}"
    print("SLIME FLOW server")
    print(f"  Sim: {W}x{H} grid (output {GRID_OUT_W}x{GRID_OUT_H}), {N_AGENTS} agents, pure Python")
    print(f"  Dashboard:   {base}/")
    print(f"  Agent guard: {base}/agents")
    print(f"  Billing:     {base}/billing/pricing (enabled={billing.enabled})")
    if args.no_state:
        print("  Guard state: memory only (quarantines are lost on restart)")
    else:
        print(f"  Guard state: {args.state} ({restored} quarantined agent(s) restored)")
    if generated:
        print(f"  Admin token (X-Slime-Admin): {token}")
        print("  Set SLIMEFLOW_ADMIN_TOKEN to choose your own.")
    else:
        print("  Admin token: from SLIMEFLOW_ADMIN_TOKEN")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("  Warning: listening beyond localhost. Agent reports carry no auth"
              " unless billing is on.")
    print("  Ctrl+C to stop", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
