"""Strict read-only eToro API client.

This module exposes account/market-data reads and two documented read-only
POSTs: hypothetical cost estimation and instrument eligibility. It contains no order execution,
modification, cancellation, transfer, or other trading-write methods.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
import os
import re
import time
from typing import Any, Mapping
from uuid import uuid4

import requests
import exchange_calendars as exchange_calendars
import pandas as pd


API_BASE_URL = "https://public-api.etoro.com"
DEFAULT_TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
INSTRUMENT_SEARCH_FIELDS = (
    "instrumentId",
    "displayname",
    "internalSymbolFull",
    "instrumentType",
    "isCurrentlyTradable",
    "isActiveInPlatform",
    "isBuyEnabled",
    "isDelisted",
)
INSTRUMENT_FILTER_FIELDS = frozenset({
    "instrumentId",
    "displayname",
    "internalSymbolFull",
    "instrumentType",
    "instrumentTypeID",
    "exchangeID",
    "isCurrentlyTradable",
    "isActiveInPlatform",
    "isBuyEnabled",
    "isDelisted",
})


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
    account_currency_source: str
    client_id: int | None
    available_cash: Decimal
    unrealized_pnl: Decimal
    positions: tuple[Mapping[str, Any], ...]
    has_unbounded_stop_risk: bool


def normalize_account_snapshot(
    payload: Mapping[str, Any],
    account_currency: str,
) -> EtoroAccountSnapshot:
    """Validate the documented nested clientPortfolio response.

    The PnL endpoint does not expose accountCurrencyId. Currency must therefore
    be supplied from the bot's independently configured account currency; this
    function does not claim that the endpoint itself verified the currency.
    """
    if not isinstance(payload, Mapping):
        raise EtoroAPIError("La respuesta de cuenta no es un objeto JSON")
    if account_currency != "USD":
        raise EtoroAPIError("Solo se admite la divisa configurada USD en este bot")

    portfolio = payload.get("clientPortfolio")
    if not isinstance(portfolio, Mapping):
        raise EtoroAPIError("Falta clientPortfolio en la respuesta de eToro")
    positions = portfolio.get("positions")
    if not isinstance(positions, list):
        raise EtoroAPIError("Falta la lista explícita de posiciones")
    mirrors = portfolio.get("mirrors")
    if not isinstance(mirrors, list):
        raise EtoroAPIError("Falta la lista explícita de mirrors")
    if mirrors:
        raise EtoroAPIError(
            "La cuenta contiene mirrors/copy trading no soportados; snapshot bloqueado"
        )

    pending_orders = portfolio.get("ordersForOpen")
    if not isinstance(pending_orders, list):
        raise EtoroAPIError("Falta la lista explícita de órdenes pendientes")
    for index, order in enumerate(pending_orders):
        if not isinstance(order, Mapping):
            raise EtoroAPIError(f"Orden pendiente inválida: {index}")
        mirror_id = _one_key(order, ("mirrorId", "mirrorID"), f"mirrorId[{index}]")
        try:
            if int(mirror_id) != 0:
                raise EtoroAPIError(
                    "La cuenta contiene órdenes pendientes mirror/copy no soportadas"
                )
        except (TypeError, ValueError):
            raise EtoroAPIError(f"mirrorId inválido en orden {index}") from None

    # Pending entries reserve exposure that is not represented in positions.
    # Until their stop risk can be priced with confirmed instrument semantics,
    # do not expose this snapshot to portfolio admission.
    if pending_orders:
        raise EtoroAPIError(
            "La cuenta contiene órdenes de entrada pendientes cuyo riesgo no está cuantificado"
        )

    market_if_touched_orders = portfolio.get("orders")
    if not isinstance(market_if_touched_orders, list):
        raise EtoroAPIError("Falta la lista explícita de órdenes Market-if-touched")
    if market_if_touched_orders:
        raise EtoroAPIError(
            "La cuenta contiene órdenes Market-if-touched cuyo riesgo no está cuantificado"
        )

    unrealized_pnl = _decimal(portfolio.get("unrealizedPnL"), "clientPortfolio.unrealizedPnL")
    available_cash = calculate_available_cash(portfolio)

    required_fields = {
        "positionId",
        "instrumentId",
        "cid",
        "mirrorId",
        "isBuy",
        "openRate",
        "stopLossRate",
        "amount",
        "leverage",
        "units",
        "settlementTypeId",
        "isNoStopLoss",
    }
    normalized_positions: list[Mapping[str, Any]] = []
    client_ids: set[int] = set()
    unbounded_stop_risk = False

    for index, position in enumerate(positions):
        if not isinstance(position, Mapping):
            raise EtoroAPIError(f"Posición inválida: {index}")

        # eToro's published Position schema uses legacy acronym casing
        # (positionID, instrumentID, CID, mirrorID, settlementTypeID).
        # Normalize only these documented aliases; reject conflicting duplicates.
        position = dict(position)
        field_aliases = {
            "positionId": ("positionId", "positionID"),
            "instrumentId": ("instrumentId", "instrumentID"),
            "cid": ("cid", "CID"),
            "mirrorId": ("mirrorId", "mirrorID"),
            "settlementTypeId": ("settlementTypeId", "settlementTypeID"),
        }
        for canonical, aliases in field_aliases.items():
            present = [name for name in aliases if name in position]
            if len(present) > 1 and any(
                position[name] != position[present[0]] for name in present[1:]
            ):
                raise EtoroAPIError(
                    f"Campos duplicados incompatibles en posición {index}: {canonical}"
                )
            if present and canonical not in position:
                position[canonical] = position[present[0]]

        missing = sorted(required_fields.difference(position))
        if missing:
            raise EtoroAPIError(
                f"Posición {index} incompleta: faltan {', '.join(missing)}"
            )

        for id_field in ("positionId", "instrumentId", "cid"):
            value = position[id_field]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise EtoroAPIError(f"{id_field} inválido en posición {index}")

        client_ids.add(position["cid"])
        if len(client_ids) > 1:
            raise EtoroAPIError("Las posiciones pertenecen a más de un CID; snapshot bloqueado")

        mirror_id = position["mirrorId"]
        if isinstance(mirror_id, bool) or not isinstance(mirror_id, int) or mirror_id < 0:
            raise EtoroAPIError(f"mirrorId inválido en posición {index}")
        if mirror_id != 0:
            raise EtoroAPIError(
                "La cuenta contiene posiciones mirror/copy no soportadas; snapshot bloqueado"
            )
        if not isinstance(position["isBuy"], bool):
            raise EtoroAPIError(f"isBuy inválido en posición {index}")
        if not isinstance(position["isNoStopLoss"], bool):
            raise EtoroAPIError(f"isNoStopLoss inválido en posición {index}")

        settlement_type = position["settlementTypeId"]
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
        account_currency=account_currency,
        account_currency_source="bot_configuration",
        client_id=next(iter(client_ids)) if client_ids else None,
        available_cash=available_cash,
        unrealized_pnl=unrealized_pnl,
        positions=tuple(normalized_positions),
        has_unbounded_stop_risk=unbounded_stop_risk,
    )


@dataclass(frozen=True)
class EtoroQuote:
    instrument_id: int
    bid: Decimal
    ask: Decimal
    timestamp: datetime
    quote_type: str


def _last_completed_exchange_session(calendar_name: str, now_utc: datetime) -> tuple[bool, datetime, datetime]:
    """Return whether a market is open and the last completed session bounds."""
    try:
        calendar = exchange_calendars.get_calendar(calendar_name)
        session = calendar.date_to_session(pd.Timestamp(now_utc.date()), direction="previous")
        session_open = calendar.session_open(session).to_pydatetime()
        session_close = calendar.session_close(session).to_pydatetime()
        if now_utc < session_open:
            session = calendar.previous_session(session)
            session_open = calendar.session_open(session).to_pydatetime()
            session_close = calendar.session_close(session).to_pydatetime()
        is_open = bool(calendar.is_open_on_minute(pd.Timestamp(now_utc).floor("min")))
        return is_open, session_open, session_close
    except Exception as exc:
        raise EtoroAPIError(
            f"Calendario bursátil no disponible o inválido ({type(exc).__name__})"
        ) from None


def normalize_realtime_quotes(
    response: EtoroResponse,
    expected_instrument_ids: list[int],
    now_utc: datetime,
    max_age_seconds: int = 30,
    asset_calendars: Mapping[int, str] | None = None,
    max_closed_age_seconds: int = 604800,
) -> dict[int, EtoroQuote]:
    """Validate complete quotes using each instrument's independent market calendar."""
    if response.status_code != 200:
        raise EtoroAPIError("Las cotizaciones no son una respuesta HTTP 200 completa")
    if (
        not isinstance(expected_instrument_ids, list)
        or not 1 <= len(expected_instrument_ids) <= 100
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in expected_instrument_ids
        )
    ):
        raise EtoroAPIError("La lista de instrumentos esperados no es válida")
    if len(set(expected_instrument_ids)) != len(expected_instrument_ids):
        raise EtoroAPIError("La lista de instrumentos esperados contiene duplicados")
    if (
        not isinstance(now_utc, datetime)
        or now_utc.tzinfo is None
        or now_utc.utcoffset() != timedelta(0)
    ):
        raise EtoroAPIError("now_utc debe incluir zona horaria UTC")
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or max_age_seconds <= 0:
        raise EtoroAPIError("max_age_seconds debe ser un entero positivo")
    if isinstance(max_closed_age_seconds, bool) or not isinstance(max_closed_age_seconds, int) or max_closed_age_seconds < max_age_seconds:
        raise EtoroAPIError("max_closed_age_seconds debe ser mayor o igual al límite de cotización reciente")
    if asset_calendars is not None and (
        not isinstance(asset_calendars, Mapping)
        or any(
            isinstance(key, bool) or not isinstance(key, int) or key <= 0
            or not isinstance(value, str) or not value.strip()
            for key, value in asset_calendars.items()
        )
    ):
        raise EtoroAPIError("asset_calendars contiene una configuración inválida")

    if not isinstance(response.data, Mapping):
        raise EtoroAPIError("La respuesta de cotizaciones no es un objeto JSON")
    is_v1_rates = "rates" in response.data
    results = response.data.get("rates" if is_v1_rates else "results")
    if not isinstance(results, list):
        raise EtoroAPIError("Falta la lista de resultados de cotizaciones")

    expected = set(expected_instrument_ids)
    quotes: dict[int, EtoroQuote] = {}
    for index, item in enumerate(results):
        if not isinstance(item, Mapping):
            raise EtoroAPIError(f"Cotización inválida: {index}")
        id_fields = ("instrumentID", "instrumentId") if is_v1_rates else ("instrumentId",)
        present_ids = [field for field in id_fields if field in item]
        if len(present_ids) != 1:
            raise EtoroAPIError(f"instrumentId inválido en cotización {index}")
        instrument_id = item[present_ids[0]]
        if isinstance(instrument_id, bool) or not isinstance(instrument_id, int) or instrument_id <= 0:
            raise EtoroAPIError(f"instrumentId inválido en cotización {index}")
        if instrument_id not in expected:
            raise EtoroAPIError(f"Instrumento inesperado en cotizaciones: {instrument_id}")
        if instrument_id in quotes:
            raise EtoroAPIError(f"Cotización duplicada para instrumento {instrument_id}")
        if not is_v1_rates and item.get("quoteType") != "realtime":
            raise EtoroAPIError(f"El instrumento {instrument_id} tiene cotización retrasada")

        bid = _decimal(item.get("bid"), f"rates[{index}].bid")
        ask = _decimal(item.get("ask"), f"rates[{index}].ask")
        if bid <= 0 or ask <= 0 or ask < bid:
            raise EtoroAPIError(f"Bid/ask inválido o cruzado para instrumento {instrument_id}")

        date_value = item.get("date")
        if not isinstance(date_value, str):
            raise EtoroAPIError(f"Fecha ausente en cotización {instrument_id}")
        try:
            quote_time = datetime.fromisoformat(
                date_value[:-1] + "+00:00" if date_value.endswith("Z") else date_value
            )
        except ValueError:
            raise EtoroAPIError(f"Fecha inválida en cotización {instrument_id}") from None
        if quote_time.tzinfo is None or quote_time.utcoffset() is None:
            raise EtoroAPIError(
                f"La fecha de cotización {instrument_id} no incluye zona horaria"
            )
        # Any explicit UTC offset identifies an unambiguous instant. Normalize
        # it to UTC instead of rejecting equivalent timestamps such as +01:00.
        quote_time = quote_time.astimezone(timezone.utc)

        age_seconds = (now_utc - quote_time).total_seconds()
        quote_type = "realtime"
        if age_seconds < -5:
            raise EtoroAPIError(f"Cotización futura para instrumento {instrument_id}")
        if age_seconds > max_age_seconds:
            calendar_name = (asset_calendars or {}).get(instrument_id)
            if not calendar_name or calendar_name == "24/7":
                raise EtoroAPIError(f"Cotización obsoleta para instrumento {instrument_id}")
            market_is_open, last_session_open, last_session_close = (
                _last_completed_exchange_session(calendar_name, now_utc)
            )
            if market_is_open:
                raise EtoroAPIError(
                    f"Cotización obsoleta con mercado abierto para instrumento {instrument_id}"
                )
            if (
                quote_time < last_session_open
                or quote_time > last_session_close + timedelta(seconds=5)
                or age_seconds > max_closed_age_seconds
            ):
                raise EtoroAPIError(
                    f"Cotización fuera de la última sesión para instrumento {instrument_id}"
                )
            quote_type = "last_session"

        quotes[instrument_id] = EtoroQuote(
            instrument_id=instrument_id,
            bid=bid,
            ask=ask,
            timestamp=quote_time,
            quote_type=quote_type,
        )

    if set(quotes) != expected:
        missing = sorted(expected.difference(quotes))
        raise EtoroAPIError(f"Faltan cotizaciones para instrumentos: {missing}")
    return quotes


