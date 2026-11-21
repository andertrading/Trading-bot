"""Transparent eToro transaction-cost model.

Published platform fees are estimates until checked in the user's execution ticket.
Market spread, account-specific conversion and CFD financing are never guessed.
"""
import math

from config import (
    ACCOUNT_CURRENCY,
    ASSETS,
    ETORO_CFD_CRYPTO_OVERNIGHT_USD_PER_UNIT_DAY,
    ETORO_CFD_STOCK_ETF_ANNUAL_FEE_PCT,
    ETORO_CFD_STOCK_ETF_BENCHMARK_RATE_PCT,
    ETORO_CRYPTO_CFD_FEE_PCT_PER_SIDE,
    ETORO_CRYPTO_REAL_FEE_PCT_PER_SIDE,
    ETORO_ETF_CFD_FEE_PCT_PER_SIDE,
    ETORO_EXECUTION_TYPES,
    ETORO_EXECUTION_TYPES_CONFIRMED,
    ETORO_FEE_SCHEDULE_CONFIRMED,
    ETORO_FX_CONVERSION_FEE_PCT,
    ETORO_FUNDING_FX_CONVERSION_FEE_PCT,
    ETORO_EXECUTION_PRICE_BASIS_CONFIRMED,
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
    leverage=1.0,
    side="BUY",
    country_code="ES",
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
    side = str(side).upper()
    country_code = str(country_code).upper()
    if not _finite(leverage) or float(leverage) < 1.0 or side not in {"BUY", "SELL"}:
        result["unpriced_costs"].append("invalid_leverage_or_side")
        return result
    leverage = float(leverage)
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
    if not ETORO_EXECUTION_PRICE_BASIS_CONFIRMED:
        result["unpriced_costs"].append("historical_ohlc_to_etoro_execution_price_basis_unverified")
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
    elif (
        asset_type in {"STOCK", "ETF"}
        and side == "BUY"
        and leverage == 1.0
    ):
        result["overnight_cost"] = 0.0
        result["assumptions"].append(
            "CFD largo sin apalancamiento en acción/ETF: sin tarifa nocturna según eToro"
        )
    elif (
        asset_type == "CRYPTO"
        and country_code == "DE"
        and side == "BUY"
        and leverage == 1.0
    ):
        result["overnight_cost"] = 0.0
        result["assumptions"].append(
            "CFD cripto largo sin apalancamiento para cliente alemán: sin tarifa nocturna"
        )
    elif overnight_charge_units is None:
        result["unpriced_costs"].append("cfd_overnight_charge_units_missing")
    elif not _finite(overnight_charge_units) or float(overnight_charge_units) < 0:
        result["unpriced_costs"].append("invalid_overnight_charge_units")
    elif float(overnight_charge_units) == 0:
        result["overnight_cost"] = 0.0
    elif asset_type in {"STOCK", "ETF"}:
        benchmark = ETORO_CFD_STOCK_ETF_BENCHMARK_RATE_PCT
        if (
            benchmark is None
            or not _finite(benchmark)
        ):
            result["unpriced_costs"].append("cfd_benchmark_rate_missing")
        else:
            annual_rate = (
                float(ETORO_CFD_STOCK_ETF_ANNUAL_FEE_PCT)
                + float(benchmark)
            )
            if annual_rate < 0:
                result["unpriced_costs"].append("invalid_cfd_annual_rate")
            else:
                result["overnight_cost"] = (
                    ((entry_notional + exit_notional) / 2.0)
                    * annual_rate
                    * float(overnight_charge_units)
                    / 365.0
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


def estimate_funding_conversion_cost(
    amount,
    source_currency,
    target_currency=None,
    *,
    fx_rate=None,
    conversion_fee_pct=None,
):
    """Estimate one-off funding FX cost separately from per-trade FX."""
    target_was_defaulted = target_currency is None
    if target_was_defaulted:
        target_currency = ACCOUNT_CURRENCY
    result = {
        "valid": False,
        "complete": False,
        "source_currency": str(source_currency or "").upper(),
        "target_currency": str(target_currency or "").upper(),
        "conversion_cost_target_currency": None,
        "unpriced_costs": [],
    }
    if not _finite(amount) or float(amount) < 0:
        result["unpriced_costs"].append("invalid_funding_amount")
        return result
    amount = float(amount)
    source = result["source_currency"]
    target = result["target_currency"]
    if not source or not target:
        result["unpriced_costs"].append("funding_currency_missing")
        return result
    if target_was_defaulted and not ETORO_ACCOUNT_CURRENCY_CONFIRMED:
        result["unpriced_costs"].append("account_currency_not_confirmed")
    if source == target:
        result["conversion_cost_target_currency"] = 0.0
        result["valid"] = True
        result["complete"] = not result["unpriced_costs"]
        return result

    if conversion_fee_pct is None:
        conversion_fee_pct = ETORO_FUNDING_FX_CONVERSION_FEE_PCT
    if (
        conversion_fee_pct is None
        or fx_rate is None
        or not _finite(conversion_fee_pct)
        or not _finite(fx_rate)
        or float(conversion_fee_pct) < 0
        or float(fx_rate) <= 0
    ):
        result["unpriced_costs"].append("funding_fx_rate_or_fee_missing")
        result["valid"] = True
        return result

    result["conversion_cost_target_currency"] = (
        amount * float(fx_rate) * float(conversion_fee_pct)
    )
    result["valid"] = True
    result["complete"] = True
    return result
