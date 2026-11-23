"""Fail-closed position sizing using the verified per-asset economic registry."""
from __future__ import annotations

import math
from typing import Any

from transaction_costs import (
    estimate_verified_registry_trade_costs,
    estimate_verified_registry_unit_costs,
)


def _finite_positive(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and number > 0


def calculate_verified_cost_aware_quantity(
    asset: str,
    entry: float,
    stop: float,
    risk_budget_usd: float,
    *,
    registry: dict | None = None,
) -> dict[str, Any]:
    """Size a long position so gross stop loss plus verified round-trip costs fit risk.

    The helper is deliberately independent of the paper engine. It requires a
    fully ELIGIBLE registry record, includes fixed entry/exit fees exactly once,
    includes variable per-unit costs, and enforces the verified minimum order
    and fractional-quantity rules. It never falls back to zero costs.
    """
    result = {
        "valid": False,
        "eligible": False,
        "status": "INVALID_INPUT",
        "asset": asset,
        "quantity": None,
        "risk_budget_usd": None,
        "gross_stop_risk_usd": None,
        "round_trip_cost_at_stop_usd": None,
        "total_risk_reserved_usd": None,
        "notional_usd": None,
        "unit_costs": None,
        "round_trip_costs_at_stop": None,
        "blockers": [],
    }
    if not all(
        _finite_positive(value)
        for value in (entry, risk_budget_usd)
    ) or not _finite_positive(stop) or float(stop) >= float(entry):
        result["blockers"].append("invalid_long_entry_stop_or_risk_budget")
        return result

    entry = float(entry)
    stop = float(stop)
    risk_budget_usd = float(risk_budget_usd)
    unit_costs = estimate_verified_registry_unit_costs(
        asset, entry, stop, registry=registry
    )
    result["unit_costs"] = unit_costs
    if not unit_costs.get("complete"):
        result["status"] = "COSTS_UNVERIFIED"
        result["blockers"].extend(unit_costs.get("unpriced_costs", []))
        if not result["blockers"]:
            result["blockers"].append("verified_unit_cost_model_incomplete")
        return result

    quote_to_usd = float(unit_costs["quote_to_usd"])
    fixed_costs_usd = float(unit_costs["fixed_costs_quote"]) * quote_to_usd
    variable_cost_per_unit_usd = (
        float(unit_costs["costs_per_unit_quote"]) * quote_to_usd
    )
    gross_loss_per_unit_usd = (entry - stop) * quote_to_usd
    if (
        not all(math.isfinite(value) for value in (
            fixed_costs_usd,
            variable_cost_per_unit_usd,
            gross_loss_per_unit_usd,
        ))
        or min(fixed_costs_usd, variable_cost_per_unit_usd) < 0
        or gross_loss_per_unit_usd <= 0
    ):
        result["status"] = "INVALID_COST_MODEL"
        result["blockers"].append("verified_cost_model_contains_invalid_values")
        return result

    remaining_risk = risk_budget_usd - fixed_costs_usd
    if remaining_risk <= 0:
        result["status"] = "RISK_BUDGET_BELOW_FIXED_COSTS"
        result["blockers"].append("fixed_round_trip_costs_consume_risk_budget")
        return result

    risk_per_unit_usd = gross_loss_per_unit_usd + variable_cost_per_unit_usd
    quantity = remaining_risk / risk_per_unit_usd
    fractional_allowed = unit_costs["fractional_allowed"]
    if not fractional_allowed:
        quantity = float(math.floor(quantity))
    if not math.isfinite(quantity) or quantity <= 0:
        result["status"] = "POSITION_TOO_SMALL"
        result["blockers"].append("risk_budget_cannot_fund_one_allowed_unit")
        return result

    minimum_notional_usd = float(unit_costs["minimum_order_notional_usd"])
    notional_usd = quantity * entry * quote_to_usd
    if notional_usd + 1e-9 < minimum_notional_usd:
        result["status"] = "BELOW_VERIFIED_MINIMUM_ORDER"
        result["blockers"].append("sized_notional_below_verified_minimum_order")
        return result

    round_trip = estimate_verified_registry_trade_costs(
        asset, entry, stop, quantity, registry=registry
    )
    result["round_trip_costs_at_stop"] = round_trip
    if not round_trip.get("complete"):
        result["status"] = "COSTS_UNVERIFIED"
        result["blockers"].extend(round_trip.get("unpriced_costs", []))
        return result

    gross_stop_risk_usd = (entry - stop) * quantity * quote_to_usd
    round_trip_cost_usd = float(round_trip["known_costs_total"])
    total_risk_usd = gross_stop_risk_usd + round_trip_cost_usd
    tolerance = max(1e-9, risk_budget_usd * 1e-10)
    if total_risk_usd > risk_budget_usd + tolerance:
        result["status"] = "RISK_BUDGET_EXCEEDED"
        result["blockers"].append("gross_stop_loss_plus_verified_costs_exceeds_budget")
        return result

    result.update({
        "valid": True,
        "eligible": True,
        "status": "SIZED_WITH_VERIFIED_ROUND_TRIP_COSTS",
        "quantity": quantity,
        "risk_budget_usd": risk_budget_usd,
        "gross_stop_risk_usd": gross_stop_risk_usd,
        "round_trip_cost_at_stop_usd": round_trip_cost_usd,
        "total_risk_reserved_usd": total_risk_usd,
        "notional_usd": notional_usd,
        "quote_to_usd": quote_to_usd,
        "minimum_order_notional_usd": minimum_notional_usd,
        "fractional_allowed": fractional_allowed,
    })
    return result
