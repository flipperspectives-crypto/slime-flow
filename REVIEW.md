# slime-flow hardening review

Project by Lauren Flipo. Review of `main` at `3302048` (slimeflow 0.2.1), October 6, 2026.

Scope: every file in the repo. Python SDK (`agent_guard.py`, `billing.py`, `server.py`, `client.py`, `models.py`, `__init__.py`), tests, examples, `server.jl`, both HTML sims, CI, `pyproject.toml`, and all Markdown docs.

File:line references point at the code before this branch (`git show 3302048:<path>`). Each Critical and High finding lists the command used to confirm it and its output. The repro server ran with `HOME` pointed at a scratch directory, on port 8099.

## How AgentGuard works (verified against the code)

- Each `report()` adds `RISK[kind]`. High-impact kinds (`send`, `delete`, `pay`, `credential`, `browser_auth`, `code_exec`) add 0.18 when `user_confirmed` is False. A secret-shaped string adds 0.20. The third and later identical reports in a row add `0.15 * min(n/3, 3)`.
- The score decays by 0.002 per second while the agent is not quarantined. It freezes when the score is `>= 0.6` (`agent_guard.py:171`).
- First unconfirmed delete: 0.20 + 0.18 = 0.38, allowed. Second: 0.76 - 0.002 * gap, which is at or above 0.6 for any gap of 80 s or less. Blocked, frozen until `release()`.
- Unconfirmed `code_exec`: 0.12 + 0.18 = 0.30. Two score 0.60 - 0.002 * gap (0.598 at 1 s), allowed whenever the clock moved between them. The third scores 0.90 - 0.002 * span, at or above 0.6 when the time from the first to the third is 150 s or less (75 s per gap when evenly spaced).
- The guard sees only what the harness passes to `report()`. It does not count money.

```
$ python r1.py   # fake clock patched into agent_guard.time.time
delete x2 gap 1s : [(0.38, True), (0.758, False)]
code_exec x3 gap 1s : [(0.3, True), (0.598, True), (0.896, False)]
code_exec x2 gap 0s (same clock tick): [(0.3, True), (0.6, False)]
code_exec x3 gap 75s: [(0.3, True), (0.45, True), (0.6, True)]
delete x2 gap 80s: [(0.38, True), (0.6, False)]  gap 80.01s: [(0.38, True), (0.6, True)]
```

The 75 s line shows a float bug (M5). The exact score is 0.60, which should freeze. The float score is 0.5999999999999999, so the agent was allowed.

## Findings

### Critical

**C1. Anyone who can reach the server can release a frozen agent, including any web page.** `server.py:440-450`, `server.py:273-276`
`POST /agents/{id}/release` and `/quarantine` had no authentication. Every response carried `Access-Control-Allow-Origin: *`, and a `text/plain` POST is a CORS "simple request" that browsers send without a preflight. So the agent itself (with any HTTP tool), another local process, or a malicious page in Lauren's browser could unfreeze an agent. The same holes let anyone freeze any agent.
```
$ for i in 1 2; do curl -s -XPOST $B/agents/report -H "X-Slime-Key: $KEY" -d '{"agent_id":"r2","kind":"delete","tool":"rm"}'; done
 report anomaly 0.38 allowed True
 report anomaly 0.7599 allowed False
 check before release: allowed False unconfirmed_delete
$ curl -s -XPOST -H 'Content-Type: text/plain' -H 'Origin: https://evil.example' -i $B/agents/r2/release
HTTP/1.0 200 OK
Access-Control-Allow-Origin: *
 check after release: allowed True anomaly 0.2399
```
Fix (done): release, quarantine, `create_key` and `topup` require `X-Slime-Admin: <token>`, checked with `hmac.compare_digest`. The token comes from `SLIMEFLOW_ADMIN_TOKEN`, or is generated and printed at startup. With no token configured, admin calls are refused. CORS headers are now sent only on the sim endpoints the dashboards use (`/status`, `/frame`, `/reset`, `/rogues`, `/fault`, `/fault/clear`, `/ping`). Because a custom header forces a preflight, a cross-origin page can't send the admin header at all.

