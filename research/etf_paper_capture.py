"""Capture validated CSPX/CNDX daily bars into a dedicated paper-data store.

Public EODHD market data only. No eToro API, account, portfolio or order access.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import ASSETS
from data_engine import _has_daily_gaps, _remove_incomplete_candle
from eodhd_provider import download_eodhd_daily
from paper_market_data import (
    PAPER_MARKET_ASSETS,
    PAPER_MARKET_DATA_KEY,
    empty_market_data_state,
    merge_asset_bars,
    validate_market_data_state,
)

OUTPUT_DIR = Path("etf_paper_capture")
OUTPUT_DIR.mkdir(exist_ok=True)


def _upstash_request(method, url, *, headers, timeout, data=None):
    try:
        response = requests.request(
            method, url, headers=headers, data=data, timeout=timeout
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        raise RuntimeError("Upstash request failed; response details suppressed") from None
    except ValueError:
        raise RuntimeError("Upstash returned invalid JSON") from None
    if not isinstance(payload, dict) or "error" in payload:
        raise RuntimeError("Upstash command failed")
    return payload


def load_state(url, token):
    endpoint = f"{url.rstrip('/')}/get/{PAPER_MARKET_DATA_KEY}"
    payload = _upstash_request(
        "GET", endpoint,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    raw = payload.get("result")
    if raw is None:
        return empty_market_data_state()
    try:
        state = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("Stored paper market-data JSON is invalid") from None
    return validate_market_data_state(state)


def save_state(url, token, state):
    encoded = json.dumps(
        state, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    endpoint = f"{url.rstrip('/')}/set/{PAPER_MARKET_DATA_KEY}"
    payload = _upstash_request(
        "POST", endpoint,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=20,
        data=encoded.encode("utf-8"),
    )
    if payload.get("result") != "OK":
        raise RuntimeError("Upstash did not confirm paper-data persistence")
    return len(encoded.encode("utf-8"))


def main():
    api_key = os.getenv("EODHD_API_KEY", "").strip()
    upstash_url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
    upstash_token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if not api_key or not upstash_url or not upstash_token:
        raise SystemExit("Missing required EODHD/Upstash credentials; values remain hidden.")

    state = load_state(upstash_url, upstash_token)
    captured_at = datetime.now(timezone.utc).isoformat()
    additions = {}
    report = {
        "mode": "PUBLIC_MARKET_DATA_PAPER_ONLY",
        "source": "EODHD",
        "calendar": "XLON",
        "captured_at": captured_at,
        "assets": {},
        "account_data_accessed": False,
        "orders_or_execution": False,
    }

    for asset in PAPER_MARKET_ASSETS:
        frame = download_eodhd_daily(
            asset,
            ASSETS[asset]["currency"],
            _remove_incomplete_candle,
            _has_daily_gaps,
        )
        if frame is None or frame.empty:
            raise RuntimeError(f"{asset}: validated EODHD daily history unavailable")
        bars = []
        for timestamp, row in frame.iterrows():
            bars.append({
                "date": timestamp.date().isoformat(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
            })
        state, added = merge_asset_bars(
            state, asset, bars, captured_at=captured_at
        )
        additions[asset] = added
        report["assets"][asset] = {
            "symbol": f"{asset}.LSE",
            "bars_available": len(bars),
            "new_bars_appended": added,
            "first_session": bars[0]["date"],
            "last_session": bars[-1]["date"],
            "price_adjustment_policy": frame.attrs.get(
                "price_adjustment_policy", "split_adjusted_ohlc_no_dividend_adjustment"
            ),
        }

    validate_market_data_state(state)
    stored_bytes = save_state(upstash_url, upstash_token, state)
    report["new_bars_appended_total"] = sum(additions.values())
    report["stored_state_bytes"] = stored_bytes
    report["stored_bar_counts"] = {
        asset: len(state["assets"][asset]["bars"])
        for asset in PAPER_MARKET_ASSETS
    }
    output = OUTPUT_DIR / "capture_report.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
