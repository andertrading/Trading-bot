"""Transparent eToro transaction-cost model.

Published platform fees are estimates until checked in the user's execution ticket.
Market spread, account-specific conversion and CFD financing are never guessed.
"""
import math

from config import (
    ACCOUNT_CURRENCY,
    ASSETS,
    ETORO_CFD_CRYPTO_OVERNIGHT_USD_PER_UNIT_DAY,
    ETORO_CFD_OVERNIGHT_USD_PER_1000_PER_DAY,
    ETORO_CRYPTO_CFD_FEE_PCT_PER_SIDE,
    ETORO_CRYPTO_REAL_FEE_PCT_PER_SIDE,
    ETORO_ETF_CFD_FEE_PCT_PER_SIDE,
    ETORO_EXECUTION_TYPES,
    ETORO_EXECUTION_TYPES_CONFIRMED,
    ETORO_FEE_SCHEDULE_CONFIRMED,
    ETORO_FX_CONVERSION_FEE_PCT,
    ETORO_MARKET_SPREAD_BPS,
    ETORO_STOCK_CFD_FEE_PCT_PER_SIDE,
    ETORO_STOCK_REAL_COMMISSION_USD_PER_SIDE,
    ETORO_ACCOUNT_CURRENCY_CONFIRMED,
)


