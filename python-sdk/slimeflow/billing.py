"""Prepaid metering for AgentGuard — income rail for self-funding fleets.

Local credits now; same counters can settle via SAP x402/prepaid later.
Set SLIMEFLOW_BILLING=0 to disable (demo / localhost).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# Defaults — change via env without code edits
PRICE_REPORT_USD = float(os.environ.get("SLIMEFLOW_PRICE_REPORT", "0.001"))
PRICE_CHECK_USD = float(os.environ.get("SLIMEFLOW_PRICE_CHECK", "0.0"))
SEAT_MONTHLY_USD = float(os.environ.get("SLIMEFLOW_SEAT_MONTHLY", "9.0"))
BILLING_ENABLED = os.environ.get("SLIMEFLOW_BILLING", "1") not in ("0", "false", "False")
STORE_PATH = Path(
    os.environ.get(
        "SLIMEFLOW_BILLING_STORE",
        str(Path.home() / ".slimeflow" / "billing.json"),
    )
)


@dataclass
class KeyRecord:
    key_id: str
    secret: str
    label: str = ""
    balance_usd: float = 0.0
    spent_usd: float = 0.0
    reports: int = 0
    checks: int = 0
    created: float = field(default_factory=time.time)
    fleet_id: str = "default"

    def to_public(self) -> Dict[str, Any]:
        return {
            "key_id": self.key_id,
            "label": self.label,
            "fleet_id": self.fleet_id,
            "balance_usd": round(self.balance_usd, 6),
            "spent_usd": round(self.spent_usd, 6),
            "reports": self.reports,
            "checks": self.checks,
            "created": self.created,
            # full secret only returned once at create
        }


class Billing:
    """API-key prepaid ledger."""

    def __init__(self, path: Path = STORE_PATH, enabled: bool = BILLING_ENABLED):
        self.path = path
        self.enabled = enabled
        self._lock = threading.Lock()
        self._keys: Dict[str, KeyRecord] = {}  # secret -> record
        self.treasury_usd = 0.0  # cumulative revenue (flywheel input)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self.treasury_usd = float(data.get("treasury_usd", 0.0))
        for row in data.get("keys", []):
            rec = KeyRecord(
                key_id=row["key_id"],
                secret=row["secret"],
                label=row.get("label", ""),
                balance_usd=float(row.get("balance_usd", 0)),
                spent_usd=float(row.get("spent_usd", 0)),
                reports=int(row.get("reports", 0)),
                checks=int(row.get("checks", 0)),
                created=float(row.get("created", time.time())),
                fleet_id=row.get("fleet_id", "default"),
            )
            self._keys[rec.secret] = rec

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "treasury_usd": round(self.treasury_usd, 6),
            "price_report_usd": PRICE_REPORT_USD,
            "price_check_usd": PRICE_CHECK_USD,
            "seat_monthly_usd": SEAT_MONTHLY_USD,
            "keys": [
                {
                    "key_id": r.key_id,
                    "secret": r.secret,
                    "label": r.label,
                    "balance_usd": r.balance_usd,
                    "spent_usd": r.spent_usd,
                    "reports": r.reports,
                    "checks": r.checks,
                    "created": r.created,
                    "fleet_id": r.fleet_id,
                }
                for r in self._keys.values()
            ],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)

    def pricing(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "price_report_usd": PRICE_REPORT_USD,
            "price_check_usd": PRICE_CHECK_USD,
            "seat_monthly_usd": SEAT_MONTHLY_USD,
            "treasury_usd": round(self.treasury_usd, 6),
            "currency": "USD",
            "note": "Local prepaid credits. Map topups to SAP x402/prepaid when ready.",
        }

    def create_key(
        self,
        *,
        label: str = "",
        fleet_id: str = "default",
        initial_usd: float = 0.0,
    ) -> Dict[str, Any]:
        with self._lock:
            secret = "sf_" + secrets.token_urlsafe(24)
            key_id = "key_" + secrets.token_hex(4)
            rec = KeyRecord(
                key_id=key_id,
                secret=secret,
                label=label,
                balance_usd=max(0.0, float(initial_usd)),
                fleet_id=fleet_id,
            )
            self._keys[secret] = rec
            self._save()
            out = rec.to_public()
            out["secret"] = secret  # show once
            out["pricing"] = self.pricing()
            return out

    def _resolve(self, secret: Optional[str]) -> Optional[KeyRecord]:
        if not secret:
            return None
        return self._keys.get(secret.strip())

    def balance(self, secret: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._resolve(secret)
            if not rec:
                return {"ok": False, "error": "invalid_key"}
            out = rec.to_public()
            out["ok"] = True
            out["pricing"] = self.pricing()
            return out

    def topup(self, secret: str, amount_usd: float) -> Dict[str, Any]:
        amount = float(amount_usd)
        if amount <= 0:
            return {"ok": False, "error": "amount_must_be_positive"}
        with self._lock:
            rec = self._resolve(secret)
            if not rec:
                return {"ok": False, "error": "invalid_key"}
            rec.balance_usd += amount
            self._save()
            out = rec.to_public()
            out["ok"] = True
            out["topped_up"] = amount
            return out

    def charge(
        self,
        secret: Optional[str],
        *,
        kind: str,
    ) -> Dict[str, Any]:
        """Charge for an action. Returns {allowed, ...}."""
        if not self.enabled:
            return {
                "allowed": True,
                "billing": False,
                "charged_usd": 0.0,
                "reason": "billing_disabled",
            }

        price = PRICE_CHECK_USD if kind == "check" else PRICE_REPORT_USD
        with self._lock:
            rec = self._resolve(secret)
            if not rec:
                return {
                    "allowed": False,
                    "billing": True,
                    "error": "missing_or_invalid_key",
                    "hint": "Pass header X-Slime-Key from POST /billing/create_key",
                }
            if rec.balance_usd < price - 1e-12:
                return {
                    "allowed": False,
                    "billing": True,
                    "error": "insufficient_credit",
                    "balance_usd": round(rec.balance_usd, 6),
                    "needed_usd": price,
                    "key_id": rec.key_id,
                }
            rec.balance_usd -= price
            rec.spent_usd += price
            self.treasury_usd += price
            if kind == "check":
                rec.checks += 1
            else:
                rec.reports += 1
            self._save()
            return {
                "allowed": True,
                "billing": True,
                "charged_usd": price,
                "balance_usd": round(rec.balance_usd, 6),
                "key_id": rec.key_id,
                "treasury_usd": round(self.treasury_usd, 6),
            }


billing = Billing()
