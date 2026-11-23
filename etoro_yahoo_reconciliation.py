"""Diagnostic comparison of eToro historical chart OHLC with Yahoo daily OHLC.

This is a secondary-source comparison only. It never changes provider data,
repairs prices, approves an asset, or validates executable bid/ask parity.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping

import yfinance as yf

from config import ASSETS


PRICE_FIELDS = ("open", "high", "low", "close")


def _parse_day(value: Any) -> date:
    if not isinstance(value, str) or len(value) < 10:
        raise ValueError("invalid candle timestamp")
    return date.fromisoformat(value[:10])


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (number > 0 and number < float("inf")):
        return None
    return number


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def compare_asset_history(
    asset: str,
    etoro_history: Mapping[str, Any],
    *,
    ticker_symbol: str,
    ticker_factory=None,
    captured_at: str | None = None,
) -> dict[str, Any]:
    if etoro_history.get("history_usable") is not True:
        return {
            "status": "SKIPPED_ETORO_HISTORY_UNUSABLE",
            "ticker": ticker_symbol,
            "overlap_bars": 0,
        }
    candles = etoro_history.get("candles")
    if not isinstance(candles, list) or not candles:
        return {
            "status": "SKIPPED_NO_ETORO_CANDLES",
            "ticker": ticker_symbol,
            "overlap_bars": 0,
        }

    as_of_day = None
    if captured_at:
        captured = datetime.fromisoformat(
            captured_at[:-1] + "+00:00" if captured_at.endswith("Z") else captured_at
        )
        if captured.tzinfo is not None:
            as_of_day = captured.astimezone(timezone.utc).date()
    by_day = {}
    excluded_incomplete_bars = 0
    for candle in candles:
        day = _parse_day(candle.get("timestamp"))
        if as_of_day is not None and day >= as_of_day:
            excluded_incomplete_bars += 1
            continue
        if day in by_day:
            raise ValueError(f"{asset}: duplicate eToro date {day}")
        by_day[day] = candle
    if not by_day:
        return {
            "status": "SKIPPED_NO_COMPLETED_ETORO_BARS",
            "ticker": ticker_symbol,
            "overlap_bars": 0,
            "excluded_incomplete_bars": excluded_incomplete_bars,
        }
    start = min(by_day)
    end = max(by_day) + timedelta(days=1)
    factory = ticker_factory or yf.Ticker
    history = factory(ticker_symbol).history(
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=True,
        repair=False,
        timeout=25,
    )
    if history is None or history.empty:
        return {
            "status": "YAHOO_HISTORY_UNAVAILABLE",
            "ticker": ticker_symbol,
            "overlap_bars": 0,
        }

    yahoo_by_day = {}
    for timestamp, row in history.iterrows():
        day = timestamp.date() if hasattr(timestamp, "date") else date.fromisoformat(str(timestamp)[:10])
        yahoo_by_day[day] = row

    differences = {field: [] for field in PRICE_FIELDS}
    observations = {field: [] for field in PRICE_FIELDS}
    overlaps = 0
    missing_yahoo = 0
    invalid_yahoo = 0
    invalid_yahoo_ohlc = 0
    invalid_yahoo_examples = []
    daily_outliers = []
    for day, candle in by_day.items():
        row = yahoo_by_day.get(day)
        if row is None:
            missing_yahoo += 1
            continue
        values = {}
        yahoo_ohlc = {}
        etoro_ohlc = {}
        valid = True
        for field in PRICE_FIELDS:
            yahoo_value = _finite_positive(row.get(field.capitalize()))
            etoro_value = _finite_positive(candle.get(field))
            if yahoo_value is None or etoro_value is None:
                valid = False
                break
            yahoo_ohlc[field] = yahoo_value
            etoro_ohlc[field] = etoro_value
            values[field] = (etoro_value - yahoo_value) / yahoo_value * 100.0
        if not valid:
            invalid_yahoo += 1
            continue
        if (
            yahoo_ohlc["low"] > min(yahoo_ohlc["open"], yahoo_ohlc["close"])
            or yahoo_ohlc["high"] < max(yahoo_ohlc["open"], yahoo_ohlc["close"])
            or yahoo_ohlc["low"] > yahoo_ohlc["high"]
        ):
            invalid_yahoo_ohlc += 1
            if len(invalid_yahoo_examples) < 10:
                invalid_yahoo_examples.append({
                    "date": day.isoformat(),
                    "open": yahoo_ohlc["open"],
                    "high": yahoo_ohlc["high"],
                    "low": yahoo_ohlc["low"],
                    "close": yahoo_ohlc["close"],
                })
            continue
        for field, value in values.items():
            observations[field].append({
                "date": day.isoformat(),
                "etoro": etoro_ohlc[field],
                "yahoo": yahoo_ohlc[field],
                "signed_difference_pct": value,
            })
        overlaps += 1
        for field, value in values.items():
            differences[field].append(value)
        daily_outliers.append({
            "date": day.isoformat(),
            "max_absolute_difference_pct": max(abs(value) for value in values.values()),
            "difference_pct_by_field": values,
            "etoro_ohlc": etoro_ohlc,
            "yahoo_ohlc": yahoo_ohlc,
            "etoro_range_pct": (
                (etoro_ohlc["high"] - etoro_ohlc["low"])
                / ((etoro_ohlc["high"] + etoro_ohlc["low"]) / 2) * 100
            ),
            "yahoo_range_pct": (
                (yahoo_ohlc["high"] - yahoo_ohlc["low"])
                / ((yahoo_ohlc["high"] + yahoo_ohlc["low"]) / 2) * 100
            ),
        })

    metrics = {}
    for field, values in differences.items():
        absolute = [abs(value) for value in values]
        metrics[field] = {
            "median_signed_pct": _percentile(values, 0.50),
            "median_absolute_pct": _percentile(absolute, 0.50),
            "p95_absolute_pct": _percentile(absolute, 0.95),
            "max_absolute_pct": max(absolute) if absolute else None,
            "largest_absolute_differences": sorted(
                observations[field],
                key=lambda item: abs(item["signed_difference_pct"]),
                reverse=True,
            )[:5],
        }
    return {
        "status": "COMPARISON_ONLY_NOT_VALIDATION" if overlaps else "NO_OVERLAP",
        "ticker": ticker_symbol,
        "etoro_bars": len(by_day),
        "excluded_incomplete_etoro_bars": excluded_incomplete_bars,
        "overlap_bars": overlaps,
        "missing_yahoo_dates": missing_yahoo,
        "invalid_comparison_bars": invalid_yahoo,
        "invalid_yahoo_ohlc_bars": invalid_yahoo_ohlc,
        "invalid_yahoo_ohlc_examples": invalid_yahoo_examples,
        "largest_daily_outlier_rows": sorted(
            daily_outliers,
            key=lambda item: item["max_absolute_difference_pct"],
            reverse=True,
        )[:10],
        "first_overlap_date": min(set(by_day) & set(yahoo_by_day)).isoformat()
        if set(by_day) & set(yahoo_by_day) else None,
        "last_overlap_date": max(set(by_day) & set(yahoo_by_day)).isoformat()
        if set(by_day) & set(yahoo_by_day) else None,
        "difference_metrics_pct": metrics,
        "interpretation": (
            "Yahoo is a secondary historical OHLC reference only. Differences may reflect "
            "corporate-action adjustment, venue, session boundaries, or provider revisions. "
            "No eToro price basis or executable bid/ask parity is validated."
        ),
    }


def build_report(
    etoro_report: Mapping[str, Any],
    *,
    assets: Mapping[str, Mapping[str, Any]] | None = None,
    ticker_factory=None,
) -> dict[str, Any]:
    assets = ASSETS if assets is None else assets
    results = {}
    source_assets = etoro_report.get("assets")
    if not isinstance(source_assets, Mapping):
        raise ValueError("eToro report assets must be an object")
    for asset, record in source_assets.items():
        settings = assets.get(asset, {})
        ticker = settings.get("yahoo")
        history = record.get("historical_candles") if isinstance(record, Mapping) else None
        if not ticker or not isinstance(history, Mapping):
            results[asset] = {
                "status": "SKIPPED_MISSING_TICKER_OR_HISTORY",
                "overlap_bars": 0,
            }
            continue
        try:
            results[asset] = compare_asset_history(
                asset,
                history,
                ticker_symbol=ticker,
                ticker_factory=ticker_factory,
                captured_at=etoro_report.get("captured_at"),
            )
        except Exception as exc:
            results[asset] = {
                "status": "YAHOO_COMPARISON_ERROR",
                "ticker": ticker,
                "error": f"{type(exc).__name__}: {exc}",
                "overlap_bars": 0,
            }
    compared = sum(
        item.get("overlap_bars", 0) > 0 for item in results.values()
    )
    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "SECONDARY_PROVIDER_PRICE_COMPARISON_ONLY",
        "source": "Yahoo Finance chart OHLC via yfinance",
        "eToro_source": "eToro historical chart OHLC",
        "eToro_capture_at": etoro_report.get("captured_at"),
        "price_basis_validation": "NOT_VALIDATED",
        "executable_price_parity": "NOT_VALIDATED",
        "account_data_accessed": False,
        "order_actions_performed": False,
        "summary": {
            "assets_in_report": len(results),
            "assets_with_overlap": compared,
            "status": "COMPARISON_ONLY_NOT_VALIDATION",
        },
        "assets": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = build_report(source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"Informe guardado: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
