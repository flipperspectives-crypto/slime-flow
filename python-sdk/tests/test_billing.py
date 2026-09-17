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
