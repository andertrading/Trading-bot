"""Research-only sensitivity of Coinbase OOS crypto trades to eToro's published manual-crypto fees."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from research.oos_trade_overlap_audit import _load

# eToro public schedule: fee charged on each manual-crypto position side.
# Rates vary by Club tier and monthly crypto trading-volume bucket.
VOLUME_BUCKETS = ("0-10k", "10k-100k", "100k-250k", "250k-1m", "1m-10m", "10m+")
FEE_SCHEDULE_PCT = {
    "Bronze/Silver/Gold": (1.00, 1.00, 1.00, 1.00, 1.00, 1.00),
    "Platinum": (0.95, 0.90, 0.85, 0.80, 0.70, 0.10),
    "Platinum+": (0.90, 0.85, 0.80, 0.75, 0.50, 0.10),
    "Diamond": (0.85, 0.80, 0.75, 0.70, 0.30, 0.10),
}


def _finite_positive(trade, field):
    value = float(trade[field])
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"invalid positive numeric field {field!r}")
    return value


def _metrics(values):
    wins = sum(value for value in values if value > 0)
    losses = sum(value for value in values if value < 0)
    return {
        "trades": len(values),
        "total_R": round(sum(values), 4),
        "mean_R": round(sum(values) / len(values), 6) if values else None,
        "win_rate_pct": round(100 * sum(value > 0 for value in values) / len(values), 2) if values else None,
        "profit_factor": round(wins / abs(losses), 4) if losses < 0 else None,
    }


def _net_r(trades, fee_pct):
    rate = fee_pct / 100.0
    values = []
    for trade in trades:
        quantity = _finite_positive(trade, "hypothetical_quantity")
        entry = _finite_positive(trade, "entry")
        exit_price = _finite_positive(trade, "exit")
        risk_per_unit = _finite_positive(trade, "initial_risk_per_unit")
        risk_usd = quantity * risk_per_unit
        if not math.isfinite(risk_usd) or risk_usd <= 0:
            raise ValueError("invalid hypothetical risk amount")
        # Fee is charged on the entry notional and the exit notional.
        round_trip_fee_usd = quantity * (entry + exit_price) * rate
        values.append(float(trade["result_R"]) - round_trip_fee_usd / risk_usd)
    return values


def audit_fee_sensitivity(input_dir):
    trades, source = _load(input_dir)
    if any(t.get("costs_included") is not False for t in trades):
        raise ValueError("expected gross OOS results without embedded costs")
    if any(t.get("costs_complete") is not False for t in trades):
        raise ValueError("unexpected claim that source trade costs are complete")
    if any(t.get("cost_model_execution_type") != "REAL" for t in trades):
        raise ValueError("scenario is limited to real/manual spot crypto, not CFDs")
    if any(t.get("etoro_price_equivalence") != "not_established" for t in trades):
        raise ValueError("unexpected eToro execution-equivalence claim")

    scenarios = {}
    for tier, rates in FEE_SCHEDULE_PCT.items():
        scenarios[tier] = {}
        for bucket, fee_pct in zip(VOLUME_BUCKETS, rates):
            net = _net_r(trades, fee_pct)
            scenarios[tier][bucket] = {
                "fee_pct_per_side": fee_pct,
                "metrics": _metrics(net),
            }

    baseline_values = _net_r(trades, 1.00)
    by_asset = {}
    for asset in sorted({t["asset"] for t in trades}):
        subset = [t for t in trades if t["asset"] == asset]
        by_asset[asset] = _metrics(_net_r(subset, 1.00))

    return {
        "mode": "RESEARCH_ONLY_ETORO_PUBLIC_CRYPTO_FEE_SENSITIVITY",
        "source_validation": source,
        "fee_source": "https://www.etoro.com/es/trading/fees/",
        "fee_scope": "Manual crypto positions; published fee rate applied separately on opening and closing notional.",
        "fee_schedule_pct_per_side": {
            tier: {bucket: rate for bucket, rate in zip(VOLUME_BUCKETS, rates)}
            for tier, rates in FEE_SCHEDULE_PCT.items()
        },
        "method": (
            "For each OOS candidate trade: net_R = gross_result_R - "
            "(entry_price * hypothetical_quantity + exit_price * hypothetical_quantity) "
            "* fee_rate / (initial_risk_per_unit * hypothetical_quantity)."
        ),
        "baseline_1pct_per_side": _metrics(baseline_values),
        "by_asset_at_1pct_per_side": by_asset,
        "sensitivity": scenarios,
        "limitations": [
            "This is a fee-only scenario, not verified eToro execution or a portfolio backtest.",
            "The user's Club tier and monthly crypto trading-volume bucket are unknown and were not accessed.",
            "The hypothetical sizes and prices originate from Coinbase-based OOS research, not eToro executable bid/ask prices.",
            "Market spread, actual account-specific charges, execution slippage, and any applicable conversion or other costs are not added.",
            "All source trades remain marked costs_complete=false; this diagnostic does not change the registry or validate net performance.",
        ],
        "approval": "NOT_APPROVED",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit_fee_sensitivity(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
