"""Research-only, gap-aware preparation of continuous-crypto histories.

This module deliberately does not modify production data routing or the
main backtest engine. It calculates indicators independently inside each
contiguous segment and exposes a warm-up eligibility mask for a future
research runner.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd


_INTERVALS = {
    "15m": pd.Timedelta(minutes=15),
    "1d": pd.Timedelta(days=1),
}


def refresh_segment_volume_provenance(
    segment: pd.DataFrame,
    source_length: int,
) -> None:
    """Rebase trusted source-volume counts to this contiguous segment.

    DataFrame.attrs are copied by pandas when slicing. Without rebasing,
    counts from the full Coinbase series fail the strategy's per-frame
    consistency check after a gap split, silently suppressing valid volume
    evidence. Untrusted or incomplete metadata is never upgraded here.
    """
    attrs = segment.attrs
    quality = attrs.get("volume_quality")
    column_present = attrs.get("volume_column_present")
    invalid_count = attrs.get("volume_invalid_count_before_normalization")
    positive_count = attrs.get("volume_positive_count_before_normalization")
    zero_count = attrs.get("volume_zero_count_before_normalization")

    trusted_source = (
        quality in {"available", "contains_zero_bars"}
        and column_present is True
        and type(invalid_count) is int
        and invalid_count == 0
        and type(positive_count) is int
        and positive_count > 0
        and type(zero_count) is int
        and zero_count >= 0
        and positive_count + zero_count == source_length
        and (quality != "available" or zero_count == 0)
        and (quality != "contains_zero_bars" or zero_count > 0)
    )
    if not trusted_source:
        return

    if "Volume" not in segment.columns:
        attrs["volume_quality"] = "missing_column"
        attrs["volume_column_present"] = False
        attrs["volume_zero_count_before_normalization"] = 0
        attrs["volume_positive_count_before_normalization"] = 0
        attrs["volume_invalid_count_before_normalization"] = len(segment)
        return

    values = pd.to_numeric(segment["Volume"], errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(values)
    invalid = int((~finite).sum() + ((values < 0) & finite).sum())
    positive = int(((values > 0) & finite).sum())
    zero = int(((values == 0) & finite).sum())

    attrs["volume_column_present"] = True
    attrs["volume_zero_count_before_normalization"] = zero
    attrs["volume_positive_count_before_normalization"] = positive
    attrs["volume_invalid_count_before_normalization"] = invalid
    if invalid:
        attrs["volume_quality"] = "invalid_or_missing_values"
    elif positive == 0:
        attrs["volume_quality"] = "all_zero"
    elif zero:
        attrs["volume_quality"] = "contains_zero_bars"
    else:
        attrs["volume_quality"] = "available"


def split_contiguous_segments(
    data: pd.DataFrame,
    interval: str,
) -> tuple[list[pd.DataFrame], dict[str, Any]]:
    """Split a UTC crypto DataFrame at every missing expected candle.

    The function never sorts, fills, interpolates, deduplicates, or changes
    source values. Invalid ordering, duplicate timestamps, timezone-naive
    indexes, and gaps that are not integer multiples of the interval fail
    closed.
    """
    if interval not in _INTERVALS:
        raise ValueError(f"Unsupported continuous-crypto interval: {interval!r}")
    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError("data must be a non-empty pandas DataFrame")

    index = data.index
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("data index must be a DatetimeIndex")
    if index.tz is None:
        raise ValueError("data timestamps must be timezone-aware")
    if index.hasnans:
        raise ValueError("data timestamps contain NaT")
    if not index.is_monotonic_increasing:
        raise ValueError("data timestamps must already be sorted")
    if index.has_duplicates:
        raise ValueError("data timestamps contain duplicates")

    expected = _INTERVALS[interval]
    deltas = index.to_series().diff().iloc[1:]
    boundaries: list[int] = [0]
    missing_bars = 0

    for position, delta in enumerate(deltas, start=1):
        if delta <= pd.Timedelta(0):
            raise ValueError("timestamps must be strictly increasing")
        if delta % expected != pd.Timedelta(0):
            raise ValueError(
                f"timestamp gap is not aligned to {interval}: "
                f"{index[position - 1]} -> {index[position]}"
            )
        if delta != expected:
            boundaries.append(position)
            missing_bars += int(delta / expected) - 1

    boundaries.append(len(data))
    segments = []
    for start, end in zip(boundaries, boundaries[1:]):
        segment = data.iloc[start:end].copy()
        refresh_segment_volume_provenance(segment, source_length=len(data))
        segments.append(segment)

    metadata = {
        "interval": interval,
        "expected_interval": str(expected),
        "input_rows": int(len(data)),
        "segment_count": int(len(segments)),
        "missing_bar_count": int(missing_bars),
        "segments": [
            {
                "segment_id": segment_id,
                "start_timestamp": segment.index[0].isoformat(),
                "end_timestamp": segment.index[-1].isoformat(),
                "row_count": int(len(segment)),
            }
            for segment_id, segment in enumerate(segments)
        ],
        "gap_policy": "split_only_no_fill_no_interpolation_no_provider_repair",
    }
    return segments, metadata


def add_segmented_indicators(
    data: pd.DataFrame,
    interval: str,
    add_indicators_fn: Callable[[pd.DataFrame], pd.DataFrame | None],
    warmup_bars: int = 220,
) -> pd.DataFrame:
    """Calculate indicators independently for each gap-free segment.

    warmup_bars=220 matches the current backtest's minimum-history
    threshold. The first 219 rows of every segment are marked ineligible;
    the 220th row is the first eligible row. Callers must evaluate signals
    only where research_signal_eligible is True and must keep simulated
    trades within one segment_id.
    """
    if not callable(add_indicators_fn):
        raise TypeError("add_indicators_fn must be callable")
    if isinstance(warmup_bars, bool) or not isinstance(warmup_bars, int):
        raise TypeError("warmup_bars must be an integer")
    if warmup_bars < 1:
        raise ValueError("warmup_bars must be at least 1")

    segments, metadata = split_contiguous_segments(data, interval)
    prepared: list[pd.DataFrame] = []
    warmup_rows_excluded = 0

    for segment_id, segment in enumerate(segments):
        indicators = add_indicators_fn(segment.copy())
        if not isinstance(indicators, pd.DataFrame):
            raise ValueError(
                f"indicator function returned no DataFrame for segment {segment_id}"
            )
        if not indicators.index.equals(segment.index):
            raise ValueError(
                f"indicator function changed timestamps for segment {segment_id}"
            )

        output = indicators.copy()
        bar_index = pd.Series(range(len(output)), index=output.index, dtype="int64")
        eligible = bar_index >= warmup_bars - 1

        output["segment_id"] = segment_id
        output["segment_bar_index"] = bar_index
        output["research_signal_eligible"] = eligible
        warmup_rows_excluded += int((~eligible).sum())
        prepared.append(output)

    result = pd.concat(prepared, axis=0)
    result.attrs["gap_aware_research"] = {
        **metadata,
        "warmup_bars": warmup_bars,
        "warmup_rows_excluded": warmup_rows_excluded,
        "eligible_rows": int(result["research_signal_eligible"].sum()),
        "segment_indicator_reset": True,
        "trade_must_remain_within_segment": True,
    }
    return result


def is_trade_within_segment(
    prepared: pd.DataFrame,
    entry_index: int,
    exit_index: int,
) -> bool:
    """Return whether an index-based simulated trade stays in one segment."""
    if not isinstance(prepared, pd.DataFrame):
        return False
    if entry_index < 0 or exit_index < entry_index or exit_index >= len(prepared):
        return False
    if "segment_id" not in prepared.columns:
        return False
    if "research_signal_eligible" not in prepared.columns:
        return False

    entry = prepared.iloc[entry_index]
    exit_ = prepared.iloc[exit_index]
    return bool(
        entry["research_signal_eligible"]
        and entry["segment_id"] == exit_["segment_id"]
    )
