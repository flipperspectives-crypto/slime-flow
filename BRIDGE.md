# SLIME FLOW — LIVE GPU BRIDGE

Connects the Julia CUDA simulation directly to the browser visualizer.

## Requirements

Julia packages (run once):

```julia
using Pkg
Pkg.add(["CUDA", "HTTP", "JSON3"])
```

## Run

**Terminal 1 — start the GPU server:**
```
julia server.jl
```

You should see:
```
🧫 Slime Flow GPU Server
   CUDA functional: true
   Device: NVIDIA GeForce RTX 4060 Laptop GPU
   Grid: 128×128  |  Agents: 512
   Starting on http://localhost:8080
```

**Terminal 2 — open the browser:**

Just open `slimeflow_live.html` in Chrome or Firefox.  
The browser auto-connects to localhost:8080 and starts rendering live GPU frames.

## What's happening

```
RTX 4060 (Julia CUDA kernels)
  → pheromone grid computed each frame
  → agent positions updated on GPU
  → /frame endpoint serializes to JSON
  → browser fetches every 50ms
  → renders pheromone grid + agent overlay
```

## Endpoints

| Endpoint | Method | Action |
|---|---|---|
| `/frame` | GET | Advance 1 sim step, return full frame JSON |
| `/reset` | GET | Reset all agents and pheromone |
| `/rogues` | GET | Spawn rogue agents |
| `/fault` | POST | Inject fault zone `{"x": 0.5, "y": 0.5}` (normalized 0–1) |
| `/fault/clear` | GET | Clear fault zone |
| `/status` | GET | GPU info + sim stats |

## Frame JSON structure

```json
{
  "step": 1234,
  "w": 64, "h": 64,
  "grid": [0.0, 0.12, ...],        // 64×64 pheromone values, normalized 0-1
  "rogue_grid": [0.0, 0.05, ...],  // 64×64 rogue pheromone
  "agents": [
    {"x": 0.4, "y": 0.6, "t": 2, "a": 0.1, "q": 0}
  ],
  "rogue_count": 3,
  "quarantine_count": 1,
  "fault_active": false
}
```

Agent types: 1=Scout 2=Harvester 3=Guardian 4=Emergent 5=Rogue  
Quarantine codes: 0=active 1=quarantined by Veilpiercer 2=fault-killed
