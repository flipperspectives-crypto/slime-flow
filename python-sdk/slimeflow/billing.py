"""Prepaid metering for the AgentGuard HTTP server.

Built by Lauren Flipo.

Local credits only: no payment provider is wired in. Keys and balances are
kept in a JSON file (``~/.slimeflow/billing.json`` by default, mode 0600).
The file holds key secrets in plain text, so treat it like a password file.

Set SLIMEFLOW_BILLING=0 to turn metering off (demos, localhost).
"""

from __future__ import annotations

import json
import math
import os
import secrets
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value < 0:
        warnings.warn(f"{name}={raw!r} is not a non-negative number; using {default}")
        return default
    return value


PRICE_REPORT_USD = _env_float("SLIMEFLOW_PRICE_REPORT", 0.001)
PRICE_CHECK_USD = _env_float("SLIMEFLOW_PRICE_CHECK", 0.0)
SEAT_MONTHLY_USD = _env_float("SLIMEFLOW_SEAT_MONTHLY", 9.0)
BILLING_ENABLED = os.environ.get("SLIMEFLOW_BILLING", "1").strip().lower() not in ("0", "false", "no", "off")
STORE_PATH = Path(
    os.environ.get(
        "SLIMEFLOW_BILLING_STORE",
        str(Path.home() / ".slimeflow" / "billing.json"),
    )
)


def _finite_amount(value: Any) -> Optional[float]:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) else None


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
            # The full secret is only returned once, by create_key.
        }


class Billing:
    """API-key prepaid ledger.

    The store file is read on first use, not at construction, so importing
    ``slimeflow`` never touches the disk.
    """

    def __init__(self, path: Path = STORE_PATH, enabled: bool = BILLING_ENABLED):
        self.path = Path(path)
        self.enabled = enabled
        self._lock = threading.Lock()
        self._keys: Dict[str, KeyRecord] = {}  # secret -> record
        self._treasury_usd = 0.0
        self._loaded = False

    @property
    def treasury_usd(self) -> float:
        """Total credits charged so far."""
        with self._lock:
            self._ensure_loaded()
            return self._treasury_usd

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            warnings.warn(f"billing store {self.path} unreadable ({exc}); starting empty")
            return
        if not isinstance(data, dict):
            warnings.warn(f"billing store {self.path} is not a JSON object; starting empty")
            return
        treasury = _finite_amount(data.get("treasury_usd", 0.0))
        self._treasury_usd = treasury if treasury is not None else 0.0
        skipped = 0
        for row in data.get("keys", []) or []:
            try:
                balance = _finite_amount(row.get("balance_usd", 0))
                spent = _finite_amount(row.get("spent_usd", 0))
                if balance is None or spent is None:
                    raise ValueError("non-finite amount")
                rec = KeyRecord(
                    key_id=str(row["key_id"]),
                    secret=str(row["secret"]),
                    label=str(row.get("label", "")),
                    balance_usd=balance,
                    spent_usd=spent,
                    reports=int(row.get("reports", 0)),
                    checks=int(row.get("checks", 0)),
                    created=float(row.get("created", time.time())),
                    fleet_id=str(row.get("fleet_id", "default")),
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                skipped += 1
                continue
            self._keys[rec.secret] = rec
        if skipped:
            warnings.warn(f"billing store {self.path}: skipped {skipped} malformed key row(s)")

    def _save(self) -> None:
        payload = {
            "treasury_usd": round(self._treasury_usd, 6),
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".billing-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def pricing(self) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            return self._pricing_locked()

    def _pricing_locked(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "price_report_usd": PRICE_REPORT_USD,
            "price_check_usd": PRICE_CHECK_USD,
            "seat_monthly_usd": SEAT_MONTHLY_USD,
            "treasury_usd": round(self._treasury_usd, 6),
            "currency": "USD",
            "note": "Local prepaid credits. No payment provider is connected.",
        }

    def create_key(
        self,
        *,
        label: str = "",
        fleet_id: str = "default",
        initial_usd: float = 0.0,
    ) -> Dict[str, Any]:
        amount = _finite_amount(initial_usd)
        if amount is None or amount < 0:
            return {"ok": False, "error": "initial_usd_must_be_a_non_negative_number"}
        with self._lock:
            self._ensure_loaded()
            secret = "sf_" + secrets.token_urlsafe(24)
            key_id = "key_" + secrets.token_hex(4)
            rec = KeyRecord(
                key_id=key_id,
                secret=secret,
                label=str(label)[:200],
                balance_usd=amount,
                fleet_id=str(fleet_id)[:200],
            )
            self._keys[secret] = rec
            self._save()
            out = rec.to_public()
            out["ok"] = True
            out["secret"] = secret  # shown once
            out["pricing"] = self._pricing_locked()
            return out

    def _resolve(self, secret: Optional[str]) -> Optional[KeyRecord]:
        if not secret:
            return None
        return self._keys.get(secret.strip())

    def balance(self, secret: str) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            rec = self._resolve(secret)
            if not rec:
                return {"ok": False, "error": "invalid_key"}
            out = rec.to_public()
            out["ok"] = True
            out["pricing"] = self._pricing_locked()
            return out

    def topup(self, secret: str, amount_usd: float) -> Dict[str, Any]:
        amount = _finite_amount(amount_usd)
        if amount is None or amount <= 0:
            return {"ok": False, "error": "amount_must_be_a_positive_number"}
        with self._lock:
            self._ensure_loaded()
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
        """Charge for one call. Returns a dict with ``allowed``."""
        if not self.enabled:
            return {
                "allowed": True,
                "billing": False,
                "charged_usd": 0.0,
                "reason": "billing_disabled",
            }

        price = PRICE_CHECK_USD if kind == "check" else PRICE_REPORT_USD
        with self._lock:
            self._ensure_loaded()
            rec = self._resolve(secret)
            if not rec:
                return {
                    "allowed": False,
                    "billing": True,
                    "error": "missing_or_invalid_key",
                    "hint": "Pass header X-Slime-Key from POST /billing/create_key",
                }
            if not math.isfinite(rec.balance_usd) or rec.balance_usd < price - 1e-12:
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
            self._treasury_usd += price
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
                "treasury_usd": round(self._treasury_usd, 6),
            }


billing = Billing()
