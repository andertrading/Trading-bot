"""Prospective, execution-free paper portfolio engine.

This module simulates only research signals against subsequent completed OHLC
bars. It has no broker/API integration and never places or manages real orders.
Gross results are reported separately; transaction costs are deliberately
unpriced until the instrument-specific eToro execution model is verified.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

PAPER_SCHEMA_VERSION = 1
DEFAULT_INITIAL_CAPITAL_USD = 1000.0
DEFAULT_RISK_PER_TRADE = 0.0025
MAX_RISK_PER_TRADE = 0.01
MAX_PORTFOLIO_RISK = 0.03


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def _timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def new_paper_state(initial_capital_usd: float = DEFAULT_INITIAL_CAPITAL_USD) -> dict:
    capital = _number(initial_capital_usd, "initial_capital_usd")
    return {
        "schema_version": PAPER_SCHEMA_VERSION,
        "currency": "USD",
        "initial_capital_usd": capital,
        "equity_usd": capital,
        "peak_equity_usd": capital,
        "realized_gross_pnl_usd": 0.0,
        "realized_costs_usd": 0.0,
        "realized_net_pnl_usd": 0.0,
        "equity_history": [{"timestamp": None, "equity_usd": capital}],
        "open_risk_usd": 0.0,
        "trades": {},
        "processed_bars": {},
        "costs_status": "NOT_INCLUDED_UNVERIFIED",
        "execution_mode": "PAPER_ONLY",
    }


def _validate_state(state: dict) -> None:
    if not isinstance(state, dict) or state.get("schema_version") != PAPER_SCHEMA_VERSION:
        raise ValueError("unsupported paper state schema")
    if not isinstance(state.get("trades"), dict) or not isinstance(state.get("processed_bars"), dict):
        raise ValueError("paper state collections are invalid")
    for field in ("initial_capital_usd", "equity_usd", "peak_equity_usd"):
        _number(state.get(field), field)


def _open_risk(state: dict) -> float:
    return sum(
        float(trade.get("risk_reserved_usd", 0.0))
        for trade in state["trades"].values()
        if trade.get("status") == "OPEN"
    )


def _open_notional(state: dict) -> float:
    """Reserve entry notional plus conservative verified cost reserves."""
    return sum(
        float(trade.get("cash_reserved_usd", trade.get("notional_usd", 0.0)))
        for trade in state["trades"].values()
        if trade.get("status") == "OPEN"
    )


def paper_portfolio_snapshot(
    state: dict,
    expected_initial_capital_usd: float = DEFAULT_INITIAL_CAPITAL_USD,
) -> dict:
    """Return a validated cash/risk snapshot from the isolated paper ledger.

    Gross-only realized gains are not allowed to increase the risk/cash base
    above the original reference capital while trading costs remain unverified.
    Open positions must carry both reserved risk and notional evidence.
    """
    _validate_state(state)
    if state.get("execution_mode") != "PAPER_ONLY" or state.get("currency") != "USD":
        raise ValueError("paper portfolio must be PAPER_ONLY and denominated in USD")

    expected_capital = _number(
        expected_initial_capital_usd, "expected_initial_capital_usd"
    )
    initial_capital = _number(
        state.get("initial_capital_usd"), "initial_capital_usd"
    )
    if not math.isclose(initial_capital, expected_capital, rel_tol=0.0, abs_tol=1e-8):
        raise ValueError("paper portfolio initial capital does not match the configured reference")

    equity = _number(state.get("equity_usd"), "equity_usd")
    # Costs are not priced in the forward ledger yet. Never let gross gains
    # enlarge the capital base; realized gross losses still reduce it.
    capital_base = min(equity, initial_capital)

    open_positions = []
    open_risk_usd = 0.0
    invested_notional_usd = 0.0
    cash_reserved_usd = 0.0
    for signal_id, trade in state["trades"].items():
        if not isinstance(trade, dict):
            raise ValueError("paper trade record is invalid")
        if trade.get("status") != "OPEN":
            continue
        risk_usd = _number(
            trade.get("risk_reserved_usd"), f"{signal_id}.risk_reserved_usd"
        )
        notional_usd = _number(
            trade.get("notional_usd"), f"{signal_id}.notional_usd"
        )
        open_risk_usd += risk_usd
        invested_notional_usd += notional_usd
        trade_cash_reserved = _number(
            trade.get("cash_reserved_usd", notional_usd),
            f"{signal_id}.cash_reserved_usd",
        )
        if trade_cash_reserved + 1e-9 < notional_usd:
            raise ValueError("paper trade cash reserve cannot be below its notional")
        cash_reserved_usd += trade_cash_reserved
        if (
            not math.isfinite(open_risk_usd)
            or not math.isfinite(invested_notional_usd)
            or not math.isfinite(cash_reserved_usd)
        ):
            raise ValueError("paper portfolio open-position totals are invalid")
        open_positions.append({
            "signal_id": str(signal_id),
            "asset": str(trade.get("asset") or ""),
            "risk_usd": risk_usd,
            "notional_usd": notional_usd,
            "cash_reserved_usd": trade_cash_reserved,
            "estimated_cost_reserve_usd": max(0.0, trade_cash_reserved - notional_usd),
        })

    available_capital_usd = capital_base - cash_reserved_usd
    if available_capital_usd < -1e-8:
        raise ValueError("paper portfolio open notional exceeds conservative capital")
    available_capital_usd = max(0.0, available_capital_usd)

    reported_costs_status = state.get("costs_status")
    if reported_costs_status not in {
        "NOT_INCLUDED_UNVERIFIED",
        "VERIFIED_COSTS_RESERVED_AT_STOP",
        "VERIFIED_NET_COSTS_INCLUDED",
        "UNRESOLVED_CLOSE_COSTS",
    }:
        reported_costs_status = "NOT_INCLUDED_UNVERIFIED"

    return {
        "execution_mode": "PAPER_ONLY",
        "portfolio_basis": (
            "PAPER_NET_CONSERVATIVE_CAPITAL"
            if reported_costs_status == "VERIFIED_NET_COSTS_INCLUDED"
            else "PAPER_GROSS_CONSERVATIVE_CAPITAL"
        ),
        "initial_capital_usd": initial_capital,
        "equity_usd": equity,
        "capital_usd": capital_base,
        "open_positions": open_positions,
        "positions_confirmed": True,
        "current_portfolio_risk_usd": open_risk_usd,
        "invested_notional_usd": invested_notional_usd,
        "estimated_cost_reserve_usd": max(0.0, cash_reserved_usd - invested_notional_usd),
        "cash_reserved_usd": cash_reserved_usd,
        "available_capital_usd": available_capital_usd,
        # The forward engine does not yet debit verified net costs.
        # Never allow a persisted flag to claim otherwise.
        "costs_status": reported_costs_status,
    }


def _bar_values(bar: dict) -> tuple[datetime, float, float, float, float]:
    if not isinstance(bar, dict):
        raise ValueError("bar must be a dictionary")
    timestamp = _timestamp(bar.get("timestamp"), "bar.timestamp")
    opening = _number(bar.get("open"), "bar.open")
    high = _number(bar.get("high"), "bar.high")
    low = _number(bar.get("low"), "bar.low")
    close = _number(bar.get("close"), "bar.close")
    if low > min(opening, close) or high < max(opening, close) or high < low:
        raise ValueError("bar OHLC envelope is inconsistent")
    return timestamp, opening, high, low, close


def _levels(signal: dict) -> tuple[float, float, float]:
    entry = _number(signal.get("entry_reference"), "signal.entry_reference")
    stop = _number(signal.get("stop"), "signal.stop")
    target = _number(signal.get("target"), "signal.target")
    direction = str(signal.get("direction", "LONG")).upper()
    if direction != "LONG" or not stop < entry < target:
        raise ValueError("paper engine currently accepts LONG signals with stop < entry < target")
    return entry, stop, target


def _finish_trade(
    state: dict,
    trade: dict,
    exit_price: float,
    exit_timestamp: str,
    reason: str,
    *,
    economic_registry: dict | None = None,
) -> None:
    quantity = float(trade["quantity"])
    pnl = (exit_price - float(trade["entry_fill_price"])) * quantity
    costs = None
    net_pnl = None
    if trade.get("cost_mode") == "VERIFIED_REGISTRY":
        from transaction_costs import estimate_verified_registry_trade_costs

        estimate = estimate_verified_registry_trade_costs(
            str(trade.get("asset") or ""),
            float(trade["entry_fill_price"]),
            exit_price,
            quantity,
            registry=economic_registry,
        )
        if estimate.get("complete"):
            costs = float(estimate["known_costs_total"])
            net_pnl = pnl - costs
            state["realized_costs_usd"] = float(state.get("realized_costs_usd", 0.0)) + costs
            state["realized_net_pnl_usd"] = float(state.get("realized_net_pnl_usd", 0.0)) + net_pnl
            state["costs_status"] = "VERIFIED_NET_COSTS_INCLUDED"
            state["equity_usd"] += net_pnl
        else:
            trade["costs_blockers"] = list(estimate.get("unpriced_costs", []))
            state["costs_status"] = "UNRESOLVED_CLOSE_COSTS"
            # Do not book a gross gain as spendable equity when close costs
            # unexpectedly become unavailable. Gross losses remain conservative.
            state["equity_usd"] += min(pnl, 0.0)
    else:
        state["equity_usd"] += pnl

    trade.update({
        "status": "CLOSED",
        "exit_fill_price": exit_price,
        "exit_timestamp": exit_timestamp,
        "exit_reason": reason,
        "gross_pnl_usd": pnl,
        "result_r": pnl / float(trade["risk_budget_usd"]),
        "risk_reserved_usd": 0.0,
        "costs_usd": costs,
        "net_pnl_usd": net_pnl,
        "result_r_net": (
            net_pnl / float(trade["risk_budget_usd"])
            if net_pnl is not None else None
        ),
    })
    state["realized_gross_pnl_usd"] += pnl
    state["peak_equity_usd"] = max(state["peak_equity_usd"], state["equity_usd"])
    state.setdefault("equity_history", []).append({
        "timestamp": exit_timestamp,
        "equity_usd": state["equity_usd"],
    })


def process_signal_bar(
    state: dict,
    signal: dict,
    bar: dict,
    *,
    risk_fraction: float = DEFAULT_RISK_PER_TRADE,
    max_risk_per_trade: float = MAX_RISK_PER_TRADE,
    max_portfolio_risk: float = MAX_PORTFOLIO_RISK,
    require_verified_costs: bool = False,
    economic_registry: dict | None = None,
) -> dict:
    """Advance one signal by one *new completed* bar; mutate and return state.

    Same-bar entry/stop ambiguity is resolved against the strategy: if both
    entry and stop are touched, no fill is assumed (stop-first path). For an
    already-open trade, if stop and target are both touched, stop wins. Stop
    gaps fill at the bar open; favorable target gaps fill at the bar open.
    """
    _validate_state(state)
    if not isinstance(signal, dict) or not signal.get("signal_id"):
        raise ValueError("signal must contain signal_id")
    signal_id = str(signal["signal_id"])
    timestamp, opening, high, low, close = _bar_values(bar)
    prior_bar = state["processed_bars"].get(signal_id)
    if prior_bar and timestamp <= _timestamp(prior_bar, "processed_bar"):
        return state

    trade = state["trades"].get(signal_id)
    if trade is None:
        trade = {"signal_id": signal_id, "asset": signal.get("asset"),
                 "timeframe": signal.get("timeframe"), "status": "WAITING"}
        state["trades"][signal_id] = trade

    if trade["status"] in {"WAITING", "OPEN"}:
        signal_time = _timestamp(signal.get("data_timestamp") or signal.get("generated_at"),
                                  "signal.data_timestamp")
        expiry = _timestamp(signal.get("expires_at"), "signal.expires_at")
        if timestamp > signal_time and trade["status"] == "WAITING":
            entry, stop, target = _levels(signal)
            if timestamp <= expiry and signal.get("status", "PENDING") == "PENDING":
                entry_touched = high >= entry
                stop_touched = low <= stop
                target_touched = high >= target
                # Ambiguous path: assume stop happened before entry; do not
                # manufacture a fill from an OHLC candle with unknown ordering.
                if entry_touched and stop_touched:
                    trade.update({"status": "NO_FILL_AMBIGUOUS_ENTRY_STOP",
                                  "completed_at": timestamp.isoformat()})
                elif stop_touched:
                    # A stop touch before entry invalidates the setup; never
                    # allow a later candle to fill the stale entry.
                    trade.update({"status": "NO_FILL_STOP_TOUCHED",
                                  "completed_at": timestamp.isoformat()})
                elif opening >= target:
                    trade.update({"status": "NO_FILL_GAP_PAST_TARGET",
                                  "completed_at": timestamp.isoformat()})
                elif entry_touched:
                    fill = max(entry, opening)
                    if (
                        isinstance(risk_fraction, bool)
                        or not math.isfinite(float(risk_fraction))
                        or float(risk_fraction) <= 0
                    ):
                        # Low-quality candidates can exist for diagnostics,
                        # but a zero provisional risk must never become a fill.
                        trade.update({
                            "status": "REJECTED_INVALID_RISK_POLICY",
                            "completed_at": timestamp.isoformat(),
                        })
                    else:
                        risk_fraction = _number(risk_fraction, "risk_fraction")
                        max_risk_per_trade = _number(max_risk_per_trade, "max_risk_per_trade")
                        max_portfolio_risk = _number(max_portfolio_risk, "max_portfolio_risk")
                        if risk_fraction > max_risk_per_trade or max_risk_per_trade > MAX_RISK_PER_TRADE:
                            raise ValueError("per-trade risk policy exceeds the hard 1% cap")
                        if max_portfolio_risk > MAX_PORTFOLIO_RISK:
                            raise ValueError("portfolio risk policy exceeds the hard 3% cap")
                        equity = float(state["equity_usd"])
                        capital_base = min(
                            equity,
                            float(state["initial_capital_usd"]),
                        )
                        risk_budget = capital_base * risk_fraction
                        reserved = _open_risk(state)
                        cost_sizing = None
                        if require_verified_costs:
                            cost_sizing = calculate_verified_cost_aware_quantity(
                                str(signal.get("asset") or ""),
                                fill,
                                stop,
                                risk_budget,
                                registry=economic_registry,
                            )
                            if not cost_sizing.get("valid"):
                                trade.update({
                                    "status": "REJECTED_COSTS_UNVERIFIED",
                                    "costs_blockers": list(cost_sizing.get("unpriced_costs", [])),
                                    "completed_at": timestamp.isoformat(),
                                })
                        if not require_verified_costs or (cost_sizing and cost_sizing.get("valid")):
                            quantity = (
                                float(cost_sizing["quantity"])
                                if cost_sizing else risk_budget / (fill - stop)
                            )
                            risk_to_reserve = (
                                float(cost_sizing["reserved_stop_risk_usd"])
                                if cost_sizing else risk_budget
                            )
                            notional = (
                                float(cost_sizing["notional_usd"])
                                if cost_sizing else quantity * fill
                            )
                            cash_reserved = notional + (
                                float(cost_sizing["estimated_round_trip_costs_usd"])
                                if cost_sizing else 0.0
                            )
                            if reserved + risk_to_reserve > capital_base * max_portfolio_risk + 1e-9:
                                trade.update({"status": "REJECTED_PORTFOLIO_RISK",
                                              "completed_at": timestamp.isoformat()})
                            elif _open_notional(state) + cash_reserved > capital_base + 1e-9:
                                trade.update({"status": "REJECTED_NO_LEVERAGE_CASH",
                                              "completed_at": timestamp.isoformat()})
                            else:
                                trade.update({
                                    "status": "OPEN", "entry_fill_price": fill,
                                    "entry_timestamp": timestamp.isoformat(),
                                    "stop": stop, "target": target,
                                    "quantity": quantity, "notional_usd": notional,
                                    "cash_reserved_usd": cash_reserved,
                                    "risk_budget_usd": risk_budget,
                                    "risk_reserved_usd": risk_to_reserve,
                                    "risk_fraction": risk_fraction,
                                    "risk_capital_base_usd": capital_base,
                                    "costs_usd": None, "net_pnl_usd": None,
                                    "cost_mode": (
                                        "VERIFIED_REGISTRY" if cost_sizing else "GROSS_DIAGNOSTIC"
                                    ),
                                    "estimated_stop_costs_usd": (
                                        float(cost_sizing["estimated_round_trip_costs_usd"])
                                        if cost_sizing else None
                                    ),
                                })
                                if cost_sizing:
                                    state["costs_status"] = "VERIFIED_COSTS_RESERVED_AT_STOP"
                                # If target is touched after entry and stop is not,
                                # the same candle can close at the target.
                                if target_touched:
                                    _finish_trade(
                                        state, trade, target, timestamp.isoformat(), "TARGET",
                                        economic_registry=economic_registry,
                                    )
            elif timestamp > expiry or signal.get("status") != "PENDING":
                trade.update({"status": "NOT_FILLED_EXPIRED_OR_INVALID",
                              "completed_at": timestamp.isoformat()})

        elif trade["status"] == "OPEN" and timestamp > _timestamp(
            trade["entry_timestamp"], "entry_timestamp"
        ):
            stop = float(trade["stop"])
            target = float(trade["target"])
            stop_hit = low <= stop
            target_hit = high >= target
            if stop_hit:
                # A gap through the stop is filled at the worse opening price.
                exit_price = min(opening, stop) if opening <= stop else stop
                _finish_trade(state, trade, exit_price, timestamp.isoformat(), "STOP", economic_registry=economic_registry)
            elif target_hit:
                # A favorable gap is filled at the opening price.
                exit_price = max(opening, target) if opening >= target else target
                _finish_trade(state, trade, exit_price, timestamp.isoformat(), "TARGET", economic_registry=economic_registry)
            else:
                monitoring_expiry = signal.get("monitoring_expires_at")
                if monitoring_expiry and timestamp >= _timestamp(
                    monitoring_expiry, "signal.monitoring_expires_at"
                ):
                    _finish_trade(
                        state, trade, close, timestamp.isoformat(), "TIME_EXIT",
                        economic_registry=economic_registry,
                    )

    state["processed_bars"][signal_id] = timestamp.isoformat()
    state["open_risk_usd"] = _open_risk(state)
    return state


def paper_summary(state: dict) -> dict:
    _validate_state(state)
    trades = list(state["trades"].values())
    closed = [trade for trade in trades if trade.get("status") == "CLOSED"]
    open_trades = [trade for trade in trades if trade.get("status") == "OPEN"]
    wins = [trade for trade in closed if float(trade["gross_pnl_usd"]) > 0]
    losses = [trade for trade in closed if float(trade["gross_pnl_usd"]) < 0]
    gross = float(state["realized_gross_pnl_usd"])
    equity_points = [
        float(point["equity_usd"])
        for point in state.get("equity_history", [])
        if isinstance(point, dict) and point.get("equity_usd") is not None
    ]
    running_peak = float(state["initial_capital_usd"])
    max_drawdown_pct = 0.0
    for equity_point in equity_points:
        running_peak = max(running_peak, equity_point)
        if running_peak > 0:
            max_drawdown_pct = max(
                max_drawdown_pct,
                (running_peak - equity_point) / running_peak * 100.0,
            )
    closed_r = [float(trade["result_r"]) for trade in closed]
    gross_wins = sum(float(t["gross_pnl_usd"]) for t in wins)
    gross_losses = abs(sum(float(t["gross_pnl_usd"]) for t in losses))
    return {
        "mode": "PAPER_ONLY",
        "initial_capital_usd": state["initial_capital_usd"],
        "equity_usd": state["equity_usd"],
        "realized_gross_pnl_usd": gross,
        "return_pct_gross": gross / state["initial_capital_usd"] * 100.0,
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "waiting_or_rejected": len(trades) - len(open_trades) - len(closed),
        "win_rate_pct_gross": len(wins) / len(closed) * 100.0 if closed else None,
        "profit_factor_gross": gross_wins / gross_losses if gross_losses > 0 else None,
        "expectancy_r_gross": sum(closed_r) / len(closed_r) if closed_r else None,
        "max_drawdown_pct_gross": max_drawdown_pct,
        "open_risk_usd": _open_risk(state),
        "realized_costs_usd": float(state.get("realized_costs_usd", 0.0)),
        "realized_net_pnl_usd": float(state.get("realized_net_pnl_usd", 0.0)),
        "costs_included": bool(closed) and all(
            trade.get("cost_mode") == "VERIFIED_REGISTRY"
            and trade.get("costs_usd") is not None
            and trade.get("net_pnl_usd") is not None
            for trade in closed
        ),
        "net_performance_validated": False,
    }


def calculate_verified_cost_aware_quantity(
    asset: str,
    entry_price: float,
    stop_price: float,
    risk_budget_usd: float,
    *,
    registry: dict | None = None,
) -> dict:
    """Size a long position so gross stop loss plus verified round-trip costs
    do not exceed the USD risk budget. Fixed fees are reserved once; variable
    fees, spread and FX are charged per unit. Unknown evidence never means zero.
    """
    from transaction_costs import estimate_verified_registry_unit_costs

    result = {
        "valid": False, "complete": False, "asset": asset,
        "quantity": None, "risk_budget_usd": None,
        "gross_stop_risk_usd": None,
        "estimated_round_trip_costs_usd": None,
        "reserved_stop_risk_usd": None, "notional_usd": None,
        "unpriced_costs": [],
    }
    try:
        entry = _number(entry_price, "entry_price")
        stop = _number(stop_price, "stop_price")
        budget = _number(risk_budget_usd, "risk_budget_usd")
    except ValueError as error:
        result["unpriced_costs"].append(str(error))
        return result
    if stop >= entry:
        result["unpriced_costs"].append("stop_must_be_below_entry")
        return result

    unit = estimate_verified_registry_unit_costs(
        asset, entry, stop, registry=registry
    )
    result["registry_status"] = unit.get("registry_status")
    result["unpriced_costs"].extend(unit.get("unpriced_costs", []))
    if not unit.get("complete"):
        return result

    rate = float(unit["quote_to_usd"])
    fixed_cost_usd = float(unit["fixed_costs_quote"]) * rate
    variable_cost_per_unit_usd = float(unit["costs_per_unit_quote"]) * rate
    gross_risk_per_unit = (entry - stop) * rate
    total_risk_per_unit = gross_risk_per_unit + variable_cost_per_unit_usd
    if (
        not all(math.isfinite(value) for value in (
            rate, fixed_cost_usd, variable_cost_per_unit_usd, total_risk_per_unit
        ))
        or rate <= 0 or fixed_cost_usd < 0
        or variable_cost_per_unit_usd < 0 or total_risk_per_unit <= 0
    ):
        result["unpriced_costs"].append("verified_cost_values_invalid")
        return result

    remaining_budget = budget - fixed_cost_usd
    if remaining_budget <= 0:
        result["unpriced_costs"].append("fixed_costs_exceed_risk_budget")
        return result
    quantity = remaining_budget / total_risk_per_unit
    if not unit["fractional_allowed"]:
        quantity = math.floor(quantity)
    if not math.isfinite(quantity) or quantity <= 0:
        result["unpriced_costs"].append("risk_budget_below_minimum_executable_quantity")
        return result

    notional_usd = quantity * entry * rate
    if notional_usd + 1e-9 < float(unit["minimum_order_notional_usd"]):
        result["unpriced_costs"].append("position_below_verified_minimum_order")
        return result

    gross_stop_risk = quantity * gross_risk_per_unit
    estimated_costs = fixed_cost_usd + quantity * variable_cost_per_unit_usd
    reserved = gross_stop_risk + estimated_costs
    if reserved > budget + 1e-8:
        result["unpriced_costs"].append("calculated_stop_risk_exceeds_budget")
        return result

    result.update({
        "valid": True, "complete": True, "quantity": quantity,
        "risk_budget_usd": budget, "gross_stop_risk_usd": gross_stop_risk,
        "estimated_round_trip_costs_usd": estimated_costs,
        "reserved_stop_risk_usd": reserved, "notional_usd": notional_usd,
        "fixed_costs_usd": fixed_cost_usd,
        "variable_cost_per_unit_usd": variable_cost_per_unit_usd,
        "minimum_order_notional_usd": float(unit["minimum_order_notional_usd"]),
        "fractional_allowed": bool(unit["fractional_allowed"]),
        "cost_source": "VERIFIED_ASSET_ECONOMIC_REGISTRY",
        "cost_estimation_exit_price": stop,
    })
    return result
