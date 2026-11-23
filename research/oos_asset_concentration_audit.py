"""Research-only asset concentration and overlap-exclusion diagnostics for Coinbase OOS trades."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from research.oos_trade_overlap_audit import _load


def _metrics(trades):
    values = [float(t["result_R"]) for t in trades]
    wins = sum(x for x in values if x > 0)
    losses = sum(x for x in values if x < 0)
    return {
        "trades": len(values),
        "gross_total_R": round(sum(values), 4),
        "mean_R_per_trade": round(sum(values) / len(values), 6) if values else None,
        "median_R": round(float(pd.Series(values).median()), 4) if values else None,
        "win_rate_pct": round(100 * sum(x > 0 for x in values) / len(values), 2) if values else None,
        "gross_profit_factor": round(wins / abs(losses), 4) if losses < 0 else None,
    }


def _cross_timeframe_pairs(trades):
    pairs = []
    by_asset = defaultdict(list)
    for i, trade in enumerate(trades):
        by_asset[trade["asset"]].append((i, trade))
    for entries in by_asset.values():
        for pos, (i, a) in enumerate(entries):
            if a["_exit"] <= a["_entry"]:
                continue
            for j, b in entries[pos + 1:]:
                if b["_exit"] <= b["_entry"] or a["timeframe"] == b["timeframe"]:
                    continue
                if max(a["_entry"], b["_entry"]) < min(a["_exit"], b["_exit"]):
                    pairs.append((i, j))
    return pairs


def audit_asset_concentration(input_dir):
    trades, source = _load(input_dir)
    by_asset = {}
    by_asset_timeframe = {}
    by_timeframe = {}
    for asset in sorted({t["asset"] for t in trades}):
        group = [t for t in trades if t["asset"] == asset]
        by_asset[asset] = _metrics(group)
        for timeframe in ("15m", "1d"):
            subset = [t for t in group if t["timeframe"] == timeframe]
            if subset:
                by_asset_timeframe[f"{asset}_{timeframe}"] = _metrics(subset)
    for timeframe in ("15m", "1d"):
        subset = [t for t in trades if t["timeframe"] == timeframe]
        by_timeframe[timeframe] = _metrics(subset)

    btc_eth = [t for t in trades if t["asset"] in {"BTC", "ETH"}]
    other = [t for t in trades if t["asset"] not in {"BTC", "ETH"}]
    pairs = _cross_timeframe_pairs(trades)
    remove_m15 = {i if trades[i]["timeframe"] == "15m" else j
                  for i, j in pairs}
    remove_daily = {i if trades[i]["timeframe"] == "1d" else j
                    for i, j in pairs}
    scenarios = {
        "baseline_candidate_trades": _metrics(trades),
        "diagnostic_exclude_m15_member_of_each_same_asset_cross_timeframe_overlap": _metrics(
            [t for i, t in enumerate(trades) if i not in remove_m15]
        ),
        "diagnostic_exclude_daily_member_of_each_same_asset_cross_timeframe_overlap": _metrics(
            [t for i, t in enumerate(trades) if i not in remove_daily]
        ),
    }
    weekly = defaultdict(lambda: {"trades": 0, "gross_R": 0.0})
    for t in trades:
        if t["timeframe"] != "15m":
            continue
        week = t["_exit"].strftime("%G-W%V")
        weekly[week]["trades"] += 1
        weekly[week]["gross_R"] += float(t["result_R"])

    return {
        "mode": "RESEARCH_ONLY_OOS_ASSET_CONCENTRATION_DIAGNOSTIC",
        "source_validation": source,
        "sample_warning": (
            "Candidate-trade, equal-weight R diagnostics only; not a reconstructed portfolio ledger. "
            "Gross R excludes costs and position sizing. Coinbase prices are not established as eToro executable prices."
        ),
        "by_asset": by_asset,
        "by_asset_timeframe": by_asset_timeframe,
        "by_timeframe": by_timeframe,
        "btc_eth_vs_other_assets": {
            "BTC_ETH": _metrics(btc_eth),
            "SOL_XLM_XRP": _metrics(other),
        },
        "same_asset_cross_timeframe_overlap": {
            "overlapping_pairs": len(pairs),
            "unique_trades_involved": len({i for pair in pairs for i in pair}),
        },
        "overlap_exclusion_sensitivity": {
            "method": "Ex-post diagnostic only; remove one timeframe's candidate trade for each positive-duration same-asset M15/daily overlap. Not a proposed entry rule.",
            "m15_member_trades_removed": len(remove_m15),
            "daily_member_trades_removed": len(remove_daily),
            "scenarios": scenarios,
            "warning": "These scenarios do not replay cash, sizing, risk reservations, entry ordering, or replacement trades; do not interpret as achievable portfolio returns.",
        },
        "weekly_m15_exit_buckets": {
            "active_exit_weeks": len(weekly),
            "weeks": {k: {"trades": v["trades"], "gross_R": round(v["gross_R"], 4)}
                      for k, v in sorted(weekly.items())},
            "effective_sample_size": None,
            "effective_sample_size_reason": (
                "Not estimated: the candidate stream lacks a validated synchronized, position-sized portfolio return series; "
                "the OOS test windows are discontinuous and cross-asset dependence is material."
            ),
        },
        "approval": "NOT_APPROVED; gross descriptive diagnostics only; verified costs and eToro execution equivalence absent.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit_asset_concentration(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
