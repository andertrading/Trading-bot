"""Research-only runner for Coinbase crypto histories with missing bars.

Each contiguous segment is passed independently to the existing backtest
engine. The engine therefore recalculates its indicators from that segment's
first candle, applies its normal minimum-history warm-up, and cannot simulate
a trade across a missing-candle boundary.

No provider download is performed: the runner temporarily replaces the
engine's download_history function with an in-memory segment provider.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import numpy as np
import pandas as pd

from research.segmented_crypto_history import split_contiguous_segments


def load_coinbase_snapshot(
    csv_path: str | Path,
    manifest_path: str | Path,
    asset: str,
    interval: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load and verify one Coinbase-only series and its explicit gap list."""
    csv_path = Path(csv_path)
    manifest_path = Path(manifest_path)
    if interval not in {"15m", "1d"}:
        raise ValueError(f"Unsupported interval: {interval!r}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    policy = manifest.get("source_policy", {})
    required_policy = {
        "coinbase_only": True,
        "twelvedata_used": False,
        "eodhd_used": False,
        "cross_provider_gap_repair": False,
        "synthetic_candles": False,
        "missing_candles_filled": False,
    }
    for key, expected in required_policy.items():
        if policy.get(key) is not expected:
            raise ValueError(
                f"Coinbase manifest source policy failed: {key}={policy.get(key)!r}"
            )

    matches = [
        item
        for item in manifest.get("series", [])
        if item.get("asset") == asset and item.get("timeframe") == interval
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one manifest series for {asset} {interval}; "
            f"found {len(matches)}"
        )
    series = matches[0]
    if series.get("provider") != "coinbase_exchange_only":
        raise ValueError("Series is not marked Coinbase Exchange-only")
    if series.get("gap_repair") is not False:
        raise ValueError("Series gap repair must be explicitly false")
    if series.get("synthetic_bars") is not False:
        raise ValueError("Series synthetic bars must be explicitly false")
    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)

    raw = pd.read_csv(csv_path)
    required_columns = {
        "timestamp",
        "timestamp_epoch",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    if not required_columns.issubset(raw.columns):
        raise ValueError(
            f"CSV columns missing: {sorted(required_columns - set(raw.columns))}"
        )

    timestamps = pd.to_datetime(raw["timestamp"], utc=True, errors="coerce")
    if timestamps.isna().any():
        raise ValueError("CSV contains invalid timestamps")
    epoch = pd.to_numeric(raw["timestamp_epoch"], errors="coerce")
    if epoch.isna().any() or not np.array_equal(
        epoch.to_numpy(dtype="int64"),
        timestamps.astype("int64").to_numpy() // 1_000_000_000,
    ):
        raise ValueError("timestamp_epoch does not match timestamp")

    data = raw.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    data.index = pd.DatetimeIndex(timestamps, name="timestamp")
    data = data[["Open", "High", "Low", "Close", "Volume"]].copy()
    for column in data.columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    if not data.index.is_monotonic_increasing or data.index.has_duplicates:
        raise ValueError("CSV timestamps must be strictly increasing and unique")
    if len(data) != int(series.get("received_bars", -1)):
        raise ValueError("CSV row count does not match manifest received_bars")

    values = data[["Open", "High", "Low", "Close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("CSV contains invalid OHLC prices")
    invalid_ohlc = (
        (data["High"] < data["Low"])
        | (data["High"] < data["Open"])
        | (data["High"] < data["Close"])
        | (data["Low"] > data["Open"])
        | (data["Low"] > data["Close"])
    )
    if invalid_ohlc.any():
        raise ValueError("CSV contains incoherent OHLC candles")

    volume = data["Volume"].to_numpy(dtype=float)
    finite_volume = np.isfinite(volume)
    observed_volume_counts = {
        "zero_volume_bars": int(((volume == 0) & finite_volume).sum()),
        "positive_volume_bars": int(((volume > 0) & finite_volume).sum()),
        "invalid_or_missing_volume_bars": int((~finite_volume).sum()),
        "negative_volume_bars": int(((volume < 0) & finite_volume).sum()),
    }
    for key, observed in observed_volume_counts.items():
        declared = series.get(key)
        if type(declared) is not int or declared != observed:
            raise ValueError(
                f"Coinbase manifest {key} does not match source CSV "
                f"(manifest={declared!r}, actual={observed})"
            )
    expected_volume_units = (
        f"{asset} base-asset units, as reported by Coinbase Exchange"
    )
    if series.get("volume_units") != expected_volume_units:
        raise ValueError("Coinbase manifest volume_units is missing or inconsistent")
    if not np.isfinite(volume).all() or (volume <= 0).any():
        raise ValueError(
            "Coinbase research runner requires positive, finite source volume"
        )

    expected_delta = pd.Timedelta(minutes=15) if interval == "15m" else pd.Timedelta(days=1)
    expected_index = pd.date_range(
        pd.Timestamp(series["start_utc"]),
        pd.Timestamp(series["end_utc"]),
        freq=expected_delta,
    )
    missing_name = Path(series["missing_timestamps_csv"]).name
    missing_path = csv_path.with_name(missing_name)
    if not missing_path.is_file():
        raise FileNotFoundError(
            f"Missing-timestamp audit file not found: {missing_path}"
        )
    missing_frame = pd.read_csv(missing_path)
    missing_timestamps = pd.to_datetime(
        missing_frame["timestamp"], utc=True, errors="coerce"
    )
    if missing_timestamps.isna().any():
        raise ValueError("Missing-timestamp CSV contains invalid timestamps")
    missing_index = pd.DatetimeIndex(missing_timestamps)

    if len(expected_index) != int(series.get("expected_bars", -1)):
        raise ValueError("Expected timestamp grid does not match manifest")
    if len(missing_index) != int(series.get("missing_bars", -1)):
        raise ValueError("Missing-timestamp CSV count does not match manifest")
    if not missing_index.is_monotonic_increasing or missing_index.has_duplicates:
        raise ValueError("Missing timestamps must be sorted and unique")
    if len(data.index.intersection(missing_index)):
        raise ValueError("A timestamp is both present and marked missing")
    combined = data.index.append(missing_index).sort_values()
    if not combined.equals(expected_index):
        raise ValueError(
            "Received and missing timestamps do not reconstruct the expected grid"
        )

    data.attrs["coinbase_manifest_series"] = series
    data.attrs["coinbase_source_policy"] = policy

    # The loader has just validated that the source Volume column exists,
    # every value is finite and strictly positive, and the series is from
    # the Coinbase-only manifest. Translate that verified source evidence
    # into the shared provenance contract used by strategy scoring.
    data.attrs["volume_quality"] = "available"
    data.attrs["volume_column_present"] = True
    data.attrs["volume_zero_count_before_normalization"] = 0
    data.attrs["volume_positive_count_before_normalization"] = int(len(data))
    data.attrs["volume_invalid_count_before_normalization"] = 0
    return data, manifest


def run_segmented_backtest(
    data: pd.DataFrame,
    asset: str,
    ticker: str,
    interval: str,
    backtest_module=None,
) -> dict[str, Any]:
    """Run the existing backtest independently on each contiguous segment."""
    if backtest_module is None:
        import backtest_v3 as backtest_module

    if interval not in backtest_module.PERIODS:
        raise ValueError(f"Backtest interval is not supported: {interval}")
    segments, metadata = split_contiguous_segments(data, interval)

    original_download = backtest_module.download_history
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

    all_trades: list[dict[str, Any]] = []
    segment_results: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    rr_rejections: list[dict[str, Any]] = []
    excluded_ohlc: list[dict[str, Any]] = []
    download_errors: dict[str, Any] = {}

    try:
        for segment_id, segment in enumerate(segments):
            start = segment.index[0]
            end = segment.index[-1]
            if len(segment) < backtest_module.MIN_BARS:
                segment_results.append(
                    {
                        "segment_id": segment_id,
                        "start_timestamp": start.isoformat(),
                        "end_timestamp": end.isoformat(),
                        "row_count": len(segment),
                        "status": "skipped_insufficient_history",
                        "minimum_bars": backtest_module.MIN_BARS,
                        "trades": 0,
                    }
                )
                continue

            # No network/provider access: run_backtest receives this segment
            # from the in-memory replacement and nothing else.
            backtest_module.download_history = (
                lambda _ticker, _interval, asset=None, _segment=segment:
                _segment.copy()
            )
            trades, error = backtest_module.run_backtest(
                asset,
                ticker,
                interval,
            )
            if error:
                raise RuntimeError(
                    f"Backtest failed for segment {segment_id} "
                    f"({start} -> {end}): {error}"
                )

            segment_trades = []
            for trade in trades:
                enriched = dict(trade)
                # The shared backtest engine normally labels trades with its
                # configured live provider. This runner supplies Coinbase
                # snapshots in memory, so override that provenance explicitly.
                enriched["data_source"] = "coinbase_exchange_only"
                enriched["source_venue"] = "Coinbase Exchange"
                enriched["price_adjustment_policy"] = "not_applicable_crypto_spot"
                enriched["research_price_basis"] = (
                    "Coinbase Exchange spot OHLC; not eToro executable bid/ask"
                )
                enriched["etoro_price_equivalence"] = "not_established"
                enriched["segment_id"] = segment_id
                enriched["segment_start"] = start.isoformat()
                enriched["segment_end"] = end.isoformat()
                segment_trades.append(enriched)
            all_trades.extend(segment_trades)

            segment_results.append(
                {
                    "segment_id": segment_id,
                    "start_timestamp": start.isoformat(),
                    "end_timestamp": end.isoformat(),
                    "row_count": len(segment),
                    "status": "completed",
                    "trades": len(segment_trades),
                }
            )

        diagnostics = list(getattr(backtest_module, "SIGNAL_DIAGNOSTICS", []))
        rr_rejections = list(getattr(backtest_module, "RR_REJECTIONS", []))
        excluded_ohlc = list(getattr(backtest_module, "EXCLUDED_OHLC_CANDLES", []))
        download_errors = dict(getattr(backtest_module, "DOWNLOAD_ERRORS", {}))
        metrics = backtest_module.calculate_metrics(all_trades)
    finally:
        backtest_module.download_history = original_download
        for name, value in original_state.items():
            setattr(backtest_module, name, value)

    return {
        "asset": asset,
        "ticker": ticker,
        "interval": interval,
        "provider": "coinbase_exchange_only",
        "research_only": True,
        "production_changes": False,
        "network_requests": 0,
        "execution_price_basis": (
            "Coinbase Exchange spot OHLC; not eToro executable bid/ask"
        ),
        "cost_model_scope": (
            "Existing eToro fee assumptions are scenario-only and unverified "
            "for Coinbase venue data; verified cost-adjusted metrics must remain null"
        ),
        "source_policy": {
            "cross_provider_repair": False,
            "synthetic_bars": False,
            "missing_bars_filled": False,
        },
        "coverage": metadata,
        "segments": segment_results,
        "trades": all_trades,
        "metrics": metrics,
        "signal_diagnostics": diagnostics,
        "rr_rejections": rr_rejections,
        "excluded_ohlc_candles": excluded_ohlc,
        "download_errors": download_errors,
        "all_segments_completed": all(
            item["status"] in {
                "completed",
                "skipped_insufficient_history",
            }
            for item in segment_results
        ),
        "trade_segment_integrity": all(
            trade.get("segment_id") is not None
            for trade in all_trades
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a gap-aware research backtest on a Coinbase-only snapshot."
    )
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--interval", required=True, choices=("15m", "1d"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    data, manifest = load_coinbase_snapshot(
        args.csv,
        args.manifest,
        args.asset,
        args.interval,
    )
    result = run_segmented_backtest(
        data,
        args.asset,
        args.ticker,
        args.interval,
    )
    result["capture_created_at_utc"] = manifest.get("created_at_utc")
    result["capture_github_run_id"] = manifest.get("github_run_id")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "asset": args.asset,
                "interval": args.interval,
                "segment_count": result["coverage"]["segment_count"],
                "trades": result["metrics"]["trades"],
                "network_requests": result["network_requests"],
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
