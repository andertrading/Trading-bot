"""Synchronized weekly block bootstrap for Coinbase-only M15 OOS trades."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd


def _week_start(value: str | pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("fold timestamps must be timezone-aware")
    stamp = stamp.tz_convert("UTC").normalize()
    return stamp - pd.Timedelta(days=stamp.weekday())


def _merge_week_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if end < start:
            raise ValueError("test fold end precedes its start")
        if not merged or start > merged[-1][1] + pd.Timedelta(days=7):
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def load_m15_test_windows(input_dir: str | Path):
    """Load test-fold trades and aggregate all assets on shared UTC weeks."""
    paths = sorted(Path(input_dir).glob("*_15m.json"))
    if not paths:
        raise FileNotFoundError(f"No *_15m.json files found in {input_dir}")
    folds_by_key = {}
    trades: list[dict[str, Any]] = []
    assets = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        asset = str(payload.get("asset") or "").upper()
        if payload.get("interval") != "15m" or payload.get("provider") != "coinbase_exchange_only":
            raise ValueError(f"{path.name} is not a Coinbase-only M15 result")
        if payload.get("all_trades_inside_oos_fold") is not True:
            raise ValueError(f"{path.name} failed its OOS fold-boundary assertion")
        assets.add(asset)
        for fold in payload.get("folds", []):
            if fold.get("fold") != "test":
                continue
            key = (asset, int(fold["segment_id"]))
            if key in folds_by_key:
                raise ValueError(f"duplicate test fold for {key}")
            start, end = pd.Timestamp(fold["evaluation_start"]), pd.Timestamp(fold["evaluation_end"])
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("fold timestamps must include timezone")
            folds_by_key[key] = (start.tz_convert("UTC"), end.tz_convert("UTC"))
        for trade in payload.get("all_trades", []):
            if trade.get("oos_fold") != "test":
                continue
            if str(trade.get("asset") or "").upper() != asset:
                raise ValueError(f"asset provenance mismatch in {path.name}")
            if trade.get("data_source") != "coinbase_exchange_only":
                raise ValueError(f"non-Coinbase trade found in {path.name}")
            if trade.get("etoro_price_equivalence") != "not_established":
                raise ValueError(f"unexpected eToro equivalence claim in {path.name}")
            key = (asset, int(trade["segment_id"]))
            if key not in folds_by_key:
                raise ValueError(f"trade has no matching test fold: {key}")
            start, end = folds_by_key[key]
            for field in ("signal_time", "entry_time", "exit_time"):
                stamp = pd.Timestamp(trade[field])
                if stamp.tzinfo is None or not start <= stamp.tz_convert("UTC") <= end:
                    raise ValueError(f"{field} crosses its test fold for {key}")
            result_r = float(trade["result_R"])
            if not math.isfinite(result_r):
                raise ValueError("trade result_R must be finite")
            exit_time = pd.Timestamp(trade["exit_time"]).tz_convert("UTC")
            trades.append({"asset": asset, "segment_id": key[1],
                           "exit_week": _week_start(exit_time), "result_R": result_r})
    if len(assets) < 2 or not trades:
        raise ValueError("need at least two assets and one M15 test trade")

    weekly_ranges = [(_week_start(start), _week_start(end))
                     for start, end in folds_by_key.values()]
    windows = []
    for start_week, end_week in _merge_week_ranges(weekly_ranges):
        weeks = pd.date_range(start_week, end_week, freq="W-MON", tz="UTC")
        week_map = {week: i for i, week in enumerate(weeks)}
        weekly_r = np.zeros(len(weeks), dtype=float)
        weekly_n = np.zeros(len(weeks), dtype=int)
        for trade in trades:
            index = week_map.get(trade["exit_week"])
            if index is not None:
                weekly_r[index] += trade["result_R"]
                weekly_n[index] += 1
        windows.append({"start_week": start_week.date().isoformat(),
                        "end_week": end_week.date().isoformat(),
                        "weekly_r": weekly_r, "weekly_trade_count": weekly_n})

    observed_r = sum(float(w["weekly_r"].sum()) for w in windows)
    observed_n = sum(int(w["weekly_trade_count"].sum()) for w in windows)
    if observed_n != len(trades):
        raise AssertionError("weekly aggregation lost or duplicated test trades")
    observed = {
        "assets": sorted(assets), "test_folds": len(folds_by_key),
        "test_trades": len(trades),
        "active_calendar_weeks": sum(len(w["weekly_r"]) for w in windows),
        "observed_total_gross_R": observed_r,
        "observed_mean_gross_R_per_trade": observed_r / observed_n,
        "windows": [{"start_week": w["start_week"], "end_week": w["end_week"],
                     "weeks": len(w["weekly_r"]),
                     "trades": int(w["weekly_trade_count"].sum()),
                     "gross_R": float(w["weekly_r"].sum())} for w in windows],
    }
    return windows, observed


def _sample_week_indices(week_count: int, block_length: int, rng):
    if week_count <= 0 or block_length <= 0:
        raise ValueError("week_count and block_length must be positive")
    if block_length == 1:
        return rng.integers(0, week_count, size=week_count)
    sampled = []
    while len(sampled) < week_count:
        start = int(rng.integers(0, week_count))
        sampled.extend((start + offset) % week_count for offset in range(block_length))
    return np.asarray(sampled[:week_count], dtype=int)


def bootstrap_synchronized_weekly_blocks(windows, *, block_length: int,
                                         replicates: int = 20_000,
                                         seed: int = 20261123):
    """Resample shared calendar-week blocks across the whole asset panel."""
    if isinstance(replicates, bool) or replicates < 1:
        raise ValueError("replicates must be a positive integer")
    if isinstance(block_length, bool) or block_length < 1:
        raise ValueError("block_length must be a positive integer")
    normalized = []
    for window in windows:
        weekly_r = np.asarray(window["weekly_r"], dtype=float)
        weekly_n = np.asarray(window["weekly_trade_count"], dtype=int)
        if (weekly_r.ndim != 1 or weekly_n.ndim != 1 or
                len(weekly_r) != len(weekly_n) or len(weekly_r) == 0):
            raise ValueError("each window must contain matching non-empty weekly arrays")
        if not np.isfinite(weekly_r).all() or (weekly_n < 0).any():
            raise ValueError("weekly R must be finite and trade counts non-negative")
        normalized.append((weekly_r, weekly_n))
    rng = np.random.default_rng(seed)
    total_r = np.empty(replicates)
    total_n = np.empty(replicates, dtype=int)
    mean_r = np.empty(replicates)
    for replicate in range(replicates):
        r_sum, n_sum = 0.0, 0
        for weekly_r, weekly_n in normalized:
            indices = _sample_week_indices(len(weekly_r), block_length, rng)
            r_sum += float(weekly_r[indices].sum())
            n_sum += int(weekly_n[indices].sum())
        total_r[replicate], total_n[replicate] = r_sum, n_sum
        mean_r[replicate] = r_sum / n_sum if n_sum else np.nan
    valid_means = mean_r[np.isfinite(mean_r)]
    return {
        "block_length_weeks": block_length, "replicates": replicates, "seed": seed,
        "total_gross_R_95pct_interval": np.quantile(total_r, [0.025, 0.975]).tolist(),
        "mean_gross_R_per_trade_95pct_interval": np.quantile(valid_means, [0.025, 0.975]).tolist(),
        "positive_total_gross_R_replicates_pct": float(np.mean(total_r > 0) * 100.0),
        "median_resampled_trade_count": float(np.median(total_n)),
        "resampled_trade_count_95pct_interval": np.quantile(total_n, [0.025, 0.975]).tolist(),
    }


def run(input_dir: str | Path, *, replicates: int = 20_000,
        seed: int = 20261123):
    windows, observed = load_m15_test_windows(input_dir)
    return {
        "method": "synchronized_utc_calendar_week_circular_block_bootstrap",
        "scope": "Coinbase-only M15 OOS test trades; gross R; all assets jointly resampled",
        "cross_asset_dependence": "preserved within each sampled calendar week by aggregating all assets before resampling",
        "time_dependence": "circular blocks sampled within each contiguous union of test-fold calendar weeks; blocks never cross disjoint data gaps",
        "limitations": [
            "This is a trade-R uncertainty diagnostic, not portfolio P&L or a portfolio simulation.",
            "It does not model verified eToro execution prices, transaction costs, simultaneous capital constraints, or position sizing.",
            "The historical OOS partition is fixed; the bootstrap does not replace repeated walk-forward tests or independent future evidence.",
            "Only observed cross-asset dependence in overlapping test weeks is retained.",
        ],
        "observed": observed,
        "bootstrap": [bootstrap_synchronized_weekly_blocks(
            windows, block_length=block, replicates=replicates, seed=seed
        ) for block in (1, 2, 4)],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--replicates", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20261123)
    args = parser.parse_args()
    result = run(args.input_dir, replicates=args.replicates, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"test_trades": result["observed"]["test_trades"],
                      "observed_total_gross_R": result["observed"]["observed_total_gross_R"],
                      "bootstrap": result["bootstrap"], "output": str(args.output)},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
