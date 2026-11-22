
import math

from config import (
    INITIAL_CAPITAL_USD,
    MAX_PORTFOLIO_RISK,
    MAX_RISK_PER_TRADE,
)

HARD_MAX_PORTFOLIO_RISK = 0.03
HARD_MAX_RISK_PER_TRADE = 0.01


def _is_finite_number(value):
    if isinstance(value, bool):
        return False

    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _blocked_status(reason):
    return {
        "status": "BLOCKED",
        "reason": reason,
        "currency": "USD",
    }


def _portfolio_limit():
    if not _is_finite_number(MAX_PORTFOLIO_RISK):
        return None

    configured_limit = float(MAX_PORTFOLIO_RISK)

    if configured_limit <= 0:
        return None

    return min(configured_limit, HARD_MAX_PORTFOLIO_RISK)


def get_portfolio_status(
    open_positions=None,
    positions_confirmed=False,
    capital_usd=None,
):
    if capital_usd is None:
        capital_usd = INITIAL_CAPITAL_USD

    if not _is_finite_number(capital_usd):
        return _blocked_status("El capital USD no es válido.")

    capital_usd = float(capital_usd)

    if capital_usd <= 0:
        return _blocked_status("El capital USD debe ser positivo.")

    if not isinstance(positions_confirmed, bool):
        return _blocked_status(
            "La confirmación de posiciones no es válida."
        )

    portfolio_limit = _portfolio_limit()

    if portfolio_limit is None:
        return _blocked_status(
            "El límite de riesgo de cartera no es válido."
        )

    max_risk_usd = capital_usd * portfolio_limit

    if not math.isfinite(max_risk_usd) or max_risk_usd <= 0:
        return _blocked_status(
            "El límite monetario de riesgo no es válido."
        )

    if not positions_confirmed:
        return {
            "status": "UNKNOWN",
            "reason": (
                "Las posiciones abiertas no están confirmadas."
            ),
            "capital_usd": capital_usd,
            "max_portfolio_risk_usd": max_risk_usd,
            "current_portfolio_risk_usd": None,
            "remaining_risk_usd": None,
            "currency": "USD",
        }

    if not isinstance(open_positions, (list, tuple)):
        return _blocked_status(
            "Las posiciones deben facilitarse explícitamente."
        )

    current_risk_usd = 0.0

    for index, position in enumerate(open_positions):
        if not isinstance(position, dict):
            return _blocked_status(
                f"La posición {index + 1} no tiene un formato válido."
            )

        risk_usd = position.get("risk_usd")

        if not _is_finite_number(risk_usd):
            return _blocked_status(
                f"El riesgo USD de la posición {index + 1} no es válido."
            )

        risk_usd = float(risk_usd)

        if risk_usd < 0:
            return _blocked_status(
                f"El riesgo USD de la posición {index + 1} es negativo."
            )

        current_risk_usd += risk_usd

        if not math.isfinite(current_risk_usd):
            return _blocked_status(
                "La suma del riesgo de cartera no es válida."
            )

    remaining_risk_usd = max(
        0.0,
        max_risk_usd - current_risk_usd,
    )

    risk_usage_pct = current_risk_usd / max_risk_usd

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

    if not _is_finite_number(capital_usd if capital_usd is not None else INITIAL_CAPITAL_USD):
        return {
            "allowed": False,
            "reason": "El capital USD no es válido para comprobar el límite por operación.",
            "currency": "USD",
        }

    effective_capital = float(
        INITIAL_CAPITAL_USD if capital_usd is None else capital_usd
    )
    if effective_capital <= 0:
        return {
            "allowed": False,
            "reason": "El capital USD debe ser positivo.",
            "currency": "USD",
        }

    if not _is_finite_number(MAX_RISK_PER_TRADE) or float(MAX_RISK_PER_TRADE) <= 0:
        return {
            "allowed": False,
            "reason": "El límite configurado de riesgo por operación no es válido.",
            "currency": "USD",
        }

    per_trade_limit_pct = min(
        float(MAX_RISK_PER_TRADE),
        HARD_MAX_RISK_PER_TRADE,
    )
    max_single_trade_risk_usd = effective_capital * per_trade_limit_pct
    if proposed_risk_usd > max_single_trade_risk_usd + 1e-10:
        return {
            "allowed": False,
            "reason": (
                "La operación supera el límite absoluto del 1 % "
                "de riesgo por operación."
            ),
            "proposed_risk_usd": proposed_risk_usd,
            "max_single_trade_risk_usd": max_single_trade_risk_usd,
            "currency": "USD",
        }

    portfolio = get_portfolio_status(
        open_positions=open_positions,
        positions_confirmed=positions_confirmed,
        capital_usd=capital_usd,
    )

    if portfolio["status"] != "OK":
        return {
            "allowed": False,
            "reason": (
                "No se puede autorizar una operación: "
                + portfolio["reason"]
            ),
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

def can_add_sized_trade(
    sizing_result,
    open_positions=None,
    positions_confirmed=False,
    capital_usd=None,
):
    """Apply portfolio admission to the complete risk returned by sizing.

    The sizing result is only a theoretical calculation. This function is a
    second, independent gate and never authorizes live execution.
    """
    if not isinstance(sizing_result, dict):
        return {
            "allowed": False,
            "reason": "Falta un resultado válido del cálculo de posición.",
            "currency": "USD",
        }

    if sizing_result.get("status") != "SIMULACION_NO_VALIDADA":
        return {
            "allowed": False,
            "reason": "El cálculo de posición está bloqueado o no validado.",
            "currency": "USD",
        }

    if sizing_result.get("validated") is not False:
        return {
            "allowed": False,
            "reason": "El cálculo no tiene el estado de simulación esperado.",
            "currency": "USD",
        }

    total_reserved_risk_usd = sizing_result.get("total_reserved_risk_usd")
    if not _is_finite_number(total_reserved_risk_usd):
        return {
            "allowed": False,
            "reason": "Falta el riesgo total reservado de la nueva posición.",
            "currency": "USD",
        }

    total_reserved_risk_usd = float(total_reserved_risk_usd)
    if total_reserved_risk_usd <= 0:
        return {
            "allowed": False,
            "reason": "El riesgo total reservado debe ser positivo.",
            "currency": "USD",
        }

    reported_risk = sizing_result.get("risk_amount_usd")
    position_risk = sizing_result.get("position_risk_usd")
    fixed_costs = sizing_result.get("fixed_costs_usd")
    if any(
        not _is_finite_number(value)
        for value in (reported_risk, position_risk, fixed_costs)
    ):
        return {
            "allowed": False,
            "reason": "Faltan componentes válidos del riesgo reservado.",
            "currency": "USD",
        }

    reported_risk = float(reported_risk)
    position_risk = float(position_risk)
    fixed_costs = float(fixed_costs)
    if (
        reported_risk <= 0
        or position_risk < 0
        or fixed_costs < 0
        or total_reserved_risk_usd > reported_risk + 1e-8
        or abs(total_reserved_risk_usd - (position_risk + fixed_costs)) > 1e-8
    ):
        return {
            "allowed": False,
            "reason": "El riesgo total reservado no cuadra con sus componentes.",
            "currency": "USD",
        }

    admission = can_add_trade(
        proposed_risk_usd=total_reserved_risk_usd,
        open_positions=open_positions,
        positions_confirmed=positions_confirmed,
        capital_usd=capital_usd,
    )
    return {
        **admission,
        "proposed_total_reserved_risk_usd": total_reserved_risk_usd,
        "admission_scope": "SIMULACION_NO_VALIDADA",
        "live_execution_authorized": False,
    }

