# Slime Flow — Income flywheel (AgentGuard)

Product: **prepaid Veilpiercer gate** for live LLM/desktop agents.
Customers pay so their fleets get quarantined before send / delete / pay / secrets / loops.

## Pricing (defaults)

| Action | Price |
|--------|-------|
| `GET /agents/{id}/check` | free (`$0`) |
| `POST /agents/report` | `$0.001` / call |
| Fleet seat (suggested) | `$9` / month |

Override with env: `SLIMEFLOW_PRICE_REPORT`, `SLIMEFLOW_PRICE_CHECK`, `SLIMEFLOW_SEAT_MONTHLY`.
Disable metering: `SLIMEFLOW_BILLING=0`.

## Flywheel

1. Customer tops up a key (`POST /billing/topup`) or buys a seat.
2. Agents call `/agents/report` with `X-Slime-Key` → credits move to **treasury**.
3. Treasury funds compute / SAP prepaid / the next agent.
4. Rule: **one agent profitable 7 days** before spawning the next.
5. Every agent still runs behind AgentGuard (threshold 0.6).

## Quick start

```bash
pip install -e python-sdk/
python -m slimeflow.server --host 127.0.0.1 --port 8080

# create key + $1 credit
curl -s -X POST http://127.0.0.1:8080/billing/create_key \
  -H 'Content-Type: application/json' \
  -d '{"label":"fleet-a","initial_usd":1}' 

# report with key
curl -s -X POST http://127.0.0.1:8080/agents/report \
  -H 'Content-Type: application/json' \
  -H "X-Slime-Key: sf_..." \
  -d '{"agent_id":"bot-1","kind":"send","tool":"gmail.send","user_confirmed":false}'
```

Or: `python python-sdk/examples/paid_guard_demo.py`

## Zero capital bootstrap

You can sell **before** you spend:
1. Self-host free (`SLIMEFLOW_BILLING=0` for demos).
2. Enroll GitHub Sponsors — see [SPONSORS.md](SPONSORS.md) + [FREE_LAUNCH.md](FREE_LAUNCH.md).
3. Design partners first, `$9` seats after proof.
4. Do **not** fund SAP prepaid / trading until inbound cash exists.

## Next rails

- Map `topup` → SAP `sap_payments_fund_prepaid` / x402 so credits settle on-chain.
- Publish `slimeflow` 0.2.1 to PyPI + enroll GitHub Sponsors (`FUNDING.yml` ready).
