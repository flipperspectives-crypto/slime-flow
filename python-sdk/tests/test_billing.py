import os
from pathlib import Path

os.environ["SLIMEFLOW_BILLING"] = "1"
os.environ["SLIMEFLOW_PRICE_REPORT"] = "0.001"
os.environ["SLIMEFLOW_PRICE_CHECK"] = "0.0"

from slimeflow.billing import Billing


def test_charge_and_treasury(tmp_path: Path):
    b = Billing(path=tmp_path / "billing.json", enabled=True)
    created = b.create_key(label="t", initial_usd=0.003)
    secret = created["secret"]
    c1 = b.charge(secret, kind="report")
    assert c1["allowed"] is True
    assert abs(c1["charged_usd"] - 0.001) < 1e-9
    c2 = b.charge(secret, kind="report")
    assert c2["allowed"] is True
    c3 = b.charge(secret, kind="report")
    assert c3["allowed"] is True
    c4 = b.charge(secret, kind="report")
    assert c4["allowed"] is False
    assert c4["error"] == "insufficient_credit"
    assert b.treasury_usd >= 0.003 - 1e-9


def test_billing_disabled(tmp_path: Path):
    b = Billing(path=tmp_path / "b2.json", enabled=False)
    assert b.charge(None, kind="report")["allowed"] is True


# ─── hardening ──────────────────────────────────────────────────────────

import json  # noqa: E402
import stat  # noqa: E402
import warnings  # noqa: E402

import pytest  # noqa: E402

import sys  # noqa: E402

billing_mod = sys.modules["slimeflow.billing"]  # the package attribute is the Billing instance


@pytest.mark.parametrize("amount", ["nan", float("nan"), float("inf"), "-inf", -1, 0, "abc", None])
def test_topup_rejects_bad_amounts(tmp_path: Path, amount):
    b = Billing(path=tmp_path / "b.json", enabled=True)
    secret = b.create_key(initial_usd=0)["secret"]
    out = b.topup(secret, amount)
    assert out["ok"] is False
    assert b.charge(secret, kind="report")["allowed"] is False


@pytest.mark.parametrize("amount", ["nan", float("inf"), -5, "abc"])
def test_create_key_rejects_bad_initial(tmp_path: Path, amount):
    b = Billing(path=tmp_path / "b.json", enabled=True)
    assert b.create_key(initial_usd=amount)["ok"] is False


def test_store_file_is_private(tmp_path: Path):
    path = tmp_path / "b.json"
    Billing(path=path, enabled=True).create_key()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_construction_does_not_read_disk(tmp_path: Path):
    path = tmp_path / "b.json"
    path.write_text("{broken")
    b = Billing(path=path, enabled=True)  # must not raise or warn yet
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert b.pricing()["treasury_usd"] == 0.0
    assert any("unreadable" in str(w.message) for w in caught)


def test_malformed_rows_are_skipped(tmp_path: Path):
    path = tmp_path / "b.json"
    good = {"key_id": "key_1", "secret": "sf_good", "balance_usd": 1}
    path.write_text(json.dumps({
        "treasury_usd": "nan",
        "keys": [{"label": "no id"}, {"key_id": "k", "secret": "s", "balance_usd": "NaN"}, good],
    }))
    b = Billing(path=path, enabled=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert b.balance("sf_good")["ok"] is True
        assert b.balance("s")["ok"] is False
        assert b.treasury_usd == 0.0


def test_bad_price_env_falls_back(monkeypatch):
    monkeypatch.setenv("SLIMEFLOW_TEST_PRICE", "abc")
    with pytest.warns(UserWarning):
        assert billing_mod._env_float("SLIMEFLOW_TEST_PRICE", 0.5) == 0.5
    monkeypatch.setenv("SLIMEFLOW_TEST_PRICE", "-1")
    with pytest.warns(UserWarning):
        assert billing_mod._env_float("SLIMEFLOW_TEST_PRICE", 0.5) == 0.5
    monkeypatch.setenv("SLIMEFLOW_TEST_PRICE", "0.25")
    assert billing_mod._env_float("SLIMEFLOW_TEST_PRICE", 0.5) == 0.25
