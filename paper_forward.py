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


def _finish_trade(state: dict, trade: dict, exit_price: float,
                  exit_timestamp: str, reason: str) -> None:
    quantity = float(trade["quantity"])
    pnl = (exit_price - float(trade["entry_fill_price"])) * quantity
    trade.update({
        "status": "CLOSED",
        "exit_fill_price": exit_price,
        "exit_timestamp": exit_timestamp,
        "exit_reason": reason,
        "gross_pnl_usd": pnl,
        "result_r": pnl / float(trade["risk_budget_usd"]),
        "risk_reserved_usd": 0.0,
        "costs_usd": None,
        "net_pnl_usd": None,
    })
    state["realized_gross_pnl_usd"] += pnl
    state["equity_usd"] += pnl
    state["peak_equity_usd"] = max(state["peak_equity_usd"], state["equity_usd"])


def process_signal_bar(
    state: dict,
    signal: dict,
    bar: dict,
    *,
    risk_fraction: float = DEFAULT_RISK_PER_TRADE,
    max_risk_per_trade: float = MAX_RISK_PER_TRADE,
    max_portfolio_risk: float = MAX_PORTFOLIO_RISK,
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
                elif opening >= target:
                    trade.update({"status": "NO_FILL_GAP_PAST_TARGET",
                                  "completed_at": timestamp.isoformat()})
                elif entry_touched:
                    fill = max(entry, opening)
                    risk_fraction = _number(risk_fraction, "risk_fraction")
                    max_risk_per_trade = _number(max_risk_per_trade, "max_risk_per_trade")
                    max_portfolio_risk = _number(max_portfolio_risk, "max_portfolio_risk")
                    if risk_fraction > max_risk_per_trade or max_risk_per_trade > MAX_RISK_PER_TRADE:
                        raise ValueError("per-trade risk policy exceeds the hard 1% cap")
                    if max_portfolio_risk > MAX_PORTFOLIO_RISK:
                        raise ValueError("portfolio risk policy exceeds the hard 3% cap")
                    equity = float(state["equity_usd"])
                    risk_budget = equity * risk_fraction
                    reserved = _open_risk(state)
                    if reserved + risk_budget > equity * max_portfolio_risk + 1e-9:
                        trade.update({"status": "REJECTED_PORTFOLIO_RISK",
                                      "completed_at": timestamp.isoformat()})
                    else:
                        quantity = risk_budget / (fill - stop)
                        notional = quantity * fill
                        if notional > equity + 1e-9:
                            trade.update({"status": "REJECTED_NO_LEVERAGE_CASH",
                                          "completed_at": timestamp.isoformat()})
                        else:
                            trade.update({
                                "status": "OPEN", "entry_fill_price": fill,
                                "entry_timestamp": timestamp.isoformat(),
                                "stop": stop, "target": target,
                                "quantity": quantity, "notional_usd": notional,
                                "risk_budget_usd": risk_budget,
                                "risk_reserved_usd": risk_budget,
                                "risk_fraction": risk_fraction,
                                "costs_usd": None, "net_pnl_usd": None,
                            })
                            # If target is touched after entry and stop is not,
                            # the same candle can close at the target.
                            if target_touched:
                                _finish_trade(state, trade, target, timestamp.isoformat(), "TARGET")
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
                _finish_trade(state, trade, exit_price, timestamp.isoformat(), "STOP")
            elif target_hit:
                # A favorable gap is filled at the opening price.
                exit_price = max(opening, target) if opening >= target else target
                _finish_trade(state, trade, exit_price, timestamp.isoformat(), "TARGET")

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
        "profit_factor_gross": (
            sum(float(t["gross_pnl_usd"]) for t in wins)
            / abs(sum(float(t["gross_pnl_usd"]) for t in losses))
            if losses else (None if not wins else math.inf)
        ),
        "open_risk_usd": _open_risk(state),
        "costs_included": False,
        "net_performance_validated": False,
    }