def _finite(value):
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def estimate_etoro_trade_costs(
    asset,
    entry,
    exit_price,
    quantity,
    *,
    execution_type=None,
    market_spread_bps=None,
    quote_to_account=None,
    fx_conversion_fee_pct=None,
    overnight_charge_units=0.0,
):
    """Estimate known costs for a long trade; report every unpriced component.

    market_spread_bps is the full bid/ask spread in basis points. The model
    charges half the spread on entry and half on exit.
    overnight_charge_units must reflect the broker's billable fee units
    (including any 3x weekend charge where applicable).
    """
    result = {
        "valid": False,
        "complete": False,
        "execution_type": None,
        "currency": ACCOUNT_CURRENCY,
        "commission_cost": 0.0,
        "market_spread_cost": None,
        "fx_conversion_cost": 0.0,
        "overnight_cost": None,
        "known_costs_total": 0.0,
        "unpriced_costs": [],
        "assumptions": [],
    }

    if asset not in ASSETS:
        result["unpriced_costs"].append("unknown_asset")
        return result

    config = ASSETS[asset]
    asset_type = str(config.get("type", "")).upper()
    quote_currency = str(config.get("currency", "")).upper()
    account_currency = str(ACCOUNT_CURRENCY).upper()

    if not all(_finite(v) for v in (entry, exit_price, quantity)):
        result["unpriced_costs"].append("invalid_trade_values")
        return result

    entry = float(entry)
    exit_price = float(exit_price)
    quantity = float(quantity)
    if entry <= 0 or exit_price <= 0 or quantity <= 0:
        result["unpriced_costs"].append("non_positive_trade_values")
        return result

    execution_type = (
        execution_type or ETORO_EXECUTION_TYPES.get(asset)
    )
    execution_type = str(execution_type or "").upper()
    if execution_type not in {"REAL", "CFD"}:
        result["unpriced_costs"].append("execution_type_unknown")
        return result
    result["execution_type"] = execution_type

    if not ETORO_EXECUTION_TYPES_CONFIRMED:
        result["unpriced_costs"].append("execution_type_not_confirmed_in_etoro_ticket")
    if not ETORO_FEE_SCHEDULE_CONFIRMED:
        result["unpriced_costs"].append("account_specific_fee_tier_not_confirmed")
    if not ETORO_ACCOUNT_CURRENCY_CONFIRMED:
        result["unpriced_costs"].append("account_currency_not_confirmed")

    entry_notional = entry * quantity
    exit_notional = exit_price * quantity
    total_turnover = entry_notional + exit_notional

    if execution_type == "REAL":
        if asset_type == "STOCK":
            result["commission_cost"] = (
                2.0 * float(ETORO_STOCK_REAL_COMMISSION_USD_PER_SIDE)
            )
            result["assumptions"].append(
                "Acción real: máximo publicado de 2 USD por lado"
            )
        elif asset_type == "ETF":
            result["commission_cost"] = 0.0
            result["assumptions"].append(
                "ETF real: comisión de operación publicada como 0"
            )
        elif asset_type == "CRYPTO":
            rate = float(ETORO_CRYPTO_REAL_FEE_PCT_PER_SIDE)
            result["commission_cost"] = total_turnover * rate
            result["assumptions"].append(
                "Cripto real: tarifa base publicada del 1% por lado; nivel Club no confirmado"
            )
        else:
            result["unpriced_costs"].append("unsupported_real_asset_type")
    else:
        if asset_type == "STOCK":
            rate = float(ETORO_STOCK_CFD_FEE_PCT_PER_SIDE)
        elif asset_type == "ETF":
            rate = float(ETORO_ETF_CFD_FEE_PCT_PER_SIDE)
        elif asset_type == "CRYPTO":
            rate = float(ETORO_CRYPTO_CFD_FEE_PCT_PER_SIDE)
        else:
            result["unpriced_costs"].append("unsupported_cfd_asset_type")
            rate = 0.0
        result["commission_cost"] = total_turnover * rate
        result["assumptions"].append(
            "CFD: spread porcentual de apertura/cierre publicado, sujeto a cambios"
        )

    if market_spread_bps is None:
        market_spread_bps = ETORO_MARKET_SPREAD_BPS.get(asset)
    if market_spread_bps is None:
        result["unpriced_costs"].append("live_market_bid_ask_spread_missing")
    elif not _finite(market_spread_bps) or float(market_spread_bps) < 0:
        result["unpriced_costs"].append("invalid_market_spread_bps")
    else:
        result["market_spread_cost"] = (
            total_turnover * float(market_spread_bps) / 20000.0
        )

    if quote_currency == account_currency:
        result["fx_conversion_cost"] = 0.0
    else:
        if fx_conversion_fee_pct is None:
            fx_conversion_fee_pct = ETORO_FX_CONVERSION_FEE_PCT
        if (
            fx_conversion_fee_pct is None
            or quote_to_account is None
            or not _finite(fx_conversion_fee_pct)
            or not _finite(quote_to_account)
            or float(fx_conversion_fee_pct) < 0
            or float(quote_to_account) <= 0
        ):
            result["unpriced_costs"].append("fx_conversion_rate_or_fee_missing")
        else:
            result["fx_conversion_cost"] = (
                total_turnover
                * float(fx_conversion_fee_pct)
                * float(quote_to_account)
            )

    if execution_type == "REAL":
        result["overnight_cost"] = 0.0
    elif overnight_charge_units is None:
        result["unpriced_costs"].append("cfd_overnight_charge_units_missing")
    elif not _finite(overnight_charge_units) or float(overnight_charge_units) < 0:
        result["unpriced_costs"].append("invalid_overnight_charge_units")
    elif float(overnight_charge_units) == 0:
        result["overnight_cost"] = 0.0
    elif asset_type in {"STOCK", "ETF"}:
        rate = ETORO_CFD_OVERNIGHT_USD_PER_1000_PER_DAY.get(asset)
        if rate is None or not _finite(rate) or float(rate) < 0:
            result["unpriced_costs"].append("cfd_overnight_fee_missing")
        else:
            result["overnight_cost"] = (
                ((entry_notional + exit_notional) / 2.0)
                / 1000.0
                * float(rate)
                * overnight_charge_units
            )
    elif asset_type == "CRYPTO":
        rate = ETORO_CFD_CRYPTO_OVERNIGHT_USD_PER_UNIT_DAY.get(asset)
        if rate is None or not _finite(rate) or float(rate) < 0:
            result["unpriced_costs"].append("cfd_crypto_overnight_fee_missing")
        else:
            result["overnight_cost"] = (
                quantity * float(rate) * overnight_charge_units
            )

    result["known_costs_total"] = (
        result["commission_cost"]
        + (result["market_spread_cost"] or 0.0)
        + result["fx_conversion_cost"]
        + (result["overnight_cost"] or 0.0)
    )
    result["valid"] = True
    result["complete"] = not result["unpriced_costs"]
    return result
