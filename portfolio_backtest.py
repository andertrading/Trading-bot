"""Portfolio-level, long-only backtest allocator.

This module combines independently simulated trade outcomes in chronological
order. It is a conservative risk/cash allocation layer, not a tick-level
execution engine. Optional broker-cost scenarios debit entry and exit costs
from equity and therefore affect subsequent position sizing.
"""
import math

import pandas as pd

from config import (
    ASSETS,
    INITIAL_CAPITAL_USD,
    MAX_PORTFOLIO_RISK,
    MAX_RISK_PER_TRADE,
)
from risk_manager import risk_pct_for_score


HARD_MAX_RISK_PER_TRADE = 0.01
HARD_MAX_PORTFOLIO_RISK = 0.03


def _finite(value):
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _timestamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("timestamp inválido")
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _empty_result(capital, costs_included=False):
    return {
        "summary": {
            "initial_capital_usd": float(capital),
            "ending_equity_usd": float(capital),
            "gross_pnl_usd": 0.0,
            "gross_return_pct": 0.0,
            "pnl_basis": "GROSS_EX_COSTS" if not costs_included else "NET_AFTER_ASSUMED_COSTS",
            "trades_considered": 0,
            "trades_accepted": 0,
            "trades_rejected": 0,
            "win_rate_pct": 0.0,
            "profit_factor": None,
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "peak_open_risk_usd": 0.0,
            "peak_open_risk_pct_of_equity": 0.0,
            "risk_limit_breach_events": 0,
            "peak_open_notional_usd": 0.0,
            "portfolio_risk_limit_pct": 0.0,
            "costs_included": bool(costs_included),
        },
        "trades": [],
        "rejections": [],
    }