def resolve_unique_instrument_by_symbol(
    response: EtoroResponse,
    symbol: str,
) -> Mapping[str, Any]:
    """Resolve one exact symbol from eToro's v2 cursor-paginated response."""
    if response.status_code != 200:
        raise EtoroAPIError("La búsqueda v2 no devolvió HTTP 200")
    if not isinstance(symbol, str) or not symbol.strip():
        raise EtoroAPIError("El símbolo solicitado está vacío")
    if not isinstance(response.data, Mapping):
        raise EtoroAPIError("La respuesta de búsqueda v2 no es un objeto JSON")
    results = response.data.get("results")
    pagination = response.data.get("pagination")
    if not isinstance(results, list):
        raise EtoroAPIError("Falta la lista results en la búsqueda v2")
    if not isinstance(pagination, Mapping) or not isinstance(pagination.get("hasNext"), bool):
        raise EtoroAPIError("Falta el estado de paginación en la búsqueda v2")
    if pagination["hasNext"]:
        raise EtoroAPIError("La búsqueda v2 requiere páginas adicionales")
    target = symbol.strip().casefold()
    matches = []
    for item in results:
        if not isinstance(item, Mapping):
            raise EtoroAPIError("La búsqueda v2 contiene un instrumento inválido")
        item_symbol = item.get("symbol")
        if isinstance(item_symbol, str) and item_symbol.strip().casefold() == target:
            matches.append(item)
    if len(matches) != 1:
        raise EtoroAPIError(
            f"Se esperaba un único símbolo exacto para '{symbol}', encontrados: {len(matches)}"
        )
    item = matches[0]
    instrument_id = item.get("instrumentId")
    if isinstance(instrument_id, bool) or not isinstance(instrument_id, int) or instrument_id <= 0:
        raise EtoroAPIError("El instrumento v2 no tiene un instrumentId válido")
    display_name = item.get("displayName")
    instrument_type = item.get("type")
    if not isinstance(display_name, str) or not display_name.strip():
        raise EtoroAPIError("El instrumento v2 no tiene displayName")
    if not isinstance(instrument_type, str) or not instrument_type.strip():
        raise EtoroAPIError("El instrumento v2 no tiene type")
    return {
        "instrumentId": instrument_id,
        "displayname": display_name,
        "internalSymbolFull": item["symbol"],
        "instrumentType": instrument_type,
    }


