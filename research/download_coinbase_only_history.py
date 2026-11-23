#!/usr/bin/env python3
"""Download Coinbase Exchange-only historical OHLCV for research.

No Twelve Data/EODHD fallback, no gap repair, no synthetic bars or volume.
Missing timestamps are reported separately and are never filled.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.exchange.coinbase.com"
ASSETS = ("BTC", "ETH", "SOL", "XLM", "XRP")
GRANULARITIES = {"15m": 900, "1d": 86400}
MAX_BARS_PER_REQUEST = 299
REQUEST_PAUSE_SECONDS = 0.5
OUTPUT_DIR = Path("artifacts/coinbase_only")
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "TradingBot-Coinbase-Only-Historical-Research/1.0",
}

session = requests.Session()
session.headers.update(HEADERS)
logical_requests = 0
request_attempts = 0


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    global logical_requests, request_attempts
    logical_requests += 1
    last_error: Exception | None = None
    for attempt in range(1, 9):
        request_attempts += 1
        try:
            response = session.get(url, params=params, timeout=40)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
                except (TypeError, ValueError):
                    delay = min(2 ** attempt, 30)
                time.sleep(max(REQUEST_PAUSE_SECONDS, min(delay, 60)))
                last_error = RuntimeError(
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )
                continue
            response.raise_for_status()
            payload = response.json()
            time.sleep(REQUEST_PAUSE_SECONDS)
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 8:
                time.sleep(max(REQUEST_PAUSE_SECONDS, min(2 ** attempt, 30)))
    raise RuntimeError(f"Request failed after 8 attempts: {url} params={params}: {last_error}")


def numeric(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def aligned_period(timeframe: str, now: datetime) -> tuple[datetime, datetime, int]:
    if timeframe == "15m":
        interval = 900
        end_epoch = int(now.timestamp()) // interval * interval - interval
        end = datetime.fromtimestamp(end_epoch, tz=timezone.utc)
        start = end - timedelta(days=700) + timedelta(seconds=interval)
    else:
        interval = 86400
        end_epoch = int(now.timestamp()) // interval * interval - interval
        end = datetime.fromtimestamp(end_epoch, tz=timezone.utc)
        start = end - timedelta(days=729)
    return start, end, interval


def download_series(asset: str, timeframe: str, start: datetime, end: datetime, interval: int) -> dict[str, Any]:
    product_id = f"{asset}-USD"
    cursor = start
    rows_by_timestamp: dict[int, dict[str, Any]] = {}
    chunk_requests = 0
    malformed_items = 0
    raw_items = 0
    out_of_window_items = 0
    duplicate_timestamps = 0

    while cursor <= end:
        chunk_end = min(
            cursor + timedelta(seconds=interval * (MAX_BARS_PER_REQUEST - 1)),
            end,
        )
        payload = get_json(
            f"{BASE_URL}/products/{product_id}/candles",
            params={
                "granularity": interval,
                "start": iso_utc(cursor),
                "end": iso_utc(chunk_end),
            },
        )
        chunk_requests += 1
        if not isinstance(payload, list):
            raise RuntimeError(f"{product_id} {timeframe}: candle response is not a list")
        raw_items += len(payload)

        for item in payload:
            if not isinstance(item, (list, tuple)) or len(item) != 6:
                malformed_items += 1
                continue
            try:
                stamp = int(item[0])
            except (TypeError, ValueError, OverflowError):
                malformed_items += 1
                continue
            if stamp < int(cursor.timestamp()) or stamp > int(chunk_end.timestamp()):
                out_of_window_items += 1
                continue

            if stamp in rows_by_timestamp:
                duplicate_timestamps += 1
            low, high, open_, close, volume = [numeric(x) for x in item[1:6]]
            rows_by_timestamp[stamp] = {
                "timestamp": datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat(),
                "timestamp_epoch": stamp,
                "low": low,
                "high": high,
                "open": open_,
                "close": close,
                "volume": volume,
            }

        cursor = chunk_end + timedelta(seconds=interval)

    sorted_rows = [rows_by_timestamp[key] for key in sorted(rows_by_timestamp)]
    expected_start = int(start.timestamp())
    expected_end = int(end.timestamp())
    expected_count = (expected_end - expected_start) // interval + 1
    expected_timestamps = range(expected_start, expected_end + 1, interval)
    actual_timestamps = set(rows_by_timestamp)
    missing = [stamp for stamp in expected_timestamps if stamp not in actual_timestamps]

    zero_volume = sum(row["volume"] == 0 for row in sorted_rows)
    positive_volume = sum(row["volume"] is not None and row["volume"] > 0 for row in sorted_rows)
    invalid_volume = sum(row["volume"] is None for row in sorted_rows)
    invalid_ohlc = 0
    for row in sorted_rows:
        values = [row[k] for k in ("low", "high", "open", "close")]
        if any(v is None for v in values):
            invalid_ohlc += 1
            continue
        low, high, open_, close = values
        if low <= 0 or high <= 0 or low > high or not (low <= open_ <= high) or not (low <= close <= high):
            invalid_ohlc += 1

    series_name = f"{asset}_{timeframe}"
    csv_path = OUTPUT_DIR / f"{series_name}.csv"
    write_csv(
        csv_path,
        sorted_rows,
        ["timestamp", "timestamp_epoch", "low", "high", "open", "close", "volume"],
    )
    missing_path = OUTPUT_DIR / f"{series_name}_missing_timestamps.csv"
    write_csv(
        missing_path,
        [{"timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(), "timestamp_epoch": ts}
         for ts in missing],
        ["timestamp", "timestamp_epoch"],
    )

    return {
        "asset": asset,
        "product_id": product_id,
        "timeframe": timeframe,
        "granularity_seconds": interval,
        "start_utc": iso_utc(start),
        "end_utc": iso_utc(end),
        "expected_bars": expected_count,
        "received_bars": len(sorted_rows),
        "missing_bars": len(missing),
        "missing_pct": round(100 * len(missing) / expected_count, 6) if expected_count else None,
        "first_missing_utc": datetime.fromtimestamp(missing[0], tz=timezone.utc).isoformat() if missing else None,
        "last_missing_utc": datetime.fromtimestamp(missing[-1], tz=timezone.utc).isoformat() if missing else None,
        "zero_volume_bars": zero_volume,
        "positive_volume_bars": positive_volume,
        "invalid_or_missing_volume_bars": invalid_volume,
        "invalid_ohlc_bars": invalid_ohlc,
        "malformed_response_items": malformed_items,
        "raw_response_items": raw_items,
        "unique_timestamps": len(rows_by_timestamp),
        "duplicate_timestamps_collapsed": duplicate_timestamps,
        "out_of_window_response_items": out_of_window_items,
        "volume_units": f"{asset} base-asset units, as reported by Coinbase Exchange",
        "provider": "coinbase_exchange_only",
        "gap_repair": False,
        "synthetic_bars": False,
        "csv": str(csv_path),
        "missing_timestamps_csv": str(missing_path),
        "candle_requests": chunk_requests,
        "complete_expected_grid": len(missing) == 0 and len(sorted_rows) == expected_count,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    product_checks: list[dict[str, Any]] = []
    failures: list[str] = []

    for asset in ASSETS:
        product_id = f"{asset}-USD"
        try:
            product = get_json(f"{BASE_URL}/products/{product_id}")
            valid = (
                isinstance(product, dict)
                and str(product.get("base_currency", "")).upper() == asset
                and str(product.get("quote_currency", "")).upper() == "USD"
            )
            product_checks.append({
                "asset": asset,
                "product_id": product_id,
                "verified": valid,
                "base_currency": product.get("base_currency") if isinstance(product, dict) else None,
                "quote_currency": product.get("quote_currency") if isinstance(product, dict) else None,
                "status": product.get("status") if isinstance(product, dict) else None,
                "trading_disabled": product.get("trading_disabled") if isinstance(product, dict) else None,
            })
            if not valid:
                failures.append(f"{product_id}: product identity/quote validation failed")
        except Exception as exc:
            product_checks.append({"asset": asset, "product_id": product_id, "verified": False, "error": str(exc)})
            failures.append(f"{product_id}: product check failed: {exc}")

    for asset in ASSETS:
        if not any(x.get("asset") == asset and x.get("verified") for x in product_checks):
            continue
        for timeframe in ("15m", "1d"):
            start, end, interval = aligned_period(timeframe, now)
            try:
                result = download_series(asset, timeframe, start, end, interval)
                results.append(result)
                if result["malformed_response_items"] or result["invalid_ohlc_bars"]:
                    failures.append(
                        f"{asset} {timeframe}: malformed={result['malformed_response_items']} "
                        f"invalid_ohlc={result['invalid_ohlc_bars']}"
                    )
            except Exception as exc:
                failures.append(f"{asset} {timeframe}: {exc}")

    manifest = {
        "title": "Coinbase Exchange-only historical OHLCV research capture",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_started_at_utc": now.isoformat(),
        "git_commit": os.getenv("GITHUB_SHA"),
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "provider": "Coinbase Exchange public market data API",
        "base_url": BASE_URL,
        "source_policy": {
            "coinbase_only": True,
            "twelvedata_used": False,
            "eodhd_used": False,
            "cross_provider_gap_repair": False,
            "synthetic_candles": False,
            "missing_candles_filled": False,
            "timestamps_timezone": "UTC",
            "volume_units": "base asset units",
        },
        "request_policy": {
            "max_bars_per_request": MAX_BARS_PER_REQUEST,
            "pause_after_success_seconds": REQUEST_PAUSE_SECONDS,
            "logical_requests": logical_requests,
            "http_attempts_including_retries": request_attempts,
        },
        "product_checks": product_checks,
        "series": results,
        "failures": failures,
        "complete_series_count": sum(bool(x["complete_expected_grid"]) for x in results),
        "series_count": len(results),
    }
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    summary_lines = [
        "# Coinbase-only OHLCV capture",
        "",
        f"- Started: {now.isoformat()}",
        f"- Git commit: {manifest['git_commit']}",
        f"- Logical HTTP requests: {logical_requests}",
        f"- HTTP attempts including retries: {request_attempts}",
        "- Providers: Coinbase Exchange only",
        "- Cross-provider repairs: none",
        "- Missing candles: reported, never filled",
        "",
        "| Asset | Timeframe | Expected | Received | Missing | Zero volume | Positive volume | Invalid volume | Complete grid |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in results:
        summary_lines.append(
            f"| {item['asset']} | {item['timeframe']} | {item['expected_bars']} | "
            f"{item['received_bars']} | {item['missing_bars']} | {item['zero_volume_bars']} | "
            f"{item['positive_volume_bars']} | {item['invalid_or_missing_volume_bars']} | "
            f"{'yes' if item['complete_expected_grid'] else 'no'} |"
        )
    summary_lines += ["", "## Failures", ""]
    summary_lines += [f"- {x}" for x in failures] if failures else ["- None"]
    (OUTPUT_DIR / "audit_summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "series_count": len(results),
        "complete_series_count": manifest["complete_series_count"],
        "logical_requests": logical_requests,
        "http_attempts": request_attempts,
        "failures": failures,
        "output": str(OUTPUT_DIR),
    }, indent=2))

    return 1 if failures or len(results) != len(ASSETS) * len(GRANULARITIES) else 0


if __name__ == "__main__":
    sys.exit(main())
