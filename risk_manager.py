
import math

from config import (
    INITIAL_CAPITAL_USD,
    MAX_RISK_PER_TRADE,
    MAX_PORTFOLIO_RISK,
    RISK_TIERS,
    ALLOW_POSITION_SIZING,
)


# Límites absolutos de seguridad.
HARD_MAX_RISK_PER_TRADE = 0.01
HARD_MAX_PORTFOLIO_RISK = 0.03


def _is_finite_number(value):
    """Comprueba que un valor sea numérico y finito."""
    if isinstance(value, bool):
        return False

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
        "currency": "USD",
    }


def _risk_tier_for_score(score):
    """Identifica el nivel provisional de calidad."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    return None


def risk_pct_for_score(score):
    """
    Devuelve el riesgo provisional según la calidad.
    Nunca supera el 1 % absoluto por operación.
    """

    if not _is_finite_number(score):
        return 0.0

    score = float(score)

    if not 0 <= score <= 100:
        return 0.0

    tier = _risk_tier_for_score(score)

    if tier is None:
        return 0.0

    if not isinstance(RISK_TIERS, dict):
        return 0.0

    tier_risk = RISK_TIERS.get(tier, 0.0)

    if not _is_finite_number(tier_risk):
        return 0.0

    tier_risk = float(tier_risk)

    if tier_risk <= 0:
        return 0.0

    if not _is_finite_number(MAX_RISK_PER_TRADE):
        return 0.0

    configured_limit = float(MAX_RISK_PER_TRADE)

    if configured_limit <= 0:
        return 0.0

    return min(
        tier_risk,
        configured_limit,
        HARD_MAX_RISK_PER_TRADE,
    )


def calculate_position_size(
    entry,
    stop,
    score,
    capital_usd=None,
    available_capital_usd=None,
    quote_to_usd=None,
    portfolio_risk_usd=None,
    costs_per_unit_quote=None,
    fixed_costs_quote=None,
    minimum_order_notional_usd=None,
    fractional_allowed=None,
):
    """
    Calcula el tamaño teórico de una posición en USD.

    entry y stop:
        Precio de entrada y stop en la moneda de cotización.

    quote_to_usd:
        Conversión de la moneda de cotización a USD.
        Si los precios ya están expresados en USD, usar 1.0.

    portfolio_risk_usd:
        Riesgo agregado actual de la cartera en USD.
        Es obligatorio facilitarlo explícitamente.

    costs_per_unit_quote:
        Costes variables estimados por unidad en moneda de cotización.

    fixed_costs_quote:
        Suma de costes fijos conocidos de apertura y cierre en moneda
        de cotización. Es obligatorio facilitarlo explícitamente,
        incluso cuando el valor confirmado sea 0.

    available_capital_usd:
        Efectivo disponible en la cartera virtual tras reservar
        posiciones abiertas. Es obligatorio y limita el nominal.

    minimum_order_notional_usd:
        Nominal mínimo confirmado del instrumento, convertido a USD.

    fractional_allowed:
        Regla confirmada del instrumento para permitir unidades fraccionarias.

    El cálculo permanece bloqueado mientras
    ALLOW_POSITION_SIZING sea False.
    """

    if not ALLOW_POSITION_SIZING:
        return _blocked_status(
            "Cálculo de tamaño deshabilitado: faltan verificaciones "
            "de conversión de divisa y costes."
        )

    if capital_usd is None:
        capital_usd = INITIAL_CAPITAL_USD

    if available_capital_usd is None:
        return _blocked_status(
            "Falta confirmar el efectivo disponible en la cartera virtual."
        )

    if portfolio_risk_usd is None:
        return _blocked_status(
            "Falta confirmar el riesgo acumulado de la cartera en USD."
        )

    if quote_to_usd is None:
        return _blocked_status(
            "Falta el tipo de conversión de la cotización a USD."
        )

    if costs_per_unit_quote is None:
        return _blocked_status(
            "Falta la estimación de costes variables por unidad."
        )

    if fixed_costs_quote is None:
        return _blocked_status(
            "Falta confirmar los costes fijos totales de apertura y cierre."
        )

    if minimum_order_notional_usd is None:
        return _blocked_status(
            "Falta confirmar el nominal mínimo del instrumento."
        )

    if fractional_allowed is None:
        return _blocked_status(
            "Falta confirmar si el instrumento admite unidades fraccionarias."
        )

    if not isinstance(fractional_allowed, bool):
        return _blocked_status(
            "La regla de unidades fraccionarias no es válida."
        )

    values = {
        "entry": entry,
        "stop": stop,
        "score": score,
        "capital_usd": capital_usd,
        "available_capital_usd": available_capital_usd,
        "quote_to_usd": quote_to_usd,
        "portfolio_risk_usd": portfolio_risk_usd,
        "costs_per_unit_quote": costs_per_unit_quote,
        "fixed_costs_quote": fixed_costs_quote,
        "minimum_order_notional_usd": minimum_order_notional_usd,
        "MAX_RISK_PER_TRADE": MAX_RISK_PER_TRADE,
        "MAX_PORTFOLIO_RISK": MAX_PORTFOLIO_RISK,
    }

    for name, value in values.items():
        if not _is_finite_number(value):
            return _blocked_status(
                f"Valor no válido o no finito: {name}."
            )

    entry = float(entry)
    stop = float(stop)
    score = float(score)
    capital_usd = float(capital_usd)
    available_capital_usd = float(available_capital_usd)
    quote_to_usd = float(quote_to_usd)
    portfolio_risk_usd = float(portfolio_risk_usd)
    costs_per_unit_quote = float(costs_per_unit_quote)
    fixed_costs_quote = float(fixed_costs_quote)
    minimum_order_notional_usd = float(minimum_order_notional_usd)

    configured_trade_limit = float(MAX_RISK_PER_TRADE)
    configured_portfolio_limit = float(MAX_PORTFOLIO_RISK)

    if not 0 <= score <= 100:
        return _blocked_status(
            "La puntuación debe estar entre 0 y 100."
        )

    if entry <= 0 or stop <= 0:
        return _blocked_status(
            "Los precios de entrada y stop deben ser positivos."
        )

    if stop >= entry:
        return _blocked_status(
            "El stop debe estar por debajo de la entrada "
            "para una operación de compra."
        )

    if capital_usd <= 0:
        return _blocked_status(
            "El capital USD debe ser positivo."
        )

    if available_capital_usd <= 0 or available_capital_usd > capital_usd + 1e-8:
        return _blocked_status(
            "El efectivo disponible debe ser positivo y no superar el capital de la cartera virtual."
        )

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
            "Los costes variables por unidad no pueden ser negativos."
        )

    if fixed_costs_quote < 0:
        return _blocked_status(
            "Los costes fijos no pueden ser negativos."
        )

    if minimum_order_notional_usd <= 0:
        return _blocked_status(
            "El nominal mínimo del instrumento debe ser positivo."
        )

    if configured_trade_limit <= 0:
        return _blocked_status(
            "El límite configurado por operación no es válido."
        )

    if configured_portfolio_limit <= 0:
        return _blocked_status(
            "El límite configurado de cartera no es válido."
        )

    requested_risk_pct = risk_pct_for_score(score)

    if requested_risk_pct <= 0:
        return _blocked_status(
            "La calidad de la señal no alcanza el nivel mínimo."
        )

    max_portfolio_risk_pct = min(
        configured_portfolio_limit,
        HARD_MAX_PORTFOLIO_RISK,
    )

    max_portfolio_risk_usd = (
        capital_usd * max_portfolio_risk_pct
    )

    if (
        not _is_finite_number(max_portfolio_risk_usd)
        or max_portfolio_risk_usd <= 0
    ):
        return _blocked_status(
            "El límite monetario de riesgo de cartera no es válido."
        )

    if portfolio_risk_usd >= max_portfolio_risk_usd:
        return _blocked_status(
            "Se ha alcanzado el límite de riesgo de cartera."
        )

    remaining_portfolio_risk_usd = (
        max_portfolio_risk_usd - portfolio_risk_usd
    )

    requested_risk_amount_usd = (
        capital_usd * requested_risk_pct
    )

    if (
        not _is_finite_number(requested_risk_amount_usd)
        or requested_risk_amount_usd <= 0
    ):
        return _blocked_status(
            "El importe de riesgo calculado no es válido."
        )

    risk_amount_usd = min(
        requested_risk_amount_usd,
        remaining_portfolio_risk_usd,
    )

    # Porcentaje efectivo después de aplicar el límite de cartera.
    risk_pct = risk_amount_usd / capital_usd

    if not _is_finite_number(risk_pct) or risk_pct <= 0:
        return _blocked_status(
            "El porcentaje de riesgo efectivo no es válido."
        )

    fixed_costs_usd = fixed_costs_quote * quote_to_usd
    if not _is_finite_number(fixed_costs_usd):
        return _blocked_status(
            "Los costes fijos convertidos a USD no son válidos."
        )

    available_risk_for_units_usd = risk_amount_usd - fixed_costs_usd
    if available_risk_for_units_usd <= 0:
        return _blocked_status(
            "Los costes fijos consumen todo el presupuesto de riesgo disponible."
        )

    price_risk_quote = entry - stop

    total_risk_per_unit_quote = (
        price_risk_quote + costs_per_unit_quote
    )

    if (
        not _is_finite_number(total_risk_per_unit_quote)
        or total_risk_per_unit_quote <= 0
    ):
        return _blocked_status(
            "El riesgo por unidad no es válido."
        )

    total_risk_per_unit_usd = (
        total_risk_per_unit_quote * quote_to_usd
    )

    if (
        not _is_finite_number(total_risk_per_unit_usd)
        or total_risk_per_unit_usd <= 0
    ):
        return _blocked_status(
            "El riesgo por unidad convertido a USD no es válido."
        )

    risk_based_position_size = (
        available_risk_for_units_usd / total_risk_per_unit_usd
    )
    cash_cost_per_unit_usd = (
        (entry + costs_per_unit_quote) * quote_to_usd
    )
    cash_after_fixed_costs_usd = available_capital_usd - fixed_costs_usd
    max_cash_position_size = (
        cash_after_fixed_costs_usd / cash_cost_per_unit_usd
        if cash_cost_per_unit_usd > 0
        else 0.0
    )
    if (
        not _is_finite_number(risk_based_position_size)
        or risk_based_position_size <= 0
        or not _is_finite_number(max_cash_position_size)
        or max_cash_position_size <= 0
    ):
        return _blocked_status(
            "No se ha podido calcular un tamaño de posición válido dentro del efectivo disponible."
        )

    # Long-only, unleveraged: never let the theoretical stop-risk quantity
    # exceed the cash available in the virtual portfolio.
    position_size = min(
        risk_based_position_size,
        max_cash_position_size,
    )
    if not fractional_allowed:
        position_size = float(math.floor(position_size))
    if not _is_finite_number(position_size) or position_size <= 0:
        return _blocked_status(
            "El tamaño calculado no alcanza una unidad mínima permitida por el instrumento."
        )

    position_notional_usd = position_size * entry * quote_to_usd
    if position_notional_usd + 1e-8 < minimum_order_notional_usd:
        return _blocked_status(
            "El nominal calculado es inferior al mínimo confirmado del instrumento."
        )
    cash_required_usd = (
        position_size * cash_cost_per_unit_usd + fixed_costs_usd
    )
    if (
        not _is_finite_number(position_notional_usd)
        or position_notional_usd <= 0
        or not _is_finite_number(cash_required_usd)
        or cash_required_usd <= 0
        or cash_required_usd > available_capital_usd + 1e-8
    ):
        return _blocked_status(
            "El nominal y los costes estimados superan el efectivo disponible."
        )

    position_risk_usd = position_size * total_risk_per_unit_usd
    total_reserved_risk_usd = fixed_costs_usd + position_risk_usd
    if (
        not _is_finite_number(position_risk_usd)
        or not _is_finite_number(total_reserved_risk_usd)
        or total_reserved_risk_usd <= 0
        or total_reserved_risk_usd > risk_amount_usd + 1e-8
    ):
        return _blocked_status(
            "El riesgo reservado no es válido tras aplicar el límite de efectivo."
        )

    risk_tier = _risk_tier_for_score(score)

    return {
        "status": "SIMULACION_NO_VALIDADA",
        "validated": False,
        "reason": (
            "Cálculo teórico. No autorizado para operativa real "
            "hasta completar la validación del sistema."
        ),
        "risk_tier": risk_tier,
        "requested_risk_pct": requested_risk_pct,
        "risk_pct": risk_pct,
        "risk_amount_usd": risk_amount_usd,
        "max_portfolio_risk_usd": max_portfolio_risk_usd,
        "remaining_portfolio_risk_usd": (
            remaining_portfolio_risk_usd
        ),
        "risk_per_unit_usd": total_risk_per_unit_usd,
        "fixed_costs_usd": fixed_costs_usd,
        "position_risk_usd": position_risk_usd,
        "total_reserved_risk_usd": total_reserved_risk_usd,
        "position_size": position_size,
        "entry_price_quote": entry,
        "quote_to_usd": quote_to_usd,
        "position_notional_usd": position_notional_usd,
        "minimum_order_notional_usd": minimum_order_notional_usd,
        "fractional_allowed": fractional_allowed,
        "cash_required_usd": cash_required_usd,
        "costs_per_unit_quote": costs_per_unit_quote,
        "available_capital_usd": available_capital_usd,
        "cash_limited": position_size < risk_based_position_size,
        "capital_usd": capital_usd,
        "currency": "USD",
    }
