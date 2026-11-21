"""Research-only strategy comparison using immutable historical snapshots.

Fixed hypotheses only; no parameter grid search, no execution, and no changes
to the production strategy. All entries use the same next-bar execution,
ATR stop/target, normalized risk tier and cost assumptions.
"""
from __future__ import annotations

import math

import numpy as np
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
        signal = dict(signal)
        # All hypotheses use the same 0.5% risk tier for fair comparison.
        signal["score"] = 85
    return signal


def trend_pullback_reclaim(data, asset, timeframe, index=-1):
    """EMA50/200 uptrend + completed candle reclaiming EMA20 after pullback."""
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

    trend = (
        values["EMA50"] > values["EMA200"]
        and values["Close"] > values["EMA200"]
    )
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
    if not (
        trend and reclaim and momentum
        and values["ATR_PERCENT"] <= strategies.MAX_ATR_PERCENT
    ):
        return {"action": "NO TRADE"}

    return _candidate(
        data, asset, timeframe, index,
        ["EMA50 sobre EMA200", "Reclaim EMA20 tras pullback",
         "RSI ascendente", "MACD alcista"],
    )


def trend_continuation(data, asset, timeframe, index=-1):
    """Aligned EMA trend + momentum continuation, without breakout trigger."""
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
    if not (
        aligned and momentum
        and values["ATR_PERCENT"] <= strategies.MAX_ATR_PERCENT
    ):
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
    trend = data.iloc[index].get("DAILY_TREND_UP", False)
    if pd.isna(trend) or not bool(trend):
        return {"action": "NO TRADE"}
    return trend_pullback_reclaim(data, asset, timeframe, index)


def weekly_daily_pullback(data, asset, timeframe, index=-1):
    """Daily pullback only when the last completed weekly trend is bullish."""
    if timeframe != "1d" or index < 1:
        return {"action": "NO TRADE"}
    trend = data.iloc[index].get("WEEKLY_TREND_UP", False)
    if pd.isna(trend) or not bool(trend):
        return {"action": "NO TRADE"}
    return trend_pullback_reclaim(data, asset, timeframe, index)


STRATEGIES = {
    "baseline": baseline,
    "trend_pullback_reclaim": trend_pullback_reclaim,
    "trend_continuation": trend_continuation,
    "mtf_daily_trend_pullback": mtf_daily_trend_pullback,
    "weekly_daily_pullback": weekly_daily_pullback,
}


def _metrics_row(strategy_name, asset, timeframe, period, trades):
    return {
        "strategy": strategy_name,
        "asset": asset,
        "timeframe": timeframe,
        "period": period,
        **bt.calculate_metrics(trades),
    }


def _walk_forward_rows(strategy_name, trades, asset, timeframe):
    if not trades:
        return [
            _metrics_row(strategy_name, asset, timeframe, "IN_SAMPLE", []),
            _metrics_row(strategy_name, asset, timeframe, "OUT_OF_SAMPLE", []),
        ]

    frame = pd.DataFrame(trades)
    frame["_entry_time"] = pd.to_datetime(
        frame["entry_time"], utc=True, errors="coerce"
    )
    frame = frame.dropna(subset=["_entry_time"]).sort_values("_entry_time")
    if frame.empty:
        return [
            _metrics_row(strategy_name, asset, timeframe, "IN_SAMPLE", []),
            _metrics_row(strategy_name, asset, timeframe, "OUT_OF_SAMPLE", []),
        ]

    cutoff = frame["_entry_time"].quantile(0.70)
    in_sample = frame[
        frame["_entry_time"] <= cutoff
    ].drop(columns=["_entry_time"]).to_dict("records")
    out_sample = frame[
        frame["_entry_time"] > cutoff
    ].drop(columns=["_entry_time"]).to_dict("records")

    rows = [
        _metrics_row(strategy_name, asset, timeframe, "IN_SAMPLE", in_sample),
        _metrics_row(strategy_name, asset, timeframe, "OUT_OF_SAMPLE", out_sample),
    ]
    for row in rows:
        sample = in_sample if row["period"] == "IN_SAMPLE" else out_sample
        row["split_timestamp_utc"] = cutoff.isoformat()
        row["sample_size_sufficient"] = len(sample) >= 100
    return rows


