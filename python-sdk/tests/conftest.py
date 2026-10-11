"""Shared test setup. Keeps tests away from the real ~/.slimeflow files."""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="slimeflow-tests-")
os.environ.setdefault("SLIMEFLOW_BILLING_STORE", os.path.join(_TMP, "billing.json"))
os.environ.setdefault("SLIMEFLOW_GUARD_STATE", os.path.join(_TMP, "guard_state.json"))


class FakeClock:
    """Manual monotonic clock for decay tests."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
