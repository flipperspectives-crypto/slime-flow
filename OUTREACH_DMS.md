# Outreach DMs (send to 2 people you trust)

Copy/paste. Personalize the `[name]`.

## DM 1 — builder / agent person

Hey [name] — I shipped AgentGuard on slime-flow: quarantines rogue LLM agents before send/delete/pay (threshold 0.6). Self-host is free.

Looking for 3 design partners for 14 days. Would you try it on one real agent and dump quarantine notes on this issue?
https://github.com/flipperspectives-crypto/slime-flow/issues/2

2-min demo:
```
pip install -e python-sdk/
SLIMEFLOW_BILLING=0 python -m slimeflow.server --host 127.0.0.1 --port 8080
python python-sdk/examples/rogue_agent_demo.py
```

## DM 2 — infra / security person

[name] — open-sourced a Veilpiercer for live agents (not just a sim). Same anomaly model as our swarm demo. Free self-host + design-partner slots here: https://github.com/flipperspectives-crypto/slime-flow/issues/2

If you’ve got an agent that touches email/Slack/payments, you’d be a perfect first case.
