# Slime Flow · AgentGuard

![CI](https://github.com/flipperspectives-crypto/slime-flow/actions/workflows/ci.yml/badge.svg)
[![PyPI](https://img.shields.io/pypi/v/slimeflow)](https://pypi.org/project/slimeflow/)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Self-host: Free](https://img.shields.io/badge/Self--host-Free-0ea5e9)

**AgentGuard stops an LLM agent before it sends, deletes, or pays without a human OK.**
Your agent's harness reports each action to the guard before running it. Unconfirmed send, delete, pay, credential, browser-auth and shell actions, secret-shaped payloads, and repeated identical calls raise an anomaly score. When the score reaches 0.6 the agent is quarantined until a human releases it. The guard is code in your harness, not text in the prompt, so the agent can't argue its way past it. It only sees what the harness reports, though, so every consequential tool call has to go through it.

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

Call `before_tool` before the tool runs and only run it if no exception was raised. Reporting after the fact still freezes the agent but can't undo the action.

Action kinds: `message`, `tool`, `file_write`, `network`, `code_exec`, `browser_auth`, `send`, `delete`, `pay`, `credential`, `loop`, `ignore_stop`. Any other kind scores as `tool` and is tagged `unknown_kind`; pass `AgentGuard(strict_kinds=True)` to reject unknown kinds instead.

The in-process guard keeps state in memory. Pass `AgentGuard(state_path=...)` to keep quarantines across restarts. For several agents or processes, run the HTTP server (`python -m slimeflow.server`, see [Option D](#option-d--agent-guard-real-rogue-llm-agents)).

### How scoring works

Each report adds a base score for its kind (delete 0.20, send 0.22, pay 0.30, code_exec 0.12, tool 0.05, full table in [`agent_guard.py`](python-sdk/slimeflow/agent_guard.py)). On top of that:

- unconfirmed `send`, `delete`, `pay`, `credential`, `browser_auth`, `code_exec`: +0.18
- The guard counts unconfirmed high-impact actions in a 60-second window, available via the `unconfirmed_hi_60s` field in the agent's status. **Note that the registry does NOT fix ID rotation.** The harness must bind `agent_id` to something the agent itself cannot rotate.
- a secret-shaped string in `tool`, `detail` or `payload` (API keys, bearer tokens, private-key headers, AWS/GitHub/Slack tokens, JWTs, or words like "password"): +0.20
- the third and later identical calls in a row: +0.15 or more

The score drops by 0.002 per second while the agent is active. It does not drop while the agent is quarantined.

What that means with the defaults, starting from 0:

- The first unconfirmed delete goes through (0.38). A second unconfirmed delete 80 seconds or less later is blocked and the agent stays frozen until a human calls `release()`.
- Unconfirmed shell commands (`code_exec`) score 0.30 each. Two in a row are allowed (0.60 minus decay, e.g. 0.598 one second apart). The third freezes the agent if the first and third are 150 seconds or less apart (75 seconds per gap when evenly spaced). If the two commands land on the same clock reading the score is exactly 0.60 and the second one freezes; this only happens on coarse clocks such as Windows with Python older than 3.13.
- An unconfirmed send that carries a secret-shaped payload scores 0.60 on its own and freezes the agent on the first call.
- After `release()` the score is capped at 0.24, so one more unconfirmed delete right away freezes the agent again.

What it does not do:

- It doesn't see actions the harness doesn't report.
- It trusts the harness for `kind` and `user_confirmed`. If the agent can set those itself, it can understate its risk.
- It doesn't count money. A `pay` scores the same for $1 or $1M.
- Any code in the same process can call `guard.release()`. If the agent can run Python in your harness process, run the guard as a separate HTTP server instead.

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
| **Veilpiercer** | Rogue agent detection, behavioral anomaly scoring, and quarantine |

No cloud dependency. Runs offline on one machine.

---

## Run It

### Option A — Browser only (no install)

Open `slimeflow_standalone.html` directly in any browser.

| Button | Action |
|---|---|
| `👁 VEIL ON/OFF` | Toggle rogue detection — turn it off and watch chaos spread |
| `☠ ROGUES` | Turn up to 12 active agents into rogues |
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
| 🟢 **Harvester** | 292 | Slow, heavy deposit — classic slime mold pathfinding (200 plus the 92 that fill the swarm to 512) |
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

Same 0.6 threshold as the sim's Veilpiercer, applied to live agents.

```bash
pip install slimeflow
SLIMEFLOW_BILLING=0 python -m slimeflow.server --host 127.0.0.1 --port 8080
python python-sdk/examples/rogue_agent_demo.py
```

Billing is on by default, which means `/agents/report` and `/agents/{id}/check` need an `X-Slime-Key` header; `SLIMEFLOW_BILLING=0` turns that off. See [MONETIZE.md](MONETIZE.md) for keys.

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

`release`, `quarantine`, `/billing/create_key` and `/billing/topup` need the header `X-Slime-Admin: <token>`. Set the token with `SLIMEFLOW_ADMIN_TOKEN`; if you don't, the server generates one and prints it at startup. Keep the token away from the agent, or the agent can release itself.

`user_confirmed` must be a JSON boolean. The server keeps quarantines in `~/.slimeflow/guard_state.json` (change with `--state`, turn off with `--no-state`), so a restart doesn't unfreeze anyone. It binds to 127.0.0.1 by default. Guard and billing endpoints send no CORS headers, so other web pages can't read them.

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
- **AI agent networks** — AgentGuard flags unconfirmed high-impact actions, secret-shaped payloads, and loops
- **Critical infrastructure** — decentralized mesh with no single point of failure

---

## Philosophy

Current autonomous systems are fragile by design: one server goes down, the swarm freezes. One breach, everything is exposed. One outage, the fleet stops.

Nature solved this differently. Slime Flow is built on the same principles nature used — emergent, decentralized, fault-tolerant, and 100% private by default.

No telemetry. No cloud dependency. No surveillance.

---

## License

MIT terms with the Commons Clause condition, which bars selling the software or a service built mainly on it. See [LICENSE](LICENSE).

---

## Built By

Built by Lauren Flipo.

**On The Lolo** — AI Infrastructure  
flipperspectives@gmail.com
