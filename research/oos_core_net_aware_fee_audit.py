"""Audit the 10-asset core OOS portfolio under eToro's assumed fee scenario."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import pandas as pd

RUN_ID = 44978022120
ARTIFACT_ID = 14736736998
ARTIFACT_SHA256 = "f2c6db65c03d4b7e37ee89801eefe20a392d6fb1f61ce2635a98cc0a330a7690"
SCENARIO = "ETORO_REAL_STOCK_1USD_CRYPTO_1PCT"


def _read_csv(archive, name):
    return pd.read_csv(archive.open(name))


def _row_summary(row):
    return {
        "strategy": str(row["strategy"]),
        "timeframe": str(row["timeframe"]),
        "status": str(row["status"]),
        "trades_considered": int(row["trades_considered"]),
        "trades_accepted": int(row["trades_accepted"]),
        "gross_pnl_usd_on_net_aware_allocations": round(float(row["gross_pnl_usd"]), 2),
        "net_pnl_usd_after_assumed_fees": round(float(row["net_pnl_usd"]), 2),
        "net_return_pct": round(float(row["net_return_pct"]), 4),
        "profit_factor_after_assumed_fees": round(float(row["profit_factor"]), 4)
        if pd.notna(row["profit_factor"]) else None,
        "max_drawdown_pct": round(float(row["max_drawdown_pct"]), 4),
        "entry_cost_usd": round(float(row["total_entry_cost_usd"]), 2),
        "exit_cost_usd": round(float(row["total_exit_cost_usd"]), 2),
    }


def audit_core_net_aware_oos(zip_path):
    zip_path = Path(zip_path)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if digest != ARTIFACT_SHA256:
        raise ValueError("artifact SHA-256 does not match the retained source")

    with zipfile.ZipFile(zip_path) as archive:
        status = json.loads(archive.read("strategy_research_status.json"))
        if status.get("status") != "COMPLETE":
            raise ValueError("strategy research artifact is not COMPLETE")
        if status.get("expected_combinations") != 24 or status.get("available_combinations") != 24:
            raise ValueError("unexpected strategy/timeframe coverage")
        coverage = status.get("core_stocks_crypto_history_calendar_coverage", {})
        all_tf = coverage.get("all_timeframes", {})
        by_tf = coverage.get("by_timeframe", {})
        if all_tf.get("expected_series") != 20 or all_tf.get("available_series") != 20:
            raise ValueError("core 10-asset history bounds are incomplete")
        for timeframe in ("15m", "1d"):
            record = by_tf.get(timeframe, {})
            if record.get("expected_series") != 10 or record.get("available_series") != 10:
                raise ValueError(f"incomplete core history bounds for {timeframe}")
            if record.get("common_window_exists") is not True:
                raise ValueError(f"no common history window for {timeframe}")

        synchronized = _read_csv(archive, "strategy_research_core_synchronized_calendar.csv")
        if len(synchronized) != 20 or set(synchronized["status"]) != {"COMPARABLE_BOUNDS_ONLY"}:
            raise ValueError("unexpected synchronized-calendar coverage/status")

        net = _read_csv(archive, "strategy_research_core_net_aware_portfolio.csv")
        selected = net[
            (net["broker_scenario"] == SCENARIO)
            & (net["spread_bps_scenario"] == 0)
        ].copy()
        if len(selected) != 20:
            raise ValueError("expected 20 core strategy/timeframe fee-scenario rows")
        if set(selected["status"]) != {"COMPARABLE_BOUNDS_ONLY"}:
            raise ValueError("unexpected core net-aware calendar status")
        if selected["costs_included"].astype(bool).any() is not True:
            # All selected rows must explicitly include the assumed cost scenario.
            if not selected["costs_included"].astype(bool).all():
                raise ValueError("selected rows do not all include assumed costs")

        traded = selected[selected["trades_considered"] > 0]
        no_trade = selected[selected["trades_considered"] == 0]
        positive = traded[traded["net_return_pct"] > 0]
        negative = traded[traded["net_return_pct"] < 0]
        if len(positive) + len(negative) != len(traded):
            raise ValueError("unexpected zero/invalid net-return row among traded strategies")

        by_asset_class = _read_csv(archive, "strategy_research_mtf_asset_class_net_portfolio.csv")
        class_rows = by_asset_class[
            (by_asset_class["strategy"] == "mtf_daily_price_confirmed_pullback")
            & (by_asset_class["timeframe"] == "15m")
            & (by_asset_class["broker_scenario"] == SCENARIO)
            & (by_asset_class["spread_bps_scenario"] == 0)
        ]
        if set(class_rows["asset_class"]) != {"US_STOCKS", "CRYPTO"}:
            raise ValueError("expected stock and crypto class breakdown for MTF pullback")

        selected_rows = [
            _row_summary(row)
            for _, row in selected.sort_values(["timeframe", "strategy"]).iterrows()
        ]
        baseline_rows = {
            timeframe: _row_summary(
                selected[(selected["strategy"] == "baseline") & (selected["timeframe"] == timeframe)].iloc[0]
            )
            for timeframe in ("15m", "1d")
        }
        class_breakdown = [
            {
                "asset_class": str(row["asset_class"]),
                "assets_included": str(row["assets_included"]),
                "trades": int(row["trades_in_class"]),
                "independent_reference_capital_usd": float(row["independent_reference_capital_usd"]),
                "portfolio_is_additive": bool(row["portfolio_is_additive"]),
                "net_pnl_usd_after_assumed_fees": round(float(row["net_pnl_usd"]), 2),
                "net_return_pct": round(float(row["net_return_pct"]), 4),
                "max_drawdown_pct": round(float(row["max_drawdown_pct"]), 4),
            }
            for _, row in class_rows.iterrows()
        ]

    best = traded.sort_values("net_return_pct", ascending=False).iloc[0]
    worst = traded.sort_values("net_return_pct", ascending=True).iloc[0]
    return {
        "mode": "RESEARCH_ONLY_CORE_10_ASSET_NET_AWARE_OOS_FEE_AUDIT",
        "source": {
            "workflow_run_id": RUN_ID,
            "artifact_id": ARTIFACT_ID,
            "artifact_sha256": ARTIFACT_SHA256,
            "strategy_research_status": status["status"],
            "expected_combinations": status["expected_combinations"],
            "available_combinations": status["available_combinations"],
            "core_assets": 10,
            "core_series": 20,
            "core_history_bounds": coverage,
        },
        "scenario": {
            "broker_scenario": SCENARIO,
            "stock_commission_usd_per_side": 1.0,
            "crypto_fee_pct_per_side": 1.0,
            "etf_commission": 0.0,
            "market_spread_bps": 0,
            "scope": "Assumed lower-fee eToro REAL scenario; zero market spread, not a verified account ticket.",
        },
        "summary": {
            "strategy_timeframe_rows": len(selected),
            "rows_with_trades": len(traded),
            "rows_net_positive": len(positive),
            "rows_net_negative": len(negative),
            "rows_without_trades": len(no_trade),
            "best_traded_row": _row_summary(best),
            "worst_traded_row": _row_summary(worst),
            "baseline_15m": baseline_rows["15m"],
            "baseline_1d": baseline_rows["1d"],
        },
        "asset_class_breakdown_for_mtf_price_confirmed_pullback_15m": class_breakdown,
        "strategy_timeframe_results": selected_rows,
        "limitations": [
            "Calendar status is COMPARABLE_BOUNDS_ONLY: common first/last timestamps do not prove continuous internal candle coverage.",
            "The core universe is five US stocks and five crypto assets; the two configured ETFs are excluded from this common M15 core sample.",
            "Fee inputs are assumed public-schedule scenarios ($1 per stock side, 1% per crypto side, zero ETF commission), not verified instrument/account tickets.",
            "Zero spread is deliberately optimistic; actual bid/ask, slippage and execution-price basis remain unpriced.",
            "No eToro executable-price equivalence is established; this remains research-only and does not approve strategies.",
            "Asset-class rows use independent $1,000 reference capital and are explicitly non-additive.",
        ],
        "approval": "NOT_APPROVED",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit_core_net_aware_oos(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
