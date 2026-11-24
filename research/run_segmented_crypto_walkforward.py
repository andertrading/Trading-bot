"""Chronological out-of-sample validation for Coinbase-only segmented histories.

For each natural contiguous segment, the first 60% is training-only,
the next 20% is validation, and the final 20% is test. Indicators are
calculated on the causal prefix ending at each fold boundary. Signals are
permitted only inside the relevant OOS window; the engine's max-hold buffer
keeps exits inside that same window. No network access is performed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pandas as pd

from research.run_segmented_crypto_backtest import (
    load_coinbase_snapshot,
)
from research.segmented_crypto_history import (
    refresh_segment_volume_provenance,
    split_contiguous_segments,
)


def run_oos_fold(
    segment: pd.DataFrame,
    asset: str,
    ticker: str,
    interval: str,
    fold: str,
    start_index: int,
    end_index: int,
    backtest_module: Any,
) -> dict[str, Any]:
    """Evaluate only signals in [start_index, end_index] on causal history."""
    if start_index < backtest_module.MIN_BARS - 1:
        raise ValueError("OOS fold starts before the full indicator warm-up")
    if end_index <= start_index or end_index >= len(segment):
        raise ValueError("Invalid OOS fold bounds")

    original_download = backtest_module.download_history
    original_evaluate = backtest_module.evaluate_signal_at
    state_names = (
        "DOWNLOAD_ERRORS",
        "EXCLUDED_OHLC_CANDLES",
        "RR_REJECTIONS",
        "SIGNAL_DIAGNOSTICS",
    )
    original_state = {
        name: getattr(backtest_module, name)
        for name in state_names
        if hasattr(backtest_module, name)
    }
    for name, value in original_state.items():
        setattr(backtest_module, name, {} if isinstance(value, dict) else [])

    start_time = segment.index[start_index]
    end_time = segment.index[end_index]

    # The full contiguous segment carries source-volume counts for its full
    # length. This fold receives only the causal prefix ending at end_index,
    # so rebase trusted counts to that exact prefix before the strategy score
    # gate evaluates provenance. Untrusted metadata remains untrusted.
    history_prefix = segment.iloc[: end_index + 1].copy()
    refresh_segment_volume_provenance(
        history_prefix,
        source_length=len(segment),
    )

    def filtered_evaluator(*args, **kwargs):
        frame = kwargs.get("data")
        index = kwargs.get("index")
        if frame is None and len(args) >= 1:
            frame = args[0]
        if index is None and len(args) >= 4:
            index = args[3]
        if frame is None or index is None:
            raise ValueError("Unexpected evaluate_signal_at call signature")
        timestamp = frame.index[index]
        if timestamp < start_time or timestamp > end_time:
            return None
        return original_evaluate(*args, **kwargs)

    try:
        backtest_module.download_history = (
            lambda _ticker, _interval, asset=None: history_prefix.copy()
        )
        backtest_module.evaluate_signal_at = filtered_evaluator
        trades, error = backtest_module.run_backtest(asset, ticker, interval)
        if error:
            raise RuntimeError(f"{asset} {interval} {fold}: {error}")

        # The engine already reserves max_bars at the end of its input, so
        # simulated exits cannot extend beyond this fold's end timestamp.
        for trade in trades:
            trade["data_source"] = "coinbase_exchange_only"
            trade["source_venue"] = "Coinbase Exchange"
            trade["research_price_basis"] = (
                "Coinbase Exchange spot OHLC; not eToro executable bid/ask"
            )
            trade["etoro_price_equivalence"] = "not_established"
            trade["oos_fold"] = fold
            trade["oos_start"] = start_time.isoformat()
            trade["oos_end"] = end_time.isoformat()

        metrics = backtest_module.calculate_metrics(trades)
        return {
            "fold": fold,
            "evaluation_start": start_time.isoformat(),
            "evaluation_end": end_time.isoformat(),
            "history_rows_including_warmup": end_index + 1,
            "evaluation_rows": end_index - start_index + 1,
            "trades": trades,
            "metrics": metrics,
            # Preserve the engine's per-candidate diagnostics before its
            # module-level state is restored in the finally block. These
            # explain why candidates were executed, rejected, or skipped.
            "signal_diagnostics": [
                dict(item)
                for item in getattr(backtest_module, "SIGNAL_DIAGNOSTICS", [])
                if isinstance(item, dict)
            ],
            "rr_rejections": [
                dict(item)
                for item in getattr(backtest_module, "RR_REJECTIONS", [])
                if isinstance(item, dict)
            ],
            "excluded_ohlc_candles": [
                dict(item)
                for item in getattr(backtest_module, "EXCLUDED_OHLC_CANDLES", [])
                if isinstance(item, dict)
            ],
            "download_errors": dict(
                getattr(backtest_module, "DOWNLOAD_ERRORS", {})
            ),
            "network_requests": 0,
            "all_trades_inside_fold": all(
                pd.Timestamp(t["signal_time"]) >= start_time
                and pd.Timestamp(t["signal_time"]) <= end_time
                and pd.Timestamp(t["entry_time"]) >= start_time
                and pd.Timestamp(t["exit_time"]) <= end_time
                for t in trades
            ),
            "verified_cost_adjusted_total_R": None,
        }
    finally:
        backtest_module.download_history = original_download
        backtest_module.evaluate_signal_at = original_evaluate
        for name, value in original_state.items():
            setattr(backtest_module, name, value)


def run_walk_forward(
    data: pd.DataFrame,
    asset: str,
    ticker: str,
    interval: str,
    backtest_module=None,
) -> dict[str, Any]:
    if backtest_module is None:
        import backtest_v3 as backtest_module

    segments, coverage = split_contiguous_segments(data, interval)
    all_folds = []
    all_trades = []
    segment_audits = []

    for segment_id, segment in enumerate(segments):
        n = len(segment)
        train_end = int(n * 0.60)
        validation_end = int(n * 0.80)
        if train_end < backtest_module.MIN_BARS:
            raise ValueError(f"Segment {segment_id} is too short for OOS warm-up")
        if validation_end - train_end <= backtest_module.PERIODS[interval]["max_bars"]:
            raise ValueError(f"Segment {segment_id} has insufficient validation rows")
        if n - validation_end <= backtest_module.PERIODS[interval]["max_bars"]:
            raise ValueError(f"Segment {segment_id} has insufficient test rows")

        segment_folds = []
        for fold, start, end in (
            ("validation", train_end, validation_end - 1),
            ("test", validation_end, n - 1),
        ):
            result = run_oos_fold(
                segment, asset, ticker, interval, fold,
                start, end, backtest_module,
            )
            result["segment_id"] = segment_id
            result["segment_start"] = segment.index[0].isoformat()
            result["segment_end"] = segment.index[-1].isoformat()
            if not result["all_trades_inside_fold"]:
                raise AssertionError("Trade crossed an OOS fold boundary")
            if any(t.get("segment_id") is not None for t in result["trades"]):
                raise AssertionError("Unexpected pre-existing segment label")
            for trade in result["trades"]:
                trade["segment_id"] = segment_id
            all_trades.extend(result["trades"])
            all_folds.append(result)
            segment_folds.append({
                "fold": fold,
                "evaluation_start": result["evaluation_start"],
                "evaluation_end": result["evaluation_end"],
                "evaluation_rows": result["evaluation_rows"],
                "trades": result["metrics"]["trades"],
            })

        segment_audits.append({
            "segment_id": segment_id,
            "start": segment.index[0].isoformat(),
            "end": segment.index[-1].isoformat(),
            "rows": n,
            "training_only_rows": train_end,
            "folds": segment_folds,
        })

    return {
        "asset": asset,
        "ticker": ticker,
        "interval": interval,
        "provider": "coinbase_exchange_only",
        "research_only": True,
        "production_changes": False,
        "network_requests": 0,
        "split_policy": "chronological_60pct_train_20pct_validation_20pct_test_per_contiguous_segment",
        "indicator_policy": "causal_prefix_only; full segment warm-up retained; no future rows supplied",
        "execution_policy": "signal, entry and exit must remain inside the same OOS fold and contiguous segment",
        "cost_policy": "no verified Coinbase/eToro executable costs; net metrics remain null",
        "coverage": coverage,
        "segments": segment_audits,
        "folds": all_folds,
        "all_trades": all_trades,
        "trade_count": len(all_trades),
        "all_trades_inside_oos_fold": all(
            fold["all_trades_inside_fold"] for fold in all_folds
        ),
        "verified_cost_adjusted_total_R": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--interval", required=True, choices=("15m", "1d"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    data, _ = load_coinbase_snapshot(
        args.csv, args.manifest, args.asset, args.interval
    )
    result = run_walk_forward(
        data, args.asset, args.ticker, args.interval
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(json.dumps({
        "asset": args.asset,
        "interval": args.interval,
        "folds": len(result["folds"]),
        "trades": result["trade_count"],
        "network_requests": 0,
        "all_trades_inside_oos_fold": result["all_trades_inside_oos_fold"],
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
