# Slime Flow Python SDK

Python client for the [Slime Flow](https://github.com/flipperspectives-crypto/slime-flow) GPU swarm simulation server.

## Install

```bash
pip install -e python-sdk/        # from repo root
# or with async + numpy support:
pip install -e "python-sdk/[all]"
```

## Quick Start

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

## API

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

## Frame Object

```python
frame.step          # int — simulation step
frame.grid_w        # int — grid width (64)
frame.grid_h        # int — grid height (64)
frame.pheromone     # list[float] — pheromone grid (0–1)
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

## Agent Object

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

## Examples

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
- Running [Slime Flow server](https://github.com/flipperspectives-crypto/slime-flow) (`julia server.jl`)
