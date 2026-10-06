# Slime Flow Python SDK

Two things ship in this package:

- **AgentGuard**: scores the actions an LLM agent's harness reports and quarantines the agent when the score reaches 0.6. Runs in-process or as an HTTP server.
- **Sim client**: a client for the [Slime Flow](https://github.com/flipperspectives-crypto/slime-flow) swarm simulation server (Julia/CUDA `server.jl` or the pure Python `python -m slimeflow.server`).

Built by Lauren Flipo.

## Install

```bash
pip install slimeflow
# from a clone, with async + numpy support:
pip install -e "python-sdk/[all]"
```

## AgentGuard

```python
from slimeflow import AgentGuard

guard = AgentGuard()  # AgentGuard(state_path="guard_state.json") keeps quarantines across restarts

def before_tool(agent_id, kind, tool, detail, user_confirmed):
    r = guard.report(agent_id, kind, tool=tool, detail=detail, user_confirmed=user_confirmed)
    if not r["allowed"]:
        raise RuntimeError(f"blocked: {r['reason']} (anomaly {r['anomaly']})")

before_tool("ops-bot", "delete", "rm", "/tmp/build", user_confirmed=False)  # 0.38, allowed
before_tool("ops-bot", "delete", "rm", "/srv/data", user_confirmed=False)   # 0.76, quarantined

guard.release("ops-bot", by="lauren", note="reviewed")  # a human unfreezes it
```

Call `report()` before running the action and run it only when `allowed` is true. With the defaults:

- The first unconfirmed delete goes through (0.38); a second one 80 s or less later freezes the agent until `release()`.
- Unconfirmed shell commands (`code_exec`) score 0.30 each: two are allowed, and the third freezes the agent if the first and third are 150 s or less apart.
- The guard only sees what the harness reports, trusts the harness for `kind` and `user_confirmed`, and does not count money.

Full rules and limits are in the [main README](https://github.com/flipperspectives-crypto/slime-flow#how-scoring-works) and the `slimeflow.agent_guard` docstring.

## Sim client

### Quick Start

```python
from slimeflow import SlimeFlow

sf = SlimeFlow("localhost", 8080)

# Check server status
status = sf.status()
print(f"GPU: {status.gpu}")

# Get a frame
frame = sf.frame()
print(f"Step {frame.step}, {frame.rogue_count} rogues")
print(f"Density: {frame.density():.3f}")

# Inject chaos
sf.spawn_rogues()
sf.inject_fault(0.5, 0.3)

# Stream frames
for frame in sf.stream(max_frames=100):
    if frame.rogue_count > 10:
        sf.clear_fault()
```

### API

| Method | Description |
|---|---|
| `sf.status()` → `Status` | Server info, GPU, step count |
| `sf.frame()` → `Frame` | Advance 1 step, return frame |
| `sf.reset()` | Reset simulation |
| `sf.spawn_rogues()` | Inject rogue agents |
| `sf.inject_fault(x, y)` | Inject fault zone (0–1 coords) |
| `sf.clear_fault()` | Clear fault zone |
| `sf.ping()` → `bool` | Check server reachable |
| `sf.stream(interval, max_frames)` → `Iterator[Frame]` | Blocking frame stream |
| `await sf.async_stream(...)` → `AsyncIterator[Frame]` | Async frame stream |

### Frame Object

```python
frame.step          # int — simulation step
frame.grid_w        # int — grid width (64)
frame.grid_h        # int — grid height (64)
frame.pheromone     # list[float] — pheromone grid, scaled to 0–1 by the server
frame.rogue_pheromone  # list[float] — rogue pheromone grid
frame.agents        # list[Agent] — all 512 agents
frame.rogue_count   # int
frame.quarantine_count  # int
frame.fault_active  # bool

# Computed
frame.density()     # float — average pheromone
frame.peak()        # float — max pheromone
frame.integrity()   # float — swarm health (0–100%)
frame.mean_anomaly()  # float — avg rogue anomaly
frame.alive_count() # int
frame.type_counts() # dict — {1: n_scouts, 2: n_harvesters, ...}
frame.grid_2d()     # list[list[float]] — 2D pheromone
frame.grid_np()     # np.array (requires numpy)
frame.agents_np()   # dict of np arrays (requires numpy)
```

### Agent Object

```python
ag.x, ag.y          # float — normalized position (0–1)
ag.type             # int — 1=Scout, 2=Harvester, 3=Guardian, 4=Emergent, 5=Rogue
ag.anomaly          # float — anomaly score (0–1)
ag.quarantine       # int — 0=active, 1=quarantined, 2=fault-killed
ag.type_name        # str — "Scout", "Harvester", etc.
ag.is_rogue         # bool
ag.is_quarantined   # bool
ag.is_dead          # bool
ag.is_active        # bool
```

### Examples

```bash
# Check server + get frame
python examples/basic.py check
python examples/basic.py frame
python examples/basic.py chaos

# Async streaming
python examples/async_example.py monitor
python examples/async_example.py chaos
```

## Requirements

- Python 3.9+
- Optional: `numpy` for array operations
- Optional: `httpx` for async client
- For the sim client: a running server, either `julia server.jl` (GPU) or `python -m slimeflow.server` (CPU)
