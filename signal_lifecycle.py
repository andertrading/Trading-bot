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

def _policy_number(value: Any, field: str, *, maximum: float | None = None) -> float:
    """Parse a finite positive policy value without accepting booleans."""
    number = _positive_number(value, field)
    if maximum is not None and number > maximum:
        raise ValueError(f"{field} must be in (0, {maximum}]")
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
    minimum_rr = _policy_number(
        result.get("minimum_risk_reward", 2.0), "minimum_risk_reward"
    )
    minimum_remaining = _policy_number(
        result.get("minimum_remaining_risk_fraction", 0.5),
        "minimum_remaining_risk_fraction",
        maximum=1.0,
    )

    record = {
        "schema_version": SCHEMA_VERSION,
        "signal_id": signal_id,
        "asset": str(result.get("asset", "")).upper(),
        "timeframe": str(result.get("timeframe", "")),
        "direction": direction,
        "status": "PENDING",
        "monitor_status": "ACTIVE",
        "generated_at": generated.isoformat(),
        "expires_at": expiry.isoformat(),
        "monitoring_expires_at": (
            expiry + (
                timedelta(hours=8)
                if str(result.get("timeframe", "")) == "15m"
                else timedelta(days=90)
            )
        ).isoformat(),
        "validity_minutes": effective_validity_minutes,
        "minimum_risk_reward": minimum_rr,
        "minimum_remaining_risk_fraction": minimum_remaining,
        "data_timestamp": _as_utc(candle_timestamp).isoformat(),
        "entry_reference": entry,
        "stop": stop,
        "target": target,
        "risk_reward": result.get("risk_reward"),
        "potential_reward_pct": round(reward_pct, 4),
        "potential_risk_pct": round(risk_pct, 4),
        "data_provider": result.get("data_provider", "unknown"),
        "price_adjustment_policy": result.get(
            "price_adjustment_policy", "unknown"
        ),
        "currency": result.get("currency", "USD"),
        "score": result.get("score"),
        "reasons": list(result.get("reasons") or []),
        "validation_reasons": list(result.get("validation_reasons") or []),
        "provisional_risk_pct": result.get("provisional_risk_pct"),
        "data_age_minutes": result.get("data_age_minutes"),
        "alert_sent": False,
        "pending_notification_event": None,
        "probability": None,
        "probability_status": "NOT_CALIBRATED",
        "position_status": "UNKNOWN",
        "last_event": "SIGNAL_CREATED",
    }
    return record


def _entry_zone_is_valid(signal: dict, price: float) -> bool:
    direction = str(signal.get("direction", "LONG")).upper()
    entry = _positive_number(signal.get("entry_reference"), "entry_reference")
    stop = _positive_number(signal.get("stop"), "stop")
    target = _positive_number(signal.get("target"), "target")
    minimum_rr = _policy_number(
        signal.get("minimum_risk_reward", 2.0), "minimum_risk_reward"
    )
    minimum_remaining = _policy_number(
        signal.get("minimum_remaining_risk_fraction", 0.5),
        "minimum_remaining_risk_fraction",
        maximum=1.0,
    )

    if direction == "LONG":
        planned_risk = entry - stop
        remaining_risk = price - stop
        reward = target - price
    elif direction == "SHORT":
        planned_risk = stop - entry
        remaining_risk = stop - price
        reward = price - target
    else:
        raise ValueError("unsupported signal direction")

    if planned_risk <= 0 or remaining_risk <= 0 or reward <= 0:
        return False

    remaining_fraction = remaining_risk / planned_risk
    current_rr = reward / remaining_risk

    return (
        remaining_fraction + 1e-9 >= minimum_remaining
        and current_rr + 1e-6 >= minimum_rr
    )


