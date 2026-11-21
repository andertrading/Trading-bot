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
    return strategies.evaluate_signal_at(data, asset, timeframe, index)


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


STRATEGIES = {
    "baseline": baseline,
    "trend_pullback_reclaim": trend_pullback_reclaim,
    "trend_continuation": trend_continuation,
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


def _walk_forward_rows(strategy_name, trades, asset, timeframe):
    if not trades:
        return [
            _metrics_row(strategy_name, asset, timeframe, "IN_SAMPLE", []),
            _metrics_row(strategy_name, asset, timeframe, "OUT_OF_SAMPLE", []),
        ]

    frame = pd.DataFrame(trades)
    frame["_entry_time"] = pd.to_datetime(frame["entry_time"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["_entry_time"]).sort_values("_entry_time")
    if frame.empty:
        return [
            _metrics_row(strategy_name, asset, timeframe, "IN_SAMPLE", []),
            _metrics_row(strategy_name, asset, timeframe, "OUT_OF_SAMPLE", []),
        ]

    cutoff = frame["_entry_time"].quantile(0.70)
    in_sample = frame[frame["_entry_time"] <= cutoff].drop(columns=["_entry_time"]).to_dict("records")
    out_sample = frame[frame["_entry_time"] > cutoff].drop(columns=["_entry_time"]).to_dict("records")

    rows = [
        _metrics_row(strategy_name, asset, timeframe, "IN_SAMPLE", in_sample),
        _metrics_row(strategy_name, asset, timeframe, "OUT_OF_SAMPLE", out_sample),
    ]
    for row in rows:
        row["split_timestamp_utc"] = cutoff.isoformat()
        row["sample_size_sufficient"] = len(in_sample if row["period"] == "IN_SAMPLE" else out_sample) >= 100
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