def _collect_oos_trades(trades):
    """Chronological 70/30 split separately for every asset/timeframe pair."""
    if not trades:
        return []
    frame = pd.DataFrame(trades)
    frame["_entry_time"] = pd.to_datetime(
        frame["entry_time"], utc=True, errors="coerce"
    )
    frame = frame.dropna(subset=["_entry_time"])
    oos_parts = []
    for _, pair in frame.groupby(["asset", "timeframe"]):
        pair = pair.sort_values("_entry_time")
        if len(pair) < 2:
            continue
        cutoff = pair["_entry_time"].quantile(0.70)
        oos_parts.append(pair[pair["_entry_time"] > cutoff])
    if not oos_parts:
        return []
    return pd.concat(oos_parts).drop(columns=["_entry_time"]).to_dict("records")


def _confidence_row(strategy_name, oos_trades):
    if not oos_trades:
        return {
            "strategy": strategy_name,
            "oos_trades": 0,
            "oos_months": 0,
            "oos_average_R": None,
            "monthly_block_ci_95_low": None,
            "monthly_block_ci_95_high": None,
            "bootstrap_fraction_positive_mean": None,
            "sample_size_sufficient": False,
        }

    frame = pd.DataFrame(oos_trades)
    frame["entry_time"] = pd.to_datetime(
        frame["entry_time"], utc=True, errors="coerce"
    )
    frame = frame.dropna(subset=["entry_time", "result_R"])
    positive = frame.loc[frame["result_R"] > 0, "result_R"].sum()
    negative = abs(frame.loc[frame["result_R"] < 0, "result_R"].sum())
    monthly = [
        group["result_R"].to_numpy(dtype=float)
        for _, group in frame.groupby(frame["entry_time"].dt.to_period("M"))
    ]

    ci_low = ci_high = fraction_positive = None
    if len(monthly) >= 3:
        block_sums = np.array([values.sum() for values in monthly], dtype=float)
        block_counts = np.array([len(values) for values in monthly], dtype=float)
        rng = np.random.default_rng(42)
        choices = rng.integers(
            0, len(monthly), size=(10000, len(monthly))
        )
        sampled_sums = block_sums[choices].sum(axis=1)
        sampled_counts = block_counts[choices].sum(axis=1)
        sampled_means = sampled_sums / sampled_counts
        ci_low, ci_high = np.quantile(sampled_means, [0.025, 0.975])
        fraction_positive = float((sampled_means > 0).mean())

    known_fee = frame.get(
        "result_R_after_known_fees", frame["result_R"]
    )
    return {
        "strategy": strategy_name,
        "oos_trades": len(frame),
        "oos_months": len(monthly),
        "oos_average_R": float(frame["result_R"].mean()),
        "oos_profit_factor": (
            float(positive / negative) if negative > 0 else None
        ),
        "oos_win_rate_pct": float((frame["result_R"] > 0).mean() * 100),
        "oos_known_fee_average_R": float(known_fee.mean()),
        "costs_complete_trades": int(
            frame["costs_complete"].fillna(False).sum()
        ) if "costs_complete" in frame else 0,
        "monthly_block_ci_95_low": (
            float(ci_low) if ci_low is not None else None
        ),
        "monthly_block_ci_95_high": (
            float(ci_high) if ci_high is not None else None
        ),
        # This is a bootstrap fraction for mean expectancy, not a trade-win probability.
        "bootstrap_fraction_positive_mean": fraction_positive,
        "sample_size_sufficient": len(frame) >= 100 and len(monthly) >= 12,
    }


def _map_completed_trend(source, target_index, trend_values, available_from):
    available = pd.DataFrame({
        "available_from": available_from,
        "trend_up": trend_values,
    }).sort_values("available_from")
    target = pd.DataFrame({
        "row_number": range(len(target_index)),
        "session_date": target_index.normalize(),
    }).sort_values("session_date")
    mapped = pd.merge_asof(
        target,
        available,
        left_on="session_date",
        right_on="available_from",
        direction="backward",
    ).sort_values("row_number")
    return mapped["trend_up"].fillna(False).to_numpy(dtype=bool)


