"""Prospective, execution-free paper portfolio engine.

This module simulates research signals against subsequent completed OHLC bars.
It has no broker/API integration and never places or manages real orders.
It supports an explicitly labelled gross-diagnostic mode and an optional
verified-registry cost mode. The latter blocks entries without complete
instrument evidence, reserves round-trip costs at the stop, and books verified
net costs at close. Neither mode validates live execution or net profitability.
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
    """Sum only strictly validated positive risk reservations."""
    total = 0.0
    for signal_id, trade in state["trades"].items():
        if not isinstance(trade, dict):
            raise ValueError(f"{signal_id} paper trade record is invalid")
        if trade.get("status") != "OPEN":
            continue
        risk_usd = _number(
            trade.get("risk_reserved_usd"),
            f"{signal_id}.risk_reserved_usd",
        )
        total += risk_usd
        if not math.isfinite(total):
            raise ValueError("paper portfolio open risk total is invalid")
    return total


def _trade_cash_reserved(trade: dict, notional_usd: float) -> float:
    """Return a validated cash reserve, including legacy verified-cost trades."""
    has_entry_evidence = any(
        field in trade
        for field in ("entry_fill_price", "quantity", "entry_timestamp")
    )
    if has_entry_evidence:
        if "entry_fill_price" not in trade or "quantity" not in trade:
            raise ValueError(
                "open paper trade is missing quantity or entry fill price"
            )
        entry_price = _number(
            trade["entry_fill_price"], "entry_fill_price"
        )
        quantity = _number(trade["quantity"], "quantity")
        calculated_notional = entry_price * quantity
        if not math.isfinite(calculated_notional):
            raise ValueError("calculated paper trade notional is invalid")
        tolerance = max(1e-8, abs(notional_usd) * 1e-8)
        if abs(calculated_notional - notional_usd) > tolerance:
            raise ValueError(
                "paper trade notional does not match quantity multiplied by entry fill price"
            )

    if "cash_reserved_usd" in trade:
        reserved = _number(trade["cash_reserved_usd"], "cash_reserved_usd")
    elif trade.get("cost_mode") == "VERIFIED_REGISTRY":
        if "estimated_stop_costs_usd" not in trade:
            raise ValueError("verified open trade is missing its estimated cost reserve")
        reserved = notional_usd + _number(
            trade["estimated_stop_costs_usd"], "estimated_stop_costs_usd"
        )
    else:
        reserved = notional_usd
    if reserved + 1e-9 < notional_usd:
        raise ValueError("paper trade cash reserve cannot be below its notional")
    return reserved


def _open_cash_reserved(state: dict) -> float:
    """Reserve entry notional plus conservative verified cost reserves."""
    total = 0.0
    for trade in state["trades"].values():
        if trade.get("status") != "OPEN":
            continue
        notional = _number(trade.get("notional_usd"), "notional_usd")
        total += _trade_cash_reserved(trade, notional)
    return total


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
    # Keep the capital base conservative: gains never raise it above the
    # reference capital, while realized net or gross losses reduce it.
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
        trade_cash_reserved = _trade_cash_reserved(trade, notional_usd)
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
        raise ValueError("paper portfolio open cash reserve exceeds conservative capital")
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
        # VERIFIED_NET_COSTS_INCLUDED is set only after _finish_trade has
        # booked complete verified close costs into paper equity.
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


def _bar_evidence(bar: dict, timestamp, opening, high, low, close) -> dict:
    evidence = {
        "timestamp": timestamp.isoformat(),
        "open": opening,
        "high": high,
        "low": low,
        "close": close,
    }
    for field in (
        "data_provider",
        "price_adjustment_policy",
        "price_adjustment_verified",
        "price_adjustment_verification_status",
        "price_adjustment_verification_source",
        "price_adjustment_verified_at",
        "currency",
    ):
        if field in bar:
            evidence[field] = bar[field]
    return evidence


def _levels(signal: dict) -> tuple[float, float, float]:
    entry = _number(signal.get("entry_reference"), "signal.entry_reference")
    stop = _number(signal.get("stop"), "signal.stop")
    target = _number(signal.get("target"), "signal.target")
    direction = str(signal.get("direction", "LONG")).upper()
    if direction != "LONG" or not stop < entry < target:
        raise ValueError("paper engine currently accepts LONG signals with stop < entry < target")
    return entry, stop, target


def _refresh_costs_status(state: dict) -> None:
    """Derive the portfolio cost label from every open and closed ledger row."""
    trades = list(state["trades"].values())
    closed = [trade for trade in trades if trade.get("status") == "CLOSED"]
    opened = [trade for trade in trades if trade.get("status") == "OPEN"]

    unresolved_close = any(
        trade.get("cost_mode") == "VERIFIED_REGISTRY"
        and (trade.get("costs_usd") is None or trade.get("net_pnl_usd") is None)
        for trade in closed
    )
    closed_net_complete = bool(closed) and all(
        trade.get("cost_mode") == "VERIFIED_REGISTRY"
        and trade.get("costs_usd") is not None
        and trade.get("net_pnl_usd") is not None
        for trade in closed
    )
    has_unverified_open = any(
        trade.get("cost_mode") != "VERIFIED_REGISTRY" for trade in opened
    )
    has_verified_open = any(
        trade.get("cost_mode") == "VERIFIED_REGISTRY" for trade in opened
    )

    if unresolved_close:
        state["costs_status"] = "UNRESOLVED_CLOSE_COSTS"
    elif closed_net_complete and not has_unverified_open:
        state["costs_status"] = "VERIFIED_NET_COSTS_INCLUDED"
    elif has_verified_open and not closed and not has_unverified_open:
        state["costs_status"] = "VERIFIED_COSTS_RESERVED_AT_STOP"
    else:
        state["costs_status"] = "NOT_INCLUDED_UNVERIFIED"


def _finish_trade(
    state: dict,
    trade: dict,
    exit_price: float,
    exit_timestamp: str,
    reason: str,
    *,
    economic_registry: dict | None = None,
    exit_bar: dict | None = None,
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
            state["equity_usd"] += net_pnl
        else:
            trade["costs_blockers"] = list(estimate.get("unpriced_costs", []))
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
    if exit_bar is not None:
        trade["exit_bar"] = dict(exit_bar)
    _refresh_costs_status(state)
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
    bar_evidence = _bar_evidence(bar, timestamp, opening, high, low, close)
    prior_bar = state["processed_bars"].get(signal_id)
    if prior_bar and timestamp <= _timestamp(prior_bar, "processed_bar"):
        return state

    trade = state["trades"].get(signal_id)
    if trade is None:
        trade = {"signal_id": signal_id, "asset": signal.get("asset"),
                 "timeframe": signal.get("timeframe"), "status": "WAITING"}
        state["trades"][signal_id] = trade

    trade.setdefault("signal_evidence", {
        field: signal.get(field)
        for field in (
            "generated_at",
            "data_timestamp",
            "signal_bar_evidence",
            "data_provider",
            "price_adjustment_policy",
            "price_adjustment_verified",
            "price_adjustment_verification_status",
            "price_adjustment_verification_source",
            "price_adjustment_verified_at",
            "currency",
            "score",
            "provisional_risk_pct",
        )
    })

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
                elif entry_touched and target_touched and opening < entry:
                    # The candle opened below entry and later touched both
                    # entry and target. OHLC cannot establish their order;
                    # do not assume the entry preceded the target.
                    trade.update({
                        "status": "NO_FILL_AMBIGUOUS_ENTRY_TARGET",
                        "completed_at": timestamp.isoformat(),
                    })
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
                            elif _open_cash_reserved(state) + cash_reserved > capital_base + 1e-9:
                                available_cash = max(
                                    0.0, capital_base - _open_cash_reserved(state)
                                )
                                trade.update({
                                    "status": "REJECTED_NO_LEVERAGE_CASH",
                                    "notional_usd": notional,
                                    "estimated_stop_costs_usd": (
                                        float(cost_sizing["estimated_round_trip_costs_usd"])
                                        if cost_sizing else 0.0
                                    ),
                                    "cash_reservation_required_usd": cash_reserved,
                                    "available_cash_before_entry_usd": available_cash,
                                    "completed_at": timestamp.isoformat(),
                                })
                            else:
                                trade.update({
                                    "status": "OPEN", "entry_fill_price": fill,
                                    "entry_timestamp": timestamp.isoformat(),
                                    "entry_bar": dict(bar_evidence),
                                    "stop": stop, "target": target,
                                    "quantity": quantity, "notional_usd": notional,
                                    "cash_reserved_usd": cash_reserved,
                                    "risk_budget_usd": risk_budget,
                                    "risk_reserved_usd": risk_to_reserve,
                                    "gross_stop_risk_usd": (
                                        float(cost_sizing["gross_stop_risk_usd"])
                                        if cost_sizing else risk_budget
                                    ),
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
                                _refresh_costs_status(state)
                                # If target is touched after entry and stop is not,
                                # the same candle can close at the target.
                                if target_touched:
                                    _finish_trade(
                                        state, trade, target, timestamp.isoformat(), "TARGET",
                                        economic_registry=economic_registry,
                                        exit_bar=bar_evidence,
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
                _finish_trade(state, trade, exit_price, timestamp.isoformat(), "STOP", economic_registry=economic_registry, exit_bar=bar_evidence)
            elif target_hit:
                # A favorable gap is filled at the opening price.
                exit_price = max(opening, target) if opening >= target else target
                _finish_trade(state, trade, exit_price, timestamp.isoformat(), "TARGET", economic_registry=economic_registry, exit_bar=bar_evidence)
            else:
                monitoring_expiry = signal.get("monitoring_expires_at")
                if monitoring_expiry and timestamp >= _timestamp(
                    monitoring_expiry, "signal.monitoring_expires_at"
                ):
                    _finish_trade(
                        state, trade, close, timestamp.isoformat(), "TIME_EXIT",
                        economic_registry=economic_registry,
                        exit_bar=bar_evidence,
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
    initial_capital = float(state["initial_capital_usd"])

    def _drawdown_pct(equity_curve):
        running_peak = initial_capital
        maximum = 0.0
        for equity_point in equity_curve:
            running_peak = max(running_peak, equity_point)
            if running_peak > 0:
                maximum = max(
                    maximum,
                    (running_peak - equity_point) / running_peak * 100.0,
                )
        return maximum

    def _group_trade_pnl(trade_list, pnl_field):
        grouped = {}
        for trade in trade_list:
            exit_time = _timestamp(
                trade.get("exit_timestamp"),
                "closed_trade.exit_timestamp",
            )
            pnl = float(trade[pnl_field])
            if not math.isfinite(pnl):
                raise ValueError(f"closed trade {pnl_field} must be finite")
            grouped.setdefault(exit_time, []).append(pnl)
        return grouped

    def _close_to_close_drawdown(grouped_pnl):
        # Trades closed on the same bar are one portfolio observation.
        # Their arbitrary dictionary/insertion order must not create a fake
        # intermediate equity peak or trough.
        equity_curve = [initial_capital]
        equity = initial_capital
        for timestamp in sorted(grouped_pnl):
            equity += sum(grouped_pnl[timestamp])
            equity_curve.append(equity)
        return _drawdown_pct(equity_curve)

    def _worst_case_intrabar_drawdown(grouped_pnl):
        # OHLC bars do not reveal the ordering of different instruments'
        # exits within the same timestamp. Bound drawdown conservatively by
        # applying all losses before gains within each timestamp bucket.
        equity = initial_capital
        running_peak = initial_capital
        maximum = 0.0
        for timestamp in sorted(grouped_pnl):
            outcomes = grouped_pnl[timestamp]
            losses_at_timestamp = sum(value for value in outcomes if value < 0)
            gains_at_timestamp = sum(value for value in outcomes if value > 0)
            equity += losses_at_timestamp
            if running_peak > 0:
                maximum = max(
                    maximum,
                    (running_peak - equity) / running_peak * 100.0,
                )
            equity += gains_at_timestamp
            running_peak = max(running_peak, equity)
        return maximum

    gross_grouped = _group_trade_pnl(closed, "gross_pnl_usd")
    max_drawdown_pct_gross = _worst_case_intrabar_drawdown(gross_grouped)
    max_drawdown_pct_gross_close_to_close = _close_to_close_drawdown(
        gross_grouped
    )

    # The persisted equity history can contain multiple closes at the same
    # timestamp. Retain only the final portfolio equity for each timestamp.
    equity_by_timestamp = {}
    for point in state.get("equity_history", []):
        if not isinstance(point, dict) or point.get("equity_usd") is None:
            continue
        timestamp_value = point.get("timestamp")
        if timestamp_value is None:
            continue
        timestamp = _timestamp(timestamp_value, "equity_history.timestamp")
        equity_value = float(point["equity_usd"])
        if not math.isfinite(equity_value):
            raise ValueError("equity history values must be finite")
        equity_by_timestamp[timestamp] = equity_value
    equity_curve = [initial_capital] + [
        equity_by_timestamp[timestamp]
        for timestamp in sorted(equity_by_timestamp)
    ]
    max_drawdown_pct_equity = _drawdown_pct(equity_curve)

    net_complete = bool(closed) and all(
        trade.get("cost_mode") == "VERIFIED_REGISTRY"
        and trade.get("costs_usd") is not None
        and trade.get("net_pnl_usd") is not None
        and (
            trade.get("result_r_net") is not None
            or trade.get("risk_budget_usd") is not None
        )
        for trade in closed
    )
    max_drawdown_pct_net = None
    max_drawdown_pct_net_close_to_close = None
    net_win_rate_pct = None
    profit_factor_net = None
    expectancy_r_net = None
    return_pct_net = None
    net_wins = []
    net_losses = []
    if net_complete:
        net_grouped = _group_trade_pnl(closed, "net_pnl_usd")
        max_drawdown_pct_net = _worst_case_intrabar_drawdown(net_grouped)
        max_drawdown_pct_net_close_to_close = _close_to_close_drawdown(
            net_grouped
        )
        net_wins = [
            trade for trade in closed if float(trade["net_pnl_usd"]) > 0
        ]
        net_losses = [
            trade for trade in closed if float(trade["net_pnl_usd"]) < 0
        ]
        net_winning_pnl = sum(float(trade["net_pnl_usd"]) for trade in net_wins)
        net_losing_pnl = abs(
            sum(float(trade["net_pnl_usd"]) for trade in net_losses)
        )
        net_win_rate_pct = len(net_wins) / len(closed) * 100.0
        profit_factor_net = (
            net_winning_pnl / net_losing_pnl
            if net_losing_pnl > 0 else None
        )
        net_r_values = [
            float(
                trade["result_r_net"]
                if trade.get("result_r_net") is not None
                else float(trade["net_pnl_usd"]) / float(trade["risk_budget_usd"])
            )
            for trade in closed
        ]
        if not all(math.isfinite(value) for value in net_r_values):
            raise ValueError("net trade R-multiples must be finite")
        expectancy_r_net = sum(net_r_values) / len(net_r_values)
        return_pct_net = (
            float(state.get("realized_net_pnl_usd", 0.0))
            / initial_capital * 100.0
        )

    # Keep intraday and daily evidence separate. Their trade counts and
    # descriptive performance must not be hidden inside one blended figure.
    timeframe_groups = {}
    for trade in closed:
        timeframe = str(trade.get("timeframe") or "UNKNOWN")
        timeframe_groups.setdefault(timeframe, []).append(trade)

    by_timeframe = {}
    for timeframe, timeframe_trades in sorted(timeframe_groups.items()):
        timeframe_wins = [
            trade for trade in timeframe_trades
            if float(trade["gross_pnl_usd"]) > 0
        ]
        timeframe_losses = [
            trade for trade in timeframe_trades
            if float(trade["gross_pnl_usd"]) < 0
        ]
        timeframe_gross_wins = sum(
            float(trade["gross_pnl_usd"]) for trade in timeframe_wins
        )
        timeframe_gross_losses = abs(sum(
            float(trade["gross_pnl_usd"]) for trade in timeframe_losses
        ))
        timeframe_net_complete = all(
            trade.get("cost_mode") == "VERIFIED_REGISTRY"
            and trade.get("costs_usd") is not None
            and trade.get("net_pnl_usd") is not None
            and (
                trade.get("result_r_net") is not None
                or trade.get("risk_budget_usd") is not None
            )
            for trade in timeframe_trades
        )
        timeframe_net = None
        timeframe_net_win_rate = None
        timeframe_net_pf = None
        timeframe_net_expectancy_r = None
        if timeframe_net_complete:
            timeframe_net_wins = [
                trade for trade in timeframe_trades
                if float(trade["net_pnl_usd"]) > 0
            ]
            timeframe_net_losses = [
                trade for trade in timeframe_trades
                if float(trade["net_pnl_usd"]) < 0
            ]
            net_winning_pnl = sum(
                float(trade["net_pnl_usd"]) for trade in timeframe_net_wins
            )
            net_losing_pnl = abs(sum(
                float(trade["net_pnl_usd"]) for trade in timeframe_net_losses
            ))
            net_r_values = [
                float(
                    trade["result_r_net"]
                    if trade.get("result_r_net") is not None
                    else float(trade["net_pnl_usd"]) / float(trade["risk_budget_usd"])
                )
                for trade in timeframe_trades
            ]
            if not all(math.isfinite(value) for value in net_r_values):
                raise ValueError("timeframe net trade R-multiples must be finite")
            timeframe_net = sum(
                float(trade["net_pnl_usd"]) for trade in timeframe_trades
            )
            timeframe_net_win_rate = (
                len(timeframe_net_wins) / len(timeframe_trades) * 100.0
            )
            timeframe_net_pf = (
                net_winning_pnl / net_losing_pnl
                if net_losing_pnl > 0 else None
            )
            timeframe_net_expectancy_r = (
                sum(net_r_values) / len(net_r_values)
            )

        timeframe_gross_pnl = sum(
            float(trade["gross_pnl_usd"]) for trade in timeframe_trades
        )
        timeframe_gross_r = [
            float(trade["result_r"]) for trade in timeframe_trades
        ]
        by_timeframe[timeframe] = {
            "closed_trades": len(timeframe_trades),
            "gross_pnl_usd": timeframe_gross_pnl,
            "win_rate_pct_gross": (
                len(timeframe_wins) / len(timeframe_trades) * 100.0
            ),
            "profit_factor_gross": (
                timeframe_gross_wins / timeframe_gross_losses
                if timeframe_gross_losses > 0 else None
            ),
            "expectancy_r_gross": (
                sum(timeframe_gross_r) / len(timeframe_gross_r)
            ),
            "costs_included": timeframe_net_complete,
            "net_pnl_usd": timeframe_net,
            "win_rate_pct_net": timeframe_net_win_rate,
            "profit_factor_net": timeframe_net_pf,
            "expectancy_r_net": timeframe_net_expectancy_r,
            "performance_status": "DESCRIPTIVE_ONLY_NOT_VALIDATED",
        }

    closed_r = [float(trade["result_r"]) for trade in closed]
    gross_wins = sum(float(trade["gross_pnl_usd"]) for trade in wins)
    gross_losses = abs(sum(float(trade["gross_pnl_usd"]) for trade in losses))
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
        "by_timeframe": by_timeframe,
        "net_closed_trades": len(closed) if net_complete else 0,
        "return_pct_net": return_pct_net,
        "win_rate_pct_net": net_win_rate_pct,
        "profit_factor_net": profit_factor_net,
        "expectancy_r_net": expectancy_r_net,
        "max_drawdown_pct_gross": max_drawdown_pct_gross,
        "max_drawdown_pct_gross_close_to_close": max_drawdown_pct_gross_close_to_close,
        "max_drawdown_pct_net": max_drawdown_pct_net,
        "max_drawdown_pct_net_close_to_close": max_drawdown_pct_net_close_to_close,
        "max_drawdown_pct_equity": max_drawdown_pct_equity,
        "max_drawdown_pct_equity_close_to_close": max_drawdown_pct_equity,
        "drawdown_scope": "REALIZED_CLOSED_TRADES_ONLY",
        "open_trade_unrealized_pnl_included": False,
        "drawdown_scope_note": (
            "Drawdown uses realized closed-trade equity only; unrealized "
            "P&L on open trades is excluded."
        ),
        "open_risk_usd": _open_risk(state),
        "realized_costs_usd": float(state.get("realized_costs_usd", 0.0)),
        "realized_net_pnl_usd": float(state.get("realized_net_pnl_usd", 0.0)),
        "costs_included": net_complete,
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
