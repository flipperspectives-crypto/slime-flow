#!/usr/bin/env python3
"""Demo: prepaid AgentGuard — pay → score → quarantine → treasury grows."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"


def req(method: str, path: str, payload: dict | None = None, key: str = "") -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Slime-Key"] = key
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"error": body, "status": e.code}


def main() -> None:
    print("=== Paid AgentGuard demo ===\n")
    print("pricing:", req("GET", "/billing/pricing"))

    created = req(
        "POST",
        "/billing/create_key",
        {"label": "demo-fleet", "fleet_id": "demo", "initial_usd": 0.005},
    )
    key = created["secret"]
    print("key:", created["key_id"], "balance=", created["balance_usd"])

    # burn a few reports (~$0.001 each) until credit runs out or quarantine
    for i in range(8):
        r = req(
            "POST",
            "/agents/report",
            {
                "agent_id": "paid-spammy",
                "kind": "send",
                "tool": "gmail.send",
                "detail": f"blast #{i}",
                "user_confirmed": False,
                "payload": "api_key=sk-live-demo-not-real-but-pattern",
            },
            key=key,
        )
        bill = r.get("billing") or r
        print(
            f"#{i} allowed={r.get('allowed')} Q={r.get('quarantined')} "
            f"anomaly={r.get('anomaly')} charged={bill.get('charged_usd')} "
            f"bal={bill.get('balance_usd')} err={r.get('error')}"
        )
        if r.get("error") == "insufficient_credit":
            print("→ out of credit — top up to keep selling gate capacity")
            break
        if r.get("quarantined"):
            print("→ rogue quarantined (product working)")
            break

    print("treasury:", req("GET", "/billing/treasury"))
    print("balance:", req("GET", "/billing/balance", key=key))


if __name__ == "__main__":
    main()
