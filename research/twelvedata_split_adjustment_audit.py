#!/usr/bin/env python3
"""Manual, low-volume audit of Twelve Data split-adjusted OHLC around known splits.

This script makes exactly two Twelve Data time_series requests (TSLA and NVDA),
plus public Yahoo Finance chart requests. It never calls the Twelve Data /splits
endpoint and never writes verification flags back into config.py.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
OHLC_FIELDS = ("open", "high", "low", "close")
MAX_OHLC_DEVIATION_PCT = 0.75
MAX_SPLIT_BOUNDARY_CLOSE_RATIO = 1.5
MIN_COMMON_SESSIONS = 4
NEW_YORK = ZoneInfo("America/New_York")

SPLIT_CASES = {
    "TSLA": {
        "ratio": 3.0,
        "effective_date": "2022-08-25",
        "start_date": "2022-08-22",
        "end_date": "2022-08-30",
        "official_source": "https://ir.tesla.com/press-release/tesla-announces-three-one-stock-split",
    },
    "NVDA": {
        "ratio": 10.0,
        "effective_date": "2024-06-10",
        "start_date": "2024-06-06",
        "end_date": "2024-06-13",
        "official_source": "https://investor.nvidia.com/news/press-release-details/2024/NVIDIA-Announces-Financial-Results-for-First-Quarter-Fiscal-2025/",
    },
}


def _safe_json_request(url, params, label):
    try:
        response = requests.get(
            url,
            params=params,
            timeout=30,
            headers={"User-Agent": "TradingBot3-SplitAdjustmentAudit/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        # Never expose request URLs: the Twelve Data URL contains the API key.
        raise RuntimeError(f"{label} request failed ({type(exc).__name__})") from None
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} returned an invalid JSON object")
    return payload


def _normalise_currency(value):
    value = str(value or "").strip().upper()
    if value in {"US DOLLAR", "UNITED STATES DOLLAR"}:
        return "USD"
    return value


def _parse_twelvedata_payload(payload, symbol):
    if "values" not in payload or not isinstance(payload.get("meta"), dict):
        raise ValueError("Twelve Data response lacks metadata or values")
    meta = payload["meta"]
    checks = {
        "symbol": str(meta.get("symbol", "")).upper().replace(" ", "") == symbol,
        "exchange": str(meta.get("exchange", "")).strip().upper() == "NASDAQ",
        "mic_code": str(meta.get("mic_code", "")).strip().upper() == "XNAS",
        "type": str(meta.get("type", "")).strip().casefold() == "common stock",
        "currency": _normalise_currency(meta.get("currency")) == "USD",
    }
    if not all(checks.values()):
        failed = [key for key, passed in checks.items() if not passed]
        raise ValueError("Twelve Data instrument metadata mismatch: " + ", ".join(failed))

    bars = {}
    for item in payload["values"]:
        day = str(item.get("datetime", ""))[:10]
        try:
            values = {field: float(item[field]) for field in OHLC_FIELDS}
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) and value > 0 for value in values.values()):
            continue
        bars[day] = values
    if not bars:
        raise ValueError("Twelve Data returned no usable OHLC bars")
    return bars, {key: meta.get(key) for key in ("symbol", "exchange", "mic_code", "type", "currency")}


def _parse_yahoo_payload(payload):
    chart = payload.get("chart", {})
    if chart.get("error") is not None:
        raise ValueError("Yahoo Finance returned a chart error")
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        raise ValueError("Yahoo Finance returned no chart data")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote_list = result.get("indicators", {}).get("quote") or []
    if not quote_list:
        raise ValueError("Yahoo Finance returned no OHLC quote")
    quote = quote_list[0]
    bars = {}
    for index, timestamp in enumerate(timestamps):
        try:
            local_day = datetime.fromtimestamp(
                int(timestamp), timezone.utc
            ).astimezone(NEW_YORK).date().isoformat()
            values = {
                field: float(quote[field][index])
                for field in OHLC_FIELDS
            }
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) and value > 0 for value in values.values()):
            continue
        bars[local_day] = values

    split_events = []
    for event in (result.get("events", {}).get("splits") or {}).values():
        try:
            event_day = datetime.fromtimestamp(
                int(event["date"]), timezone.utc
            ).astimezone(NEW_YORK).date().isoformat()
            numerator = float(event["numerator"])
            denominator = float(event["denominator"])
            split_events.append({
                "date": event_day,
                "ratio": numerator / denominator,
                "split_ratio": event.get("splitRatio"),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return bars, split_events


def _yahoo_period_timestamp(day):
    value = datetime.combine(date.fromisoformat(day), time.min, tzinfo=timezone.utc)
    return int(value.timestamp())


def _max_ohlc_deviation_pct(twelvedata_bars, yahoo_bars):
    common = sorted(set(twelvedata_bars) & set(yahoo_bars))
    if len(common) < MIN_COMMON_SESSIONS:
        raise ValueError(
            f"Only {len(common)} common sessions; need at least {MIN_COMMON_SESSIONS}"
        )
    maximum = 0.0
    comparisons = 0
    for day in common:
        for field in OHLC_FIELDS:
            reference = yahoo_bars[day][field]
            observed = twelvedata_bars[day][field]
            deviation = abs(observed - reference) / abs(reference) * 100.0
            maximum = max(maximum, deviation)
            comparisons += 1
    return maximum, common, comparisons


def _validate_yahoo_split_event(split_events, expected_date, expected_ratio):
    for event in split_events:
        if (
            event["date"] == expected_date
            and math.isclose(event["ratio"], expected_ratio, rel_tol=0, abs_tol=1e-9)
        ):
            return event
    raise ValueError(
        f"Yahoo split event mismatch: expected {expected_ratio:g}:1 on {expected_date}"
    )


def _audit_asset(symbol, case, api_key):
    td_params = {
        "symbol": symbol,
        "exchange": "NASDAQ",
        "mic_code": "XNAS",
        "interval": "1day",
        "timezone": "UTC",
        "adjust": "splits",
        "start_date": case["start_date"],
        "end_date": case["end_date"],
        "apikey": api_key,
    }
    td_payload = _safe_json_request(TWELVE_DATA_URL, td_params, f"Twelve Data {symbol}")
    td_bars, provider_metadata = _parse_twelvedata_payload(td_payload, symbol)

    yahoo_params = {
        "period1": _yahoo_period_timestamp(case["start_date"]),
        "period2": _yahoo_period_timestamp(case["end_date"]),
        "interval": "1d",
        "events": "div,splits",
        "includeAdjustedClose": "true",
    }
    yahoo_payload = _safe_json_request(
        YAHOO_CHART_URL.format(symbol=symbol),
        yahoo_params,
        f"Yahoo Finance {symbol}",
    )
    yahoo_bars, split_events = _parse_yahoo_payload(yahoo_payload)
    split_event = _validate_yahoo_split_event(
        split_events,
        case["effective_date"],
        case["ratio"],
    )

    max_deviation, common_days, comparisons = _max_ohlc_deviation_pct(
        td_bars, yahoo_bars
    )
    if max_deviation > MAX_OHLC_DEVIATION_PCT:
        raise ValueError(
            f"OHLC cross-source deviation {max_deviation:.4f}% exceeds "
            f"{MAX_OHLC_DEVIATION_PCT:.2f}%"
        )

    effective = case["effective_date"]
    pre_days = [day for day in common_days if day < effective]
    post_days = [day for day in common_days if day >= effective]
    if not pre_days or not post_days:
        raise ValueError("Split boundary is not covered by common sessions")
    pre_day = pre_days[-1]
    post_day = post_days[0]
    pre_close = td_bars[pre_day]["close"]
    post_close = td_bars[post_day]["close"]
    boundary_ratio = max(pre_close, post_close) / min(pre_close, post_close)
    if boundary_ratio > MAX_SPLIT_BOUNDARY_CLOSE_RATIO:
        raise ValueError(
            f"Split boundary still shows a discontinuity ({boundary_ratio:.4f}x)"
        )

    return {
        "status": "PASS",
        "symbol": symbol,
        "expected_split_ratio": case["ratio"],
        "effective_date": effective,
        "official_split_source": case["official_source"],
        "yahoo_split_event": split_event,
        "provider_metadata": provider_metadata,
        "adjust_parameter": "splits",
        "common_sessions": common_days,
        "ohlc_comparisons": comparisons,
        "max_ohlc_deviation_pct": round(max_deviation, 6),
        "pre_split_session": pre_day,
        "post_split_session": post_day,
        "pre_split_adjusted_close": pre_close,
        "post_split_adjusted_close": post_close,
        "split_boundary_close_ratio": round(boundary_ratio, 6),
        "twelvedata_endpoint": TWELVE_DATA_URL,
        "yahoo_endpoint": YAHOO_CHART_URL.format(symbol=symbol),
    }


def main():
    api_key = os.getenv("TWELVE_DATA_API_KEY")
    report_path = Path("twelvedata_split_adjustment_audit.json")
    run_url = None
    repository = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if repository and run_id:
        run_url = f"https://github.com/{repository}/actions/runs/{run_id}"

    report = {
        "status": "FAIL",
        "verification_status": "NOT_VERIFIED",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "evidence_source": run_url,
        "twelvedata_time_series_requests_planned": 2,
        "twelvedata_splits_endpoint_requests": 0,
        "scope": (
            "Cross-source OHLC reconciliation around the known TSLA 3:1 and "
            "NVDA 10:1 split events only; this is not a full-history audit."
        ),
        "assets": {},
    }

    if not api_key:
        report["error"] = "TWELVE_DATA_API_KEY is not configured"
    else:
        for symbol, case in SPLIT_CASES.items():
            try:
                report["assets"][symbol] = _audit_asset(symbol, case, api_key)
            except Exception as exc:
                report["assets"][symbol] = {
                    "status": "FAIL",
                    "error": str(exc),
                }

        if report["assets"] and all(
            result.get("status") == "PASS"
            for result in report["assets"].values()
        ):
            report["status"] = "PASS"
            report["verification_status"] = "INDEPENDENTLY_VERIFIED"
            report["suggested_config_evidence"] = {
                symbol: {
                    "status": "INDEPENDENTLY_VERIFIED",
                    "source": run_url,
                    "verified_at": report["verified_at"],
                    "method": (
                        "Twelve Data adjust=splits OHLC reconciled against "
                        "Yahoo Finance split-adjusted OHLC and issuer split notice."
                    ),
                }
                for symbol in report["assets"]
            }

    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
