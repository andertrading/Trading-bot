"""Live-network smoke test of the exact extended Yahoo path used by backtests.

Public market data only. No eToro access, account data, portfolio, orders, or signals.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["BACKTEST_EXTENDED_DAILY_HISTORY"] = "true"
os.environ["BACKTEST_DAILY_LOOKBACK_DAYS"] = "1825"

from config import ASSETS
from data_engine import _download_yahoo, _invalid_ohlc_mask

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
    report = {
        "scope": "actual_data_engine_extended_yahoo_daily_download",
        "requested_lookback_days": 1825,
        "eToro_account_or_portfolio_accessed": False,
        "orders_or_signals_enabled": False,
        "results": results,
        "errors": errors,
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
