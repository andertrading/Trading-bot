"""Research-only aggregation of Coinbase 15m candles into complete 1h/4h bars.

No synthetic candles are created. A higher-timeframe candle is retained only
when every expected 15m source candle is present in the same contiguous source
segment. Production timeframe support is not modified.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from typing import Any, Iterator

import numpy as np
import pandas as pd

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from research.run_segmented_crypto_backtest import load_coinbase_snapshot
from research.run_segmented_crypto_walkforward import run_walk_forward
from research.segmented_crypto_history import split_contiguous_segments


TARGET_INTERVALS = {
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
}
SOURCE_INTERVAL = pd.Timedelta(minutes=15)
BARS_PER_TARGET = {
    name: int(delta / SOURCE_INTERVAL)
    for name, delta in TARGET_INTERVALS.items()
}
RESEARCH_MAX_HOLD = {
    "1h": 32,
    "4h": 32,
}


def _set_volume_provenance(frame: pd.DataFrame) -> None:
    """Set trusted Coinbase volume metadata for already validated aggregates."""
    if "Volume" not in frame.columns or frame.empty:
        frame.attrs["volume_quality"] = "missing_column"
        frame.attrs["volume_column_present"] = False
        frame.attrs["volume_zero_count_before_normalization"] = 0
        frame.attrs["volume_positive_count_before_normalization"] = 0
        frame.attrs["volume_invalid_count_before_normalization"] = len(frame)
        return

    values = pd.to_numeric(frame["Volume"], errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(values)
    invalid = int((~finite).sum() + ((values < 0) & finite).sum())
    positive = int(((values > 0) & finite).sum())
    zero = int(((values == 0) & finite).sum())
    frame.attrs["volume_column_present"] = True
    frame.attrs["volume_invalid_count_before_normalization"] = invalid
    frame.attrs["volume_positive_count_before_normalization"] = positive
    frame.attrs["volume_zero_count_before_normalization"] = zero
    if invalid:
        frame.attrs["volume_quality"] = "invalid_or_missing_values"
    elif positive == 0:
        frame.attrs["volume_quality"] = "all_zero"
    elif zero:
        frame.attrs["volume_quality"] = "contains_zero_bars"
    else:
        frame.attrs["volume_quality"] = "available"


def resample_complete_coinbase_bars(
    data: pd.DataFrame,
    target_interval: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Aggregate complete UTC 15m bars; discard partial buckets and keep gaps."""
    if target_interval not in TARGET_INTERVALS:
        raise ValueError(f"Unsupported research interval: {target_interval!r}")
    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError("data must be a non-empty DataFrame")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise ValueError("source timestamps must be timezone-aware")
    if not data.index.is_monotonic_increasing or data.index.has_duplicates:
        raise ValueError("source timestamps must be sorted and unique")
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(data.columns):
        raise ValueError(f"source columns missing: {sorted(required - set(data.columns))}")

    segments, source_coverage = split_contiguous_segments(data, "15m")
    bars_per_target = BARS_PER_TARGET[target_interval]
    target_delta = TARGET_INTERVALS[target_interval]
    output_rows: list[dict[str, float]] = []
    output_times: list[pd.Timestamp] = []
    partial_buckets = 0

    for segment in segments:
        bucket_labels = segment.index.floor(target_interval)
        for bucket, group in segment.groupby(bucket_labels, sort=True):
            expected = pd.date_range(
                start=bucket,
                periods=bars_per_target,
                freq=SOURCE_INTERVAL,
            )
            if len(group) != bars_per_target or not group.index.equals(expected):
                partial_buckets += 1
                continue
            output_times.append(pd.Timestamp(bucket))
            output_rows.append({
                "Open": float(group["Open"].iloc[0]),
                "High": float(group["High"].max()),
                "Low": float(group["Low"].min()),
                "Close": float(group["Close"].iloc[-1]),
                "Volume": float(group["Volume"].sum()),
            })

    if not output_rows:
        raise ValueError(
            f"No complete {target_interval} candles could be built from source history"
        )

    result = pd.DataFrame(
        output_rows,
        index=pd.DatetimeIndex(output_times, name=data.index.name),
    ).sort_index()
    if result.index.has_duplicates:
        raise ValueError("resampling produced duplicate target timestamps")
    _set_volume_provenance(result)
    result.attrs["coinbase_source_policy"] = data.attrs.get("coinbase_source_policy")
    result.attrs["resampled_from"] = "Coinbase Exchange 15m OHLCV"
    result.attrs["resampling_interval"] = target_interval
    result.attrs["synthetic_bars"] = False
    result.attrs["partial_buckets_discarded"] = partial_buckets

    # The higher-timeframe series may still contain missing full candles.
    # Keep them absent; the existing segmented walk-forward splits at those gaps.
    _, target_coverage = split_contiguous_segments(result, target_interval)
    metadata = {
        "source_interval": "15m",
        "target_interval": target_interval,
        "source_rows": int(len(data)),
        "source_segment_count": source_coverage["segment_count"],
        "source_missing_15m_bars": source_coverage["missing_bar_count"],
        "complete_target_bars": int(len(result)),
        "partial_target_buckets_discarded": int(partial_buckets),
        "target_segment_count": target_coverage["segment_count"],
        "target_missing_bars": target_coverage["missing_bar_count"],
        "aggregation": {
            "Open": "first",
            "High": "maximum",
            "Low": "minimum",
            "Close": "last",
            "Volume": "sum",
        },
        "timezone": "UTC",
        "gap_policy": "no_fill_no_interpolation_no_cross_gap_aggregation",
        "synthetic_bars": False,
    }
    return result, metadata


