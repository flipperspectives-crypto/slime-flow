"""Basic Slime Flow SDK usage examples."""

import sys
import os

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from slimeflow import SlimeFlow


def check_server():
    """Check if the Slime Flow server is running."""
    sf = SlimeFlow()
    if sf.ping():
        status = sf.status()
        print(f"Connected! GPU: {status.gpu}")
        print(f"Step: {status.step}, Rogues: {status.rogue_count}")
        return True
    else:
        print("Cannot connect to Slime Flow server at localhost:8080")
        print("Start it with: julia server.jl")
        return False


def one_frame():
    """Fetch and display a single simulation frame."""
    sf = SlimeFlow()
    frame = sf.frame()

    print(frame)
    print(f"  Grid: {frame.grid_w}×{frame.grid_h}")
    print(f"  Density: {frame.density():.4f}")
    print(f"  Peak: {frame.peak():.4f}")
    print(f"  Integrity: {frame.integrity():.1f}%")
    print(f"  Mean anomaly: {frame.mean_anomaly():.3f}")

    # Agent type breakdown
    counts = frame.type_counts()
    for t, name in [(1, "Scouts"), (2, "Harvesters"), (3, "Guardians"),
                     (4, "Emergents"), (5, "Rogues")]:
        print(f"  {name}: {counts.get(t, 0)}")


def inject_and_watch():
    """Inject rogues and a fault, watch the swarm respond."""
    sf = SlimeFlow()

    print("Resetting simulation...")
    sf.reset()

    print("Spawning rogues...")
    sf.spawn_rogues()

    print("Injecting fault zone...")
    sf.inject_fault(0.5, 0.3)

    print("\nWatching 20 frames:\n")
    for i, frame in enumerate(sf.stream(max_frames=20), 1):
        print(
            f"  [{i:2d}] Step {frame.step:5d} | "
            f"Rogues: {frame.rogue_count:3d} | "
            f"Quarantined: {frame.quarantine_count:3d} | "
            f"Density: {frame.density():.3f} | "
            f"Integrity: {frame.integrity():.1f}%"
        )


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
    else:
        cmd = "check"

    if cmd == "check":
        check_server()
    elif cmd == "frame":
        one_frame()
    elif cmd == "chaos":
        inject_and_watch()
    else:
        print(f"Usage: python basic.py [check|frame|chaos]")
