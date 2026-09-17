#!/usr/bin/env python3
"""
Slime Flow — Pure Python simulation server.

Matches the standalone JS engine behavior: 512 agents, 128×128 grid,
pheromone decay/sensing, Veilpiercer rogue detection, fault zones.

Start: python -m slimeflow.server          (port 8080)
       python -m slimeflow.server --port 9090

Then:  from slimeflow import SlimeFlow
       sf = SlimeFlow()  # connects to localhost:8080
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

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

            grid_out = self._downsample_grid(self.pheromone, W, H, GRID_OUT_W, GRID_OUT_H)
            rogue_grid_out = self._downsample_grid(self.rogue_pheromone, W, H, GRID_OUT_W, GRID_OUT_H)

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
            rouge_count = sum(1 for i in range(N_AGENTS) if self.atype[i] == 5)
            quarantine_count = sum(1 for i in range(N_AGENTS) if self.quarantined[i] == 1)
        return {
            "step": self.step,
            "rogue_count": rouge_count,
            "quarantine_count": quarantine_count,
            "fault_active": self.fault_active,
            "gpu": "Pure Python CPU (phone-ready)",
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


class SlimeHandler(BaseHTTPRequestHandler):
    """HTTP handler for Slime Flow API."""

    # Silence request logs
    def log_message(self, format, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html_path: str):
        """Serve the standalone HTML file."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up from slimeflow/ to repo root
        repo_root = os.path.dirname(os.path.dirname(script_dir))
        html_file = os.path.join(repo_root, "slimeflow_standalone.html")

        try:
            with open(html_file, "rb") as f:
                content = f.read()
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            return self._html(path)

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

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'{"error": "not found"}')

    def do_POST(self):
        path = self.path.split("?")[0]

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        if path == "/fault":
            try:
                data = json.loads(body)
                x = float(data.get("x", 0.5))
                y = float(data.get("y", 0.5))
            except (json.JSONDecodeError, ValueError, TypeError):
                return self._json({"error": "invalid JSON"}, 400)
            sim.inject_fault(x, y)
            return self._json({"status": "fault_injected", "x": x, "y": y})

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'{"error": "not found"}')


def main():
    parser = argparse.ArgumentParser(description="Slime Flow simulation server")
    parser.add_argument("--port", type=int, default=8080, help="Server port (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), SlimeHandler)
    print(f"SLIME FLOW — Pure Python Server")
    print(f"  Grid: {W}×{W} (output {GRID_OUT_W}×{GRID_OUT_H})")
    print(f"  Agents: {N_AGENTS}")
    print(f"  GPU: Pure Python CPU (phone-ready)")
    print(f"  Listening: http://{args.host}:{args.port}")
    print(f"  Dashboard: http://{args.host}:{args.port}/")
    print(f"  Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
