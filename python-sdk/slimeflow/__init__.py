"""Slime Flow Python SDK — swarm sim client + real agent Veilpiercer.

Sim usage::

    from slimeflow import SlimeFlow
    sf = SlimeFlow()
    sf.spawn_rogues()
    for frame in sf.stream(max_frames=50):
        print(frame)

Real rogue-agent gate::

    from slimeflow import AgentGuard
    guard = AgentGuard()
    gate = guard.check("research-bot")
    if gate["allowed"]:
        result = guard.report(
            "research-bot",
            "send",
            tool="gmail.send",
            detail="reply without user confirm",
            user_confirmed=False,
        )
        if not result["allowed"]:
            raise RuntimeError("quarantined: " + result["reason"])
"""

from slimeflow.client import SlimeFlow
from slimeflow.models import Frame, Agent, Status
from slimeflow.agent_guard import AgentGuard, guard

__all__ = ["SlimeFlow", "Frame", "Agent", "Status", "AgentGuard", "guard"]
__version__ = "0.2.0"