def simulate_portfolio(
    trades,
    capital_usd=None,
    max_portfolio_risk_pct=None,
    max_risk_per_trade_pct=None,
    execution_costs=None,
):
    """Allocate simultaneous long trades under shared risk and cash limits.

    Optional execution_costs applies entry costs immediately and exit costs
    at closure, so subsequent position sizing uses net realized equity.

    Entry events at a timestamp are allocated before exits at that same
    timestamp. This intentionally does not release capital early when an
    existing trade exits on the same timestamp as a new entry.

    Simultaneous candidates receive proportional allocation, avoiding an
    arbitrary alphabetical/asset-priority advantage. Risk budgets are based
    on current realized equity; open risk and notional are reserved until exit.
    """
    capital = INITIAL_CAPITAL_USD if capital_usd is None else capital_usd
    portfolio_pct = (
        MAX_PORTFOLIO_RISK
        if max_portfolio_risk_pct is None
        else max_portfolio_risk_pct
    )
    trade_pct = (
        MAX_RISK_PER_TRADE
        if max_risk_per_trade_pct is None
        else max_risk_per_trade_pct
    )

    if not _finite(capital) or float(capital) <= 0:
        raise ValueError("capital_usd debe ser positivo y finito")
    if not _finite(portfolio_pct) or float(portfolio_pct) <= 0:
        raise ValueError("max_portfolio_risk_pct debe ser positivo y finito")
    if not _finite(trade_pct) or float(trade_pct) <= 0:
        raise ValueError("max_risk_per_trade_pct debe ser positivo y finito")

    capital = float(capital)
    portfolio_limit_pct = min(float(portfolio_pct), HARD_MAX_PORTFOLIO_RISK)
    per_trade_limit_pct = min(float(trade_pct), HARD_MAX_RISK_PER_TRADE)

    if execution_costs is not None and not isinstance(execution_costs, dict):
        raise ValueError("execution_costs debe ser un objeto")
    cost_model = dict(execution_costs or {})
    spread_bps = float(cost_model.get("spread_bps", 0.0))
    if not _finite(spread_bps) or spread_bps < 0 or spread_bps > 5000:
        raise ValueError("spread_bps debe estar entre 0 y 5000")
    venue = str(cost_model.get("venue", "ETORO_REAL")).upper()
    if venue not in {"ETORO_REAL", "BITVAVO_MAKER", "BITVAVO_TAKER"}:
        raise ValueError("venue de costes no admitido")
    crypto_rate = float(cost_model.get(
        "crypto_rate_per_side",
        0.01 if venue == "ETORO_REAL" else 0.0015 if venue == "BITVAVO_MAKER" else 0.0025,
    ))
    stock_fee = float(cost_model.get("stock_fee_per_side_usd", 1.0))
    crypto_rate_by_asset = cost_model.get("crypto_rate_by_asset", {})
    if not _finite(crypto_rate) or not 0 <= crypto_rate <= 0.1:
        raise ValueError("crypto_rate_per_side inválido")
    if not _finite(stock_fee) or not 0 <= stock_fee <= 100:
        raise ValueError("stock_fee_per_side_usd inválido")
    if not isinstance(crypto_rate_by_asset, dict):
        raise ValueError("crypto_rate_by_asset debe ser un objeto")
    normalized_crypto_rates = {}
    for raw_asset, raw_rate in crypto_rate_by_asset.items():
        asset_key = str(raw_asset).strip().upper()
        settings = ASSETS.get(asset_key)
        if not isinstance(settings, dict) or str(settings.get("type", "")).upper() != "CRYPTO":
            raise ValueError("crypto_rate_by_asset contiene un activo no cripto")
        if not _finite(raw_rate) or not 0 <= float(raw_rate) <= 0.1:
            raise ValueError("crypto_rate_by_asset contiene una tasa inválida")
        normalized_crypto_rates[asset_key] = float(raw_rate)

    def crypto_rate_for_asset(asset):
        return normalized_crypto_rates.get(asset, crypto_rate)

    costs_included = execution_costs is not None
    result = _empty_result(capital, costs_included=costs_included)
    result["summary"]["trades_considered"] = len(trades or [])
    result["summary"]["portfolio_risk_limit_pct"] = portfolio_limit_pct * 100

    entries = {}
    exits = {}
    rejected = []
    normalized = []

    for index, trade in enumerate(trades or []):
        reason = None
        if not isinstance(trade, dict):
            reason = "registro_no_es_objeto"
        else:
            asset = str(trade.get("asset", "")).strip().upper()
            settings = ASSETS.get(asset)
            if not asset or not isinstance(settings, dict):
                reason = "activo_no_configurado"
            elif str(settings.get("currency", "")).upper() != "USD":
                reason = "moneda_distinta_de_usd_no_admitida"
            elif str(trade.get("timeframe", "")).strip() not in ("15m", "1d"):
                reason = "temporalidad_no_admitida"

        if reason is None:
            try:
                entry_time = _timestamp(trade["entry_time"])
                exit_time = _timestamp(trade["exit_time"])
                entry = float(trade["entry"])
                stop = float(trade["stop"])
                score = float(trade["score"])
                result_r = float(trade["result_R"])
                exit_price = float(trade.get("exit", entry + result_r * (entry - stop)))
                if not all(_finite(v) for v in (entry, stop, score, result_r, exit_price)):
                    raise ValueError("valor no finito")
                if (
                    entry <= 0
                    or stop <= 0
                    or stop >= entry
                    or (execution_costs is not None and exit_price <= 0)
                ):
                    raise ValueError("niveles long inválidos")
                if exit_time < entry_time:
                    raise ValueError("salida anterior a entrada")
                requested_pct = min(
                    risk_pct_for_score(score),
                    per_trade_limit_pct,
                    HARD_MAX_RISK_PER_TRADE,
                )
                if requested_pct <= 0:
                    raise ValueError("score inferior al mínimo de riesgo")
                normalized.append({
                    "source_index": index,
                    "asset": asset,
                    "timeframe": str(trade["timeframe"]),
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "entry": entry,
                    "stop": stop,
                    "score": score,
                    "result_R": result_r,
                    "exit_price": exit_price,
                    "requested_risk_pct": requested_pct,
                    "risk_per_unit": entry - stop,
                })
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                reason = "datos_de_operacion_invalidos: " + str(exc)

        if reason:
            rejected.append({
                "source_index": index,
                "asset": trade.get("asset") if isinstance(trade, dict) else None,
                "timeframe": trade.get("timeframe") if isinstance(trade, dict) else None,
                "reason": reason,
            })

    for item in normalized:
        entries.setdefault(item["entry_time"], []).append(item)
        exits.setdefault(item["exit_time"], []).append(item)

    timeline = sorted(set(entries) | set(exits))
    open_positions = {}
    accepted = []
    equity = capital
    gross_total_pnl = 0.0
    total_entry_cost = 0.0
    total_exit_cost = 0.0
    equity_peak = capital
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    peak_open_risk = 0.0
    peak_open_risk_pct = 0.0
    risk_limit_breach_events = 0
    peak_open_notional = 0.0

    for event_time in timeline:
        # New entries are allocated while positions exiting at this timestamp
        # still reserve risk and cash: conservative same-bar ordering.
        batch = sorted(
            entries.get(event_time, []),
            key=lambda item: (-item["score"], item["asset"], item["timeframe"], item["source_index"]),
        )

        open_risk = sum(p["allocated_risk_usd"] for p in open_positions.values())
        open_notional = sum(p["notional_usd"] for p in open_positions.values())
        reserved_exit_cost = sum(
            p.get("reserved_exit_cost_usd", 0.0)
            for p in open_positions.values()
        )
        equity_for_limits = max(0.0, equity)
        available_risk = max(
            0.0,
            equity_for_limits * portfolio_limit_pct - open_risk,
        )
        available_cash = max(
            0.0,
            equity_for_limits - open_notional - reserved_exit_cost,
        )

        requests = []
        for item in batch:
            requested_risk = equity_for_limits * item["requested_risk_pct"]
            quantity_per_risk = 1.0 / item["risk_per_unit"]
            notional_per_risk = item["entry"] * quantity_per_risk
            requested_notional = requested_risk * notional_per_risk
            requests.append((item, requested_risk, requested_notional))

        total_requested_risk = sum(row[1] for row in requests)
        total_requested_notional = sum(row[2] for row in requests)
        risk_factor = (
            min(1.0, available_risk / total_requested_risk)
            if total_requested_risk > 0 else 0.0
        )
        # Entry fees reduce both spendable cash and the equity denominator
        # used by the hard per-trade and aggregate risk limits.
        def batch_entry_costs(factor):
            if factor <= 1e-12 or not costs_included:
                return 0.0
            entry_fees = 0.0
            for item, requested_risk, _ in requests:
                allocated = requested_risk * factor
                quantity = allocated / item["risk_per_unit"]
                entry_notional = item["entry"] * quantity
                asset_type = str(ASSETS[item["asset"]].get("type", "")).upper()
                if asset_type == "STOCK":
                    fee = stock_fee
                elif asset_type == "ETF":
                    fee = 0.0
                else:
                    fee = entry_notional * crypto_rate_for_asset(item["asset"])
                fee += entry_notional * spread_bps / 20000.0
                entry_fees += fee
            return entry_fees

        def risk_constraints_satisfied(factor):
            post_entry_equity = max(
                0.0,
                equity_for_limits - batch_entry_costs(factor),
            )
            allocated_risk_total = total_requested_risk * factor
            if (
                open_risk + allocated_risk_total
                > portfolio_limit_pct * post_entry_equity + 1e-10
            ):
                return False
            return all(
                requested_risk * factor
                <= per_trade_limit_pct * post_entry_equity + 1e-10
                for _, requested_risk, _ in requests
            )

        # Solve the risk cap after entry costs; a fixed commission makes the
        # relationship piecewise, so use a bounded monotonic binary search.
        low, high = 0.0, risk_factor
        if risk_constraints_satisfied(high):
            risk_factor = high
        else:
            for _ in range(60):
                middle = (low + high) / 2.0
                if risk_constraints_satisfied(middle):
                    low = middle
                else:
                    high = middle
            risk_factor = low

        # Reserve both notional and entry fees. Fixed stock fees do not shrink
        # with a smaller position, so calculate the cash constraint separately.
        def batch_cash_usage(factor):
            if factor <= 1e-12:
                return 0.0
            # Reserve a second side of estimated fees at entry. This keeps
            # fixed stock commissions from overdrawing a tiny remaining
            # account balance when the position later closes.
            return (
                total_requested_notional * factor
                + 2.0 * batch_entry_costs(factor)
            )

        low, high = 0.0, risk_factor
        if batch_cash_usage(high) <= available_cash:
            cash_factor = high
        else:
            for _ in range(60):
                middle = (low + high) / 2.0
                if batch_cash_usage(middle) <= available_cash:
                    low = middle
                else:
                    high = middle
            cash_factor = low
        allocation_factor = min(risk_factor, cash_factor)

        for item, requested_risk, requested_notional in requests:
            allocated_risk = requested_risk * allocation_factor
            notional = requested_notional * allocation_factor
            if allocated_risk <= 1e-12 or notional <= 1e-12:
                rejected.append({
                    "source_index": item["source_index"],
                    "asset": item["asset"],
                    "timeframe": item["timeframe"],
                    "reason": "limite_de_riesgo_o_capital_disponible",
                })
                continue

            quantity = allocated_risk / item["risk_per_unit"]
            asset_type = str(ASSETS[item["asset"]].get("type", "")).upper()
            entry_notional = item["entry"] * quantity
            if not costs_included:
                entry_cost = 0.0
            elif asset_type == "STOCK":
                entry_cost = stock_fee
            elif asset_type == "ETF":
                entry_cost = 0.0
            else:
                entry_cost = entry_notional * crypto_rate_for_asset(item["asset"])
            entry_cost += entry_notional * spread_bps / 20000.0 if costs_included else 0.0
            total_entry_cost += entry_cost
            position = {
                **item,
                "entry_cost_usd": entry_cost,
                "reserved_exit_cost_usd": entry_cost,
                "asset_type": asset_type,
                "allocated_risk_usd": allocated_risk,
                "notional_usd": notional,
                "quantity": quantity,
                "allocation_factor": allocation_factor,
            }
            open_positions[item["source_index"]] = position
            accepted.append(position)

        open_risk = sum(p["allocated_risk_usd"] for p in open_positions.values())
        open_notional = sum(p["notional_usd"] for p in open_positions.values())
        peak_open_risk = max(peak_open_risk, open_risk)
        peak_open_notional = max(peak_open_notional, open_notional)
        event_risk_breach = False
        if equity > 0:
            current_open_risk_pct = open_risk / equity * 100
            peak_open_risk_pct = max(peak_open_risk_pct, current_open_risk_pct)
            event_risk_breach = current_open_risk_pct > portfolio_limit_pct * 100 + 1e-10
        elif open_risk > 0:
            event_risk_breach = True

        # Realize exits only after allocating entries at this timestamp.
        closed_at_timestamp = []
        timestamp_pnl = -sum(
            p["entry_cost_usd"] for p in accepted
            if p["entry_time"] == event_time
        )
        for item in sorted(
            exits.get(event_time, []),
            key=lambda row: row["source_index"],
        ):
            position = open_positions.pop(item["source_index"], None)
            if position is None:
                continue
            # Use the actual simulated execution prices and allocated
            # quantity. result_R is rounded in the research CSV and can
            # otherwise accumulate small portfolio-level P&L discrepancies.
            pnl = (
                position["exit_price"] - position["entry"]
            ) * position["quantity"]
            if not costs_included:
                exit_cost = 0.0
            elif position["asset_type"] == "STOCK":
                exit_cost = stock_fee
            elif position["asset_type"] == "ETF":
                exit_cost = 0.0
            else:
                exit_cost = 0.0
            exit_notional = position["exit_price"] * position["quantity"]
            if position["asset_type"] == "CRYPTO" and costs_included:
                exit_cost = exit_notional * crypto_rate_for_asset(position["asset"])
            exit_cost += exit_notional * spread_bps / 20000.0 if costs_included else 0.0
            total_exit_cost += exit_cost
            position["realized_pnl_usd"] = pnl
            position["exit_cost_usd"] = exit_cost
            position["net_realized_pnl_usd"] = pnl - exit_cost - position["entry_cost_usd"]
            position["status"] = "CLOSED"
            closed_at_timestamp.append(position)
            timestamp_pnl += pnl - exit_cost

        # All exits at one timestamp are realized as a single portfolio event;
        # the arbitrary order of same-time instruments must not distort DD.
        if closed_at_timestamp or timestamp_pnl:
            equity += timestamp_pnl
            gross_total_pnl += sum(p["realized_pnl_usd"] for p in closed_at_timestamp)
            equity_peak = max(equity_peak, equity)
            current_drawdown = equity_peak - equity
            max_drawdown = max(max_drawdown, current_drawdown)
            current_drawdown_pct = (
                current_drawdown / equity_peak * 100
                if equity_peak > 0 else 0.0
            )
            max_drawdown_pct = max(
                max_drawdown_pct,
                current_drawdown_pct,
            )
            # A realized loss can increase the percentage risk of positions
            # that remain open, even though their USD stop risk is unchanged.
            remaining_open_risk = sum(
                p["allocated_risk_usd"] for p in open_positions.values()
            )
            if equity > 0:
                current_open_risk_pct = remaining_open_risk / equity * 100
                peak_open_risk_pct = max(peak_open_risk_pct, current_open_risk_pct)
                event_risk_breach = event_risk_breach or (
                    current_open_risk_pct > portfolio_limit_pct * 100 + 1e-10
                )
            elif remaining_open_risk > 0:
                event_risk_breach = True
            for position in closed_at_timestamp:
                position["equity_after_exit_usd"] = equity

        if event_risk_breach:
            risk_limit_breach_events += 1

    accepted_rows = []
    for position in accepted:
        accepted_rows.append({
            "asset": position["asset"],
            "timeframe": position["timeframe"],
            "entry_time": position["entry_time"].isoformat(),
            "exit_time": position["exit_time"].isoformat(),
            "score": position["score"],
            "entry": position["entry"],
            "stop": position["stop"],
            "result_R": position["result_R"],
            "requested_risk_pct": position["requested_risk_pct"],
            "allocated_risk_usd": round(position["allocated_risk_usd"], 8),
            "quantity": round(position["quantity"], 10),
            "notional_usd": round(position["notional_usd"], 8),
            "allocation_factor": round(position["allocation_factor"], 8),
            "realized_pnl_usd": round(position.get("realized_pnl_usd", 0.0), 8),
            "entry_cost_usd": round(position.get("entry_cost_usd", 0.0), 8),
            "reserved_exit_cost_usd": round(position.get("reserved_exit_cost_usd", 0.0), 8),
            "exit_cost_usd": round(position.get("exit_cost_usd", 0.0), 8),
            "net_realized_pnl_usd": round(position.get("net_realized_pnl_usd", 0.0), 8),
            "equity_after_exit_usd": round(position.get("equity_after_exit_usd", equity), 8),
            "costs_included": costs_included,
        })

    pnl_values = [
        row["net_realized_pnl_usd"] if costs_included else row["realized_pnl_usd"]
        for row in accepted_rows
    ]
    gains = sum(value for value in pnl_values if value > 0)
    losses = abs(sum(value for value in pnl_values if value < 0))
    summary = result["summary"]
    summary.update({
        "ending_equity_usd": round(equity, 8),
        "gross_pnl_usd": round(gross_total_pnl if costs_included else equity - capital, 8),
        "gross_return_pct": round(((capital + (gross_total_pnl if costs_included else equity - capital)) / capital - 1) * 100, 6),
        "pnl_basis": "NET_AFTER_ASSUMED_COSTS" if costs_included else "GROSS_EX_COSTS",
        "trades_accepted": len(accepted_rows),
        "trades_rejected": len(rejected),
        "win_rate_pct": round(
            sum(value > 0 for value in pnl_values) / len(pnl_values) * 100,
            4,
        ) if pnl_values else 0.0,
        "profit_factor": round(gains / losses, 6) if losses > 0 else (None if gains == 0 else None),
        "max_drawdown_usd": round(max_drawdown, 8),
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "peak_open_risk_usd": round(peak_open_risk, 8),
        "peak_open_risk_pct_of_equity": round(peak_open_risk_pct, 6),
        "risk_limit_breach_events": risk_limit_breach_events,
        "peak_open_notional_usd": round(peak_open_notional, 8),
        "costs_included": costs_included,
    })
    if costs_included:
        summary.update({
            "net_pnl_usd": round(equity - capital, 8),
            "net_return_pct": round((equity / capital - 1) * 100, 6),
            "total_entry_cost_usd": round(total_entry_cost, 8),
            "total_exit_cost_usd": round(total_exit_cost, 8),
        })
    result["trades"] = accepted_rows
    result["rejections"] = rejected
    return result