def advance_signal(
    signal: dict,
    now: datetime | str,
    latest_price: float | None = None,
    conditions_valid: bool = True,
    latest_high: float | None = None,
    latest_low: float | None = None,
    latest_data_timestamp: datetime | str | None = None,
) -> tuple[dict, str | None]:
    """
    Advance entry validity and market-level monitoring independently.

    An expired/invalidated entry remains monitored for stop/target touches,
    because the bot cannot know whether the user entered manually.
    """
    if not isinstance(signal, dict):
        return {}, None

    updated = dict(signal)
    current = _as_utc(now)
    entry_status = updated.get("status", "PENDING")
    monitor_status = updated.get("monitor_status")

    if monitor_status is None:
        monitor_status = (
            "ACTIVE"
            if entry_status in {"PENDING", "EXPIRED", "INVALIDATED"}
            else "ENDED"
        )
        updated["monitor_status"] = monitor_status

    if monitor_status != "ACTIVE":
        return updated, None

    expires = _as_utc(updated["expires_at"])
    monitoring_expiry_raw = updated.get("monitoring_expires_at")
    if monitoring_expiry_raw:
        monitoring_expires = _as_utc(monitoring_expiry_raw)
    else:
        monitoring_window = (
            timedelta(hours=8)
            if updated.get("timeframe") == "15m"
            else timedelta(days=90)
        )
        monitoring_expires = expires + monitoring_window
        updated["monitoring_expires_at"] = monitoring_expires.isoformat()

    monitoring_expired = current >= monitoring_expires
    expired_now = entry_status == "PENDING" and current >= expires
    event = None

    # Never evaluate the candle that created the signal: its high/low
    # happened before the alert could be acted on. Only later completed
    # candles may advance the signal lifecycle.
    price_data_is_new = True
    signal_data_timestamp = updated.get("data_timestamp")
    last_market_timestamp = updated.get("last_market_data_timestamp")
    if latest_data_timestamp is not None:
        incoming_timestamp = _as_utc(latest_data_timestamp)
        reference_timestamps = [
            _as_utc(value)
            for value in (signal_data_timestamp, last_market_timestamp)
            if value
        ]
        # A candle timestamp later than the scan clock is not completed data.
        # Ignore it rather than letting a provider clock error advance state.
        if incoming_timestamp > current:
            price_data_is_new = False
        else:
            # Ignore duplicate or out-of-order candles. Without this guard, a
            # delayed provider response can invalidate a live entry using an old
            # stop/target touch that predates the most recently processed candle.
            price_data_is_new = not reference_timestamps or (
                incoming_timestamp > max(reference_timestamps)
            )
        if price_data_is_new and (
            latest_price is not None
            or latest_high is not None
            or latest_low is not None
        ):
            updated["last_market_data_timestamp"] = incoming_timestamp.isoformat()

    # Use the completed candle's high/low so intrabar stop/target touches
    # are not missed merely because the candle later closed back inside.
    # Validate the OHLC envelope before accepting its timestamp as processed.
    high = None
    low = None
    if price_data_is_new and (latest_high is not None or latest_low is not None):
        if (latest_high is None) != (latest_low is None):
            raise ValueError("latest_high and latest_low must be supplied together")
        fallback = (
            _positive_number(latest_price, "latest_price")
            if latest_price is not None
            else None
        )
        high = (
            _positive_number(latest_high, "latest_high")
            if latest_high is not None
            else fallback
        )
        low = (
            _positive_number(latest_low, "latest_low")
            if latest_low is not None
            else fallback
        )
        if high is not None and low is not None and high < low:
            raise ValueError("latest_high must be greater than or equal to latest_low")
        if latest_price is not None:
            close = _positive_number(latest_price, "latest_price")
            if high is not None and close > high:
                raise ValueError("latest_price must not exceed latest_high")
            if low is not None and close < low:
                raise ValueError("latest_price must not be below latest_low")
    elif price_data_is_new and latest_price is not None:
        price = _positive_number(latest_price, "latest_price")
        high = price
        low = price

    if price_data_is_new and latest_data_timestamp is not None and (
        latest_price is not None or latest_high is not None or latest_low is not None
    ):
        updated["last_market_data_timestamp"] = _as_utc(
            latest_data_timestamp
        ).isoformat()

    direction = str(updated.get("direction", "LONG")).upper()
    stop = _positive_number(updated.get("stop"), "stop")
    target = _positive_number(updated.get("target"), "target")

    stop_hit = False
    target_hit = False
    if high is not None and low is not None:
        if direction == "LONG":
            stop_hit = low <= stop
            target_hit = high >= target
        elif direction == "SHORT":
            stop_hit = high >= stop
            target_hit = low <= target
        else:
            raise ValueError("unsupported signal direction")

    # Conservative ordering: if both levels were touched in one candle,
    # assume the stop happened first.
    if stop_hit:
        updated["monitor_status"] = "STOP_TOUCHED"
        if entry_status == "PENDING":
            updated["status"] = "EXPIRED" if expired_now else "INVALIDATED"
        event = "SIGNAL_INVALIDATED_STOP_LEVEL"
    elif target_hit:
        updated["monitor_status"] = "TARGET_TOUCHED"
        if entry_status == "PENDING":
            updated["status"] = "EXPIRED" if expired_now else "TARGET_REACHED"
        event = "TARGET_LEVEL_REACHED"
    elif monitoring_expired:
        updated["monitor_status"] = "ENDED"
        if entry_status == "PENDING" and expired_now:
            updated["status"] = "EXPIRED"
            event = "SIGNAL_EXPIRED"
        else:
            updated["last_checked_at"] = current.isoformat()
            updated["last_event"] = "SIGNAL_MONITORING_ENDED"
            updated["position_status"] = "UNKNOWN"
            return updated, None
    elif entry_status == "PENDING":
        if expired_now:
            updated["status"] = "EXPIRED"
            event = "SIGNAL_EXPIRED"
        elif price_data_is_new and not conditions_valid:
            updated["status"] = "INVALIDATED"
            event = "SIGNAL_INVALIDATED"
        elif price_data_is_new and latest_price is not None:
            price = _positive_number(latest_price, "latest_price")
            if not _entry_zone_is_valid(updated, price):
                updated["status"] = "INVALIDATED"
                event = "SIGNAL_ENTRY_ZONE_INVALIDATED"

    if event is None:
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
        endpoint = f"{self.url}/get/{quote(self.key, safe=':')}"
        response = requests.get(
            endpoint, headers=self._headers, timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "error" in payload:
            raise RuntimeError("Upstash GET command failed")
        return payload.get("result")

    def _set_raw(self, value: str) -> None:
        endpoint = f"{self.url}/set/{quote(self.key, safe=':')}"
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

    def prune(self, now=None, retention_days: int = 90,
              max_terminal_records: int = 10000) -> int:
        """Bound state growth while preserving every pending signal."""
        if isinstance(retention_days, bool) or retention_days <= 0:
            raise ValueError("retention_days must be positive")
        if isinstance(max_terminal_records, bool) or max_terminal_records <= 0:
            raise ValueError("max_terminal_records must be positive")

        current = _as_utc(now or datetime.now(timezone.utc))
        cutoff = current - timedelta(days=retention_days)

        with self._lock:
            payload = self.load()
            signals = payload["signals"]
            removed = 0
            terminal = []

            for signal_id, signal in list(signals.items()):
                if signal.get("status") not in TERMINAL_STATUSES:
                    continue
                terminal_at = signal.get("last_checked_at") or signal.get("generated_at")
                try:
                    terminal_time = _as_utc(terminal_at)
                except (TypeError, ValueError):
                    terminal_time = current
                if terminal_time < cutoff:
                    del signals[signal_id]
                    removed += 1
                else:
                    terminal.append((terminal_time, signal_id))

            if len(terminal) > max_terminal_records:
                terminal.sort(reverse=True)
                for _, signal_id in terminal[max_terminal_records:]:
                    del signals[signal_id]
                    removed += 1

            if removed:
                self._set_raw(json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                ))
            return removed
