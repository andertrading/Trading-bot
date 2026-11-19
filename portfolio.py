
import math

from config import (
    INITIAL_CAPITAL_USD,
    MAX_PORTFOLIO_RISK,
)


def _is_finite_number(value):
    """Comprueba que un valor sea numérico y finito."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def get_portfolio_status(
    open_positions=None,
    positions_confirmed=False,
    capital_usd=None,
):
    """
    Calcula el riesgo agregado de la cartera en USD.

    Cada posición debe incluir:
        risk_usd: riesgo monetario estimado de la posición en USD.

    No se presupone que las posiciones estén sincronizadas
    automáticamente con eToro.
    """

    if capital_usd is None:
        capital_usd = INITIAL_CAPITAL_USD

    if not _is_finite_number(capital_usd):
        return {
            "status": "BLOCKED",
            "reason": "El capital USD no es válido.",
            "currency": "USD",
        }

    capital_usd = float(capital_usd)

    if capital_usd <= 0:
        return {
            "status": "BLOCKED",
            "reason": "El capital USD debe ser positivo.",
            "currency": "USD",
        }

    max_risk_usd = capital_usd * MAX_PORTFOLIO_RISK

    if not positions_confirmed:
        return {
            "status": "UNKNOWN",
            "reason": (
                "Las posiciones abiertas no están confirmadas. "
                "No se puede verificar el riesgo real de la cartera."
            ),
            "capital_usd": capital_usd,
            "max_portfolio_risk_usd": max_risk_usd,
            "current_portfolio_risk_usd": None,
            "remaining_risk_usd": None,
            "currency": "USD",
        }

    if open_positions is None:
        open_positions = []

    if not isinstance(open_positions, (list, tuple)):
        return {
            "status": "BLOCKED",
            "reason": "El formato de las posiciones no es válido.",
            "currency": "USD",
        }

    current_risk_usd = 0.0

    for index, position in enumerate(open_positions):
        if not isinstance(position, dict):
            return {
                "status": "BLOCKED",
                "reason": (
                    f"La posición {index + 1} no tiene un formato válido."
                ),
                "currency": "USD",
            }

        risk_usd = position.get("risk_usd")

        if not _is_finite_number(risk_usd):
            return {
                "status": "BLOCKED",
                "reason": (
                    f"El riesgo USD de la posición {index + 1} "
                    "no es válido."
                ),
                "currency": "USD",
            }

        risk_usd = float(risk_usd)

        if risk_usd < 0:
            return {
                "status": "BLOCKED",
                "reason": (
                    f"El riesgo USD de la posición {index + 1} "
                    "no puede ser negativo."
                ),
                "currency": "USD",
            }

        current_risk_usd += risk_usd

    remaining_risk_usd = max(
        0.0,
        max_risk_usd - current_risk_usd,
    )

    risk_usage_pct = (
        current_risk_usd / max_risk_usd
        if max_risk_usd > 0
        else 0.0
    )

    status = (
        "BLOCKED"
        if current_risk_usd >= max_risk_usd
        else "OK"
    )

    return {
        "status": status,
        "reason": (
            "Límite de riesgo de cartera alcanzado."
            if status == "BLOCKED"
            else "Riesgo de cartera dentro del límite."
        ),
        "capital_usd": capital_usd,
        "max_portfolio_risk_usd": max_risk_usd,
        "current_portfolio_risk_usd": current_risk_usd,
        "remaining_risk_usd": remaining_risk_usd,
        "risk_usage_pct": risk_usage_pct,
        "currency": "USD",
    }


def can_add_trade(
    proposed_risk_usd,
    open_positions=None,
    positions_confirmed=False,
    capital_usd=None,
):
    """Comprueba si una nueva operación cabe en el límite de cartera."""

    if not _is_finite_number(proposed_risk_usd):
        return {
            "allowed": False,
            "reason": "El riesgo propuesto en USD no es válido.",
            "currency": "USD",
        }

    proposed_risk_usd = float(proposed_risk_usd)

    if proposed_risk_usd <= 0:
        return {
            "allowed": False,
            "reason": "El riesgo propuesto debe ser positivo.",
            "currency": "USD",
        }

    portfolio = get_portfolio_status(
        open_positions=open_positions,
        positions_confirmed=positions_confirmed,
        capital_usd=capital_usd,
    )

    if portfolio["status"] == "UNKNOWN":
        return {
            "allowed": False,
            "reason": (
                "No se puede autorizar una operación sin confirmar "
                "las posiciones abiertas."
            ),
            "portfolio": portfolio,
            "currency": "USD",
        }

    if portfolio["status"] == "BLOCKED":
        return {
            "allowed": False,
            "reason": portfolio["reason"],
            "portfolio": portfolio,
            "currency": "USD",
        }

    allowed = (
        proposed_risk_usd <= portfolio["remaining_risk_usd"]
    )

    return {
        "allowed": allowed,
        "reason": (
            "Riesgo dentro del límite disponible."
            if allowed
            else "La operación superaría el límite de riesgo de cartera."
        ),
        "proposed_risk_usd": proposed_risk_usd,
        "remaining_risk_usd": portfolio["remaining_risk_usd"],
        "portfolio": portfolio,
        "currency": "USD",
    }
