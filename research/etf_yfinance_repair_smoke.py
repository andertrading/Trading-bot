"""Live-network smoke test of the exact extended Yahoo path used by backtests.

Public market data only. No eToro access, account data, portfolio, orders, or signals.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

os.environ["BACKTEST_EXTENDED_DAILY_HISTORY"] = "true"
os.environ["BACKTEST_DAILY_LOOKBACK_DAYS"] = "1825"

from config import ASSETS
from data_engine import _download_yahoo, _invalid_ohlc_mask, _is_trading_day, _remove_incomplete_candle, _has_daily_gaps
from eodhd_provider import download_eodhd_daily

OUTPUT = Path("etf_yfinance_repair_smoke")
OUTPUT.mkdir(exist_ok=True)


def main():
    results = []
    errors = []
    for asset in ("CSPX", "CNDX"):
        try:
            frame = _download_yahoo(asset, ASSETS[asset], "1d")
            if frame is None or frame.empty:
                raise RuntimeError("production extended-history helper returned no data")
            invalid = int(_invalid_ohlc_mask(frame).sum())
            if invalid:
                raise RuntimeError(f"production helper returned {invalid} invalid OHLC rows")
            results.append({
                "asset": asset,
                "rows": int(len(frame)),
                "first_session": str(frame.index.min()),
                "last_session": str(frame.index.max()),
                "invalid_ohlc_after_repair": invalid,
                "repair_count": int(frame.attrs.get("ohlc_repair_count", 0) or 0),
                "repair_source_ticker": frame.attrs.get("ohlc_repair_source_ticker"),
                "repair_max_close_deviation_pct": frame.attrs.get(
                    "ohlc_repair_max_close_deviation_pct"
                ),
                "price_source_detail": frame.attrs.get("price_source_detail"),
                "status": "PASS",
            })
        except Exception as exc:
            errors.append(f"{asset}: {type(exc).__name__}: {exc}")
            results.append({"asset": asset, "status": "FAIL", "error": errors[-1]})
    eodhd_results = []
    eodhd_errors = []
    for asset in ("CSPX", "CNDX"):
        try:
            frame = download_eodhd_daily(
                asset,
                ASSETS[asset]["currency"],
                _remove_incomplete_candle,
                _has_daily_gaps,
            )
            if frame is None or frame.empty:
                raise RuntimeError("production EODHD daily helper returned no data")
            out_of_session = [
                str(timestamp.date())
                for timestamp in frame.index
                if not _is_trading_day(timestamp.date(), asset)
            ]
            if out_of_session:
                raise RuntimeError(
                    f"EODHD helper returned non-session bars: {out_of_session}"
                )
            eodhd_results.append({
                "asset": asset,
                "rows": int(len(frame)),
                "first_session": str(frame.index.min()),
                "last_session": str(frame.index.max()),
                "non_session_bars_after_filter": 0,
                "status": "PASS",
            })
        except Exception as exc:
            eodhd_errors.append(f"{asset}: {type(exc).__name__}: {exc}")
            eodhd_results.append({
                "asset": asset,
                "status": "FAIL",
                "error": eodhd_errors[-1],
            })
    report = {
        "scope": "actual_data_engine_extended_yahoo_and_eodhd_daily_download",
        "requested_lookback_days": 1825,
        "eToro_account_or_portfolio_accessed": False,
        "orders_or_signals_enabled": False,
        "results": results,
        "errors": errors,
        "eodhd_results": eodhd_results,
        "eodhd_errors": eodhd_errors,
        "eodhd_overall_pass": len(eodhd_results) == 2 and not eodhd_errors
            and all(item.get("status") == "PASS" for item in eodhd_results),
        "overall_pass": len(results) == 2 and not errors
            and all(item.get("status") == "PASS" for item in results),
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, default=str))
    if not report["overall_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