**C2. Secrets the agent handles are stored and served to any web page.** `agent_guard.py:158`, `server.py:357-358`
`detail` (first 200 chars) went into history and the event log unredacted. `GET /agents` returned it with no auth and `Access-Control-Allow-Origin: *`, so any site Lauren visited while the server ran could read the API keys and tokens her agents touched.
```
$ curl -s -XPOST $B/agents/report -H "X-Slime-Key: $KEY" -d '{"agent_id":"leak","kind":"network","detail":"curl -H Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz0123456789"}'
$ curl -s -i -H 'Origin: https://evil.example' $B/agents | grep -o -i "access-control-allow-origin: \*\|Bearer ghp_[a-z0-9]*" | sort -u
Access-Control-Allow-Origin: *
Bearer ghp_abcdefghijklmnopqrstuvwxyz0123456789
```
Fix (done): `redact()` strips token-shaped values and `key=value` secrets from `tool` and `detail` before storage. `payload` was never stored and still isn't. There are no CORS headers on `/agents` (C1). Tests: `test_secrets_redacted_from_history_and_status`, `test_secrets_not_served_back`.

### High

**H1. `"user_confirmed": "false"` counted as confirmed.** `server.py:431`
`bool("false")` is `True`, so any client that sends the flag as a string got every high-impact action scored as confirmed. A delete scored 0.20 instead of 0.38, and the second delete was not blocked.
```
$ curl -s -XPOST $B/agents/report -H "X-Slime-Key: $KEY" -d '{"agent_id":"s1","kind":"delete","user_confirmed":"false"}'
bump 0.2 recent True
```
Fix (done): the server returns 400 unless `user_confirmed` is a JSON boolean. In-process, `report()` raises `TypeError` for non-bool values.

**H2. Unlimited free credit.** `server.py:392-413`, `billing.py:141,166-169,206`
`create_key` took any `initial_usd` with no auth. `topup` took any amount from anyone holding a key. `"nan"` passed the `amount <= 0` check and turned the balance into NaN. `NaN < price` is False, so every later charge succeeded, and the server emitted invalid JSON (`NaN`).
```
$ curl -s -XPOST $B/billing/create_key -d '{"initial_usd":1000000}'      -> balance 1000000.0
$ curl -s -XPOST $B/billing/topup -H "X-Slime-Key: $K2" -d '{"amount_usd":"nan"}'
{"key_id": "key_d36ff9e8", ..., "balance_usd": NaN, ..., "ok": true, "topped_up": NaN}
nan-key report allowed: True balance: nan   (x3)
```
Fix (done): `create_key`/`topup` are admin-only. Amounts must be finite (and positive for topup, non-negative for initial credit). `charge()` refuses a non-finite balance. The server serializes with `allow_nan=False`.

**H3. Quarantine did not survive a restart.** `agent_guard.py:73`
State was memory-only. A crash, redeploy, or an agent with shell access restarting the guard server unfroze every agent. That broke "frozen until a human releases it."
Fix (done): `AgentGuard(state_path=...)` / `attach_state()` writes quarantines atomically to a 0600 JSON file and reloads them at startup. A damaged file raises instead of silently unfreezing. The server uses `~/.slimeflow/guard_state.json` by default (`--state`, `--no-state`). Verified by restarting the server: the agent quarantined before the restart still returned `allowed: false`. Test: `test_quarantine_survives_restart`.

**H4. One slow client stalled every gate.** `server.py:463`
`HTTPServer` handles one connection at a time with no socket timeout. A client that sends headers and stops blocks every `/check` and `/report` from every agent indefinitely.
```
$ (send "POST /agents/report ... Content-Length: 100" and one byte, then wait)
 /ping while one client stalls: TimeoutError 3.0 s
```
Fix (done): `ThreadingHTTPServer` with daemon threads and a 15 s per-connection timeout. Test: `test_slow_client_does_not_block_others`.

**H5. Unknown action kinds silently scored as harmless.** `agent_guard.py:121-122`
Any kind not in `RISK` became `tool` (0.05) with no unconfirmed bump. A harness that reports `"shell"`, `"exec"`, `"rm"` or `"email"` gets almost no protection and no warning.
```
  'shell' bump=0.05   'exec' bump=0.05   'rm' bump=0.05   'transfer' bump=0.05
  'email' bump=0.05   'delete_file' bump=0.05   'DELETE ' bump=0.38
```
Fix (done, defaults unchanged): unknown kinds are tagged `unknown_kind` in history, and `AgentGuard(strict_kinds=True)` raises on them. Making strict the default, or mapping aliases, is proposal P1.

