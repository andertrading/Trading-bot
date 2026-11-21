"""Strict read-only eToro API client.

This module intentionally exposes only account/market-data reads and the
documented what-if cost-estimation POST. It contains no order execution,
modification, cancellation, transfer, or other trading-write methods.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
import re
import time
from typing import Any, Mapping
from uuid import uuid4

import requests


API_BASE_URL = "https://api.etoro.com"
DEFAULT_TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class EtoroAPIError(RuntimeError):
    """Raised when an eToro API response is unusable or unsuccessful."""


class EtoroConfigurationError(RuntimeError):
    """Raised when read-only API credentials are unavailable."""


@dataclass(frozen=True)
class EtoroResponse:
    data: Any
    status_code: int
    headers: Mapping[str, str]


def _decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise EtoroAPIError(f"Campo numérico inválido: {field_name}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise EtoroAPIError(f"Campo numérico inválido: {field_name}") from None
    if not result.is_finite():
        raise EtoroAPIError(f"Campo numérico no finito: {field_name}")
    return result


def _one_key(payload: Mapping[str, Any], names: tuple[str, ...], label: str) -> Any:
    present = [name for name in names if name in payload]
    if len(present) != 1:
        raise EtoroAPIError(
            f"Esquema ambiguo o incompleto en {label}: se esperaba exactamente "
            f"uno de {', '.join(names)}"
        )
    return payload[present[0]]


def calculate_available_cash(account_payload: Mapping[str, Any]) -> Decimal:
    """Calculate eToro available cash; reject missing or ambiguous schema fields.

    Formula documented by eToro:
      credit - manual pending-entry amounts - all amounts in orders.
    Mirrored pending entries (mirror ID != 0) are excluded from this cash
    deduction. Closing-order arrays are deliberately not treated as entries.
    """
    if not isinstance(account_payload, Mapping):
        raise EtoroAPIError("La respuesta de cuenta no es un objeto JSON")

    credit = _decimal(
        _one_key(account_payload, ("credit", "credits"), "credit"),
        "credit",
    )
    pending = _one_key(
        account_payload, ("ordersForOpen",), "ordersForOpen"
    )
    orders = _one_key(account_payload, ("orders",), "orders")

    if not isinstance(pending, list) or not isinstance(orders, list):
        raise EtoroAPIError("Las listas de órdenes de la cuenta están incompletas")

    deductions = Decimal("0")
    for index, order in enumerate(pending):
        if not isinstance(order, Mapping):
            raise EtoroAPIError(f"Orden pendiente inválida: {index}")
        mirror_id = _one_key(order, ("mirrorId", "mirrorID"), f"mirrorId[{index}]")
        try:
            is_manual = int(mirror_id) == 0
        except (TypeError, ValueError):
            raise EtoroAPIError(f"mirrorId inválido en orden {index}") from None
        if is_manual:
            amount = _decimal(order.get("amount"), f"ordersForOpen[{index}].amount")
            if amount < 0:
                raise EtoroAPIError(f"Importe negativo en orden pendiente {index}")
            deductions += amount

    for index, order in enumerate(orders):
        if not isinstance(order, Mapping):
            raise EtoroAPIError(f"Orden activa inválida: {index}")
        amount = _decimal(order.get("amount"), f"orders[{index}].amount")
        if amount < 0:
            raise EtoroAPIError(f"Importe negativo en orden activa {index}")
        deductions += amount

    available = credit - deductions
    if available < 0:
        raise EtoroAPIError("El efectivo disponible calculado es negativo")
    return available


@dataclass(frozen=True)
class EtoroAccountSnapshot:
    """Validated account facts; position risk is intentionally not inferred."""

    account_currency: str
    available_cash: Decimal
    unrealized_pnl: Decimal
    positions: tuple[Mapping[str, Any], ...]
    has_unbounded_stop_risk: bool


def normalize_account_snapshot(payload: Mapping[str, Any]) -> EtoroAccountSnapshot:
    """Validate the minimum real-account schema required for a trusted snapshot.

    This confirms USD account context and preserves positions, but does not
    calculate position risk, infer multipliers, or treat missing stops as zero.
    """
    if not isinstance(payload, Mapping):
        raise EtoroAPIError("La respuesta de cuenta no es un objeto JSON")

    currency_id = payload.get("accountCurrencyId")
    if isinstance(currency_id, bool) or not isinstance(currency_id, int) or currency_id != 1:
        raise EtoroAPIError("La divisa de cuenta no está confirmada como USD (ID 1)")

    portfolio = payload.get("clientPortfolio")
    if not isinstance(portfolio, Mapping):
        raise EtoroAPIError("Falta clientPortfolio en la respuesta de eToro")
    positions = portfolio.get("positions")
    if not isinstance(positions, list):
        raise EtoroAPIError("Falta la lista explícita de posiciones")

    unrealized_pnl = _decimal(payload.get("unrealizedPnL"), "unrealizedPnL")
    available_cash = calculate_available_cash(payload)

    required_fields = {
        "positionID",
        "instrumentID",
        "isBuy",
        "openRate",
        "stopLossRate",
        "amount",
        "leverage",
        "units",
        "settlementTypeID",
        "isNoStopLoss",
    }
    normalized_positions: list[Mapping[str, Any]] = []
    unbounded_stop_risk = False

    for index, position in enumerate(positions):
        if not isinstance(position, Mapping):
            raise EtoroAPIError(f"Posición inválida: {index}")
        missing = sorted(required_fields.difference(position))
        if missing:
            raise EtoroAPIError(
                f"Posición {index} incompleta: faltan {', '.join(missing)}"
            )

        for id_field in ("positionID", "instrumentID"):
            value = position[id_field]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise EtoroAPIError(f"{id_field} inválido en posición {index}")

        if not isinstance(position["isBuy"], bool):
            raise EtoroAPIError(f"isBuy inválido en posición {index}")
        if not isinstance(position["isNoStopLoss"], bool):
            raise EtoroAPIError(f"isNoStopLoss inválido en posición {index}")

        settlement_type = position["settlementTypeID"]
        if (
            isinstance(settlement_type, bool)
            or not isinstance(settlement_type, int)
            or settlement_type not in {0, 1, 2, 3, 4}
        ):
            raise EtoroAPIError(f"Tipo de liquidación no reconocido en posición {index}")

        for numeric_field in ("openRate", "amount", "leverage", "units"):
            number = _decimal(position[numeric_field], f"positions[{index}].{numeric_field}")
            if number <= 0:
                raise EtoroAPIError(
                    f"{numeric_field} debe ser positivo en posición {index}"
                )

        stop_value = position["stopLossRate"]
        if position["isNoStopLoss"]:
            unbounded_stop_risk = True
        else:
            stop = _decimal(stop_value, f"positions[{index}].stopLossRate")
            if stop <= 0:
                raise EtoroAPIError(f"Stop loss inválido en posición {index}")

        normalized_positions.append(dict(position))

    return EtoroAccountSnapshot(
        account_currency="USD",
        available_cash=available_cash,
        unrealized_pnl=unrealized_pnl,
        positions=tuple(normalized_positions),
        has_unbounded_stop_risk=unbounded_stop_risk,
    )


class EtoroReadOnlyClient:
    """Small eToro client with a deliberately restricted endpoint surface."""

    def __init__(
        self,
        app_key: str | None = None,
        user_key: str | None = None,
        *,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.app_key = app_key or os.getenv("ETORO_API_KEY")
        self.user_key = user_key or os.getenv("ETORO_USER_KEY")
        if not self.app_key or not self.user_key:
            raise EtoroConfigurationError(
                "Faltan ETORO_API_KEY y/o ETORO_USER_KEY; no se realiza ninguna petición."
            )
        if timeout <= 0:
            raise ValueError("timeout debe ser positivo")
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts debe estar entre 1 y 3")

        self._session = session or requests.Session()
        self._timeout = timeout
        self._max_attempts = max_attempts

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        allow_partial: bool = False,
    ) -> EtoroResponse:
        # Internal allowlist is intentionally narrow; callers cannot supply URLs.
        allowed = {
            ("GET", "/api/v1/trading/info/real/pnl"),
            ("GET", "/api/v2/market-data/rates"),
            ("POST", "/api/v2/trading/info/costs"),
        }
        candle_pattern = (
            r"^/api/v1/market-data/instruments/[1-9][0-9]*/history/"
            r"candles/(asc|desc)/(1m|5m|10m|15m|30m|1h|4h|1d|1w)/"
            r"([1-9][0-9]{0,2}|1000)$"
        )
        is_candle_path = (
            method == "GET" and re.fullmatch(candle_pattern, path) is not None
        )
        if (method, path) not in allowed and not is_candle_path:
            raise EtoroAPIError("Endpoint no permitido por el cliente de solo lectura")
        if path == "/api/v2/trading/info/costs":
            if method != "POST" or not isinstance(json_body, Mapping) or json_body.get("action") != "open":
                raise EtoroAPIError("Solo se permite la estimación hipotética de apertura")
        elif json_body is not None:
            raise EtoroAPIError("No se permiten cuerpos de escritura en endpoints GET")

        headers = {
            "x-api-key": self.app_key,
            "x-user-key": self.user_key,
            "x-request-id": str(uuid4()),
            "Accept": "application/json",
        }
        url = API_BASE_URL + path

        for attempt in range(self._max_attempts):
            try:
                response = self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self._timeout,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                if attempt + 1 >= self._max_attempts:
                    raise EtoroAPIError(
                        f"Error de conexión con eToro ({type(exc).__name__})"
                    ) from None
                time.sleep(min(0.5 * (2 ** attempt), 2.0))
                continue

            status = response.status_code
            if status in RETRYABLE_STATUS_CODES and attempt + 1 < self._max_attempts:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = 0.5 * (2 ** attempt)
                time.sleep(max(0.0, min(delay, 5.0)))
                continue
            if status >= 400:
                raise EtoroAPIError(f"eToro devolvió HTTP {status}")
            if status == 206 and not allow_partial:
                raise EtoroAPIError(
                    "Respuesta HTTP 206 parcial; el conjunto de datos no se considera completo"
                )
            try:
                data = response.json()
            except (ValueError, requests.JSONDecodeError):
                raise EtoroAPIError("eToro devolvió una respuesta JSON inválida") from None
            return EtoroResponse(data=data, status_code=status, headers=dict(response.headers))

        raise EtoroAPIError("No se pudo completar la petición a eToro")

    def get_account_pnl(self) -> EtoroResponse:
        return self._request("GET", "/api/v1/trading/info/real/pnl")

    def get_rates(self, instrument_ids: list[int]) -> EtoroResponse:
        if not instrument_ids or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in instrument_ids
        ):
            raise ValueError("instrument_ids debe contener IDs enteros positivos")
        return self._request(
            "GET",
            "/api/v2/market-data/rates",
            params={"instrumentIds": ",".join(map(str, instrument_ids))},
            allow_partial=True,
        )

    def get_candles(
        self,
        instrument_id: int,
        direction: str,
        interval: str,
        count: int,
    ) -> EtoroResponse:
        if isinstance(instrument_id, bool) or not isinstance(instrument_id, int) or instrument_id <= 0:
            raise ValueError("instrument_id debe ser un entero positivo")
        if direction not in {"asc", "desc"}:
            raise ValueError("direction debe ser asc o desc")
        if interval not in {"1m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1w"}:
            raise ValueError("intervalo de velas no permitido")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
            raise ValueError("count debe estar entre 1 y 1000")
        path = (
            f"/api/v1/market-data/instruments/{instrument_id}/history/"
            f"candles/{direction}/{interval}/{count}"
        )
        return self._request("GET", path)

    def get_what_if_costs(self, payload: Mapping[str, Any]) -> EtoroResponse:
        if not isinstance(payload, Mapping) or not payload:
            raise ValueError("El payload de costes hipotéticos debe ser un objeto no vacío")
        if payload.get("action") != "open":
            raise ValueError("Solo se permite action='open' en la consulta hipotética")
        return self._request(
            "POST",
            "/api/v2/trading/info/costs",
            json_body=dict(payload),
        )