def resolve_unique_instrument(
    response: EtoroResponse,
    identifier: str,
) -> Mapping[str, Any]:
    """Resolve one exact eToro symbol/display name; never choose fuzzy matches."""
    if response.status_code != 200:
        raise EtoroAPIError("La búsqueda de instrumentos no devolvió HTTP 200")
    if not isinstance(identifier, str) or not identifier.strip():
        raise EtoroAPIError("El identificador del instrumento está vacío")
    if not isinstance(response.data, Mapping):
        raise EtoroAPIError("La respuesta de búsqueda no es un objeto JSON")
    items = response.data.get("items")
    if not isinstance(items, list):
        raise EtoroAPIError("Falta la lista de instrumentos en la búsqueda")
    total_items = response.data.get("totalItems")
    if (
        isinstance(total_items, bool)
        or not isinstance(total_items, int)
        or total_items != len(items)
    ):
        raise EtoroAPIError(
            "La búsqueda de instrumentos está incompleta o requiere paginación adicional"
        )

    target = identifier.strip().casefold()
    matches = []
    for item in items:
        if not isinstance(item, Mapping):
            raise EtoroAPIError("La búsqueda contiene un instrumento inválido")
        symbol = item.get("internalSymbolFull")
        display_name = item.get("displayname")
        if (
            isinstance(symbol, str) and symbol.strip().casefold() == target
        ) or (
            isinstance(display_name, str) and display_name.strip().casefold() == target
        ):
            matches.append(item)

    if len(matches) != 1:
        raise EtoroAPIError(
            f"Se esperaba un único instrumento exacto para '{identifier}', encontrados: {len(matches)}"
        )

    item = matches[0]
    instrument_id = item.get("instrumentId")
    if isinstance(instrument_id, bool) or not isinstance(instrument_id, int) or instrument_id <= 0:
        raise EtoroAPIError("El instrumento encontrado no tiene un instrumentId válido")
    for flag in ("isCurrentlyTradable", "isActiveInPlatform", "isBuyEnabled"):
        if item.get(flag) is not True:
            raise EtoroAPIError(f"El instrumento no supera el control {flag}")
    if item.get("isDelisted") is not False:
        raise EtoroAPIError("El instrumento está excluido o no se ha confirmado que esté activo")
    if not isinstance(item.get("instrumentType"), str) or not item["instrumentType"].strip():
        raise EtoroAPIError("El tipo de instrumento no está confirmado")
    return dict(item)