**H6. Common secret formats were missed.** `agent_guard.py:34-37`
```
  openai sk-proj  matched=False   anthropic matched=False   aws matched=False
  github          matched=False   slack     matched=False   EC key matched=False
  jwt             matched=False
```
The old `sk-[a-z0-9]{16,}` also matched inside ordinary words, e.g. `task-abcdefghijklmnop`.
Fix (done): added OpenAI project/Anthropic keys (`sk-` followed by letters, digits, `_` and `-`), AWS access key IDs, GitHub classic and fine-grained tokens, Slack tokens, any `BEGIN ... PRIVATE KEY` header, and JWTs. `sk-` now needs a word boundary. Measured cost on a 5 MB adversarial string is 0.05 to 0.26 s and linear, with no catastrophic backtracking. The scan now runs outside the lock. Tests: `test_secret_patterns_detected` (13 formats), `test_secret_pattern_false_positives_avoided`.

**H7. README claims the code doesn't back up.** `README.md:9,70,72,85,123,217,234`
- "It scores every action your agent takes." It scores only what the harness reports.
- "a prompt-injected or looping agent can't talk its way past it." True of the prompt, but over HTTP the agent could release itself (C1), and in-process any code can call `release()`.
- "data leak monitoring", "Runs fully offline on edge hardware" (the edge deployment roadmap item is unchecked), "Veilpiercer catches prompt injection", "Spawn 8–16 rogue agents near existing clusters" (the code converts up to 12, in index order), and "Harvester 200" (the Python and browser sims fill to 292).
- "MIT". The LICENSE is MIT plus the Commons Clause.
- Option D tells you to run `rogue_agent_demo.py` against a default server, but billing is on by default, so it crashes:
```
$ python demo.py
urllib.error.HTTPError: HTTP Error 402: Payment Required
```
Fix (done): see "Docs corrected" below.

**H8. A bad billing file or env var broke `import slimeflow`.** `billing.py:19-21,68-88,233`, `__init__.py:31`
`slimeflow/__init__.py` imports billing, which built a `Billing()` at import time. That read `~/.slimeflow/billing.json` and parsed price env vars. So AgentGuard users who never touch billing got:
```
$ echo '{"keys":[{"label":"x"}]}' > ~/.slimeflow/billing.json; python -c "from slimeflow import AgentGuard"
KeyError: 'key_id'
$ SLIMEFLOW_PRICE_REPORT=abc python -c "import slimeflow"
ValueError: could not convert string to float: 'abc'
```
Fix (done): the store loads on first use. Malformed rows are skipped with a warning. A bad env value falls back to the default with a warning.

### Medium

