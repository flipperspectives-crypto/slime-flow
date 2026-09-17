"""Async Slime Flow example — real-time swarm monitoring with httpx."""

import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from slimeflow import SlimeFlow


async def monitor():
    """Monitor the swarm in real time with async streaming."""
    sf = SlimeFlow()

    if not await sf.async_ping():
        print("Server not reachable. Start with: julia server.jl")
        return

    status = await sf.async_status()
    print(f"Connected! GPU: {status.gpu}")

    # Inject some chaos
    await sf.async_spawn_rogues()
    await sf.async_inject_fault(0.6, 0.4)

    print("\nStreaming frames (Ctrl+C to stop):\n")

    try:
        async for frame in sf.async_stream(max_frames=50):
            bar = "█" * int(frame.integrity() / 2)
            print(
                f"\rStep {frame.step:5d} | "
                f"Rogues: {frame.rogue_count:3d} | "
                f"Integrity: {bar:<50s} {frame.integrity():.0f}%",
                end="",
                flush=True,
            )
    except asyncio.CancelledError:
        pass
    finally:
        print("\nDone.")


async def chaos_test():
    """Automated chaos engineering test."""
    sf = SlimeFlow()

    await sf.async_reset()
    print("Reset. Injecting rogues...")
    await sf.async_spawn_rogues()

    results = []

    async for frame in sf.async_stream(max_frames=100):
        results.append({
            "step": frame.step,
            "rogues": frame.rogue_count,
            "quarantined": frame.quarantine_count,
            "integrity": frame.integrity(),
            "density": frame.density(),
        })

        # Auto-inject fault every 20 frames
        if frame.step % 20 == 0 and frame.step > 0:
            await sf.async_inject_fault()

    # Summary
    print(f"\nChaos test complete — {len(results)} frames")
    print(f"Min integrity: {min(r['integrity'] for r in results):.1f}%")
    print(f"Max rogues: {max(r['rogues'] for r in results)}")
    print(f"Max quarantined: {max(r['quarantined'] for r in results)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "monitor"

    try:
        if cmd == "monitor":
            asyncio.run(monitor())
        elif cmd == "chaos":
            asyncio.run(chaos_test())
        else:
            print(f"Usage: python async_example.py [monitor|chaos]")
    except KeyboardInterrupt:
        print("\nInterrupted.")
