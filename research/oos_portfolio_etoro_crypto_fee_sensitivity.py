"""Research-only eToro manual-crypto fee sensitivity on accepted portfolio allocations."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

VOLUME_BUCKETS = ("0-10k", "10k-100k", "100k-250k", "250k-1m", "1m-10m", "10m+")
FEE_SCHEDULE_PCT = {
    "Bronze/Silver/Gold": (1.00, 1.00, 1.00, 1.00, 1.00, 1.00),
    "Platinum": (0.95, 0.90, 0.85, 0.80, 0.70, 0.10),
    "Platinum+": (0.90, 0.85, 0.80, 0.75, 0.50, 0.10),
    "Diamond": (0.85, 0.80, 0.75, 0.70, 0.30, 0.10),
}


def _positive(trade, key):
    value = float(trade[key])
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"invalid positive field {key!r}")
    return value


def _metrics(trades, fee_pct):
    rate = fee_pct / 100.0
    gross = []
    fees = []
    adjusted = []
    for trade in trades:
        quantity = _positive(trade, "quantity")
        entry = _positive(trade, "entry")
        exit_price = _positive(trade, "exit_price")
        pnl = float(trade["realized_pnl_usd"])
        if not math.isfinite(pnl):
            raise ValueError("non-finite realized_pnl_usd")
        fee = quantity * (entry + exit_price) * rate
        gross.append(pnl)
        fees.append(fee)
        adjusted.append(pnl - fee)
    gross_total = sum(gross)
    fee_total = sum(fees)
    adjusted_total = sum(adjusted)
    wins = sum(value for value in adjusted if value > 0)
    losses = sum(value for value in adjusted if value < 0)
    return {
        "trades": len(trades),
        "gross_pnl_usd": round(gross_total, 2),
        "fee_only_cost_usd": round(fee_total, 2),
        "fee_only_adjusted_pnl_on_fixed_allocations_usd": round(adjusted_total, 2),
        "fee_only_adjusted_mean_pnl_usd": round(adjusted_total / len(trades), 4) if trades else None,
        "fee_only_adjusted_win_rate_pct": round(100 * sum(value > 0 for value in adjusted) / len(adjusted), 2) if adjusted else None,
        "fee_only_adjusted_profit_factor": round(wins / abs(losses), 4) if losses < 0 else None,
        "fee_to_gross_pnl_multiple": round(fee_total / gross_total, 4) if gross_total > 0 else None,
    }


def audit_portfolio_fee_sensitivity(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("mode") != "RESEARCH_ONLY_OOS_PORTFOLIO_SIMULATION":
        raise ValueError("unexpected portfolio artifact mode")
    source = payload.get("source_validation", {})
    summary = payload.get("summary", {})
    policy = payload.get("portfolio_policy", {})
    if source.get("provider") != "coinbase_exchange_only":
        raise ValueError("unexpected provider")
    if source.get("eToro_price_equivalence") != "not_established":
        raise ValueError("unexpected eToro price-equivalence claim")
    if source.get("verified_transaction_costs") != "not_complete":
        raise ValueError("source does not declare incomplete verified costs")
    if summary.get("costs_included") is not False or summary.get("net_performance_validated") is not False:
        raise ValueError("source must remain gross-only and unvalidated")
    if summary.get("verified_costs_complete_trades") != 0:
        raise ValueError("source unexpectedly reports verified complete costs")
    if policy.get("capital_usd") != 1000.0:
        raise ValueError("unexpected paper capital")
    if policy.get("maximum_risk_per_trade_pct") != 1.0 or policy.get("maximum_aggregate_open_risk_pct") != 3.0:
        raise ValueError("unexpected portfolio risk policy")

    trades = payload.get("accepted_trades")
    if not isinstance(trades, list) or not trades:
        raise ValueError("accepted_trades must be a non-empty list")
    if len(trades) != summary.get("trades_accepted"):
        raise ValueError("accepted trade count does not match summary")
    if any(t.get("costs_included") is not False or t.get("verified_costs_complete") is not False
           or t.get("verified_costs_total_usd") is not None for t in trades):
        raise ValueError("accepted trade carries unexpected verified-cost evidence")

    tiers = {}
    for tier, rates in FEE_SCHEDULE_PCT.items():
        tiers[tier] = {
            bucket: {"fee_pct_per_side": rate, "metrics": _metrics(trades, rate)}
            for bucket, rate in zip(VOLUME_BUCKETS, rates)
        }

    by_asset = {}
    by_timeframe = {}
    for asset in sorted({t["asset"] for t in trades}):
        by_asset[asset] = _metrics([t for t in trades if t["asset"] == asset], 1.0)
    for timeframe in sorted({t["timeframe"] for t in trades}):
        by_timeframe[timeframe] = _metrics([t for t in trades if t["timeframe"] == timeframe], 1.0)

    return {
        "mode": "RESEARCH_ONLY_ACCEPTED_PORTFOLIO_ETORO_CRYPTO_FEE_SENSITIVITY",
        "source": {
            "workflow_run_id": 45257891167,
            "artifact_id": 14859052638,
            "artifact_sha256": "a4886b52920572f67e9740c34db81b493a2d5761971f973645793d0edec36703",
            "source_validation": source,
            "gross_portfolio_summary": summary,
            "portfolio_policy": policy,
        },
        "fee_source": "https://www.etoro.com/es/trading/fees/",
        "fee_scope": "Manual crypto fee applied separately to each accepted trade's entry and exit notional.",
        "method": "Fee USD = quantity * (entry + exit_price) * per_side_fee_pct / 100. Adjusted trade PnL = realized gross PnL - fee USD.",
        "sensitivity": tiers,
        "by_asset_at_1pct_per_side": by_asset,
        "by_timeframe_at_1pct_per_side": by_timeframe,
        "limitations": [
            "Ex-post fee-only sensitivity on the accepted gross allocations; the portfolio allocator is not rerun after fees reduce equity.",
            "Club tier and monthly crypto trading-volume bucket are unknown and were not accessed.",
            "Coinbase prices are not established as eToro executable bid/ask prices.",
            "Assumes manual real-crypto execution; instrument-specific eToro execution classification is not confirmed by this artifact.",
            "Market spread, slippage, and any other unpriced charges are excluded.",
            "This is not a verified net portfolio backtest; source trades remain costs_unverified and no approval is implied.",
        ],
        "approval": "NOT_APPROVED",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit_portfolio_fee_sensitivity(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
