#!/usr/bin/env python3
"""Demo: prepaid AgentGuard. Create a key, spend credits on reports, get
quarantined.

Start the server with a known admin token, then run this with the same one:

    SLIMEFLOW_ADMIN_TOKEN=dev-token python -m slimeflow.server --no-state
    SLIMEFLOW_ADMIN_TOKEN=dev-token python examples/paid_guard_demo.py

Built by Lauren Flipo.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional

BASE = os.environ.get("SLIMEFLOW_URL", "http://127.0.0.1:8080")
ADMIN = os.environ.get("SLIMEFLOW_ADMIN_TOKEN", "")


def req(method: str, path: str, payload: Optional[dict] = None, key: str = "",
        admin: bool = False) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Slime-Key"] = key
    if admin:
        headers["X-Slime-Admin"] = ADMIN
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
    if not ADMIN:
        sys.exit("Set SLIMEFLOW_ADMIN_TOKEN to the token the server uses.")
    print("pricing:", req("GET", "/billing/pricing"))

    created = req(
        "POST",
        "/billing/create_key",
        {"label": "demo-fleet", "fleet_id": "demo", "initial_usd": 0.005},
        admin=True,
    )
    if "secret" not in created:
        sys.exit(f"create_key failed: {created}")
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
            print("-> out of credit")
            break
        if r.get("quarantined"):
            print("-> quarantined")
            break

    print("treasury:", req("GET", "/billing/treasury"))
    print("balance:", req("GET", "/billing/balance", key=key))


if __name__ == "__main__":
    main()
