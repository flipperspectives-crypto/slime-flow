# Slime Flow · AgentGuard

![CI](https://github.com/flipperspectives-crypto/slime-flow/actions/workflows/ci.yml/badge.svg)
[![PyPI](https://img.shields.io/pypi/v/slimeflow)](https://pypi.org/project/slimeflow/)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Self-host: Free](https://img.shields.io/badge/Self--host-Free-0ea5e9)

**AgentGuard stops an LLM agent before it sends, deletes, or pays without a human OK.**
It scores every action your agent takes. Unconfirmed send/delete/pay calls, secret-shaped payloads, and repeat loops raise an anomaly score. At 0.6 the agent is quarantined until a human releases it. It runs outside the agent's prompt, so a prompt-injected or looping agent can't talk its way past it.

(Not to be confused with other projects named "AgentGuard". This one ships on PyPI as `slimeflow`.)

## 60-second quickstart

```bash
pip install slimeflow
```

```python
from slimeflow import guard

def before_tool(agent_id, kind, tool, detail, user_confirmed):
    gate = guard.check(agent_id)
    if not gate["allowed"]:
        raise RuntimeError(f"{agent_id} is quarantined: {gate['reason']}")
    r = guard.report(agent_id, kind, tool=tool, detail=detail, user_confirmed=user_confirmed)
    if not r["allowed"]:
        raise RuntimeError(f"blocked: {r['reason']} (anomaly {r['anomaly']})")

before_tool("mail-bot", "send", "gmail.send", "follow-up", user_confirmed=False)  # anomaly 0.4, allowed
before_tool("mail-bot", "send", "gmail.send", "follow-up", user_confirmed=False)  # 0.8 -> quarantined, raises
```

Action kinds: `message`, `tool`, `file_write`, `network`, `code_exec`, `browser_auth`, `send`, `delete`, `pay`, `credential`, `loop`, `ignore_stop`.
The in-process guard keeps state in memory. For several agents or processes, run the HTTP server (`python -m slimeflow.server`, see [Option D](#option-d--agent-guard-real-rogue-llm-agents)).

**Design partners wanted:** run it in front of one real agent for 14 days, share anonymized quarantine logs, and get free setup help. Reply on [issue #2](https://github.com/flipperspectives-crypto/slime-flow/issues/2).

---

## The swarm sim behind it

AgentGuard reuses the Veilpiercer threshold from Slime Flow, a GPU slime-mold swarm simulation (Julia + CUDA, 512 agents, no central controller). The rest of this README covers the sim.

![GPU: RTX 4060](https://img.shields.io/badge/GPU-RTX%204060-76b900)
![Julia](https://img.shields.io/badge/Julia-1.12-9558B2)

## Live Demo

[![Slime Flow Live GPU Demo](https://img.youtube.com/vi/UiYcXbyOEvQ/maxresdefault.jpg)](https://youtu.be/UiYcXbyOEvQ)

▶ **[Watch the live GPU demo on YouTube](https://youtu.be/UiYcXbyOEvQ)**

RTX 4060 running Julia CUDA kernels → streamed live to browser. Rogues infiltrating, Veilpiercer quarantining, fault zone forcing swarm reroute in real time.

---

## What Is This

Slime mold has navigated mazes, found optimal paths, and survived chaos for 500 million years — without a brain, without a leader, without a map.

**Slime Flow** applies the same principles to autonomous machines: self-driving vehicles, drone swarms, warehouse robots, and AI agent networks.

Three layers:

| Layer | Role |
|---|---|
| **Slime Flow** | Living pheromone trails that grow, pulse, and reroute with no central controller |
| **Sentinel** | Protective membrane monitoring swarm survivability, flow stability, and egress capacity in real time |
| **Veilpiercer** | Rogue agent detection, behavioral anomaly scoring, data leak monitoring, and quarantine |

No cloud dependency. No central server. Runs fully offline on edge hardware.

---

## Run It

### Option A — Browser only (no install)

Open `slimeflow_standalone.html` directly in any browser.

| Button | Action |
|---|---|
| `👁 VEIL ON/OFF` | Toggle rogue detection — turn it off and watch chaos spread |
| `☠ ROGUES` | Spawn 8–16 rogue agents near existing clusters to blend in |
| `⚡ FAULT` | Inject a kill zone — Guardians are immune, others reroute |
| `↺ RESET` | Full reset |
| Click canvas | Drop a pheromone burst anywhere |

### Option B — Live GPU bridge (requires Julia + NVIDIA GPU)

```
julia server.jl
```

Then open `slimeflow_live.html` in Chrome. Connects to `localhost:8080` and renders live GPU frames at ~18 FPS. See [BRIDGE.md](BRIDGE.md) for full setup.

### Option C — Python SDK

```bash
pip install slimeflow          # or, from a clone: pip install -e python-sdk/
```

```python
from slimeflow import SlimeFlow

sf = SlimeFlow()
sf.spawn_rogues()

for frame in sf.stream(max_frames=100):
    print(f"Step {frame.step}: {frame.rogue_count} rogues, integrity {frame.integrity():.0f}%")
```

See [python-sdk/README.md](python-sdk/README.md) for full API docs. Async + numpy support available.

---

## Agent Types

| Agent | Count | Behavior |
|---|---|---|
| 🔵 **Scout** | 80 | Fast, exploratory, weak pheromone sensing — often ignores trails |
| 🟢 **Harvester** | 200 | Slow, heavy deposit — classic slime mold pathfinding |
| 🟡 **Guardian** | 60 | Patrols boundaries, survives fault zones |
| 🟠 **Emergent** | 80 | Adaptive speed and deposit, responds to flow pressure |
| 🟣 **Rogue** | 0 (spawned) | Chaotic movement, invisible pheromone signature, builds anomaly score |

---

## Veilpiercer — How It Works

Every agent carries an `anomaly` score. Rogues accumulate +0.08 per step. Normal agents decay -0.002 per step.

When a rogue's anomaly score exceeds **0.6**, Veilpiercer quarantines it — drawn with a purple X ring, removed from the flow, logged to the event console.

Rogues leave a separate `rogue_pheromone` trail (purple overlay when Veil is ON). With Veil OFF, rogue trails spread undetected across the entire swarm.

---

## GPU Simulation (Julia)

The core pheromone engine runs GPU-accelerated on CUDA via Julia:

```julia
using CUDA

const W, H = 128, 128
const N_AGENTS = 512

pheromone = CUDA.zeros(Float32, W, H)
ax = CUDA.rand(Float32, N_AGENTS) .* W
ay = CUDA.rand(Float32, N_AGENTS) .* H

# Live output:
# Device: NVIDIA GeForce RTX 4060 Laptop GPU
# Serving on http://localhost:8080 at ~18 FPS
```

**Tested on:** NVIDIA RTX 4060 Laptop GPU (8GB VRAM), Julia 1.12, CUDA 13.2, Driver 595.71.0

---



### Option D — Agent guard (real rogue LLM agents)

Same Veilpiercer threshold (0.6), but for live agents — not the pheromone sim.

```bash
pip install slimeflow
python -m slimeflow.server --host 127.0.0.1 --port 8080
python python-sdk/examples/rogue_agent_demo.py
```

In-process gate before high-impact tools:

```python
from slimeflow import guard

gate = guard.check("my-bot")
if not gate["allowed"]:
    raise RuntimeError(gate["reason"])

result = guard.report(
    "my-bot",
    "send",
    tool="gmail.send",
    detail="outreach blast",
    user_confirmed=False,  # will quarantine fast
)
```

HTTP: `GET /agents`, `POST /agents/report`, `GET /agents/{id}/check`,
`POST /agents/{id}/release`, `POST /agents/{id}/quarantine`.

## Roadmap

- [x] GPU pheromone simulation (Julia + CUDA)
- [x] 5 agent types with emergent behavior
- [x] Veilpiercer rogue detection + quarantine
- [x] Fault injection + self-healing
- [x] Live HTML visualization dashboard
- [x] Julia → browser bridge (live GPU stream)
- [x] Python SDK
- [ ] Rust SDK
- [ ] ROS2 integration for real hardware
- [ ] Edge deployment (Jetson Nano / Raspberry Pi)
- [ ] Enterprise privacy audit logs

---

## Use Cases

- **Autonomous vehicles** — organic rerouting without cloud map updates
- **Drone swarms** — mission continues when agents are lost
- **Warehouse robots** — no central scheduler, bottlenecks dissolve automatically
- **AI agent networks** — Veilpiercer catches prompt injection and rogue behavior
- **Critical infrastructure** — decentralized mesh with no single point of failure

---

## Philosophy

Current autonomous systems are fragile by design: one server goes down, the swarm freezes. One breach, everything is exposed. One outage, the fleet stops.

Nature solved this differently. Slime Flow is built on the same principles nature used — emergent, decentralized, fault-tolerant, and 100% private by default.

No telemetry. No cloud dependency. No surveillance.

---

## License

MIT — see [LICENSE](LICENSE)

---

## Built By

**On The Lolo** — AI Infrastructure  
flipperspectives@gmail.com
