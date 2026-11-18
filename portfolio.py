
from config import (
    INITIAL_CAPITAL_EUR,
    MAX_PORTFOLIO_RISK,
)


def get_portfolio_status(
    open_positions=None,
    positions_confirmed=False,
    capital_eur=None,
):
    """
    Calcula el riesgo agregado de las posiciones abiertas.

    El usuario debe confirmar las posiciones manuales de eToro.
    Si no se conocen, el estado queda bloqueado.
    """

    if capital_eur is None:
        capital_eur = INITIAL_CAPITAL_EUR

    if capital_eur <= 0:
        return {
            "status": "BLOCKED",
            "reason": "Capital no válido",
            "risk_eur": None,
        }

    if not positions_confirmed:
        return {
            "status": "UNKNOWN",
            "reason": "No se han confirmado las posiciones abiertas",
            "risk_eur": None,
            "remaining_risk_eur": None,
            "can_open_trade": False,
        }

    if open_positions is None:
        open_positions = []

    total_risk_eur = 0.0

    for position in open_positions:
        if not isinstance(position, dict):
            return {
                "status": "BLOCKED",
                "reason": "Formato de posición incorrecto",
                "risk_eur": None,
                "can_open_trade": False,
            }

        risk = position.get("risk_eur")

        if risk is None:
            return {
                "status": "BLOCKED",
                "reason": "Falta el riesgo de una posición",
                "risk_eur": None,
                "can_open_trade": False,
            }

        try:
            risk = float(risk)
        except (TypeError, ValueError):
            return {
                "status": "BLOCKED",
                "reason": "Riesgo de posición no válido",
                "risk_eur": None,
                "can_open_trade": False,
            }

        if risk < 0:
            return {
                "status": "BLOCKED",
                "reason": "Riesgo negativo no válido",
                "risk_eur": None,
                "can_open_trade": False,
            }

        total_risk_eur += risk

    maximum_risk_eur = (
        capital_eur * MAX_PORTFOLIO_RISK
    )

    remaining_risk_eur = max(
        0.0,
        maximum_risk_eur - total_risk_eur
    )

    return {
        "status": "CONFIRMED",
        "risk_eur": round(total_risk_eur, 2),
        "maximum_risk_eur": round(maximum_risk_eur, 2),
        "remaining_risk_eur": round(remaining_risk_eur, 2),
        "can_open_trade": remaining_risk_eur > 0,
    }


def can_add_trade(
    portfolio_status,
    proposed_risk_eur,
):
    """Comprueba si una operación cabe en el riesgo disponible."""

    if not isinstance(portfolio_status, dict):
        return False

    if portfolio_status.get("status") != "CONFIRMED":
        return False

    if not portfolio_status.get("can_open_trade", False):
        return False

    remaining = portfolio_status.get("remaining_risk_eur")

    if remaining is None or proposed_risk_eur is None:
        return False

    try:
        proposed_risk_eur = float(proposed_risk_eur)
    except (TypeError, ValueError):
        return False

    if proposed_risk_eur <= 0:
        return False

    return proposed_risk_eur <= remaining
