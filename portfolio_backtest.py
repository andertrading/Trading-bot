"""Portfolio-level, long-only backtest allocator.

This module combines independently simulated trade outcomes in chronological
order. It is deliberately a conservative *risk/cash allocation layer*, not
a tick-level portfolio execution engine. Costs remain excluded from P&L because
the current cost model has unpriced components.
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


def _empty_result(capital):
    return {
        "summary": {
            "initial_capital_usd": float(capital),
            "ending_equity_usd": float(capital),
            "gross_pnl_usd": 0.0,
            "net_pnl_usd": None,
            "net_performance_validated": False,
            "gross_return_pct": 0.0,
            "net_return_pct": None,
            "return_pct": 0.0,
            "return_basis": "gross_before_costs",
            "trades_considered": 0,
            "trades_accepted": 0,
            "trades_rejected": 0,
            "win_rate_pct": 0.0,
            "profit_factor": None,
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "drawdown_basis": "realized_exit_events_only",
            "intratrade_drawdown_included": False,
            "unrealized_pnl_included": False,
            "peak_open_risk_usd": 0.0,
            "peak_open_risk_pct_of_equity": 0.0,
            "risk_limit_breach_events": 0,
            "trades_exceeding_planned_gross_stop_risk": 0,
            "total_loss_beyond_planned_gross_stop_risk_usd": 0.0,
            "max_loss_beyond_planned_gross_stop_risk_usd": 0.0,
            "peak_open_notional_usd": 0.0,
            "portfolio_risk_limit_pct": 0.0,
            "costs_included": False,
        },
        "trades": [],
        "rejections": [],
    }


def simulate_portfolio(
    trades,
    capital_usd=None,
    max_portfolio_risk_pct=None,
    max_risk_per_trade_pct=None,
):
    """Allocate simultaneous long trades under shared risk and cash limits.

    Drawdown is measured only from realized equity after grouped exit events.
    Intratrade mark-to-market losses and unrealized P&L are not available here.

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

    result = _empty_result(capital)
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
                if not all(_finite(v) for v in (entry, stop, score, result_r)):
                    raise ValueError("valor no finito")
                if entry <= 0 or stop <= 0 or stop >= entry:
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
        equity_for_limits = max(0.0, equity)
        available_risk = max(
            0.0,
            equity_for_limits * portfolio_limit_pct - open_risk,
        )
        available_cash = max(0.0, equity_for_limits - open_notional)

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
        cash_factor = (
            min(1.0, available_cash / total_requested_notional)
            if total_requested_notional > 0 else 0.0
        )
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
            position = {
                **item,
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
        timestamp_pnl = 0.0
        for item in sorted(
            exits.get(event_time, []),
            key=lambda row: row["source_index"],
        ):
            position = open_positions.pop(item["source_index"], None)
            if position is None:
                continue
            pnl = position["allocated_risk_usd"] * position["result_R"]
            position["realized_pnl_usd"] = pnl
            loss_beyond_stop = max(
                0.0,
                -pnl - position["allocated_risk_usd"],
            )
            position["loss_beyond_planned_gross_stop_risk_usd"] = loss_beyond_stop
            position["exceeded_planned_gross_stop_risk"] = loss_beyond_stop > 1e-10
            position["status"] = "CLOSED"
            closed_at_timestamp.append(position)
            timestamp_pnl += pnl

        # All exits at one timestamp are realized as a single portfolio event;
        # the arbitrary order of same-time instruments must not distort DD.
        if closed_at_timestamp:
            equity += timestamp_pnl
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
            "planned_gross_stop_risk_usd": round(position["allocated_risk_usd"], 8),
            "loss_beyond_planned_gross_stop_risk_usd": round(
                position.get("loss_beyond_planned_gross_stop_risk_usd", 0.0), 8
            ),
            "exceeded_planned_gross_stop_risk": bool(
                position.get("exceeded_planned_gross_stop_risk", False)
            ),
            "quantity": round(position["quantity"], 10),
            "notional_usd": round(position["notional_usd"], 8),
            "allocation_factor": round(position["allocation_factor"], 8),
            "realized_pnl_usd": round(position.get("realized_pnl_usd", 0.0), 8),
            "equity_after_exit_usd": round(position.get("equity_after_exit_usd", equity), 8),
            "costs_included": False,
        })

    pnl_values = [row["realized_pnl_usd"] for row in accepted_rows]
    gains = sum(value for value in pnl_values if value > 0)
    losses = abs(sum(value for value in pnl_values if value < 0))
    summary = result["summary"]
    summary.update({
        "ending_equity_usd": round(equity, 8),
        "gross_pnl_usd": round(equity - capital, 8),
        "net_pnl_usd": None,
        "net_performance_validated": False,
        "gross_return_pct": round((equity / capital - 1) * 100, 6),
        "net_return_pct": None,
        "return_pct": round((equity / capital - 1) * 100, 6),
        "return_basis": "gross_before_costs",
        "trades_accepted": len(accepted_rows),
        "trades_rejected": len(rejected),
        "win_rate_pct": round(
            sum(value > 0 for value in pnl_values) / len(pnl_values) * 100,
            4,
        ) if pnl_values else 0.0,
        "profit_factor": round(gains / losses, 6) if losses > 0 else (None if gains == 0 else None),
        "max_drawdown_usd": round(max_drawdown, 8),
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "drawdown_basis": "realized_exit_events_only",
        "intratrade_drawdown_included": False,
        "unrealized_pnl_included": False,
        "peak_open_risk_usd": round(peak_open_risk, 8),
        "peak_open_risk_pct_of_equity": round(peak_open_risk_pct, 6),
        "risk_limit_breach_events": risk_limit_breach_events,
        "trades_exceeding_planned_gross_stop_risk": sum(
            row["exceeded_planned_gross_stop_risk"] for row in accepted_rows
        ),
        "total_loss_beyond_planned_gross_stop_risk_usd": round(sum(
            row["loss_beyond_planned_gross_stop_risk_usd"] for row in accepted_rows
        ), 8),
        "max_loss_beyond_planned_gross_stop_risk_usd": round(max(
            (row["loss_beyond_planned_gross_stop_risk_usd"] for row in accepted_rows),
            default=0.0,
        ), 8),
        "peak_open_notional_usd": round(peak_open_notional, 8),
        "costs_included": False,
    })
    result["trades"] = accepted_rows
    result["rejections"] = rejected
    return result
