#!/usr/bin/env python3
"""Demo: real LLM agents going rogue → Veilpiercer quarantine."""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8080"


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=5) as resp:
        return json.loads(resp.read())


def main() -> None:
    print("=== Slime Flow agent guard demo ===\n")

    # Healthy worker
    ok = post(
        "/agents/report",
        {
            "agent_id": "research-bot",
            "kind": "tool",
            "tool": "WebSearch",
            "detail": "lookup slime mold papers",
            "user_confirmed": True,
        },
    )
    print("research-bot search:", ok["anomaly"], "allowed=", ok["allowed"])

    # Rogue: tries to send mail without confirmation, then loops
    for i in range(5):
        r = post(
            "/agents/report",
            {
                "agent_id": "spammy-bot",
                "kind": "send",
                "tool": "gmail.send",
                "detail": f"blast outreach #{i}",
                "user_confirmed": False,
                "payload": "unsubscribe? here is api_key=sk-live-not-a-real-key-but-looks-like-one",
            },
        )
        print(
            f"spammy-bot send #{i}: anomaly={r['anomaly']} "
            f"allowed={r['allowed']} quarantined={r['quarantined']} reason={r.get('reason')!r}"
        )
        if r["quarantined"]:
            break

    # Gate check blocks further work
    gate = get("/agents/spammy-bot/check")
    print("\nspammy-bot gate:", gate)

    # Fleet view
    fleet = get("/agents")
    print("\nfleet quarantined:", fleet["quarantined"], "/", fleet["total"])
    for a in fleet["agents"]:
        print(" -", a["agent_id"], "anomaly=", a["anomaly"], "Q=", a["quarantined"])


if __name__ == "__main__":
    main()
