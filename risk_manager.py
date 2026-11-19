
import math

from config import (
    INITIAL_CAPITAL_USD,
    MAX_RISK_PER_TRADE,
    MAX_PORTFOLIO_RISK,
    RISK_TIERS,
    ALLOW_POSITION_SIZING,
)


def _is_finite_number(value):
    """Comprueba que un valor sea numérico y finito."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _blocked_status(reason):
    """Respuesta uniforme cuando no se puede calcular el riesgo."""
    return {
        "status": "BLOCKED",
        "validated": False,
        "reason": reason,
        "risk_pct": 0.0,
        "risk_amount_usd": 0.0,
        "position_size": 0.0,
    }


def risk_pct_for_score(score):
    """
    Devuelve el porcentaje de riesgo provisional según la calidad
    de la señal. Nunca supera MAX_RISK_PER_TRADE.
    """
    if not _is_finite_number(score):
        return 0.0

    score = float(score)

    if score >= 90:
        tier = "A"
    elif score >= 80:
        tier = "B"
    elif score >= 70:
        tier = "C"
    else:
        return 0.0

    tier_risk = RISK_TIERS.get(tier, 0.0)

    if not _is_finite_number(tier_risk):
        return 0.0

    return min(float(tier_risk), MAX_RISK_PER_TRADE)


def calculate_position_size(
    entry,
    stop,
    score,
    capital_usd=None,
    quote_to_usd=None,
    portfolio_risk_usd=None,
    costs_per_unit_quote=0.0,
):
    """
    Calcula el tamaño teórico de una posición usando USD.

    entry y stop:
        Precio de entrada y stop en la moneda de cotización del activo.

    quote_to_usd:
        Tipo de conversión de la moneda de cotización a USD.
        Si los precios ya están normalizados a USD, debe ser 1.0.

    portfolio_risk_usd:
        Riesgo agregado actual de la cartera, expresado en USD.

    costs_per_unit_quote:
        Costes estimados por unidad en la moneda de cotización.

    El cálculo permanece bloqueado mientras ALLOW_POSITION_SIZING
    sea False.
    """

    if not ALLOW_POSITION_SIZING:
        return _blocked_status(
            "Cálculo de tamaño deshabilitado: faltan verificaciones "
            "de conversión de divisa y costes."
        )

    if capital_usd is None:
        capital_usd = INITIAL_CAPITAL_USD

    if portfolio_risk_usd is None:
        portfolio_risk_usd = 0.0

    if quote_to_usd is None:
        return _blocked_status(
            "Falta el tipo de conversión de la cotización a USD."
        )

    values = {
        "entry": entry,
        "stop": stop,
        "capital_usd": capital_usd,
        "quote_to_usd": quote_to_usd,
        "portfolio_risk_usd": portfolio_risk_usd,
        "costs_per_unit_quote": costs_per_unit_quote,
    }

    for name, value in values.items():
        if not _is_finite_number(value):
            return _blocked_status(
                f"Valor no válido o no finito: {name}."
            )

    entry = float(entry)
    stop = float(stop)
    capital_usd = float(capital_usd)
    quote_to_usd = float(quote_to_usd)
    portfolio_risk_usd = float(portfolio_risk_usd)
    costs_per_unit_quote = float(costs_per_unit_quote)

    if entry <= 0 or stop <= 0:
        return _blocked_status(
            "Los precios de entrada y stop deben ser positivos."
        )

    if stop >= entry:
        return _blocked_status(
            "El stop debe estar por debajo de la entrada para una "
            "operación de compra."
        )

    if capital_usd <= 0:
        return _blocked_status("El capital USD debe ser positivo.")

    if quote_to_usd <= 0:
        return _blocked_status(
            "El tipo de conversión a USD debe ser positivo."
        )

    if portfolio_risk_usd < 0:
        return _blocked_status(
            "El riesgo acumulado de cartera no puede ser negativo."
        )

    if costs_per_unit_quote < 0:
        return _blocked_status(
            "Los costes por unidad no pueden ser negativos."
        )

    risk_pct = risk_pct_for_score(score)

    if risk_pct <= 0:
        return _blocked_status(
            "La calidad de la señal no alcanza el nivel mínimo."
        )

    risk_amount_usd = capital_usd * risk_pct

    max_portfolio_risk_usd = (
        capital_usd * MAX_PORTFOLIO_RISK
    )

    remaining_portfolio_risk_usd = (
        max_portfolio_risk_usd - portfolio_risk_usd
    )

    if remaining_portfolio_risk_usd <= 0:
        return _blocked_status(
            "Se ha alcanzado el límite de riesgo de cartera."
        )

    risk_amount_usd = min(
        risk_amount_usd,
        remaining_portfolio_risk_usd,
    )

    price_risk_quote = entry - stop
    total_risk_per_unit_quote = (
        price_risk_quote + costs_per_unit_quote
    )

    if total_risk_per_unit_quote <= 0:
        return _blocked_status(
            "El riesgo por unidad no es válido."
        )

    total_risk_per_unit_usd = (
        total_risk_per_unit_quote * quote_to_usd
    )

    if total_risk_per_unit_usd <= 0:
        return _blocked_status(
            "El riesgo por unidad convertido a USD no es válido."
        )

    position_size = (
        risk_amount_usd / total_risk_per_unit_usd
    )

    if not _is_finite_number(position_size) or position_size <= 0:
        return _blocked_status(
            "No se ha podido calcular un tamaño de posición válido."
        )

    return {
        "status": "SIMULACION_NO_VALIDADA",
        "validated": False,
        "reason": (
            "Cálculo teórico. No autorizado para operativa real "
            "hasta completar la validación del sistema."
        ),
        "risk_tier": (
            "A" if score >= 90 else
            "B" if score >= 80 else
            "C"
        ),
        "risk_pct": risk_pct,
        "risk_amount_usd": risk_amount_usd,
        "max_portfolio_risk_usd": max_portfolio_risk_usd,
        "remaining_portfolio_risk_usd": (
            remaining_portfolio_risk_usd
        ),
        "risk_per_unit_usd": total_risk_per_unit_usd,
        "position_size": position_size,
        "capital_usd": capital_usd,
        "currency": "USD",
    }
