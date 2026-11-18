
from config import (
    INITIAL_CAPITAL_EUR,
    MAX_RISK_PER_TRADE,
    MAX_PORTFOLIO_RISK,
    RISK_TIERS,
)


def risk_pct_for_score(score):
    """
    Asigna un nivel de riesgo según la calidad de la señal.
    Los niveles son provisionales y no están validados.
    """

    if score >= 90:
        tier = "D"
    elif score >= 80:
        tier = "C"
    elif score >= 70:
        tier = "B"
    elif score >= 60:
        tier = "A"
    else:
        return 0.0

    return min(
        RISK_TIERS[tier],
        MAX_RISK_PER_TRADE
    )


def calculate_position_size(
    entry,
    stop,
    score,
    capital_eur=None,
    quote_to_eur=None,
    portfolio_risk_eur=None,
    costs_per_unit_quote=0.0,
):
    """
    Calcula unidades únicamente si se dispone de:
    - Conversión de divisa verificada.
    - Riesgo agregado de cartera conocido.
    - Costes por unidad correctamente estimados.

    No presupone que USD y EUR valgan lo mismo.
    """

    if capital_eur is None:
        capital_eur = INITIAL_CAPITAL_EUR

    if entry is None or stop is None:
        return None

    if entry <= 0 or stop <= 0 or stop >= entry:
        return None

    # Sin conversión fiable no se calcula tamaño.
    if quote_to_eur is None or quote_to_eur <= 0:
        return None

    # Sin riesgo agregado conocido no se autoriza el cálculo.
    if portfolio_risk_eur is None or portfolio_risk_eur < 0:
        return None

    if capital_eur <= 0:
        return None

    risk_pct = risk_pct_for_score(score)

    if risk_pct <= 0:
        return None

    risk_pct = min(
        risk_pct,
        MAX_RISK_PER_TRADE
    )

    risk_budget_eur = capital_eur * risk_pct

    portfolio_limit_eur = (
        capital_eur * MAX_PORTFOLIO_RISK
    )

    remaining_portfolio_risk = (
        portfolio_limit_eur - portfolio_risk_eur
    )

    if remaining_portfolio_risk <= 0:
        return None

    allowed_risk_eur = min(
        risk_budget_eur,
        remaining_portfolio_risk
    )

    price_risk_quote = entry - stop

    total_risk_per_unit_quote = (
        price_risk_quote + max(costs_per_unit_quote, 0)
    )

    total_risk_per_unit_eur = (
        total_risk_per_unit_quote * quote_to_eur
    )

    if total_risk_per_unit_eur <= 0:
        return None

    units = allowed_risk_eur / total_risk_per_unit_eur

    actual_risk_eur = (
        units * total_risk_per_unit_eur
    )

    return {
        "units": units,
        "risk_pct": risk_pct,
        "risk_budget_eur": allowed_risk_eur,
        "actual_risk_eur": actual_risk_eur,
        "portfolio_risk_after_eur": (
            portfolio_risk_eur + actual_risk_eur
        ),
        "quote_to_eur": quote_to_eur,
        "validated": False,
        "status": "SIMULACION_NO_VALIDADA",
    }
