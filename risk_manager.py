
import math

from config import (
    INITIAL_CAPITAL_EUR,
    MAX_RISK_PER_TRADE,
    MAX_PORTFOLIO_RISK,
    RISK_TIERS,
)


def _is_finite_number(value):
    """Comprueba que un valor sea numérico y finito."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def risk_pct_for_score(score):
    """
    Asigna un nivel de riesgo según la calidad de la señal.
    Los niveles son provisionales y no están validados.
    """

    if not _is_finite_number(score):
        return 0.0

    score = float(score)

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

    tier_risk = RISK_TIERS.get(tier)

    if not _is_finite_number(tier_risk):
        return 0.0

    if not _is_finite_number(MAX_RISK_PER_TRADE):
        return 0.0

    if MAX_RISK_PER_TRADE <= 0 or tier_risk < 0:
        return 0.0

    return min(float(tier_risk), float(MAX_RISK_PER_TRADE))


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
    Calcula unidades únicamente si todos los datos son válidos.

    No presupone que USD y EUR valgan lo mismo.
    No autoriza operaciones reales.
    """

    if capital_eur is None:
        capital_eur = INITIAL_CAPITAL_EUR

    values = (
        entry,
        stop,
        capital_eur,
        quote_to_eur,
        portfolio_risk_eur,
        costs_per_unit_quote,
        MAX_RISK_PER_TRADE,
        MAX_PORTFOLIO_RISK,
    )

    if any(not _is_finite_number(value) for value in values):
        return None

    if not _is_finite_number(score):
        return None

    entry = float(entry)
    stop = float(stop)
    capital_eur = float(capital_eur)
    quote_to_eur = float(quote_to_eur)
    portfolio_risk_eur = float(portfolio_risk_eur)
    costs_per_unit_quote = float(costs_per_unit_quote)

    if entry <= 0 or stop <= 0 or stop >= entry:
        return None

    if capital_eur <= 0:
        return None

    if quote_to_eur <= 0:
        return None

    if portfolio_risk_eur < 0:
        return None

    if costs_per_unit_quote < 0:
        return None

    if MAX_RISK_PER_TRADE <= 0 or MAX_PORTFOLIO_RISK <= 0:
        return None

    risk_pct = risk_pct_for_score(score)

    if risk_pct <= 0:
        return None

    risk_pct = min(
        risk_pct,
        float(MAX_RISK_PER_TRADE)
    )

    risk_budget_eur = capital_eur * risk_pct

    portfolio_limit_eur = (
        capital_eur * float(MAX_PORTFOLIO_RISK)
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
        price_risk_quote + costs_per_unit_quote
    )

    total_risk_per_unit_eur = (
        total_risk_per_unit_quote * quote_to_eur
    )

    if (
        not math.isfinite(total_risk_per_unit_eur)
        or total_risk_per_unit_eur <= 0
    ):
        return None

    units = allowed_risk_eur / total_risk_per_unit_eur

    actual_risk_eur = (
        units * total_risk_per_unit_eur
    )

    portfolio_risk_after_eur = (
        portfolio_risk_eur + actual_risk_eur
    )

    if not all(math.isfinite(value) for value in (
        units,
        actual_risk_eur,
        portfolio_risk_after_eur,
    )):
        return None

    return {
        "units": units,
        "risk_pct": risk_pct,
        "risk_budget_eur": allowed_risk_eur,
        "actual_risk_eur": actual_risk_eur,
        "portfolio_risk_after_eur": portfolio_risk_after_eur,
        "quote_to_eur": quote_to_eur,
        "validated": False,
        "status": "SIMULACION_NO_VALIDADA",
    }
