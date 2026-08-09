"""Slime Flow Python SDK — connect to the Julia CUDA swarm server.

Usage::

    from slimeflow import SlimeFlow

    sf = SlimeFlow("localhost", 8080)

    # One-shot frame
    frame = sf.frame()
    print(f"Step {frame.step}, {frame.rogue_count} rogues")

    # Async streaming
    async for frame in sf.stream():
        print(frame.density())

    # Inject chaos
    sf.spawn_rogues()
    sf.inject_fault(0.4, 0.6)
"""

from slimeflow.client import SlimeFlow
from slimeflow.models import Frame, Agent, Status

__all__ = ["SlimeFlow", "Frame", "Agent", "Status"]
__version__ = "0.1.0"