- **M1. Release credited the time spent frozen.** `agent_guard.py:180-188`. `release()` capped the score at 0.24 but left the decay anchor at the last report. After a 10-minute quarantine, the next report decayed the 0.24 to 0, so an unconfirmed delete right after release scored 0.38 instead of 0.62 (repro: `next unconfirmed delete right after release -> anomaly 0.38 ... True`). Fixed: release restarts decay from now. Test: `test_release_caps_score_and_gives_no_decay_credit_for_frozen_time`.
- **M2. Wall-clock decay.** `agent_guard.py:90,114` used `time.time()`. A forward clock step (NTP, VM resume, manual change) erased scores: `second delete after +1h clock jump: 0.38 True`. Fixed: decay uses an injectable monotonic clock (default `time.monotonic`). Wall time is kept for display only.
- **M3. Threshold not validated.** `agent_guard.py:70`. `threshold=nan` never froze anything (fail-open), and `-1` froze everything. Fixed: must be finite and between 0 and 1, exclusive.
- **M4. Blocked attempts weren't recorded.** `agent_guard.py:118-119`. A frozen agent's further attempts left no trace (`history len 0 actions 0`). Fixed: logged as `blocked_quarantined` and counted in `blocked`.
- **M5. Float error decided some freezes.** `agent_guard.py:171`. Three shell commands 75 s apart score exactly 0.60 but compute to 0.5999999999999999 (allowed). Fixed: scores are rounded to 12 decimals before the `>= 0.6` comparison. The comparison and threshold are unchanged.
- **M6. Same-clock-tick edge case for two shell commands.** With zero elapsed time, two unconfirmed `code_exec` score exactly 0.60 and the second freezes. That happens only when both calls read the same clock value: sub-microsecond on Linux/macOS, and also on Windows from Python 3.13, which moved `time.monotonic` to a ~1 µs clock. Older Python on Windows ticks about every 15.6 ms. Left in place (it fails toward blocking) and documented. Proposal P3.
- **M7. Any agent ID creates a permanent record.** `agent_guard.py:76-79,91`. `GET /agents/<anything>/check` added an entry forever (`total agents 207` after 200 junk checks). Fixed: `check()` doesn't create records, and agent IDs are validated (non-empty, at most 200 chars, no control characters). Reports still create records, and reports are metered when billing is on.
- **M8. Malformed requests killed the connection.** `server.py:379`. `Content-Length: abc` raised `ValueError` in the handler, and the client got an empty reply. There was no body size limit either. Fixed: 400 for bad lengths, 413 over 1 MiB, JSON body must be an object, a catch-all 500 handler, and quiet handling of disconnects.
- **M9. The key file was world-readable.** `billing.py:112-114`. The file holds plaintext secrets at mode 0644 (`644 .../billing.json`), with a fixed `.tmp` name shared across processes. Fixed: unique temp file, chmod 0600, atomic replace.
- **M10. In-process `release()` has no authorization.** This is inherent to a library: any code in the harness process can call it. Fixed what can be fixed: `release(by=..., note=...)` is written to history for audit, and the README says to run the guard as a separate HTTP server if the agent can execute Python in-process.
- **M11. Percent-encoded IDs didn't match.** `server.py:361,441`. `/agents/my%20bot/check` looked up `my%20bot`, while reports use `my bot`. Fixed: path IDs are URL-decoded.
- **M12. Python sim grid wasn't scaled to 0–1.** `server.py:199-200`. The docs and `Frame.integrity()` assume 0–1, and `server.jl` normalizes, but the Python server sent raw values. Fixed: same normalization as `server.jl`.
- **M13. `server.jl` left 92 agents with type 0.** `server.jl:17,47-53`. `TYPE_COUNTS` sums to 420 of 512, and the rest stayed type 0 (no such type). Rogue spawning also ignored the "non-quarantined" rule its comment states (`server.jl:170-176`). Fixed: fill with Harvesters, skip quarantined agents. Not executed: there's no Julia or CUDA on the review box.
- **M14. License metadata mismatch.** `pyproject.toml:10,18` says MIT with the "OSI Approved :: MIT License" classifier, but the LICENSE adds the Commons Clause, which is not OSI-approved. README fixed. Package metadata left for Lauren (P6).
- **M15. Healthy agents can freeze from volume alone.** With 1 s between calls, the 13th confirmed `tool` call freezes an agent (0.05 x 13 - 0.002 x 12 = 0.626), so a research agent doing quick searches can trip it. Pinned by `test_tool_calls_one_second_apart_freeze_on_the_thirteenth`. Defaults not changed (P2).
- **M16. The loop detector is easy to dodge.** The signature includes the first 120 chars of `detail`, so changing one character resets it (`10 near-identical tool calls -> anomaly 0.5 reasons []`). Left as is (P4).

### Low

- L1. The sim's state-changing endpoints are GETs (`/reset`, `/rogues`, `/frame`), so any page can trigger them with an `<img>` tag. Affects only the sim. Left as is (P10).
- L2. 404 responses had no Content-Type and no JSON header. Fixed.
- L3. `slimeflow.billing` and `slimeflow.guard` are instances that shadow the module names (`from slimeflow import billing` gives the object). Documented in `__init__.py`. Changing it would break imports.
- L4. `client.ConnectionError` shadows the builtin. Left alone (public API).
- L5. CONTRIBUTING.md told contributors to run `npm test` and use 2-space indentation, which doesn't fit this Python repo. Rewritten.
- L6. Lint: unused imports, unused variable, f-strings without placeholders. Fixed. `ruff check` (pyflakes plus syntax errors, configured in `pyproject.toml`) passes. Adding it to CI is pending, see plan item 12.
- L7. Startup output was buffered when stdout is a file, so logs showed nothing. Fixed with flush.
- L8. The keywords "password" and "secret" flag ordinary text ("reset password page"). This adds 0.20 to the score but isn't redacted. Left as is (P5).
- L9. PYPI_PUBLISH.md is a 0.2.1-specific checklist with box paths. Left as a historical record.

### Checked and found sound

- Thread safety: every read and write of guard state happens under one lock. 8 threads x 300 reports counted exactly. 10 simultaneous unconfirmed deletes let exactly one through. Tests: `test_concurrent_reports_are_counted_exactly`, `test_concurrent_deletes_freeze_exactly_once`.
- ReDoS: no nested quantifiers. Worst case measured at 0.26 s on 5 MB.
- Decay math: linear and clamped at 0, so a `check()` between reports doesn't change the result.

