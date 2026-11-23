"""Research-only audit of temporal overlap in Coinbase OOS test trades."""
from __future__ import annotations
import argparse, json, math
from collections import defaultdict
from pathlib import Path
import pandas as pd

ASSETS = {"BTC", "ETH", "SOL", "XLM", "XRP"}
TIMEFRAMES = {"15m", "1d"}

def _utc(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("OOS timestamps must be valid and timezone-aware")
    return stamp.tz_convert("UTC")

def _load(input_dir):
    expected = {(a, tf) for a in ASSETS for tf in TIMEFRAMES}
    found, folds, trades, identities = set(), {}, [], set()
    for path in sorted(Path(input_dir).glob("*.json")):
        p = json.loads(path.read_text(encoding="utf-8"))
        asset, tf = str(p.get("asset") or "").upper(), str(p.get("interval") or "")
        key = (asset, tf)
        if key not in expected or key in found:
            raise ValueError(f"unexpected or duplicate series: {key}")
        found.add(key)
        if p.get("provider") != "coinbase_exchange_only":
            raise ValueError(f"{path.name} is not Coinbase Exchange-only")
        if p.get("research_only") is not True or p.get("production_changes") is not False:
            raise ValueError(f"{path.name} is not marked research-only")
        if p.get("network_requests") != 0:
            raise ValueError(f"{path.name} reports network requests")
        if p.get("all_trades_inside_oos_fold") is not True:
            raise ValueError(f"{path.name} failed fold-boundary validation")
        for fold in p.get("folds", []):
            if fold.get("fold") != "test":
                continue
            fk = (asset, tf, int(fold["segment_id"]))
            if fk in folds:
                raise ValueError(f"duplicate test fold: {fk}")
            start, end = _utc(fold["evaluation_start"]), _utc(fold["evaluation_end"])
            if end < start:
                raise ValueError(f"invalid test fold: {fk}")
            folds[fk] = (start, end)
        for t in p.get("all_trades", []):
            if t.get("oos_fold") != "test":
                continue
            if str(t.get("asset") or "").upper() != asset or str(t.get("timeframe") or "") != tf:
                raise ValueError(f"trade provenance mismatch in {path.name}")
            if t.get("data_source") != "coinbase_exchange_only" or t.get("source_venue") != "Coinbase Exchange":
                raise ValueError(f"unexpected source in {path.name}")
            if t.get("etoro_price_equivalence") != "not_established" or t.get("costs_included") is not False:
                raise ValueError(f"unexpected cost/execution claim in {path.name}")
            fk = (asset, tf, int(t["segment_id"]))
            if fk not in folds:
                raise ValueError(f"trade has no test fold: {fk}")
            start, end = folds[fk]
            signal, entry, exit_ = _utc(t["signal_time"]), _utc(t["entry_time"]), _utc(t["exit_time"])
            if exit_ < entry or not (start <= signal <= entry <= end) or not (start <= exit_ <= end):
                raise ValueError(f"invalid/out-of-fold trade: {fk}")
            if not math.isfinite(float(t["result_R"])):
                raise ValueError("non-finite result_R")
            ident = (asset, tf, fk[2], signal, entry, exit_)
            if ident in identities:
                raise ValueError(f"duplicate trade identity: {ident}")
            identities.add(ident)
            trades.append({**t, "_signal": signal, "_entry": entry, "_exit": exit_})
    if found != expected:
        raise ValueError(f"incomplete artifact; missing series: {sorted(expected-found)}")
    if not trades:
        raise ValueError("no OOS test trades")
    return trades, {"series_count": len(found), "test_folds": len(folds), "test_trades": len(trades)}

def _pairs(trades, predicate):
    count, involved, active = 0, set(), []
    for i, t in sorted(enumerate(trades), key=lambda row: row[1]["_entry"]):
        active = [(j, other) for j, other in active if other["_exit"] > t["_entry"]]
        # Zero-duration records are not positive-duration overlapping exposures.
        if t["_exit"] <= t["_entry"]:
            continue
        for j, other in active:
            if other["_exit"] > other["_entry"] and predicate(t, other):
                count += 1
                involved.update((i, j))
        active.append((i, t))
    return {"overlapping_pairs": count, "unique_trades_involved": len(involved)}

def _peaks(trades, key_fn):
    groups = defaultdict(list)
    for t in trades:
        groups[str(key_fn(t))].extend(((t["_entry"], 1), (t["_exit"], -1)))
    result = {}
    for key, events in groups.items():
        active = peak = 0
        # Entry before exit at equal timestamps, matching the portfolio allocator.
        for _, delta in sorted(events, key=lambda x: (x[0], -x[1])):
            active += delta
            peak = max(peak, active)
        result[key] = peak
    return result

def audit_oos_trade_overlaps(input_dir):
    trades, source = _load(input_dir)
    by_signal = defaultdict(set)
    for t in trades:
        by_signal[(t["asset"], t["_signal"])].add(t["timeframe"])
    asset_tf = _peaks(trades, lambda t: f'{t["asset"]}_{t["timeframe"]}')
    by_asset = _peaks(trades, lambda t: t["asset"])
    portfolio = _peaks(trades, lambda _: "portfolio")
    return {
        "mode": "RESEARCH_ONLY_OOS_TEMPORAL_DEPENDENCE_AUDIT",
        "source_validation": {
            "provider": "coinbase_exchange_only", **source,
            "duplicate_trade_identities": 0,
            "duplicate_same_asset_same_signal_time_across_timeframes": sum(len(v)>1 for v in by_signal.values()),
            "network_requests": 0, "production_changes": False,
            "etoro_price_equivalence": "not_established",
        },
        "overlap_policy": {
            "pair_overlap": "positive-duration entry-to-exit intersection",
            "concurrency_ties": "entries before exits at same timestamp, matching conservative portfolio allocator",
        },
        "overlaps": {
            "same_asset_any_timeframe": _pairs(trades, lambda a,b: a["asset"]==b["asset"]),
            "same_asset_same_timeframe": _pairs(trades, lambda a,b: a["asset"]==b["asset"] and a["timeframe"]==b["timeframe"]),
            "same_asset_cross_timeframe": _pairs(trades, lambda a,b: a["asset"]==b["asset"] and a["timeframe"]!=b["timeframe"]),
            "different_assets": _pairs(trades, lambda a,b: a["asset"]!=b["asset"]),
        },
        "maximum_concurrent_positions": {
            "portfolio": portfolio.get("portfolio", 0),
            "by_asset": by_asset, "by_asset_timeframe": asset_tf,
        },
        "interpretation": [
            "Trade count is not an independent-observation count: positions overlap in calendar time, especially across assets.",
            "Same-asset cross-timeframe overlaps are dependent exposures, not independent confirmations.",
            "The existing allocator reserves aggregate risk and cash for simultaneous positions; this audit does not change allocation.",
            "Overlap is not a return-correlation estimate; synchronized time-block resampling remains necessary.",
            "This descriptive audit does not establish net profitability or eToro execution equivalence.",
        ],
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit_oos_trade_overlaps(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
