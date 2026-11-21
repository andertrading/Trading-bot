"""Conservative gross stop-risk diagnostics for supported eToro spot positions.

Only USD-quoted, unleveraged REAL positions with fixed stops are priced.
This deliberately does not return an admission-ready risk_usd: eToro's
documented what-if API does not currently support close-cost estimates.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from etoro_read_only import EtoroAPIError, EtoroQuote, EtoroResponse


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise EtoroAPIError(f"Campo numérico inválido: {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise EtoroAPIError(f"Campo numérico inválido: {field}") from None
    if not result.is_finite():
        raise EtoroAPIError(f"Campo numérico no finito: {field}")
    return result


def _utc_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise EtoroAPIError(f"Fecha ausente: {field}")
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        raise EtoroAPIError(f"Fecha inválida: {field}") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EtoroAPIError(f"La fecha {field} no está en UTC")
    return parsed


def normalize_instrument_breakdown(
    response: EtoroResponse,
    expected_client_id: int,
    expected_position_ids: list[int],
    now_utc: datetime,
    max_age_seconds: int = 60,
) -> dict[int, Mapping[str, Any]]:
    """Validate a complete, fresh instrument-breakdown snapshot.

    The API's instrument-breakdown endpoint requires the account CID in a
    request header. Position IDs must match the account PnL snapshot exactly.
    """
    if response.status_code != 200 or not isinstance(response.data, Mapping):
        raise EtoroAPIError("El desglose de posiciones no es una respuesta HTTP 200 válida")
    if isinstance(expected_client_id, bool) or not isinstance(expected_client_id, int) or expected_client_id <= 0:
        raise EtoroAPIError("CID esperado inválido")
    if (
        not isinstance(expected_position_ids, list)
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in expected_position_ids)
        or len(set(expected_position_ids)) != len(expected_position_ids)
    ):
        raise EtoroAPIError("La lista esperada de positionId no es válida")
    if (
        not isinstance(now_utc, datetime)
        or now_utc.tzinfo is None
        or now_utc.utcoffset() != timedelta(0)
        or isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, int)
        or max_age_seconds <= 0
    ):
        raise EtoroAPIError("Parámetros temporales inválidos para el desglose")

    data = response.data
    if data.get("accountCurrency") != "USD":
        raise EtoroAPIError("El desglose no confirma la divisa de cuenta USD")
    snapshot_time = _utc_datetime(data.get("timestamp"), "instrument-breakdown.timestamp")
    snapshot_age = (now_utc - snapshot_time).total_seconds()
    if snapshot_age < -5 or snapshot_age > max_age_seconds:
        raise EtoroAPIError("El desglose de posiciones está obsoleto o tiene fecha futura")

    instruments = data.get("instruments")
    if not isinstance(instruments, list):
        raise EtoroAPIError("Falta la lista de instrumentos del desglose")

    expected = set(expected_position_ids)
    normalized: dict[int, Mapping[str, Any]] = {}
    for instrument_index, instrument in enumerate(instruments):
        if not isinstance(instrument, Mapping):
            raise EtoroAPIError(f"Instrumento inválido en desglose: {instrument_index}")
        instrument_id = instrument.get("instrumentId")
        if isinstance(instrument_id, bool) or not isinstance(instrument_id, int) or instrument_id <= 0:
            raise EtoroAPIError("instrumentId inválido en desglose")
        positions = instrument.get("positions")
        if not isinstance(positions, list):
            raise EtoroAPIError(f"Falta positions para instrumento {instrument_id}")
        for position in positions:
            if not isinstance(position, Mapping):
                raise EtoroAPIError("Posición inválida en desglose")
            position_id = position.get("positionId")
            if isinstance(position_id, bool) or not isinstance(position_id, int) or position_id <= 0:
                raise EtoroAPIError("positionId inválido en desglose")
            if position_id not in expected or position_id in normalized:
                raise EtoroAPIError("El desglose contiene posiciones inesperadas o duplicadas")
            if position.get("instrumentId") != instrument_id:
                raise EtoroAPIError("instrumentId inconsistente dentro del desglose")

            required = (
                "assetCurrency", "settlementType", "direction", "leverage",
                "stopLossRate", "stopLossType", "units", "currentRate",
                "currentConversionRate", "currentExposureAcctCcy", "rateTimestamp",
            )
            missing = [field for field in required if field not in position]
            if missing:
                raise EtoroAPIError(
                    f"Posición {position_id} incompleta en desglose: {', '.join(missing)}"
                )
            rate_time = _utc_datetime(position["rateTimestamp"], f"position[{position_id}].rateTimestamp")
            rate_age = (now_utc - rate_time).total_seconds()
            if rate_age < -5 or rate_age > max_age_seconds:
                raise EtoroAPIError(f"La cotización del desglose está obsoleta para {position_id}")

            normalized[position_id] = dict(position)

    if set(normalized) != expected:
        missing = sorted(expected.difference(normalized))
        raise EtoroAPIError(f"El desglose no contiene todas las posiciones esperadas: {missing}")
    return normalized


def calculate_position_gross_stop_risk_usd(
    position: Mapping[str, Any],
    quote: EtoroQuote,
) -> dict[str, Any]:
    """Calculate gross mark-to-stop loss for a strictly supported spot position.

    This is a diagnostic, not a complete monetary-risk value. It excludes
    closing fees/taxes, future funding and gap/slippage beyond the stop.
    """
    if not isinstance(position, Mapping) or not isinstance(quote, EtoroQuote):
        raise EtoroAPIError("Faltan datos válidos de posición o cotización")

    position_id = position.get("positionId")
    instrument_id = position.get("instrumentId")
    if (
        isinstance(position_id, bool) or not isinstance(position_id, int) or position_id <= 0
        or isinstance(instrument_id, bool) or not isinstance(instrument_id, int) or instrument_id <= 0
        or quote.instrument_id != instrument_id
        or quote.quote_type != "realtime"
    ):
        raise EtoroAPIError("La posición y la cotización no coinciden")

    if position.get("settlementType") != "real":
        raise EtoroAPIError("Solo se puede valorar settlementType=real; CFD/futuros/margin quedan bloqueados")
    if position.get("assetCurrency") != "USD":
        raise EtoroAPIError("La divisa del activo no es USD; falta conversión de divisa al stop")
    if _decimal(position.get("leverage"), "leverage") != Decimal("1"):
        raise EtoroAPIError("La posición está apalancada; cálculo spot no aplicable")
    if position.get("stopLossType") != "fixed":
        raise EtoroAPIError("El stop no es fijo; se requiere validar la semántica del trailing stop")

    rate_time = _utc_datetime(position.get("rateTimestamp"), f"position[{position_id}].rateTimestamp")
    if (
        not isinstance(quote.timestamp, datetime)
        or quote.timestamp.tzinfo is None
        or quote.timestamp.utcoffset() != timedelta(0)
        or abs((quote.timestamp - rate_time).total_seconds()) > 30
        or quote.bid <= 0
        or quote.ask <= 0
        or quote.ask < quote.bid
    ):
        raise EtoroAPIError("La cotización ejecutable no es válida o no está sincronizada con la posición")

    direction = position.get("direction")
    if direction not in {"long", "short"}:
        raise EtoroAPIError("Dirección de posición no reconocida")

    units = _decimal(position.get("units"), "units")
    current_rate = _decimal(position.get("currentRate"), "currentRate")
    stop_rate = _decimal(position.get("stopLossRate"), "stopLossRate")
    conversion = _decimal(position.get("currentConversionRate"), "currentConversionRate")
    exposure = _decimal(position.get("currentExposureAcctCcy"), "currentExposureAcctCcy")
    if min(units, current_rate, stop_rate, conversion, exposure) <= 0:
        raise EtoroAPIError("Unidades, precios, conversión o exposición no positivos")
    if conversion != Decimal("1"):
        raise EtoroAPIError("La conversión actual no es 1; no se puede confirmar riesgo USD")
    implied_exposure = units * current_rate
    tolerance = max(Decimal("0.02"), exposure * Decimal("0.001"))
    if abs(implied_exposure - exposure) > tolerance:
        raise EtoroAPIError("Las unidades no reproducen la exposición declarada por eToro")

    if direction == "long":
        executable_exit = quote.bid
        if stop_rate >= executable_exit:
            raise EtoroAPIError("El stop long está alcanzado o por encima del bid ejecutable")
        gross_risk = (executable_exit - stop_rate) * units
    else:
        executable_exit = quote.ask
        if stop_rate <= executable_exit:
            raise EtoroAPIError("El stop short está alcanzado o por debajo del ask ejecutable")
        gross_risk = (stop_rate - executable_exit) * units

    if gross_risk < 0 or not gross_risk.is_finite():
        raise EtoroAPIError("El riesgo bruto al stop calculado no es válido")

    return {
        "position_id": position_id,
        "instrument_id": instrument_id,
        "direction": direction,
        "settlement_type": "real",
        "gross_stop_risk_usd": gross_risk,
        "risk_usd": None,
        "status": "GROSS_ONLY_UNVALIDATED",
        "complete": False,
        "reason": (
            "Faltan costes de cierre confirmados; el cálculo tampoco cubre "
            "gaps ni deslizamiento más allá del stop."
        ),
        "currency": "USD",
    }


def calculate_account_gross_stop_risk_usd(
    positions: Mapping[int, Mapping[str, Any]],
    quotes: Mapping[int, EtoroQuote],
) -> dict[str, Any]:
    """Aggregate gross stop risk only when every position has a valid quote."""
    if not isinstance(positions, Mapping) or not isinstance(quotes, Mapping):
        raise EtoroAPIError("Posiciones y cotizaciones deben ser mapas")
    if not positions:
        return {
            "positions": [],
            "gross_stop_risk_usd": Decimal("0"),
            "risk_usd": Decimal("0"),
            "status": "COMPLETE_NO_OPEN_POSITIONS",
            "complete": True,
            "reason": "No hay posiciones abiertas que valorar.",
            "currency": "USD",
        }

    results = []
    for position_id, position in positions.items():
        if not isinstance(position, Mapping) or position.get("positionId") != position_id:
            raise EtoroAPIError(f"El identificador de posición no coincide: {position_id}")
        instrument_id = position.get("instrumentId")
        quote = quotes.get(instrument_id)
        if quote is None:
            raise EtoroAPIError(f"Falta cotización para la posición {position_id}")
        results.append(calculate_position_gross_stop_risk_usd(position, quote))

    total = sum((item["gross_stop_risk_usd"] for item in results), Decimal("0"))
    return {
        "positions": results,
        "gross_stop_risk_usd": total,
        "risk_usd": None,
        "status": "GROSS_ONLY_UNVALIDATED",
        "complete": False,
        "reason": (
            "No se puede reservar riesgo total: eToro documenta que el cálculo "
            "hipotético de costes de cierre aún no está soportado."
        ),
        "currency": "USD",
    }