## Improvement plan (impact order)

| # | Item | Status |
|---|------|--------|
| 1 | Admin token for release/quarantine/create_key/topup; CORS limited to sim endpoints (C1) | Done |
| 2 | Redact secrets before storing; stop serving guard data cross-origin (C2) | Done |
| 3 | Strict JSON boolean for `user_confirmed` (H1) | Done |
| 4 | Finite amounts, admin-only credit, NaN-safe charge (H2) | Done |
| 5 | Persist quarantines; fail closed on a damaged state file (H3) | Done |
| 6 | Threaded server, timeouts, body limits, input validation (H4, M8, M11) | Done |
| 7 | Flag unknown kinds; `strict_kinds` option (H5) | Done |
| 8 | Wider secret detection, scan outside the lock (H6) | Done |
| 9 | Docs match code: scoring rules, limits, exact bounds (H7) | Done |
| 10 | Lazy, tolerant billing load (H8) | Done |
| 11 | Monotonic clock, release decay fix, threshold validation, blocked-attempt log, float rounding (M1-M5) | Done |
| 12 | pytest suite: 131 offline tests, which the existing CI job runs as-is. CI steps for ruff and for the 9 live sim tests against the Python server are written but not pushed, because the push token lacks GitHub's `workflow` scope. The patch is in the pull request description. | Tests done; CI change pending |
| 13 | Version bump to 0.3.0 in source (HTTP admin endpoints changed) | Done; not built or published |

Proposals for Lauren. Not implemented, because each changes a default or the product's behavior:

- **P1.** Make `strict_kinds=True` the default, or map common aliases (`shell`/`exec` to `code_exec`, `rm` to `delete`, `email` to `send`).
- **P2.** Benign volume (M15): lower `tool`/`message` scores, raise decay, or exempt confirmed low-risk kinds, so busy healthy agents don't freeze.
- **P3.** Same-tick edge (M6): decide whether two back-to-back unconfirmed shell commands should always pass (for example, compare with `>` after rounding). Note that `>` would also let an unconfirmed send that carries a secret (exactly 0.60) through on its first call, which today freezes. Today's `>=` was kept on purpose.
- **P4.** A loop signature that ignores `detail`, or counts calls per tool per minute.
- **P5.** Drop the bare keywords ("password", "secret") from detection, or score them lower than real token formats.
- **P6.** License metadata: either drop the OSI classifier and set `license` to the actual terms, or drop the Commons Clause.
- **P7.** Per-agent report tokens on the HTTP server. With billing off, any local process can report as any agent and freeze it.
- **P8.** Tamper resistance for the state file (HMAC with the admin token, or run the guard as a separate OS user the agent can't write as).
- **P9.** An AgentGuard HTTP client class in the SDK, so harnesses don't hand-roll urllib calls.
- **P10.** Move the sim's state-changing GET endpoints to POST.
- **P11.** Publish 0.3.0 to PyPI. Needs Lauren's go-ahead.

## Docs corrected

- README: "scores every action your agent takes" → it scores what the harness reports, before the action runs.
- README: added the exact scoring rules, the 80 s / 150 s bounds, the same-tick edge case, the release cap, and a "What it does not do" list (unreported actions, harness-trusted fields, no money counting, in-process release).
- README: "can't talk its way past it" → qualified (the guard is code, not prompt; it only sees what's reported).
- README: removed "data leak monitoring" and "No central server. Runs fully offline on edge hardware." Now reads "No cloud dependency. Runs offline on one machine."
- README: "Spawn 8–16 rogue agents near existing clusters" → "Turn up to 12 active agents into rogues". "Harvester 200" → 292 (200 plus 92 fill).
- README: "Veilpiercer catches prompt injection" → "AgentGuard flags unconfirmed high-impact actions, secret-shaped payloads, and loops".
- README: Option D now starts the server with `SLIMEFLOW_BILLING=0` and documents the admin token, state file, strict booleans, and CORS policy.
- README: "MIT" → MIT terms with the Commons Clause condition.
- python-sdk/README.md (the PyPI page): added an AgentGuard section, the Python server as an alternative to `julia server.jl`, and grid scaling.
- MONETIZE.md: `create_key`/`topup` need the admin header; credits are local and stored in plain text.
- OUTREACH_DMS.md: removed "2-min setup" / "2-minute start" claims. No timed setup has been measured.
- CONTRIBUTING.md: rewritten for this repo.
