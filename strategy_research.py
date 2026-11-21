"""Research-only comparison of fixed strategy hypotheses on immutable snapshots.

No parameter grid search, no execution, and no changes to production strategy.
All entries use the same next-bar execution, stop/target, risk and cost model.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import backtest_v3 as bt
import strategies


def _number(row, key):
    try:
        value = float(row[key])
        return value if math.isfinite(value) else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _candidate(data, asset, timeframe, index, reasons):
    row = data.iloc[index]
    price = _number(row, "Close")
    atr = _number(row, "ATR")
    if price is None or atr is None or price <= 0 or atr <= 0:
        return {"action": "NO TRADE"}

    stop = price - strategies.STOP_ATR * atr
    target = price + strategies.TARGET_ATR * atr
    if stop <= 0 or target <= price:
        return {"action": "NO TRADE"}

    return {
        "action": "BUY CANDIDATE",
        "asset": asset,
        "timeframe": timeframe,
        "direction": "LONG",
        "entry_reference": round(price, 8),
        "stop": round(stop, 8),
        "target": round(target, 8),
        "risk_reward": round((target - price) / (price - stop), 4),
        "score": 85,
        "reasons": list(reasons),
        "status": "PENDING_VALIDATION",
        "tradable": False,
        "validated": False,
    }


def baseline(data, asset, timeframe, index=-1):
    signal = strategies.evaluate_signal_at(data, asset, timeframe, index)
    if signal.get("action") == "BUY CANDIDATE":
        # Equalize portfolio risk tier across all research hypotheses.
        signal = dict(signal)
        signal["score"] = 85
    return signal


def trend_pullback_reclaim(data, asset, timeframe, index=-1):
    """Uptrend + completed candle reclaiming EMA20 after a pullback."""
    if index < 1:
        return {"action": "NO TRADE"}
    row = data.iloc[index]
    previous = data.iloc[index - 1]
    required = ("Close", "Low", "EMA20", "EMA50", "EMA200", "RSI",
                "MACD", "MACD_SIGNAL", "ATR", "ATR_PERCENT")
    values = {key: _number(row, key) for key in required}
    previous_values = {
        key: _number(previous, key)
        for key in ("Close", "EMA20", "RSI")
    }
    if any(value is None for value in values.values()):
        return {"action": "NO TRADE"}
    if any(value is None for value in previous_values.values()):
        return {"action": "NO TRADE"}

    trend = values["EMA50"] > values["EMA200"] and values["Close"] > values["EMA200"]
    reclaim = (
        previous_values["Close"] <= previous_values["EMA20"]
        and values["Close"] > values["EMA20"]
        and values["Low"] <= values["EMA20"] + 0.20 * values["ATR"]
    )
    momentum = (
        45 <= values["RSI"] <= 65
        and values["RSI"] > previous_values["RSI"]
        and values["MACD"] > values["MACD_SIGNAL"]
    )
    if not (trend and reclaim and momentum and values["ATR_PERCENT"] <= strategies.MAX_ATR_PERCENT):
        return {"action": "NO TRADE"}

    return _candidate(
        data, asset, timeframe, index,
        ["EMA50 sobre EMA200", "Reclaim EMA20 tras pullback",
         "RSI ascendente", "MACD alcista"],
    )


def trend_continuation(data, asset, timeframe, index=-1):
    """Aligned EMA trend + momentum continuation, without a breakout trigger."""
    row = data.iloc[index]
    required = ("Close", "EMA20", "EMA50", "EMA200", "RSI",
                "MACD", "MACD_SIGNAL", "ATR", "ATR_PERCENT")
    values = {key: _number(row, key) for key in required}
    if any(value is None for value in values.values()):
        return {"action": "NO TRADE"}

    aligned = (
        values["EMA20"] > values["EMA50"] > values["EMA200"]
        and values["Close"] > values["EMA20"]
    )
    momentum = (
        52 <= values["RSI"] <= 68
        and values["MACD"] > values["MACD_SIGNAL"]
    )
    if not (aligned and momentum and values["ATR_PERCENT"] <= strategies.MAX_ATR_PERCENT):
        return {"action": "NO TRADE"}

    return _candidate(
        data, asset, timeframe, index,
        ["EMA20/50/200 alineadas", "Precio sobre EMA20",
         "RSI en zona de momentum", "MACD alcista"],
    )


def mtf_daily_trend_pullback(data, asset, timeframe, index=-1):
    """M15 pullback only when the last completed daily trend is bullish."""
    if timeframe != "15m":
        return {"action": "NO TRADE"}
    row = data.iloc[index]
    if row.get("DAILY_TREND_UP") is not True and row.get("DAILY_TREND_UP") != 1:
        return {"action": "NO TRADE"}
    return trend_pullback_reclaim(data, asset, timeframe, index)


STRATEGIES = {
    "baseline": baseline,
    "trend_pullback_reclaim": trend_pullback_reclaim,
    "trend_continuation": trend_continuation,
    "mtf_daily_trend_pullback": mtf_daily_trend_pullback,
}



def _snapshot_coverage(assets, timeframes, manifest):
    """Compare immutable snapshots with the configured asset/timeframe universe."""
    if not isinstance(manifest, list):
        raise ValueError("Snapshot manifest must be a list")

    available = set()
    for item in manifest:
        if not isinstance(item, dict):
            raise ValueError("Snapshot manifest contains a non-object entry")
        asset = str(item.get("asset", "")).upper()
        timeframe = str(item.get("timeframe", "")).lower()
        if not asset or not timeframe:
            raise ValueError("Snapshot manifest entry lacks asset/timeframe")
        key = (asset, timeframe)
        if key in available:
            raise ValueError(f"Duplicate snapshot manifest entry: {asset} {timeframe}")
        available.add(key)

    expected = {
        (str(asset).upper(), str(timeframe).lower())
        for asset, _ in assets
        for timeframe in timeframes
    }
    rows = []
    for asset, timeframe in sorted(expected):
        present = (asset, timeframe) in available
        rows.append({
            "asset": asset,
            "timeframe": timeframe,
            "status": "AVAILABLE" if present else "MISSING",
        })

    unexpected = sorted(available - expected)
    for asset, timeframe in unexpected:
        rows.append({
            "asset": asset,
            "timeframe": timeframe,
            "status": "UNEXPECTED",
        })

    missing = sorted(expected - available)
    return rows, {
        "status": "COMPLETE" if not missing and not unexpected else "PARTIAL",
        "expected_combinations": len(expected),
        "available_combinations": len(expected & available),
        "missing_combinations": [
            {"asset": asset, "timeframe": timeframe}
            for asset, timeframe in missing
        ],
        "unexpected_combinations": [
            {"asset": asset, "timeframe": timeframe}
            for asset, timeframe in unexpected
        ],
    }


def _metrics_row(strategy_name, asset, timeframe, period, trades):
    metrics = bt.calculate_metrics(trades)
    return {
        "strategy": strategy_name,
        "asset": asset,
        "timeframe": timeframe,
        "period": period,
        **metrics,
    }


def _utc_timestamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("invalid history boundary")
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _chronological_period_bounds(history_start, history_end):
    """Return common calendar-based early/OOS boundaries for one data series."""
    if history_start is None or history_end is None:
        return None
    try:
        start_time = _utc_timestamp(history_start)
        end_time = _utc_timestamp(history_end)
    except (TypeError, ValueError, OverflowError):
        return None
    if end_time <= start_time:
        return None

    early_end = start_time + (end_time - start_time) * 0.5
    oos_duration = (end_time - early_end) / 3
    folds = []
    for fold in range(1, 4):
        fold_start = early_end + oos_duration * (fold - 1)
        fold_end = end_time if fold == 3 else early_end + oos_duration * fold
        folds.append((fold_start, fold_end))
    return start_time, early_end, end_time, folds


def _out_of_sample_trades(trades, history_start, history_end):
    """Select trades after the shared first-half calendar cutoff."""
    bounds = _chronological_period_bounds(history_start, history_end)
    if bounds is None:
        return []
    _, early_end, _, _ = bounds
    selected = []
    for trade in trades or []:
        try:
            entry_time = _utc_timestamp(trade["entry_time"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if entry_time > early_end:
            selected.append(dict(trade))
    return selected


def _walk_forward_rows(
    strategy_name, trades, asset, timeframe, history_start, history_end
):
    """Early diagnostic plus three fixed calendar-based chronological OOS windows.

    Boundaries come from the complete price history, not the first/last trade.
    This keeps the same asset/timeframe split identical across all strategies,
    including strategies that generate very few trades.
    """
    bounds = _chronological_period_bounds(history_start, history_end)
    if bounds is None:
        periods = [
            "EARLY_HISTORY_DIAGNOSTIC",
            "OUT_OF_SAMPLE_FOLD_1",
            "OUT_OF_SAMPLE_FOLD_2",
            "OUT_OF_SAMPLE_FOLD_3",
        ]
        rows = [
            _metrics_row(strategy_name, asset, timeframe, period, [])
            for period in periods
        ]
        for row in rows:
            row.update({
                "fold_start_utc": None,
                "fold_end_utc": None,
                "sample_size_sufficient": False,
                "probability_calibrated": False,
                "history_bounds_valid": False,
            })
        return rows

    start_time, early_end, end_time, folds = bounds
    frame = pd.DataFrame(trades or [])
    if not frame.empty and "entry_time" in frame:
        frame["_entry_time"] = pd.to_datetime(
            frame["entry_time"], utc=True, errors="coerce"
        )
        frame = frame.dropna(subset=["_entry_time"]).sort_values("_entry_time")
    else:
        frame = pd.DataFrame(columns=["_entry_time"])

    early = (
        frame.loc[frame["_entry_time"] <= early_end]
        .drop(columns=["_entry_time"], errors="ignore")
        .to_dict("records")
    )
    rows = [_metrics_row(
        strategy_name, asset, timeframe, "EARLY_HISTORY_DIAGNOSTIC", early
    )]
    rows[0].update({
        "fold_start_utc": start_time.isoformat(),
        "fold_end_utc": early_end.isoformat(),
        "sample_size_sufficient": len(early) >= 100,
        "probability_calibrated": False,
        "history_bounds_valid": True,
    })

    for fold_number, (fold_start, fold_end) in enumerate(folds, start=1):
        if frame.empty:
            fold_trades = []
        else:
            mask = (
                (frame["_entry_time"] > early_end)
                & (frame["_entry_time"] >= fold_start)
                & (
                    (frame["_entry_time"] <= fold_end)
                    if fold_number == 3
                    else (frame["_entry_time"] < fold_end)
                )
            )
            fold_trades = frame.loc[mask].drop(
                columns=["_entry_time"], errors="ignore"
            ).to_dict("records")
        row = _metrics_row(
            strategy_name, asset, timeframe,
            f"OUT_OF_SAMPLE_FOLD_{fold_number}", fold_trades
        )
        row.update({
            "fold_start_utc": fold_start.isoformat(),
            "fold_end_utc": fold_end.isoformat(),
            "sample_size_sufficient": len(fold_trades) >= 100,
            "probability_calibrated": False,
            "history_bounds_valid": True,
        })
        rows.append(row)
    return rows


def main():
    assets = bt.get_assets()
    timeframes = bt.get_timeframes()
    all_summary = []
    all_walk_forward = []
    all_trades = []
    all_errors = []
    portfolio_rows = []
    oos_portfolio_rows = []
    history_bounds = {}

    if bt.BACKTEST_DATA_MODE != "replay":
        raise SystemExit("Research requires BACKTEST_DATA_MODE=replay")

    manifest_path = bt.SNAPSHOT_DIR / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"Snapshot manifest missing: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as manifest_file:
        snapshot_manifest = json.load(manifest_file)
    coverage_rows, coverage_summary = _snapshot_coverage(
        assets, timeframes, snapshot_manifest
    )
    coverage_summary.update({
        "oos_calendar_policy": "PER_ASSET_TIMEFRAME_HISTORY_BOUNDS",
        "oos_portfolio_calendar_comparable": False,
        "oos_portfolio_note": (
            "The aggregate OOS portfolio combines asset/timeframe series "
            "whose history windows and calendar cutoffs differ. Its return "
            "must not be interpreted as a single synchronized portfolio "
            "backtest until a common calendar window is available."
        ),
    })
    pd.DataFrame(coverage_rows).to_csv(
        "strategy_research_coverage.csv", index=False
    )
    with open("strategy_research_status.json", "w", encoding="utf-8") as status_file:
        json.dump(coverage_summary, status_file, ensure_ascii=False, indent=2)
    print(
        "UNIVERSE COVERAGE:",
        coverage_summary["status"],
        f'{coverage_summary["available_combinations"]}/'
        f'{coverage_summary["expected_combinations"]}',
    )
    if coverage_summary["missing_combinations"]:
        print("MISSING SNAPSHOTS:", coverage_summary["missing_combinations"])

    print("RESEARCH ONLY — no live alerts, no orders, no parameter optimization")
    print("Assets:", ", ".join(asset for asset, _ in assets))
    print("Timeframes:", ", ".join(timeframes))
    print("Snapshot directory:", bt.SNAPSHOT_DIR)

    # Add a completed-daily-trend filter to M15 rows. A daily candle only
    # becomes available from the following UTC date, preventing lookahead.
    original_download_history = bt.download_history
    daily_cache = {}

    def download_history_with_daily_trend(ticker, interval, asset=None):
        data = original_download_history(ticker, interval, asset=asset)
        if data is not None and not data.empty and asset is not None:
            valid_index = pd.to_datetime(data.index, utc=True, errors="coerce")
            valid_index = valid_index[~valid_index.isna()]
            if len(valid_index):
                history_bounds.setdefault(
                    (asset, interval),
                    (valid_index.min(), valid_index.max()),
                )
        if data is None or interval != "15m" or asset is None:
            return data

        if asset not in daily_cache:
            daily = original_download_history(ticker, "1d", asset=asset)
            if daily is None or daily.empty:
                daily_cache[asset] = None
            else:
                daily_indicators = bt.add_indicators(daily.copy())
                available = pd.DataFrame({
                    "available_from": (
                        daily_indicators.index.normalize()
                        + pd.Timedelta(days=1)
                    ),
                    "daily_trend_up": (
                        daily_indicators["EMA50"]
                        > daily_indicators["EMA200"]
                    ).fillna(False).to_numpy(),
                }).sort_values("available_from")
                daily_cache[asset] = available

        available = daily_cache[asset]
        if available is None or available.empty:
            data["DAILY_TREND_UP"] = False
            return data

        intraday_dates = pd.DataFrame({
            "row_number": range(len(data)),
            "session_date": data.index.normalize(),
        }).sort_values("session_date")
        mapped = pd.merge_asof(
            intraday_dates,
            available,
            left_on="session_date",
            right_on="available_from",
            direction="backward",
        ).sort_values("row_number")
        data["DAILY_TREND_UP"] = (
            mapped["daily_trend_up"].fillna(False).to_numpy()
        )
        return data

    bt.download_history = download_history_with_daily_trend

    for strategy_name, evaluator in STRATEGIES.items():
        print(f"\n=== STRATEGY: {strategy_name} ===")
        bt.evaluate_signal_at = evaluator
        strategy_trades = []
        strategy_oos_trades = []

        bt.DOWNLOAD_ERRORS.clear()
        bt.EXCLUDED_OHLC_CANDLES.clear()
        bt.RR_REJECTIONS.clear()
        bt.SIGNAL_DIAGNOSTICS.clear()
        bt.SNAPSHOT_MANIFEST.clear()

        for asset, ticker in assets:
            for timeframe in timeframes:
                trades, error = bt.run_backtest(asset, ticker, timeframe)
                if error:
                    all_errors.append({
                        "strategy": strategy_name,
                        "asset": asset,
                        "timeframe": timeframe,
                        "error": error,
                    })
                    continue

                for trade in trades:
                    item = dict(trade)
                    item["strategy"] = strategy_name
                    strategy_trades.append(item)
                    all_trades.append(item)

                metrics = bt.calculate_metrics(trades)
                all_summary.append({
                    "strategy": strategy_name,
                    "asset": asset,
                    "timeframe": timeframe,
                    **metrics,
                })
                bounds = history_bounds.get((asset, timeframe))
                history_start, history_end = bounds if bounds else (None, None)
                all_walk_forward.extend(
                    _walk_forward_rows(
                        strategy_name, trades, asset, timeframe,
                        history_start, history_end,
                    )
                )
                strategy_oos_trades.extend(
                    _out_of_sample_trades(
                        trades, history_start, history_end
                    )
                )
                print(
                    f"{asset} {timeframe}: n={metrics['trades']} "
                    f"avgR={metrics['average_R']:.4f} "
                    f"PF={metrics['profit_factor_R']}"
                )

        portfolio = bt.simulate_portfolio(
            strategy_trades,
            capital_usd=bt.INITIAL_CAPITAL_USD,
        )
        portfolio_rows.append({
            "strategy": strategy_name,
            **portfolio["summary"],
        })

        oos_portfolio = bt.simulate_portfolio(
            strategy_oos_trades,
            capital_usd=bt.INITIAL_CAPITAL_USD,
        )
        oos_portfolio_rows.append({
            "strategy": strategy_name,
            "sample": "OUT_OF_SAMPLE_ONLY",
            "universe_coverage_status": coverage_summary["status"],
            "universe_expected_combinations": coverage_summary["expected_combinations"],
            "universe_available_combinations": coverage_summary["available_combinations"],
            "oos_calendar_policy": coverage_summary["oos_calendar_policy"],
            "portfolio_calendar_comparable": False,
            **oos_portfolio["summary"],
        })

    failed_pairs = sorted({
        (str(row["asset"]).upper(), str(row["timeframe"]).lower())
        for row in all_errors
    })
    failed_pair_set = set(failed_pairs)
    for coverage_row in coverage_rows:
        key = (
            str(coverage_row["asset"]).upper(),
            str(coverage_row["timeframe"]).lower(),
        )
        if key in failed_pair_set and coverage_row["status"] == "AVAILABLE":
            coverage_row["status"] = "RESEARCH_ERROR"
    coverage_summary["research_error_combinations"] = [
        {"asset": asset, "timeframe": timeframe}
        for asset, timeframe in failed_pairs
    ]
    if failed_pairs:
        coverage_summary["status"] = "PARTIAL"
    for portfolio_row in oos_portfolio_rows:
        portfolio_row["universe_coverage_status"] = coverage_summary["status"]
    pd.DataFrame(coverage_rows).to_csv(
        "strategy_research_coverage.csv", index=False
    )
    with open("strategy_research_status.json", "w", encoding="utf-8") as status_file:
        json.dump(coverage_summary, status_file, ensure_ascii=False, indent=2)

    pd.DataFrame(all_summary).to_csv("strategy_research_summary.csv", index=False)
    pd.DataFrame(all_walk_forward).to_csv("strategy_research_walkforward.csv", index=False)
    pd.DataFrame(all_trades).to_csv("strategy_research_trades.csv", index=False)
    pd.DataFrame(all_errors).to_csv("strategy_research_errors.csv", index=False)
    pd.DataFrame(portfolio_rows).to_csv("strategy_research_portfolio.csv", index=False)
    pd.DataFrame(oos_portfolio_rows).to_csv(
        "strategy_research_oos_portfolio.csv", index=False
    )

    print("\n=== PORTFOLIO COMPARISON (GROSS, COSTS NOT INCLUDED) ===")
    for row in portfolio_rows:
        print(
            row["strategy"],
            "GROSS_PNL_USD=", round(row.get("gross_pnl_usd", 0.0), 2),
            "max_DD_USD=", round(row.get("max_drawdown_usd", 0.0), 2),
            "accepted=", row.get("trades_accepted", 0),
        )

    print("\\n=== OUT-OF-SAMPLE PORTFOLIO ONLY (GROSS, COSTS NOT INCLUDED) ===")
    for row in oos_portfolio_rows:
        print(
            row["strategy"],
            "GROSS_PNL_USD=", round(row.get("gross_pnl_usd", 0.0), 2),
            "max_DD_USD=", round(row.get("max_drawdown_usd", 0.0), 2),
            "accepted=", row.get("trades_accepted", 0),
        )


if __name__ == "__main__":
    main()
