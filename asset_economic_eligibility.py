"""Pre-backtest economic eligibility audit for the daily MVP.

This is a read-only diagnostic. It never accesses an eToro account, portfolio,
positions or order endpoints, and it never enables sizing or signal delivery.
Unknown costs and execution details fail closed as COSTS_UNVERIFIED.
"""
from __future__ import annotations

import json
import math
from typing import Any

from config import (
    ACCOUNT_CURRENCY,
    ASSETS,
    ETORO_ACCOUNT_CURRENCY_CONFIRMED,
    ETORO_EXECUTION_PRICE_BASIS_CONFIRMED,
    ETORO_EXECUTION_TYPES,
    ETORO_EXECUTION_TYPES_CONFIRMED,
    ETORO_FEE_SCHEDULE_CONFIRMED,
    ETORO_FX_CONVERSION_FEE_PCT,
    ETORO_MARKET_SPREAD_BPS,
)


def _valid_non_negative_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and number >= 0


def classify_asset(
    asset: str,
    *,
    asset_config: dict | None = None,
    execution_type: str | None = None,
    execution_type_confirmed: bool | None = None,
    fee_schedule_confirmed: bool | None = None,
    execution_price_basis_confirmed: bool | None = None,
    market_spread_bps: float | None = None,
    account_currency: str | None = None,
    account_currency_confirmed: bool | None = None,
    fx_conversion_fee_pct: float | None = None,
    minimum_order_constraints_confirmed: bool = False,
) -> dict:
    """Classify one asset for the daily long-only, unleveraged REAL MVP.

    ELIGIBLE means all mandatory economic inputs are explicitly verified.
    INELIGIBLE is reserved for a known hard exclusion (unknown/disabled asset
    or a confirmed non-REAL execution type under this MVP policy).
    Missing evidence is COSTS_UNVERIFIED, never silently treated as zero.
    """
    config = asset_config if asset_config is not None else ASSETS.get(asset)
    result = {
        "asset": asset,
        "name": (config or {}).get("name", asset),
        "asset_type": (config or {}).get("type"),
        "execution_type": None,
        "status": None,
        "blockers": [],
        "policy": "DAILY_LONG_ONLY_UNLEVERAGED_REAL",
    }

    if config is None:
        result["status"] = "INELIGIBLE"
        result["blockers"] = ["asset_not_configured"]
        return result

    if not config.get("enabled", True):
        result["status"] = "INELIGIBLE"
        result["blockers"] = ["asset_disabled_in_configuration"]
        return result

    configured_execution = (
        execution_type if execution_type is not None
        else ETORO_EXECUTION_TYPES.get(asset)
    )
    execution_confirmed = (
        ETORO_EXECUTION_TYPES_CONFIRMED
        if execution_type_confirmed is None
        else bool(execution_type_confirmed)
    )
    result["execution_type"] = (
        str(configured_execution).upper() if configured_execution else None
    )

    if execution_confirmed and str(configured_execution).upper() != "REAL":
        result["status"] = "INELIGIBLE"
        result["blockers"] = ["mvp_requires_confirmed_REAL_execution"]
        return result

    blockers = []
    if not execution_confirmed:
        blockers.append("execution_type_not_confirmed_in_instrument_ticket")

    fee_confirmed = (
        ETORO_FEE_SCHEDULE_CONFIRMED
        if fee_schedule_confirmed is None
        else bool(fee_schedule_confirmed)
    )
    if not fee_confirmed:
        blockers.append("account_specific_fee_schedule_not_confirmed")

    price_basis_confirmed = (
        ETORO_EXECUTION_PRICE_BASIS_CONFIRMED
        if execution_price_basis_confirmed is None
        else bool(execution_price_basis_confirmed)
    )
    if not price_basis_confirmed:
        blockers.append("historical_price_to_execution_price_basis_not_confirmed")

    spread = (
        ETORO_MARKET_SPREAD_BPS.get(asset)
        if market_spread_bps is None
        else market_spread_bps
    )
    if not _valid_non_negative_number(spread):
        blockers.append("observed_bid_ask_spread_missing_or_invalid")

    acct_currency = str(
        account_currency if account_currency is not None else ACCOUNT_CURRENCY
    ).upper()
    quote_currency = str(config.get("currency", "")).upper()
    acct_confirmed = (
        ETORO_ACCOUNT_CURRENCY_CONFIRMED
        if account_currency_confirmed is None
        else bool(account_currency_confirmed)
    )
    if not acct_confirmed:
        blockers.append("account_currency_not_confirmed")
    if not quote_currency:
        blockers.append("instrument_quote_currency_missing")
    elif quote_currency != acct_currency:
        if not _valid_non_negative_number(fx_conversion_fee_pct):
            blockers.append("trade_FX_conversion_fee_or_rate_not_verified")

    if not minimum_order_constraints_confirmed:
        blockers.append("minimum_order_and_fractional_quantity_rules_not_confirmed")

    result["blockers"] = blockers
    result["status"] = "ELIGIBLE" if not blockers else "COSTS_UNVERIFIED"
    return result


def build_eligibility_report() -> dict:
    """Return a deterministic, account-independent report for configured assets."""
    assets = [
        classify_asset(asset)
        for asset in sorted(ASSETS)
    ]
    counts = {
        status: sum(item["status"] == status for item in assets)
        for status in ("ELIGIBLE", "INELIGIBLE", "COSTS_UNVERIFIED")
    }
    return {
        "schema_version": 1,
        "audit_scope": "read_only_instrument_economic_eligibility",
        "account_portfolio_data_accessed": False,
        "orders_or_alerts_enabled": False,
        "sizing_enabled": False,
        "policy": "DAILY_LONG_ONLY_UNLEVERAGED_REAL",
        "account_currency": ACCOUNT_CURRENCY,
        "summary": counts,
        "assets": assets,
    }


def main() -> None:
    print(json.dumps(build_eligibility_report(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