def main():
    assets = bt.get_assets()
    timeframes = bt.get_timeframes()
    all_summary = []
    all_walk_forward = []
    all_trades = []
    all_errors = []
    portfolio_rows = []
    confidence_rows = []

    if bt.BACKTEST_DATA_MODE != "replay":
        raise SystemExit("Research requires BACKTEST_DATA_MODE=replay")
    if bt.BACKTEST_DAILY_LOOKBACK_DAYS < 1825:
        raise SystemExit("Research requires at least five years of daily snapshots")

    print("RESEARCH ONLY — no live alerts, no orders, no parameter optimization")
    print("Assets:", ", ".join(asset for asset, _ in assets))
    print("Timeframes:", ", ".join(timeframes))
    print("Daily lookback:", bt.BACKTEST_DAILY_LOOKBACK_DAYS)
    print("Snapshot directory:", bt.SNAPSHOT_DIR)

    original_download_history = bt.download_history
    daily_cache = {}
    weekly_cache = {}

    def download_history_with_mtf_filters(ticker, interval, asset=None):
        data = original_download_history(ticker, interval, asset=asset)
        if data is None or data.empty or asset is None:
            return data

        if interval == "15m":
            if asset not in daily_cache:
                daily = original_download_history(ticker, "1d", asset=asset)
                if daily is None or daily.empty:
                    daily_cache[asset] = None
                else:
                    daily_indicators = bt.add_indicators(daily.copy())
                    daily_cache[asset] = pd.DataFrame({
                        "available_from": (
                            daily_indicators.index.normalize()
                            + pd.Timedelta(days=1)
                        ),
                        "trend_up": (
                            daily_indicators["EMA50"]
                            > daily_indicators["EMA200"]
                        ).fillna(False).to_numpy(),
                    }).sort_values("available_from")

            available = daily_cache[asset]
            if available is None or available.empty:
                data["DAILY_TREND_UP"] = False
            else:
                data["DAILY_TREND_UP"] = _map_completed_trend(
                    available,
                    data.index,
                    available["trend_up"].to_numpy(),
                    available["available_from"],
                )

        elif interval == "1d":
            if asset not in weekly_cache:
                aggregation = {
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                }
                if "Volume" in data.columns:
                    aggregation["Volume"] = "sum"
                weekly = (
                    data.resample("W-FRI", label="right", closed="right")
                    .agg(aggregation)
                    .dropna(subset=["Open", "High", "Low", "Close"])
                )
                weekly_indicators = bt.add_indicators(weekly)
                weekly_cache[asset] = pd.DataFrame({
                    "available_from": (
                        weekly_indicators.index.normalize()
                        + pd.Timedelta(days=1)
                    ),
                    "trend_up": (
                        (weekly_indicators["EMA20"] > weekly_indicators["EMA50"])
                        & (weekly_indicators["Close"] > weekly_indicators["EMA20"])
                    ).fillna(False).to_numpy(),
                }).sort_values("available_from")

            available = weekly_cache[asset]
            if available is None or available.empty:
                data["WEEKLY_TREND_UP"] = False
            else:
                data["WEEKLY_TREND_UP"] = _map_completed_trend(
                    available,
                    data.index,
                    available["trend_up"].to_numpy(),
                    available["available_from"],
                )
        return data

    bt.download_history = download_history_with_mtf_filters

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

        oos_trades = _collect_oos_trades(strategy_trades)
        confidence_rows.append(_confidence_row(strategy_name, oos_trades))

        for period, selected_trades in (
            ("ALL", strategy_trades),
            ("OUT_OF_SAMPLE", oos_trades),
        ):
            portfolio = bt.simulate_portfolio(
                selected_trades,
                capital_usd=bt.INITIAL_CAPITAL_USD,
            )
            portfolio_rows.append({
                "strategy": strategy_name,
                "period": period,
                **portfolio["summary"],
            })

    pd.DataFrame(all_summary).to_csv("strategy_research_summary.csv", index=False)
    pd.DataFrame(all_walk_forward).to_csv("strategy_research_walkforward.csv", index=False)
    pd.DataFrame(all_trades).to_csv("strategy_research_trades.csv", index=False)
    pd.DataFrame(all_errors).to_csv("strategy_research_errors.csv", index=False)
    pd.DataFrame(portfolio_rows).to_csv("strategy_research_portfolio.csv", index=False)
    pd.DataFrame(confidence_rows).to_csv("strategy_research_confidence.csv", index=False)

    print("\n=== PORTFOLIO COMPARISON (GROSS, COSTS NOT INCLUDED) ===")
    for row in portfolio_rows:
        print(
            row["strategy"], row["period"],
            "PnL_USD=", round(row.get("net_pnl_usd", 0.0), 2),
            "max_DD_USD=", round(row.get("max_drawdown_usd", 0.0), 2),
            "accepted=", row.get("trades_accepted", 0),
        )


if __name__ == "__main__":
    main()
