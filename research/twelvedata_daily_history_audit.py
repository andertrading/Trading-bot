"""Read-only five-year Twelve Data daily-history audit for the bot's ten assets.

Uses the same symbols, currency expectations and split-adjusted daily request
policy as data_engine.py. It never writes production data or places orders.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import exchange_calendars as xcals

API_URL = "https://api.twelvedata.com/time_series"
OUT = Path("twelvedata_history_audit")
OUT.mkdir(exist_ok=True)
ASSETS = {
    "TSLA": {"symbol": "TSLA", "currency": "USD", "calendar": "XNYS"},
    "NVDA": {"symbol": "NVDA", "currency": "USD", "calendar": "XNYS"},
    "AAPL": {"symbol": "AAPL", "currency": "USD", "calendar": "XNYS"},
    "MSFT": {"symbol": "MSFT", "currency": "USD", "calendar": "XNYS"},
    "TTWO": {"symbol": "TTWO", "currency": "USD", "calendar": "XNYS"},
    "BTC": {"symbol": "BTC/USD", "currency": "USD", "calendar": "24/7"},
    "ETH": {"symbol": "ETH/USD", "currency": "USD", "calendar": "24/7"},
    "SOL": {"symbol": "SOL/USD", "currency": "USD", "calendar": "24/7"},
    "XLM": {"symbol": "XLM/USD", "currency": "USD", "calendar": "24/7"},
    "XRP": {"symbol": "XRP/USD", "currency": "USD", "calendar": "24/7"},
}
OHLC = ["Open", "High", "Low", "Close"]


def parse_history(payload, spec, start, end):
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("missing_metadata")
    actual_symbol = str(meta.get("symbol", "")).upper().replace(" ", "")
    expected_symbol = spec["symbol"].upper().replace(" ", "")
    if actual_symbol != expected_symbol:
        raise ValueError(f"symbol_mismatch:{actual_symbol or 'missing'}")
    currency_field = "currency_quote" if spec["calendar"] == "24/7" else "currency"
    actual_currency = str(meta.get(currency_field, "")).upper().strip()
    if actual_currency != spec["currency"]:
        raise ValueError(f"currency_mismatch:{actual_currency or 'missing'}")
    values = payload.get("values")
    if not isinstance(values, list) or not values:
        raise ValueError("empty_history")
    frame = pd.DataFrame(values)
    required = {"datetime", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("missing_ohlc_fields")
    frame["date"] = pd.to_datetime(frame["datetime"], errors="coerce").dt.date
    if frame["date"].isna().any():
        raise ValueError("invalid_dates")
    frame = frame.set_index("date").sort_index()
    frame = frame.loc[(frame.index >= start) & (frame.index <= end)]
    duplicate_count = int(frame.index.duplicated(keep=False).sum())
    numeric = pd.DataFrame(index=frame.index)
    for col in OHLC:
        numeric[col] = pd.to_numeric(frame[col.lower()], errors="coerce")
    values_array = numeric[OHLC].to_numpy(dtype=float)
    finite = np.isfinite(values_array).all(axis=1)
    positive = (numeric[OHLC] > 0).all(axis=1).to_numpy()
    coherent = (
        (numeric["High"] >= numeric["Low"])
        & (numeric["High"] >= numeric["Open"])
        & (numeric["High"] >= numeric["Close"])
        & (numeric["Low"] <= numeric["Open"])
        & (numeric["Low"] <= numeric["Close"])
    ).to_numpy()
    invalid_mask = ~(finite & positive & coherent)
    invalid_dates = [str(day) for day in numeric.index[invalid_mask]]
    duplicate_dates = sorted({str(day) for day in frame.index[frame.index.duplicated(keep=False)]})
    if spec["calendar"] == "24/7":
        expected = set(pd.date_range(start, end, freq="D").date)
    else:
        cal = xcals.get_calendar(spec["calendar"])
        sessions = cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
        expected = {session.date() for session in sessions}
    observed = set(numeric.index)
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    return {
        "meta": meta,
        "frame": frame,
        "rows": int(len(frame)),
        "first_date": str(min(observed)) if observed else None,
        "last_date": str(max(observed)) if observed else None,
        "expected_sessions": len(expected),
        "observed_sessions": len(observed),
        "coverage_ratio": round(len(expected & observed) / len(expected), 6) if expected else None,
        "missing_dates": [str(day) for day in missing],
        "unexpected_dates": [str(day) for day in unexpected],
        "duplicate_rows": duplicate_count,
        "duplicate_dates": duplicate_dates,
        "invalid_ohlc_rows": int(invalid_mask.sum()),
        "invalid_ohlc_dates": invalid_dates,
    }


def audit_asset(session, asset, spec, token, start, end):
    params = {
        "symbol": spec["symbol"],
        "interval": "1day",
        "outputsize": 5000,
        "timezone": "UTC",
        "adjust": "splits",
        "order": "ASC",
        "apikey": token,
    }
    try:
        response = session.get(API_URL, params=params, timeout=45)
        if response.status_code >= 400:
            raise RuntimeError(f"http_{response.status_code}")
        payload = response.json()
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise RuntimeError(f"api_error_{payload.get('code', 'unknown')}")
        result = parse_history(payload, spec, start, end)
        result.pop("frame", None)
        result["asset"] = asset
        result["requested_symbol"] = spec["symbol"]
        result["expected_currency"] = spec["currency"]
        result["calendar"] = spec["calendar"]
        result["adjustment_requested"] = "splits"
        result["split_adjustment_verification"] = "REQUESTED_NOT_INDEPENDENTLY_VERIFIED"
        result["status"] = (
            "PASS"
            if result["coverage_ratio"] is not None
            and result["coverage_ratio"] >= 0.995
            and result["duplicate_rows"] == 0
            and result["invalid_ohlc_rows"] == 0
            and not result["unexpected_dates"]
            else "REVIEW"
        )
        return result
    except Exception as exc:
        return {
            "asset": asset,
            "requested_symbol": spec["symbol"],
            "calendar": spec["calendar"],
            "status": "ERROR",
            "error": str(exc),
        }


def main():
    token = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    if not token:
        print("ERROR: TWELVE_DATA_API_KEY is not configured", file=sys.stderr)
        return 2
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=5 * 365 + 2)
    session = requests.Session()
    session.headers.update({"User-Agent": "TradingBot3-TwelveDataAudit/1.0"})
    results = []
    for asset, spec in ASSETS.items():
        print(f"Auditing {asset} ...", flush=True)
        results.append(audit_asset(session, asset, spec, token, start, end))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": str(start), "end": str(end)},
        "request_policy": {
            "interval": "1day",
            "outputsize": 5000,
            "timezone": "UTC",
            "adjust": "splits",
            "production_provider_changes": False,
        },
        "assets": results,
        "summary": {
            "pass": sum(item["status"] == "PASS" for item in results),
            "review": sum(item["status"] == "REVIEW" for item in results),
            "error": sum(item["status"] == "ERROR" for item in results),
        },
    }
    path = OUT / "report.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    summary = pd.DataFrame([
        {key: item.get(key) for key in (
            "asset", "status", "rows", "first_date", "last_date",
            "expected_sessions", "observed_sessions", "coverage_ratio",
            "duplicate_rows", "invalid_ohlc_rows", "error",
        )}
        for item in results
    ])
    summary.to_csv(OUT / "summary.csv", index=False)
    print(summary.to_string(index=False))
    print(f"Report: {path}")
    return 0 if report["summary"]["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
