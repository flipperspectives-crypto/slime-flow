"""Slime Flow Python SDK: AgentGuard for LLM agents, plus the swarm sim client.

Built by Lauren Flipo.

Sim usage::

    from slimeflow import SlimeFlow
    sf = SlimeFlow()
    sf.spawn_rogues()
    for frame in sf.stream(max_frames=50):
        print(frame)

AgentGuard (call report() before the action, run it only if allowed)::

    from slimeflow import AgentGuard
    guard = AgentGuard()
    result = guard.report(
        "research-bot",
        "send",
        tool="gmail.send",
        detail="reply without user confirm",
        user_confirmed=False,
    )
    if not result["allowed"]:
        raise RuntimeError("quarantined: " + result["reason"])

Note: ``slimeflow.billing`` and ``slimeflow.guard`` are the default
instances, not the modules. Import the modules as
``slimeflow.agent_guard`` / ``sys.modules["slimeflow.billing"]`` if needed.
"""

from slimeflow.client import SlimeFlow
from slimeflow.models import Frame, Agent, Status
from slimeflow.agent_guard import AgentGuard, guard
from slimeflow.billing import Billing, billing

__all__ = ["SlimeFlow", "Frame", "Agent", "Status", "AgentGuard", "guard", "Billing", "billing"]
__version__ = "0.3.0"
