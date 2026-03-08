# SLIME FLOW // SENTINEL // VEILPIERCER
🧫 I built a swarm brain for autonomous machines — powered by a GPU, inspired by slime mold.
No central controller. No cloud. No single point of failure.
It's called Slime Flow. Here's what it does:
→ 512 agents coordinate using pheromone trails — no instructions, no map, no leader
→ Fault zones appear — the swarm reroutes in milliseconds
→ Rogue agents infiltrate — Veilpiercer detects them, scores their anomaly, quarantines them live
→ All of it running on an RTX 4060, streamed to the browser in real time
The same logic slime mold has used for 500 million years. Applied to drones, robots, and AI agent networks.
Demo: https://youtu.be/UiYcXbyOEvQ
Repo: https://github.com/flipperspectives-crypto/slime-flow
Open to conversations with anyone building autonomous systems who needs a coordination layer that actually survives chaos.
#swarmAI #autonomoussystems #biomimetic #drones #robotics #AIinfrastructure #Julia #CUDA
> Biomimetic swarm intelligence for autonomous machines — no central controller, no cloud, no surveillance.

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Status: Active](https://img.shields.io/badge/Status-Active%20Build-brightgreen)
![GPU: RTX 4060](https://img.shields.io/badge/GPU-RTX%204060-76b900)
![Julia](https://img.shields.io/badge/Julia-1.12-9558B2)

---

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

Open `slimeflow_veilpiercer.html` directly in any browser.

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

## Roadmap

- [x] GPU pheromone simulation (Julia + CUDA)
- [x] 5 agent types with emergent behavior
- [x] Veilpiercer rogue detection + quarantine
- [x] Fault injection + self-healing
- [x] Live HTML visualization dashboard
- [x] Julia → browser bridge (live GPU stream)
- [ ] Python / Rust SDK
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
