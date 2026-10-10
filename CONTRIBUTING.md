# Contributing to Slime Flow

Slime Flow is built by Lauren Flipo. Pull requests are welcome.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e "python-sdk/[all]" pytest ruff
```

## Before you open a pull request

```bash
cd python-sdk
ruff check .
python -m pytest -q
```

CI runs the test suite on Python 3.9 to 3.13.

The tests in `tests/test_agent_guard.py` pin AgentGuard's documented behavior (scores, thresholds, when an agent freezes). If you change scoring, update the tests, the README "How scoring works" section, and the `agent_guard.py` docstring in the same pull request.

The live sim tests in `tests/test_client.py` are skipped unless a server is running on localhost:8080 (`python -m slimeflow.server`).

## Bug reports

Include steps to reproduce, what you expected, what happened, and your Python version and OS.
