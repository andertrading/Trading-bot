
import math

from config import (
    INITIAL_CAPITAL_EUR,
    MAX_PORTFOLIO_RISK,
)


def _is_finite_number(value):
    """Comprueba que un valor sea numérico y finito."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _blocked_status(reason):
    return {
        "status": "BLOCKED",
        "reason": reason,
        "risk_eur": None,
        "remaining_risk_eur": None,
        "can_open_trade": False,
    }


def get_portfolio_status(
    open_positions=None,
    positions_confirmed=False,
    capital_eur=None,
):
    """
    Calcula el riesgo agregado de las posiciones abiertas.

    Si las posiciones manuales no están confirmadas,
    no permite abrir nuevas operaciones.
    """

    if capital_eur is None:
        capital_eur = INITIAL_CAPITAL_EUR

    if not _is_finite_number(capital_eur):
        return _blocked_status("Capital no válido")

    capital_eur = float(capital_eur)

    if capital_eur <= 0:
        return _blocked_status("Capital no válido")

    if not _is_finite_number(MAX_PORTFOLIO_RISK):
        return _blocked_status("Límite de riesgo de cartera no válido")

    maximum_risk_pct = float(MAX_PORTFOLIO_RISK)

    if maximum_risk_pct <= 0:
        return _blocked_status("Límite de riesgo de cartera no válido")

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

    if not isinstance(open_positions, (list, tuple)):
        return _blocked_status("Formato de posiciones incorrecto")

    total_risk_eur = 0.0

    for position in open_positions:

        if not isinstance(position, dict):
            return _blocked_status("Formato de posición incorrecto")

        risk = position.get("risk_eur")

        if not _is_finite_number(risk):
            return _blocked_status("Riesgo de posición no válido")

        risk = float(risk)

        if risk < 0:
            return _blocked_status("Riesgo negativo no válido")

        total_risk_eur += risk

        if not math.isfinite(total_risk_eur):
            return _blocked_status("Riesgo agregado no válido")

    maximum_risk_eur = capital_eur * maximum_risk_pct

    if not math.isfinite(maximum_risk_eur):
        return _blocked_status("Límite de riesgo calculado no válido")

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

    if portfolio_status.get("can_open_trade") is not True:
        return False

    remaining = portfolio_status.get("remaining_risk_eur")

    if not _is_finite_number(remaining):
        return False

    if not _is_finite_number(proposed_risk_eur):
        return False

    remaining = float(remaining)
    proposed_risk_eur = float(proposed_risk_eur)

    if remaining <= 0 or proposed_risk_eur <= 0:
        return False

    return proposed_risk_eur <= remaining
