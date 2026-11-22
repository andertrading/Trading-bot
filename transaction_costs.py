"""Transparent eToro transaction-cost model.

Published platform fees are estimates until checked in the user's execution ticket.
Market spread, account-specific conversion and CFD financing are never guessed.
"""
import math

from asset_economic_registry import (
    classify_registry_asset,
    load_registry,
    valid_fee_side,
)
from config import (
    ACCOUNT_CURRENCY,
    ASSETS,
    ETORO_CFD_CRYPTO_OVERNIGHT_USD_PER_UNIT_DAY,
    ETORO_CFD_STOCK_ETF_ANNUAL_FEE_PCT,
    ETORO_CFD_STOCK_ETF_BENCHMARK_RATE_PCT,
    ETORO_CFD_STOCK_SHORT_BORROW_ANNUAL_RATE_PCT,
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
    overnight_charge_units=None,
    leverage=1.0,
    side="BUY",
    country_code="ES",
):
    """Estimate known costs for one trade; report every unpriced component.

    market_spread_bps is the full bid/ask spread in basis points. The model
    charges half the spread on entry and half on exit.
    overnight_charge_units must reflect the broker's billable fee units
    (including any 3x weekend charge where applicable). It defaults to None
    so omitted CFD financing data is reported as unpriced, never as zero cost.
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
    elif asset_type in {"STOCK", "ETF"} and side == "BUY":
        benchmark = ETORO_CFD_STOCK_ETF_BENCHMARK_RATE_PCT
        if benchmark is None or not _finite(benchmark):
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
                    entry_notional
                    * annual_rate
                    * float(overnight_charge_units)
                    / 365.0
                )
    elif asset_type in {"STOCK", "ETF"} and side == "SELL":
        borrow_rate = ETORO_CFD_STOCK_SHORT_BORROW_ANNUAL_RATE_PCT.get(asset)
        if borrow_rate is None or not _finite(borrow_rate) or float(borrow_rate) < 0:
            result["unpriced_costs"].append("cfd_short_borrow_rate_missing")
        elif float(borrow_rate) < 0.10:
            # eToro classifies annual borrow costs below 10% as
            # easy-to-borrow: no overnight fee applies.
            result["overnight_cost"] = 0.0
        elif float(borrow_rate) > 0.10:
            # Hard-to-borrow: annual borrow cost is charged against
            # opening bid notional, prorated by billable fee units.
            if overnight_charge_units is None:
                result["unpriced_costs"].append(
                    "cfd_overnight_charge_units_missing"
                )
            elif (
                not _finite(overnight_charge_units)
                or float(overnight_charge_units) < 0
            ):
                result["unpriced_costs"].append(
                    "invalid_overnight_charge_units"
                )
            else:
                result["overnight_cost"] = (
                    entry_notional
                    * float(borrow_rate)
                    * float(overnight_charge_units)
                    / 365.0
                )
        else:
            # eToro's public rule defines below and above 10%, but not
            # exactly 10%; fail closed rather than invent a classification.
            result["unpriced_costs"].append("cfd_short_borrow_rate_threshold_ambiguous")
    elif asset_type == "CRYPTO":
        rate = ETORO_CFD_CRYPTO_OVERNIGHT_USD_PER_UNIT_DAY.get(asset)
        if rate is None or not _finite(rate) or float(rate) < 0:
            result["unpriced_costs"].append("cfd_crypto_overnight_fee_missing")
        elif overnight_charge_units is None:
            result["unpriced_costs"].append(
                "cfd_overnight_charge_units_missing"
            )
        elif (
            not _finite(overnight_charge_units)
            or float(overnight_charge_units) < 0
        ):
            result["unpriced_costs"].append(
                "invalid_overnight_charge_units"
            )
        else:
            result["overnight_cost"] = (
                quantity * float(rate) * float(overnight_charge_units)
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


def estimate_verified_registry_trade_costs(
    asset,
    entry,
    exit_price,
    quantity,
    *,
    registry=None,
):
    """Calculate REAL round-trip costs only from fully verified registry evidence.

    This is the admission-grade cost path for the REAL-only MVP. The legacy
    estimate_etoro_trade_costs() remains a published-fee scenario calculator;
    it must not be used to certify an instrument or override registry blockers.
    """
    result = {
        "valid": False,
        "complete": False,
        "source": "asset_economic_registry",
        "registry_status": None,
        "execution_type": None,
        "currency": ACCOUNT_CURRENCY,
        "entry_commission_cost": None,
        "exit_commission_cost": None,
        "commission_cost": None,
        "market_spread_cost": None,
        "fx_conversion_cost": None,
        "overnight_cost": None,
        "known_costs_total": None,
        "unpriced_costs": [],
    }

    if asset not in ASSETS:
        result["unpriced_costs"].append("asset_not_configured")
        return result
    if not all(_finite(value) for value in (entry, exit_price, quantity)):
        result["unpriced_costs"].append("invalid_trade_values")
        return result

    entry = float(entry)
    exit_price = float(exit_price)
    quantity = float(quantity)
    if entry <= 0 or exit_price <= 0 or quantity <= 0:
        result["unpriced_costs"].append("non_positive_trade_values")
        return result

    try:
        if registry is None:
            registry = load_registry()
        classification = classify_registry_asset(asset, registry)
    except (OSError, ValueError, TypeError) as error:
        result["unpriced_costs"].append("economic_registry_invalid")
        result["registry_error_type"] = type(error).__name__
        return result

    result["valid"] = True
    result["registry_status"] = classification["status"]
    result["execution_type"] = classification.get("execution_type")
    if classification["status"] != "ELIGIBLE":
        result["unpriced_costs"].extend(classification.get("blockers", []))
        return result

    item = registry["assets"][asset]
    fees = item["fees"]
    entry_fee = fees["entry"]
    exit_fee = fees["exit"]
    if not valid_fee_side(entry_fee) or not valid_fee_side(exit_fee):
        result["unpriced_costs"].append("round_trip_fee_evidence_missing")
        return result

    entry_notional = entry * quantity
    exit_notional = exit_price * quantity
    total_turnover = entry_notional + exit_notional
    fx = item["fx"]
    quote_to_account_rate = (
        float(fx["quote_to_account_rate"]) if fx.get("required") else 1.0
    )

    def side_cost(side, notional):
        model = side["model"]
        value = float(side["value"])
        if model == "ZERO":
            return 0.0
        if model == "FIXED":
            return value
        if model == "PERCENT_NOTIONAL":
            return notional * value / 100.0 * quote_to_account_rate
        raise ValueError("unsupported verified fee model")

    result["entry_commission_cost"] = side_cost(entry_fee, entry_notional)
    result["exit_commission_cost"] = side_cost(exit_fee, exit_notional)
    result["commission_cost"] = (
        result["entry_commission_cost"] + result["exit_commission_cost"]
    )

    spread_bps = float(item["spread"]["bid_ask_bps"])
    result["market_spread_cost"] = (
        total_turnover * spread_bps / 20000.0 * quote_to_account_rate
    )

    if fx.get("required"):
        result["fx_conversion_cost"] = (
            total_turnover
            * float(fx["conversion_fee_pct"]) / 100.0
            * quote_to_account_rate
        )
    else:
        result["fx_conversion_cost"] = 0.0

    # The REAL execution path has no CFD overnight financing.
    result["overnight_cost"] = 0.0
    result["known_costs_total"] = (
        result["commission_cost"]
        + result["market_spread_cost"]
        + result["fx_conversion_cost"]
    )
    result["complete"] = True
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