WHAT_IF_ALLOWED_FIELDS = frozenset({
    "action",
    "transaction",
    "symbol",
    "instrumentId",
    "settlementType",
    "orderType",
    "triggerRate",
    "leverage",
    "amount",
    "orderCurrency",
    "units",
    "contracts",
    "stopLossRate",
    "takeProfitRate",
    "stopLossType",
    "additionalMargin",
    "limitRate",
})
WHAT_IF_SETTLEMENT_TYPES = {"cfd", "real", "realFutures", "marginTrade"}
WHAT_IF_ORDER_TYPES = {"mkt", "mit", "limitIOC"}
WHAT_IF_COST_TYPES = {
    "markup",
    "marketSpread",
    "transactionFee",
    "overnightFee",
    "overWeekendFee",
    "sdrt",
}


@dataclass(frozen=True)
class EtoroCostEstimate:
    instrument_id: int
    symbol: str | None
    costs: Mapping[str, Decimal]
    last_updated: datetime


def validate_what_if_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the documented hypothetical-open schema before sending POST."""
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("El payload de costes hipotéticos debe ser un objeto no vacío")
    unknown = [key for key in payload if key not in WHAT_IF_ALLOWED_FIELDS]
    if unknown:
        raise ValueError(f"Campos no permitidos en costes hipotéticos: {unknown!r}")
    if payload.get("action") != "open":
        raise ValueError("Solo se permite action='open'")
    transaction = payload.get("transaction")
    if not isinstance(transaction, str) or transaction not in {"buy", "sellShort"}:
        raise ValueError("transaction debe ser buy o sellShort")

    has_id = "instrumentId" in payload
    has_symbol = "symbol" in payload
    if has_id == has_symbol:
        raise ValueError("Debe indicarse exactamente uno de instrumentId o symbol")
    if has_id:
        value = payload["instrumentId"]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("instrumentId debe ser un entero positivo")
    elif not isinstance(payload["symbol"], str) or not payload["symbol"].strip():
        raise ValueError("symbol debe ser un texto no vacío")

    settlement = payload.get("settlementType")
    if not isinstance(settlement, str) or settlement not in WHAT_IF_SETTLEMENT_TYPES:
        raise ValueError("settlementType no está confirmado o no está permitido")
    order_type = payload.get("orderType")
    if not isinstance(order_type, str) or order_type not in WHAT_IF_ORDER_TYPES:
        raise ValueError("orderType no está confirmado o no está permitido")

    leverage = payload.get("leverage")
    if isinstance(leverage, bool) or not isinstance(leverage, int) or leverage < 1:
        raise ValueError("leverage debe ser un entero igual o superior a 1")
    if payload.get("orderCurrency") != "usd":
        raise ValueError("Solo se admite orderCurrency='usd'")

    size_fields = [field for field in ("amount", "units", "contracts") if field in payload]
    if len(size_fields) != 1:
        raise ValueError("Debe indicarse exactamente uno de amount, units o contracts")
    size_field = size_fields[0]
    size = _decimal(payload[size_field], size_field)
    if size <= 0:
        raise ValueError(f"{size_field} debe ser positivo")
    if settlement == "realFutures" and size_field != "contracts":
        raise ValueError("realFutures requiere contracts")
    if size_field == "contracts":
        if settlement != "realFutures" or size != size.to_integral_value():
            raise ValueError("contracts solo admite enteros para realFutures")

    if "additionalMargin" in payload:
        if settlement != "realFutures" or _decimal(payload["additionalMargin"], "additionalMargin") <= 0:
            raise ValueError("additionalMargin solo se admite positivo para realFutures")

    if "stopLossRate" in payload:
        if _decimal(payload["stopLossRate"], "stopLossRate") <= 0:
            raise ValueError("stopLossRate debe ser positivo")
    requires_stop = leverage > 1 or payload["transaction"] == "sellShort" or settlement == "realFutures"
    if requires_stop and "stopLossRate" not in payload:
        raise ValueError("Esta combinación requiere stopLossRate")

    if "takeProfitRate" in payload and _decimal(payload["takeProfitRate"], "takeProfitRate") <= 0:
        raise ValueError("takeProfitRate debe ser positivo")
    stop_loss_type = payload.get("stopLossType")
    if stop_loss_type is not None and (
        not isinstance(stop_loss_type, str) or stop_loss_type not in {"fixed", "trailing"}
    ):
        raise ValueError("stopLossType no permitido")
    if stop_loss_type == "trailing" and "stopLossRate" not in payload:
        raise ValueError("El stop trailing requiere stopLossRate")

    if order_type == "mit":
        if settlement == "realFutures":
            raise ValueError("mit no está permitido para realFutures")
        if "triggerRate" not in payload or _decimal(payload["triggerRate"], "triggerRate") <= 0:
            raise ValueError("mit requiere triggerRate positivo")
    elif "triggerRate" in payload:
        raise ValueError("triggerRate solo se permite con orderType='mit'")

    if order_type == "limitIOC":
        if "limitRate" not in payload or _decimal(payload["limitRate"], "limitRate") <= 0:
            raise ValueError("limitIOC requiere limitRate positivo")
    elif "limitRate" in payload:
        raise ValueError("limitRate solo se permite con orderType='limitIOC'")

    return dict(payload)


def normalize_cost_estimate(
    response: EtoroResponse,
    *,
    now_utc: datetime,
    expected_instrument_id: int | None = None,
    expected_symbol: str | None = None,
    max_age_seconds: int = 300,
) -> EtoroCostEstimate:
    """Validate a fresh USD cost response and its instrument identity."""
    if response.status_code != 200 or not isinstance(response.data, Mapping):
        raise EtoroAPIError("La estimación de costes no es una respuesta HTTP 200 válida")
    if (expected_instrument_id is None) == (expected_symbol is None):
        raise EtoroAPIError("Debe indicarse exactamente un instrumento esperado")
    if expected_instrument_id is not None and (
        isinstance(expected_instrument_id, bool)
        or not isinstance(expected_instrument_id, int)
        or expected_instrument_id <= 0
    ):
        raise EtoroAPIError("expected_instrument_id inválido")
    if expected_symbol is not None and (
        not isinstance(expected_symbol, str) or not expected_symbol.strip()
    ):
        raise EtoroAPIError("expected_symbol inválido")
    if (
        not isinstance(now_utc, datetime)
        or now_utc.tzinfo is None
        or now_utc.utcoffset() != timedelta(0)
    ):
        raise EtoroAPIError("now_utc debe incluir zona horaria UTC")
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or max_age_seconds <= 0:
        raise EtoroAPIError("max_age_seconds debe ser un entero positivo")

    instrument_id = response.data.get("instrumentId")
    if isinstance(instrument_id, bool) or not isinstance(instrument_id, int) or instrument_id <= 0:
        raise EtoroAPIError("La respuesta de costes no contiene instrumentId válido")
    if expected_instrument_id is not None and instrument_id != expected_instrument_id:
        raise EtoroAPIError("La respuesta de costes corresponde a otro instrumento")

    symbol = response.data.get("symbol")
    if symbol is not None and not isinstance(symbol, str):
        raise EtoroAPIError("symbol inválido en respuesta de costes")
    if expected_symbol is not None and (
        not isinstance(expected_symbol, str)
        or not isinstance(symbol, str)
        or symbol.casefold() != expected_symbol.casefold()
    ):
        raise EtoroAPIError("La respuesta de costes corresponde a otro símbolo")

    raw_costs = response.data.get("costs")
    if not isinstance(raw_costs, list) or not raw_costs:
        raise EtoroAPIError("La respuesta no contiene componentes de coste")
    costs: dict[str, Decimal] = {}
    for index, item in enumerate(raw_costs):
        if not isinstance(item, Mapping):
            raise EtoroAPIError(f"Componente de coste inválido: {index}")
        cost_type = item.get("costType")
        if not isinstance(cost_type, str) or cost_type not in WHAT_IF_COST_TYPES or cost_type in costs:
            raise EtoroAPIError(f"Tipo de coste desconocido o duplicado: {cost_type}")
        if item.get("currency") != "USD":
            raise EtoroAPIError(f"Divisa de coste no confirmada para {cost_type}")
        value = _decimal(item.get("value"), f"costs[{index}].value")
        if value < 0:
            raise EtoroAPIError(f"Importe de coste negativo para {cost_type}")
        costs[cost_type] = value

    updated_value = response.data.get("lastUpdated")
    if not isinstance(updated_value, str):
        raise EtoroAPIError("Falta lastUpdated en la respuesta de costes")
    try:
        updated = datetime.fromisoformat(
            updated_value[:-1] + "+00:00" if updated_value.endswith("Z") else updated_value
        )
    except ValueError:
        raise EtoroAPIError("lastUpdated inválido") from None
    if updated.tzinfo is None or updated.utcoffset() != timedelta(0):
        raise EtoroAPIError("lastUpdated no está expresado en UTC")
    age_seconds = (now_utc - updated).total_seconds()
    if age_seconds < -5 or age_seconds > max_age_seconds:
        raise EtoroAPIError("La estimación de costes está obsoleta o tiene fecha futura")

    return EtoroCostEstimate(
        instrument_id=instrument_id,
        symbol=symbol,
        costs=costs,
        last_updated=updated,
    )


@dataclass(frozen=True)
class EtoroInstrumentEligibility:
    instrument_id: int
    symbol: str
    allow_open_position: bool
    supports_long: bool
    allow_mit_orders: bool
    allow_entry_orders: bool
    allow_exit_orders: bool
    allow_trailing_stop_loss: bool
    min_position_exposure: Decimal | None
    max_units_per_order: Decimal | None
    units_quantity_type: str
    order_fill_behavior_type: str
    allowed_order_quantity_type: str
    trade_unit_type: str
    leverage_configs: tuple[Mapping[str, Any], ...]


def normalize_instrument_eligibility(
    response: EtoroResponse,
    expected_instrument_ids: list[int],
) -> dict[int, EtoroInstrumentEligibility]:
    """Validate complete per-account eligibility without inferring permission."""
    if response.status_code != 200 or not isinstance(response.data, Mapping):
        raise EtoroAPIError("La elegibilidad no es una respuesta HTTP 200 válida")
    if not expected_instrument_ids or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in expected_instrument_ids
    ):
        raise EtoroAPIError("Los instrumentIds esperados no son válidos")
    if len(set(expected_instrument_ids)) != len(expected_instrument_ids):
        raise EtoroAPIError("Los instrumentIds esperados contienen duplicados")
    if "currency" not in response.data:
        raise EtoroAPIError("La respuesta de elegibilidad no incluye el campo currency")
    response_currency = response.data.get("currency")
    if not isinstance(response_currency, str) or not response_currency.strip():
        raise EtoroAPIError("La divisa de la respuesta de elegibilidad no es válida")
    # Currency codes are case-insensitive; tolerate harmless surrounding
    # whitespace while continuing to reject any currency other than USD.
    if response_currency.strip().upper() != "USD":
        raise EtoroAPIError("La respuesta de elegibilidad no está en USD")

    not_found_ids = response.data.get("notFoundInstrumentIds")
    not_found_symbols = response.data.get("notFoundSymbols")
    raw_eligibilities = response.data.get("eligibilities")
    if (
        not isinstance(not_found_ids, list)
        or not isinstance(not_found_symbols, list)
        or not isinstance(raw_eligibilities, list)
        or not_found_ids
        or not_found_symbols
    ):
        raise EtoroAPIError("La respuesta de elegibilidad está incompleta o contiene activos no encontrados")

    expected = set(expected_instrument_ids)
    normalized: dict[int, EtoroInstrumentEligibility] = {}
    boolean_fields = (
        "allowOpenPosition",
        "allowMitOrders",
        "allowEntryOrders",
        "allowExitOrders",
        "allowTrailingStopLoss",
    )
    string_fields = (
        "unitsQuantityType",
        "orderFillBehaviorType",
        "allowedOrderQuantityType",
        "tradeUnitType",
    )

    for index, item in enumerate(raw_eligibilities):
        if not isinstance(item, Mapping):
            raise EtoroAPIError(f"Elegibilidad inválida: {index}")
        instrument_id = item.get("instrumentId")
        if (
            isinstance(instrument_id, bool)
            or not isinstance(instrument_id, int)
            or instrument_id not in expected
            or instrument_id in normalized
        ):
            raise EtoroAPIError("La respuesta contiene instrumentId inesperado o duplicado")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise EtoroAPIError(f"symbol ausente en elegibilidad {instrument_id}")

        for field in boolean_fields:
            if not isinstance(item.get(field), bool):
                raise EtoroAPIError(f"Campo {field} inválido en elegibilidad {instrument_id}")
        for field in string_fields:
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise EtoroAPIError(f"Campo {field} ausente en elegibilidad {instrument_id}")

        allow_open = item["allowOpenPosition"]
        min_exposure = None
        max_units = None
        if item.get("minPositionExposure") is not None:
            min_exposure = _decimal(item["minPositionExposure"], "minPositionExposure")
            if min_exposure <= 0:
                raise EtoroAPIError("minPositionExposure debe ser positivo")
        if item.get("maxUnitsPerOrder") is not None:
            max_units = _decimal(item["maxUnitsPerOrder"], "maxUnitsPerOrder")
            if max_units <= 0:
                raise EtoroAPIError("maxUnitsPerOrder debe ser positivo")
        if allow_open and (min_exposure is None or max_units is None):
            raise EtoroAPIError("Faltan límites de exposición para un instrumento habilitado")

        configs = item.get("leverageConfigs")
        if not isinstance(configs, list) or (allow_open and not configs):
            raise EtoroAPIError("Falta la configuración de apalancamiento")
        validated_configs: list[Mapping[str, Any]] = []
        for config_index, config in enumerate(configs):
            if not isinstance(config, Mapping):
                raise EtoroAPIError(f"Leverage config inválida: {config_index}")
            settlement = config.get("settlementType")
            direction = config.get("direction")
            leverage_values = config.get("leverageValues")
            if not isinstance(settlement, str) or not settlement.strip():
                raise EtoroAPIError("settlementType ausente en leverage config")
            if not isinstance(direction, str) or direction not in {"LONG", "SHORT"}:
                raise EtoroAPIError("direction inválida en leverage config")
            if not isinstance(leverage_values, list) or not leverage_values:
                raise EtoroAPIError("leverageValues ausente en leverage config")
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in leverage_values
            ) or len(set(leverage_values)) != len(leverage_values):
                raise EtoroAPIError("leverageValues inválidos o duplicados")
            if not isinstance(config.get("isPotential"), bool):
                raise EtoroAPIError("isPotential inválido en leverage config")
            if config.get("minPositionAmount") is not None:
                if _decimal(config["minPositionAmount"], "minPositionAmount") <= 0:
                    raise EtoroAPIError("minPositionAmount debe ser positivo")
            elif allow_open:
                raise EtoroAPIError("Falta minPositionAmount para un instrumento habilitado")
            for flag in ("allowEditStopLoss", "allowEditTakeProfit", "allowStopLossTakeProfit"):
                if not isinstance(config.get(flag), bool):
                    raise EtoroAPIError(f"{flag} inválido en leverage config")

            percentage_fields = (
                "minStopLossPercentage",
                "maxStopLossPercentage",
                "defaultStopLossPercentage",
                "minTakeProfitPercentage",
                "maxTakeProfitPercentage",
                "defaultTakeProfitPercentage",
            )
            percentages: dict[str, Decimal] = {}
            for field in percentage_fields:
                value = config.get(field)
                if value is not None:
                    percentages[field] = _decimal(value, field)
                    if percentages[field] <= 0:
                        raise EtoroAPIError(f"{field} debe ser positivo")
            if config["allowStopLossTakeProfit"] and len(percentages) != len(percentage_fields):
                raise EtoroAPIError("Faltan límites de stop/take profit en leverage config")
            for prefix in ("StopLoss", "TakeProfit"):
                minimum = percentages.get(f"min{prefix}Percentage")
                maximum = percentages.get(f"max{prefix}Percentage")
                default = percentages.get(f"default{prefix}Percentage")
                if minimum is not None and maximum is not None and default is not None:
                    if not minimum <= default <= maximum:
                        raise EtoroAPIError(f"Rangos {prefix} inconsistentes")

            validated_configs.append(dict(config))

        normalized[instrument_id] = EtoroInstrumentEligibility(
            instrument_id=instrument_id,
            symbol=symbol,
            allow_open_position=allow_open,
            supports_long=allow_open and any(
                config["direction"] == "LONG" for config in validated_configs
            ),
            allow_mit_orders=item["allowMitOrders"],
            allow_entry_orders=item["allowEntryOrders"],
            allow_exit_orders=item["allowExitOrders"],
            allow_trailing_stop_loss=item["allowTrailingStopLoss"],
            min_position_exposure=min_exposure,
            max_units_per_order=max_units,
            units_quantity_type=item["unitsQuantityType"],
            order_fill_behavior_type=item["orderFillBehaviorType"],
            allowed_order_quantity_type=item["allowedOrderQuantityType"],
            trade_unit_type=item["tradeUnitType"],
            leverage_configs=tuple(validated_configs),
        )

    if set(normalized) != expected:
        raise EtoroAPIError("La respuesta de elegibilidad no cubre todos los instrumentos")
    return normalized


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
        client_id: int | None = None,
    ) -> EtoroResponse:
        # Internal allowlist is intentionally narrow; callers cannot supply URLs.
        allowed = {
            ("GET", "/api/v1/trading/info/real/pnl"),
            ("GET", "/api/v2/trading/info/instrument-breakdown"),
            ("GET", "/api/v2/market-data/rates"),
            ("GET", "/api/v1/market-data/instruments/rates"),
            ("GET", "/api/v1/market-data/search"),
            ("GET", "/api/v2/market-data/instruments"),
            ("POST", "/api/v2/trading/info/costs"),
            ("POST", "/api/v2/trading/info/eligibility"),
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
        if path == "/api/v2/trading/info/instrument-breakdown":
            if (
                method != "GET"
                or isinstance(client_id, bool)
                or not isinstance(client_id, int)
                or client_id <= 0
                or params != {
                    "positionLevel": "Normal",
                    "orderLevel": "Normal",
                    "mirrorLevel": "None",
                }
            ):
                raise EtoroAPIError("CID o parámetros inválidos para el desglose de posiciones")
        elif client_id is not None:
            raise EtoroAPIError("CID no permitido para este endpoint")
        if path == "/api/v2/trading/info/costs":
            if method != "POST" or not isinstance(json_body, Mapping) or json_body.get("action") != "open":
                raise EtoroAPIError("Solo se permite la estimación hipotética de apertura")
        elif path == "/api/v2/trading/info/eligibility":
            if (
                method != "POST"
                or not isinstance(json_body, Mapping)
                or set(json_body) != {"instrumentIds", "currency"}
                or json_body.get("currency") != "USD"
                or not isinstance(json_body.get("instrumentIds"), list)
                or not 1 <= len(json_body["instrumentIds"]) <= 100
                or any(
                    isinstance(value, bool) or not isinstance(value, int) or value <= 0
                    for value in json_body["instrumentIds"]
                )
                or len(set(json_body["instrumentIds"])) != len(json_body["instrumentIds"])
            ):
                raise EtoroAPIError("Payload de elegibilidad no permitido o incompleto")
        elif json_body is not None:
            raise EtoroAPIError("No se permiten cuerpos en endpoints GET")

        headers = {
            "x-api-key": self.app_key,
            "x-user-key": self.user_key,
            "x-request-id": str(uuid4()),
            "Accept": "application/json",
        }
        if path == "/api/v2/trading/info/instrument-breakdown":
            headers["CID"] = str(client_id)
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

    def search_instrument_by_symbol(self, symbol: str, page_size: int = 100) -> EtoroResponse:
        """Search by exact trading symbol using eToro's cursor-based v2 endpoint."""
        if (
            not isinstance(symbol, str)
            or not symbol.strip()
            or len(symbol.strip()) > 32
            or not re.fullmatch(r"[A-Za-z0-9._-]+", symbol.strip())
        ):
            raise ValueError("symbol debe ser un ticker válido")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
            raise ValueError("page_size debe estar entre 1 y 100")
        return self._request(
            "GET",
            "/api/v2/market-data/instruments",
            params={"symbols": symbol.strip(), "pageSize": page_size},
        )

    def search_instruments(
        self,
        filters: Mapping[str, str | int | bool],
        *,
        fields: tuple[str, ...] | list[str] = INSTRUMENT_SEARCH_FIELDS,
        page_size: int = 100,
        page_number: int = 1,
    ) -> EtoroResponse:
        if not isinstance(filters, Mapping):
            raise ValueError("filters debe ser un objeto")
        if not fields or any(
            not isinstance(field, str) or field not in INSTRUMENT_FILTER_FIELDS
            for field in fields
        ):
            raise ValueError("fields contiene un campo no permitido")
        if len(set(fields)) != len(fields):
            raise ValueError("fields no puede contener duplicados")
        if not set(INSTRUMENT_SEARCH_FIELDS).issubset(set(fields)):
            raise ValueError(
                "fields debe incluir todos los campos necesarios para validar el instrumento"
            )
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 1000:
            raise ValueError("page_size debe estar entre 1 y 1000")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise ValueError("page_number debe ser un entero positivo")

        params: dict[str, Any] = {
            "fields": ",".join(fields),
            "pageSize": page_size,
            "pageNumber": page_number,
        }
        for key, value in filters.items():
            if key not in INSTRUMENT_FILTER_FIELDS:
                raise ValueError(f"Filtro no permitido: {key}")
            if isinstance(value, bool):
                params[key] = "true" if value else "false"
            elif isinstance(value, (str, int)) and str(value).strip():
                params[key] = value
            else:
                raise ValueError(f"Valor de filtro inválido: {key}")

        return self._request(
            "GET",
            "/api/v1/market-data/search",
            params=params,
        )

    def get_instrument_eligibility(
        self,
        instrument_ids: list[int],
        currency: str = "USD",
    ) -> EtoroResponse:
        if not isinstance(instrument_ids, list) or not 1 <= len(instrument_ids) <= 100:
            raise ValueError("instrument_ids debe contener entre 1 y 100 IDs")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in instrument_ids
        ):
            raise ValueError("instrument_ids debe contener IDs enteros positivos")
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("instrument_ids no puede contener duplicados")
        if currency != "USD":
            raise ValueError("La API de elegibilidad solo admite USD")
        return self._request(
            "POST",
            "/api/v2/trading/info/eligibility",
            json_body={"instrumentIds": list(instrument_ids), "currency": currency},
        )

    def get_validated_instrument_eligibility(
        self,
        instrument_ids: list[int],
        currency: str = "USD",
    ) -> dict[int, EtoroInstrumentEligibility]:
        response = self.get_instrument_eligibility(instrument_ids, currency)
        return normalize_instrument_eligibility(response, instrument_ids)

    def get_instrument_breakdown(self, client_id: int) -> EtoroResponse:
        """Fetch the documented position-level breakdown for one client ID."""
        if isinstance(client_id, bool) or not isinstance(client_id, int) or client_id <= 0:
            raise ValueError("client_id debe ser un entero positivo")
        return self._request(
            "GET",
            "/api/v2/trading/info/instrument-breakdown",
            params={
                "positionLevel": "Normal",
                "orderLevel": "Normal",
                "mirrorLevel": "None",
            },
            client_id=client_id,
        )

    def get_account_pnl(self) -> EtoroResponse:
        return self._request("GET", "/api/v1/trading/info/real/pnl")

    def get_account_snapshot(self, account_currency: str) -> EtoroAccountSnapshot:
        """Fetch and validate the snapshot using the bot-configured currency."""
        response = self.get_account_pnl()
        return normalize_account_snapshot(response.data, account_currency)

    def get_realtime_quotes(
        self,
        instrument_ids: list[int],
        now_utc: datetime,
        max_age_seconds: int = 30,
        asset_calendars: Mapping[int, str] | None = None,
        max_closed_age_seconds: int = 604800,
    ) -> dict[int, EtoroQuote]:
        response = self.get_rates(instrument_ids)
        return normalize_realtime_quotes(
            response,
            instrument_ids,
            now_utc,
            max_age_seconds=max_age_seconds,
            asset_calendars=asset_calendars,
            max_closed_age_seconds=max_closed_age_seconds,
        )

    def get_rates(self, instrument_ids: list[int]) -> EtoroResponse:
        if not instrument_ids or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in instrument_ids
        ):
            raise ValueError("instrument_ids debe contener IDs enteros positivos")
        if len(instrument_ids) > 1000:
            raise ValueError("eToro admite como máximo 1000 instrumentIds por petición")
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("instrument_ids no puede contener duplicados")
        return self._request(
            "GET",
            "/api/v1/market-data/instruments/rates",
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
        validated = validate_what_if_payload(payload)
        return self._request(
            "POST",
            "/api/v2/trading/info/costs",
            json_body=validated,
        )

    def get_validated_what_if_costs(
        self,
        payload: Mapping[str, Any],
        now_utc: datetime,
        max_age_seconds: int = 300,
    ) -> EtoroCostEstimate:
        validated = validate_what_if_payload(payload)
        response = self._request(
            "POST",
            "/api/v2/trading/info/costs",
            json_body=validated,
        )
        return normalize_cost_estimate(
            response,
            now_utc=now_utc,
            expected_instrument_id=validated.get("instrumentId"),
            expected_symbol=validated.get("symbol"),
            max_age_seconds=max_age_seconds,
        )
