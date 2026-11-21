"""Persistent, execution-free lifecycle primitives for research signals.

This module never opens, closes, or infers a broker position. A signal is an
analysis opportunity only; price milestones are market observations.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
from urllib.parse import quote

import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
TERMINAL_STATUSES = {"EXPIRED", "INVALIDATED", "TARGET_REACHED"}


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        value = datetime.fromisoformat(text)
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime or ISO-8601 string")
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite positive number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return number


def make_signal_id(asset: str, timeframe: str, candle_timestamp: str,
                   direction: str = "LONG") -> str:
    """Stable ID: rerunning the same candle cannot create a new signal."""
    parts = (asset.strip().upper(), timeframe.strip().lower(),
             _as_utc(candle_timestamp).isoformat(), direction.strip().upper())
    if not all(parts):
        raise ValueError("asset, timeframe, timestamp and direction are required")
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def build_signal_record(result: dict, generated_at: datetime | str,
                        validity_minutes: int | None = None,
                        expires_at: datetime | str | None = None) -> dict:
    """Convert a technical candidate into a time-bounded research signal.

    The caller supplies either a validated duration or an explicit calendar-aware
    expiry. This function deliberately does not invent a universal expiry window.
    """
    if not isinstance(result, dict):
        raise ValueError("result must be a dictionary")
    if result.get("action") != "BUY CANDIDATE":
        raise ValueError("only technical candidates can become signals")
    if (validity_minutes is None) == (expires_at is None):
        raise ValueError("supply exactly one of validity_minutes or expires_at")
    if validity_minutes is not None:
        if isinstance(validity_minutes, bool) or not isinstance(validity_minutes, int):
            raise ValueError("validity_minutes must be a positive integer")
        if validity_minutes <= 0:
            raise ValueError("validity_minutes must be a positive integer")

    generated = _as_utc(generated_at)
    expiry = (
        generated + timedelta(minutes=validity_minutes)
        if expires_at is None
        else _as_utc(expires_at)
    )
    if expiry <= generated:
        raise ValueError("expires_at must be later than generated_at")
    effective_validity_minutes = max(
        1, math.ceil((expiry - generated).total_seconds() / 60)
    )
    entry = _positive_number(result.get("entry_reference"), "entry_reference")
    stop = _positive_number(result.get("stop"), "stop")
    target = _positive_number(result.get("target"), "target")
    direction = str(result.get("direction") or "LONG").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    if direction == "LONG" and not (stop < entry < target):
        raise ValueError("LONG levels must satisfy stop < entry < target")
    if direction == "SHORT" and not (target < entry < stop):
        raise ValueError("SHORT levels must satisfy target < entry < stop")

    candle_timestamp = result.get("data_timestamp") or generated.isoformat()
    signal_id = make_signal_id(
        str(result.get("asset", "")),
        str(result.get("timeframe", "")),
        candle_timestamp,
        direction,
    )
    risk_pct = abs(entry - stop) / entry * 100
    reward_pct = abs(target - entry) / entry * 100

    record = {
        "schema_version": SCHEMA_VERSION,
        "signal_id": signal_id,
        "asset": str(result.get("asset", "")).upper(),
        "timeframe": str(result.get("timeframe", "")),
        "direction": direction,
        "status": "PENDING",
        "generated_at": generated.isoformat(),
        "expires_at": expiry.isoformat(),
        "validity_minutes": effective_validity_minutes,
        "data_timestamp": _as_utc(candle_timestamp).isoformat(),
        "entry_reference": entry,
        "stop": stop,
        "target": target,
        "potential_reward_pct": round(reward_pct, 4),
        "potential_risk_pct": round(risk_pct, 4),
        "score": result.get("score"),
        "reasons": list(result.get("reasons") or []),
        "probability": None,
        "probability_status": "NOT_CALIBRATED",
        "position_status": "UNKNOWN",
        "last_event": "SIGNAL_CREATED",
    }
    return record


def advance_signal(signal: dict, now: datetime | str,
                   latest_price: float | None = None,
                   conditions_valid: bool = True) -> tuple[dict, str | None]:
    """Advance a pending signal and emit at most one event per state change."""
    if not isinstance(signal, dict) or signal.get("status") != "PENDING":
        return dict(signal), None

    updated = dict(signal)
    current = _as_utc(now)
    expires = _as_utc(updated["expires_at"])

    if not conditions_valid:
        updated["status"] = "INVALIDATED"
        event = "SIGNAL_INVALIDATED"
    elif current >= expires:
        updated["status"] = "EXPIRED"
        event = "SIGNAL_EXPIRED"
    elif latest_price is not None:
        price = _positive_number(latest_price, "latest_price")
        direction = updated.get("direction", "LONG")
        stop = float(updated["stop"])
        target = float(updated["target"])
        if direction == "LONG" and price <= stop:
            updated["status"] = "INVALIDATED"
            event = "SIGNAL_INVALIDATED_STOP_LEVEL"
        elif direction == "SHORT" and price >= stop:
            updated["status"] = "INVALIDATED"
            event = "SIGNAL_INVALIDATED_STOP_LEVEL"
        elif direction == "LONG" and price >= target:
            updated["status"] = "TARGET_REACHED"
            event = "TARGET_LEVEL_REACHED"
        elif direction == "SHORT" and price <= target:
            updated["status"] = "MISSED_ENTRY"
            event = "TARGET_REACHED_BEFORE_ENTRY"
        else:
            return updated, None
    else:
        return updated, None

    updated["last_event"] = event
    updated["last_checked_at"] = current.isoformat()
    # The bot cannot know whether the user entered manually.
    updated["position_status"] = "UNKNOWN"
    return updated, event


class SignalStateStore:
    """Small atomic JSON store for a persistent host volume.

    GitHub Actions runners are ephemeral; this store must not be treated as
    persistent across Actions jobs unless its file is backed by durable storage.
    """

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> dict:
        with self._lock:
            if not self.path.exists():
                return {"schema_version": SCHEMA_VERSION, "signals": {}}
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"signal state file is unreadable: {self.path}") from exc
            if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported signal state schema")
            if not isinstance(payload.get("signals"), dict):
                raise ValueError("signal state has an invalid signals collection")
            return payload

    def upsert(self, signal: dict) -> bool:
        """Store a signal once. Return False for a duplicate signal ID."""
        signal_id = signal.get("signal_id") if isinstance(signal, dict) else None
        if not signal_id:
            raise ValueError("signal must contain signal_id")
        with self._lock:
            payload = self.load()
            if signal_id in payload["signals"]:
                return False
            payload["signals"][signal_id] = dict(signal)
            self._write_atomic(payload)
            return True

    def update(self, signal: dict) -> None:
        signal_id = signal.get("signal_id") if isinstance(signal, dict) else None
        if not signal_id:
            raise ValueError("signal must contain signal_id")
        with self._lock:
            payload = self.load()
            if signal_id not in payload["signals"]:
                raise KeyError(signal_id)
            payload["signals"][signal_id] = dict(signal)
            self._write_atomic(payload)

    def _write_atomic(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise



class UpstashSignalStateStore:
    """Persistent signal store backed by the Upstash Redis REST API.

    The scheduler must serialize writers (the GitHub Actions workflow does so
    with a concurrency group). Render Free local files are not durable storage.
    """

    def __init__(self, url: str, token: str, key: str = "tradingbot:signals:v1",
                 timeout: int = 10):
        if not url or not token:
            raise ValueError("Upstash REST URL and token are required")
        self.url = url.rstrip("/")
        self.token = token
        self.key = key
        self.timeout = timeout
        self._lock = threading.RLock()
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _get_raw(self):
        endpoint = f"{self.url}/get/{quote(self.key, safe='')}"
        response = requests.get(
            endpoint, headers=self._headers, timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "error" in payload:
            raise RuntimeError("Upstash GET command failed")
        return payload.get("result")

    def _set_raw(self, value: str) -> None:
        endpoint = f"{self.url}/set/{quote(self.key, safe='')}"
        response = requests.post(
            endpoint,
            data=value.encode("utf-8"),
            headers=self._headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("result") != "OK":
            raise RuntimeError("Upstash SET command failed")

    def load(self) -> dict:
        with self._lock:
            raw = self._get_raw()
            if raw is None:
                return {"schema_version": SCHEMA_VERSION, "signals": {}}
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Upstash signal state contains invalid JSON") from exc
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != SCHEMA_VERSION
                or not isinstance(payload.get("signals"), dict)
            ):
                raise ValueError("Upstash signal state schema is invalid")
            return payload

    def upsert(self, signal: dict) -> bool:
        signal_id = signal.get("signal_id") if isinstance(signal, dict) else None
        if not signal_id:
            raise ValueError("signal must contain signal_id")
        with self._lock:
            payload = self.load()
            if signal_id in payload["signals"]:
                return False
            payload["signals"][signal_id] = dict(signal)
            self._set_raw(json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ))
            return True

    def update(self, signal: dict) -> None:
        signal_id = signal.get("signal_id") if isinstance(signal, dict) else None
        if not signal_id:
            raise ValueError("signal must contain signal_id")
        with self._lock:
            payload = self.load()
            if signal_id not in payload["signals"]:
                raise KeyError(signal_id)
            payload["signals"][signal_id] = dict(signal)
            self._set_raw(json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ))
