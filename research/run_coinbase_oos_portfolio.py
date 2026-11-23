"""Combine Coinbase-only OOS test trades in the shared virtual portfolio allocator.

This research runner reuses portfolio_backtest.simulate_portfolio. It performs no
market-data downloads and never connects to eToro or a broker.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
import sys
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from portfolio_backtest import simulate_portfolio

EXPECTED_ASSETS = {"BTC", "ETH", "SOL", "XLM", "XRP"}
EXPECTED_TIMEFRAMES = {"15m", "1d"}


def _utc(value: str):
    import pandas as pd

    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("OOS timestamps must include a timezone")
    return stamp.tz_convert("UTC")


def load_oos_test_trades(input_dir: str | Path) -> tuple[list[dict], dict]:
    """Validate and collect only test-fold trades from the ten OOS result files."""
    root = Path(input_dir)
    paths = sorted(
        path for path in root.glob("*.json")
        if path.stem.rsplit("_", 1)[-1] in EXPECTED_TIMEFRAMES
    )
    expected = {(asset, timeframe) for asset in EXPECTED_ASSETS
                for timeframe in EXPECTED_TIMEFRAMES}
    found = set()
    trades: list[dict[str, Any]] = []
    folds_seen = 0
    identities = set()
    fold_windows = {}

    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        asset = str(payload.get("asset") or "").upper()
        timeframe = str(payload.get("interval") or "")
        key = (asset, timeframe)
        if key not in expected or key in found:
            raise ValueError(f"unexpected or duplicate OOS series: {key}")
        found.add(key)
        if payload.get("provider") != "coinbase_exchange_only":
            raise ValueError(f"{path.name} is not Coinbase Exchange-only")
        if payload.get("research_only") is not True:
            raise ValueError(f"{path.name} is not labelled research-only")
        if payload.get("production_changes") is not False:
            raise ValueError(f"{path.name} does not confirm production_changes=false")
        if payload.get("network_requests") != 0:
            raise ValueError(f"{path.name} reports unexpected network requests")
        if payload.get("all_trades_inside_oos_fold") is not True:
            raise ValueError(f"{path.name} failed its OOS fold-boundary assertion")

        for fold in payload.get("folds", []):
            if fold.get("fold") != "test":
                continue
            segment_id = int(fold["segment_id"])
            fold_key = (asset, timeframe, segment_id)
            if fold_key in fold_windows:
                raise ValueError(f"duplicate test fold: {fold_key}")
            start = _utc(fold["evaluation_start"])
            end = _utc(fold["evaluation_end"])
            if end < start:
                raise ValueError(f"invalid test fold window: {fold_key}")
            fold_windows[fold_key] = (start, end)
            folds_seen += 1

        for source_trade in payload.get("all_trades", []):
            if source_trade.get("oos_fold") != "test":
                continue
            if str(source_trade.get("asset") or "").upper() != asset:
                raise ValueError(f"asset provenance mismatch in {path.name}")
            if str(source_trade.get("timeframe") or "") != timeframe:
                raise ValueError(f"timeframe provenance mismatch in {path.name}")
            if source_trade.get("data_source") != "coinbase_exchange_only":
                raise ValueError(f"non-Coinbase trade in {path.name}")
            if source_trade.get("source_venue") != "Coinbase Exchange":
                raise ValueError(f"unexpected source venue in {path.name}")
            if source_trade.get("etoro_price_equivalence") != "not_established":
                raise ValueError(f"unexpected eToro price-equivalence claim in {path.name}")
            if source_trade.get("costs_included") is not False:
                raise ValueError(f"trade costs must remain excluded in {path.name}")

            segment_id = int(source_trade["segment_id"])
            fold_key = (asset, timeframe, segment_id)
            if fold_key not in fold_windows:
                raise ValueError(f"trade has no matching test fold: {fold_key}")
            start, end = fold_windows[fold_key]
            for field in ("signal_time", "entry_time", "exit_time"):
                stamp = _utc(source_trade[field])
                if not start <= stamp <= end:
                    raise ValueError(f"{field} crosses its test fold for {fold_key}")

            identity = (
                asset, timeframe, segment_id,
                str(source_trade["signal_time"]),
                str(source_trade["entry_time"]),
                str(source_trade["exit_time"]),
            )
            if identity in identities:
                raise ValueError(f"duplicate OOS trade identity: {identity}")
            identities.add(identity)

            result_r = float(source_trade["result_R"])
            if not math.isfinite(result_r):
                raise ValueError("result_R must be finite")
            # Keep source evidence attached while passing the original
            # validated record to the existing portfolio allocator.
            trades.append(dict(source_trade))

    if found != expected:
        missing = sorted(expected - found)
        raise ValueError(f"OOS artifact is incomplete; missing series: {missing}")
    if not trades:
        raise ValueError("OOS artifact contains no test-fold trades")

    return trades, {
        "provider": "coinbase_exchange_only",
        "assets": sorted(EXPECTED_ASSETS),
        "timeframes": sorted(EXPECTED_TIMEFRAMES),
        "series_count": len(found),
        "test_folds": folds_seen,
        "test_trades": len(trades),
        "duplicate_trade_identities": 0,
        "network_requests": 0,
        "production_changes": False,
        "eToro_price_equivalence": "not_established",
        "verified_transaction_costs": "not_complete",
    }


def _accepted_breakdown(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["asset"], row["timeframe"])].append(row)
    output = {}
    for (asset, timeframe), group in sorted(groups.items()):
        pnl = [float(row["realized_pnl_usd"]) for row in group]
        risk = sum(float(row["allocated_risk_usd"]) for row in group)
        output[f"{asset}_{timeframe}"] = {
            "accepted_trades": len(group),
            "gross_pnl_usd": sum(pnl),
            "allocated_risk_usd": risk,
            "win_rate_pct_gross": (
                sum(value > 0 for value in pnl) / len(pnl) * 100.0
            ),
            "profit_factor_gross": (
                sum(value for value in pnl if value > 0)
                / abs(sum(value for value in pnl if value < 0))
                if any(value < 0 for value in pnl) else None
            ),
        }
    return output


def run_portfolio_oos(input_dir: str | Path, capital_usd: float = 1000.0) -> dict:
    if isinstance(capital_usd, bool) or not math.isfinite(float(capital_usd)) or float(capital_usd) <= 0:
        raise ValueError("capital_usd must be positive and finite")
    trades, source = load_oos_test_trades(input_dir)
    result = simulate_portfolio(trades, capital_usd=float(capital_usd))

    rejections_by_reason = defaultdict(int)
    for rejection in result["rejections"]:
        rejections_by_reason[str(rejection.get("reason") or "UNKNOWN")] += 1

    summary = dict(result["summary"])
    summary.update({
        "initial_capital_usd": float(capital_usd),
        "test_trades_supplied": len(trades),
        "accepted_trade_details": len(result["trades"]),
        "rejected_trade_details": len(result["rejections"]),
        "drawdown_basis": "realized_exit_events_only",
        "intratrade_drawdown_included": False,
        "unrealized_pnl_included": False,
        "costs_included": False,
        "net_pnl_usd": None,
        "net_return_pct": None,
        "net_performance_validated": False,
    })
    return {
        "mode": "RESEARCH_ONLY_OOS_PORTFOLIO_SIMULATION",
        "source_validation": source,
        "portfolio_policy": {
            "capital_usd": float(capital_usd),
            "allocator": "portfolio_backtest.simulate_portfolio",
            "risk_tiers": "existing config.py and risk_manager.py",
            "maximum_risk_per_trade_pct": 1.0,
            "maximum_aggregate_open_risk_pct": 3.0,
            "cash_policy": "long_only_unleveraged_shared_cash",
            "same_timestamp_policy": "entries allocated before exits",
            "simultaneous_entry_policy": "proportional allocation across same-timestamp candidates",
        },
        "summary": summary,
        "accepted_by_asset_timeframe": _accepted_breakdown(result["trades"]),
        "rejections_by_reason": dict(sorted(rejections_by_reason.items())),
        "accepted_trades": result["trades"],
        "rejections": result["rejections"],
        "limitations": [
            "All trade outcomes originate from independent per-asset OOS backtests and are combined by the shared portfolio allocator.",
            "This is a gross-only simulation; verified eToro executable prices and complete transaction costs are unavailable.",
            "Drawdown is based on realized exit events; intratrade mark-to-market losses and unrealized P&L are excluded.",
            "Coinbase Exchange spot OHLC is not eToro execution evidence.",
            "This research result does not approve a strategy or authorize live trading.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--capital-usd", type=float, default=1000.0)
    args = parser.parse_args()
    result = run_portfolio_oos(args.input_dir, capital_usd=args.capital_usd)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({
        "source_validation": result["source_validation"],
        "summary": result["summary"],
        "rejections_by_reason": result["rejections_by_reason"],
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