@contextmanager
def research_timeframe_enabled(backtest_module: Any, interval: str) -> Iterator[None]:
    """Temporarily expose 1h/4h to the research runner only."""
    if interval not in TARGET_INTERVALS:
        raise ValueError(f"Unsupported research interval: {interval!r}")
    import strategies as strategy_rules

    original_periods = backtest_module.PERIODS
    original_supported = strategy_rules.SUPPORTED_TIMEFRAMES
    original_gap_checker = backtest_module._has_unexpected_gap
    periods = dict(original_periods)
    periods[interval] = {"period": "research_resampled", "max_bars": RESEARCH_MAX_HOLD[interval]}

    def research_gap_checker(previous, current, candidate_interval, asset_type, asset=None):
        if str(asset_type).upper() == "CRYPTO" and candidate_interval in TARGET_INTERVALS:
            elapsed = pd.Timestamp(current) - pd.Timestamp(previous)
            return elapsed != TARGET_INTERVALS[candidate_interval]
        return original_gap_checker(
            previous, current, candidate_interval, asset_type, asset=asset
        )

    try:
        backtest_module.PERIODS = periods
        strategy_rules.SUPPORTED_TIMEFRAMES = tuple(
            dict.fromkeys((*original_supported, *TARGET_INTERVALS.keys()))
        )
        backtest_module._has_unexpected_gap = research_gap_checker
        yield
    finally:
        backtest_module.PERIODS = original_periods
        strategy_rules.SUPPORTED_TIMEFRAMES = original_supported
        backtest_module._has_unexpected_gap = original_gap_checker


def run_resampled_walk_forward(
    data: pd.DataFrame,
    asset: str,
    ticker: str,
    interval: str,
    backtest_module=None,
) -> dict[str, Any]:
    if backtest_module is None:
        import backtest_v3 as backtest_module
    resampled, coverage = resample_complete_coinbase_bars(data, interval)
    with research_timeframe_enabled(backtest_module, interval):
        result = run_walk_forward(
            resampled,
            asset,
            ticker,
            interval,
            backtest_module=backtest_module,
        )
    result["resampling"] = coverage
    result["timeframe_support_scope"] = "temporary_research_runtime_only"
    result["production_timeframe_support_changed"] = False
    result["network_requests"] = 0
    result["verified_cost_adjusted_total_R"] = None
    result["cost_policy"] = (
        "No verified Coinbase/eToro executable costs. Existing published crypto "
        "fee scenarios may be diagnostic only; verified net expectancy remains null."
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Coinbase 1h/4h walk-forward from retained 15m candles."
    )
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--interval", required=True, choices=("1h", "4h"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source, _ = load_coinbase_snapshot(
        args.csv, args.manifest, args.asset, "15m"
    )
    result = run_resampled_walk_forward(
        source, args.asset, args.ticker, args.interval
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "asset": args.asset,
        "interval": args.interval,
        "trades": result["trade_count"],
        "resampling": result["resampling"],
        "network_requests": 0,
        "production_timeframe_support_changed": False,
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
