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

    if bt.BACKTEST_DATA_MODE != "replay":
        raise SystemExit("Research requires BACKTEST_DATA_MODE=replay")

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
                all_walk_forward.extend(
                    _walk_forward_rows(strategy_name, trades, asset, timeframe)
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

    pd.DataFrame(all_summary).to_csv("strategy_research_summary.csv", index=False)
    pd.DataFrame(all_walk_forward).to_csv("strategy_research_walkforward.csv", index=False)
    pd.DataFrame(all_trades).to_csv("strategy_research_trades.csv", index=False)
    pd.DataFrame(all_errors).to_csv("strategy_research_errors.csv", index=False)
    pd.DataFrame(portfolio_rows).to_csv("strategy_research_portfolio.csv", index=False)

    print("\n=== PORTFOLIO COMPARISON (GROSS, COSTS NOT INCLUDED) ===")
    for row in portfolio_rows:
        print(
            row["strategy"],
            "PnL_USD=", round(row.get("net_pnl_usd", 0.0), 2),
            "max_DD_USD=", round(row.get("max_drawdown_usd", 0.0), 2),
            "accepted=", row.get("trades_accepted", 0),
        )


if __name__ == "__main__":
    main()
