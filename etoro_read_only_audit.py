"""Manual, read-only eToro audit.

The report contains only public instrument IDs and pass/fail flags. It never
prints balances, positions, order details, credentials, or raw API responses.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
from typing import Any, Mapping

from config import ACCOUNT_CURRENCY, ASSETS
from etoro_read_only import (
    EtoroConfigurationError,
    EtoroReadOnlyClient,
    resolve_unique_instrument_by_symbol,
)
from etoro_position_risk import calculate_account_stop_risk_from_client


def _resolution_error_status(exc: Exception) -> str:
    """Return a stable, non-sensitive code for a failed read-only validation."""
    message = str(exc)
    if "HTTP " in message:
        suffix = message.split("HTTP ", 1)[1][:3]
        if suffix.isdigit():
            return f"HTTP_{suffix}"

    # Exact, generic markers emitted by the strict response normalizers.
    # Never include exception text, payload fragments, symbols, or account data.
    known = (
        # Instrument search
        ("Falta la lista results", "SEARCH_RESULTS_MISSING"),
        ("Falta el estado de paginación", "SEARCH_PAGINATION_MISSING"),
        ("requiere páginas adicionales", "SEARCH_PAGINATION_INCOMPLETE"),
        ("Se esperaba un único símbolo exacto", "EXACT_SYMBOL_MATCH_COUNT"),
        ("instrumentId válido", "INVALID_INSTRUMENT_ID"),
        ("no tiene displayName", "MISSING_DISPLAY_NAME"),
        ("no tiene type", "MISSING_INSTRUMENT_TYPE"),
        ("contiene un instrumento inválido", "INVALID_SEARCH_ITEM"),
        ("no es un objeto JSON", "INVALID_SEARCH_RESPONSE"),
        ("no devolvió HTTP 200", "SEARCH_HTTP_ERROR"),
        # Instrument eligibility
        ("La elegibilidad no es una respuesta HTTP 200 válida", "ELIGIBILITY_RESPONSE_INVALID"),
        ("La respuesta de elegibilidad no incluye el campo currency", "ELIGIBILITY_CURRENCY_MISSING"),
        ("La respuesta de elegibilidad no está en USD", "ELIGIBILITY_CURRENCY_MISMATCH"),
        ("Los instrumentIds esperados no son válidos", "ELIGIBILITY_EXPECTED_IDS_INVALID"),
        ("Los instrumentIds esperados contienen duplicados", "ELIGIBILITY_EXPECTED_IDS_DUPLICATED"),
        ("no está en USD", "ELIGIBILITY_CURRENCY_MISMATCH"),
        ("respuesta de elegibilidad está incompleta", "ELIGIBILITY_RESULTS_INCOMPLETE"),
        ("Elegibilidad inválida", "ELIGIBILITY_ITEM_INVALID"),
        ("Campo ", "ELIGIBILITY_FIELD_INVALID"),
        ("Falta la configuración de apalancamiento", "ELIGIBILITY_LEVERAGE_CONFIG_MISSING"),
        ("leverage config", "ELIGIBILITY_LEVERAGE_CONFIG_INVALID"),
        ("Faltan límites de exposición", "ELIGIBILITY_EXPOSURE_LIMITS_MISSING"),
        ("no cubre todos los instrumentos", "ELIGIBILITY_INSTRUMENTS_INCOMPLETE"),
        # Realtime quotes
        ("Las cotizaciones no son una respuesta HTTP 200 completa", "QUOTES_RESPONSE_INVALID"),
        ("La respuesta de cotizaciones no es un objeto JSON", "QUOTES_RESPONSE_SCHEMA_INVALID"),
        ("instrumentId inválido en cotización", "QUOTES_INSTRUMENT_ID_INVALID"),
        ("Instrumento inesperado en cotizaciones", "QUOTES_UNEXPECTED_INSTRUMENT"),
        ("Fecha ausente en cotización", "QUOTES_TIMESTAMP_MISSING"),
        ("Fecha inválida en cotización", "QUOTES_TIMESTAMP_INVALID"),
        ("La fecha de cotización", "QUOTES_TIMESTAMP_NOT_UTC"),
        ("now_utc debe incluir zona horaria UTC", "QUOTES_CLOCK_INVALID"),
        ("max_age_seconds debe ser un entero positivo", "QUOTES_MAX_AGE_INVALID"),
        ("Error de conexión con eToro", "ETORO_CONNECTION_ERROR"),
        ("eToro devolvió una respuesta JSON inválida", "ETORO_INVALID_JSON_RESPONSE"),
        ("Campo numérico inválido", "QUOTES_NUMERIC_FIELD_INVALID"),
        ("Campo numérico no finito", "QUOTES_NUMERIC_FIELD_INVALID"),
        ("lista de instrumentos esperados no es válida", "QUOTES_EXPECTED_IDS_INVALID"),
        ("lista de instrumentos esperados contiene duplicados", "QUOTES_EXPECTED_IDS_DUPLICATED"),
        ("Falta la lista de resultados de cotizaciones", "QUOTES_RESULTS_MISSING"),
        ("cotización retrasada", "QUOTES_NOT_REALTIME"),
        ("Faltan cotizaciones para instrumentos", "QUOTES_INSTRUMENTS_INCOMPLETE"),
        ("Cotización obsoleta o futura", "QUOTES_STALE_OR_FUTURE"),
        ("Bid/ask inválido o cruzado", "QUOTES_BID_ASK_INVALID"),
        ("Cotización duplicada", "QUOTES_DUPLICATED"),
        ("Cotización inválida", "QUOTES_ITEM_INVALID"),
        # Position breakdown / stop-risk diagnostics
        ("El desglose de posiciones no es una respuesta HTTP 200 válida", "RISK_BREAKDOWN_RESPONSE_INVALID"),
        ("CID esperado inválido", "RISK_CID_INVALID"),
        ("El desglose no confirma la divisa", "RISK_CURRENCY_UNCONFIRMED"),
        ("timestamp", "RISK_TIMESTAMP_INVALID"),
        ("desglose de posiciones está obsoleto", "RISK_BREAKDOWN_STALE"),
        ("contiene mirrors", "RISK_MIRRORS_UNSUPPORTED"),
        ("Falta la lista de instrumentos del desglose", "RISK_BREAKDOWN_INSTRUMENTS_MISSING"),
        ("Posición ", "RISK_POSITION_SCHEMA_INVALID"),
        ("Falta positions para instrumento", "RISK_POSITION_LIST_MISSING"),
        ("El desglose no contiene todas las posiciones esperadas", "RISK_POSITIONS_INCOMPLETE"),
        ("no coincide entre endpoints", "RISK_ENDPOINT_INSTRUMENT_MISMATCH"),
        ("no tiene stop-loss confirmado", "RISK_UNBOUNDED_STOP"),
        ("no contiene CID", "RISK_CID_MISSING"),
        ("Solo se puede valorar settlementType=real", "RISK_UNSUPPORTED_SETTLEMENT"),
        ("apalancada", "RISK_LEVERAGE_UNSUPPORTED"),
        ("El stop no es fijo", "RISK_STOP_TYPE_UNSUPPORTED"),
        ("no está en USD", "RISK_CURRENCY_UNSUPPORTED"),
        ("no es válida o no está sincronizada", "RISK_QUOTE_INVALID"),
        ("no es válido", "RISK_CALCULATION_INVALID"),
    )
    for marker, code in known:
        if marker in message:
            return code
    if type(exc).__name__ == "EtoroAPIError":
        return "ETORO_API_VALIDATION_ERROR"
    return type(exc).__name__.upper()


def _resolve_asset(
    client: EtoroReadOnlyClient,
    symbol: str,
    asset: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, str]:
    """Resolve an exact eToro symbol, including configured exchange suffixes."""
    candidates = [symbol]
    configured_market_symbol = asset.get("yahoo")
    # eToro lists some London ETFs with the exchange suffix (e.g. CSPX.L).
    # Reuse the explicitly configured market symbol only when it is the same
    # base ticker plus a suffix; never fuzzy-match or guess an instrument ID.
    if (
        isinstance(configured_market_symbol, str)
        and configured_market_symbol.startswith(symbol + ".")
        and configured_market_symbol not in candidates
    ):
        candidates.append(configured_market_symbol)

    item = None
    last_status = "UNRESOLVED"
    for candidate_index, candidate in enumerate(candidates):
        try:
            response = client.search_instrument_by_symbol(candidate, page_size=100)
            item = resolve_unique_instrument_by_symbol(response, candidate)
            break
        except Exception as exc:
            last_status = _resolution_error_status(exc)
            can_try_suffix = (
                candidate_index + 1 < len(candidates)
                and last_status in {"HTTP_404", "EXACT_SYMBOL_MATCH_COUNT"}
            )
            if not can_try_suffix:
                return None, last_status

    if item is None:
        return None, last_status

    expected_type = asset.get("type")
    actual_type = _fee_class(item.get("instrumentType"))
    if (
        not isinstance(expected_type, str)
        or actual_type is None
        or actual_type != expected_type.strip().upper()
    ):
        return None, "ASSET_TYPE_MISMATCH"
    return item, "RESOLVED"


def _account_snapshot_missing_fields(exc: Exception) -> list[str]:
    message = str(exc)
    marker = "incompleta: faltan "
    if marker not in message:
        return []
    raw_fields = message.split(marker, 1)[1].split(";", 1)[0]
    allowed = {
        "positionId", "instrumentId", "cid", "mirrorId", "isBuy",
        "openRate", "stopLossRate", "amount", "leverage", "units",
        "settlementTypeId", "isNoStopLoss",
    }
    return [field.strip() for field in raw_fields.split(",") if field.strip() in allowed]


def _account_snapshot_status(exc: Exception) -> str:
    """Return a redacted diagnostic code; never expose account/API payloads."""
    message = str(exc)
    if "HTTP " in message:
        suffix = message.split("HTTP ", 1)[1][:3]
        if suffix.isdigit():
            return f"ETORO_HTTP_{suffix}"

    known_failures = (
        ("La respuesta de cuenta no es un objeto JSON", "INVALID_ACCOUNT_RESPONSE"),
        ("Solo se admite la divisa configurada", "UNSUPPORTED_ACCOUNT_CURRENCY"),
        ("Falta clientPortfolio", "MISSING_CLIENT_PORTFOLIO"),
        ("Falta la lista explícita de posiciones", "MISSING_POSITIONS"),
        ("Falta la lista explícita de mirrors", "MISSING_MIRRORS"),
        ("contiene mirrors/copy trading", "UNSUPPORTED_COPY_TRADING"),
        ("Falta la lista explícita de órdenes pendientes", "MISSING_PENDING_ORDERS"),
        ("Orden pendiente inválida", "INVALID_PENDING_ORDER"),
        ("mirrorId inválido en orden", "INVALID_PENDING_ORDER_MIRROR_ID"),
        ("órdenes pendientes mirror/copy", "UNSUPPORTED_MIRROR_PENDING_ORDER"),
        ("órdenes de entrada pendientes", "UNQUANTIFIED_PENDING_ENTRY_ORDER"),
        ("Falta la lista explícita de órdenes Market-if-touched", "MISSING_MIT_ORDERS"),
        ("Market-if-touched", "UNQUANTIFIED_MIT_ORDER"),
        ("clientPortfolio.unrealizedPnL", "INVALID_UNREALIZED_PNL"),
        ("Esquema ambiguo o incompleto", "AMBIGUOUS_ACCOUNT_SCHEMA"),
        ("Las listas de órdenes de la cuenta están incompletas", "INCOMPLETE_ACCOUNT_ORDER_LISTS"),
        ("Orden activa inválida", "INVALID_ACTIVE_ORDER"),
        ("Importe negativo", "NEGATIVE_ORDER_AMOUNT"),
        ("El efectivo disponible calculado es negativo", "NEGATIVE_AVAILABLE_CASH"),
        ("Posición inválida", "INVALID_POSITION"),
        ("incompleta: faltan", "POSITION_MISSING_FIELDS"),
        ("Posición " , "INVALID_POSITION_SCHEMA"),
        ("positionId inválido", "INVALID_POSITION_ID"),
        ("instrumentId inválido", "INVALID_INSTRUMENT_ID"),
        ("cid inválido", "INVALID_POSITION_CID"),
        ("más de un CID", "MULTIPLE_POSITION_CIDS"),
        ("mirrorId inválido", "INVALID_POSITION_MIRROR_ID"),
        ("mirror/copy no soportadas", "UNSUPPORTED_MIRROR_POSITION"),
        ("isBuy inválido", "INVALID_POSITION_DIRECTION"),
        ("isNoStopLoss inválido", "INVALID_POSITION_STOP_FLAG"),
        ("Tipo de liquidación no reconocido", "UNSUPPORTED_SETTLEMENT_TYPE"),
        ("Campo numérico inválido", "INVALID_NUMERIC_FIELD"),
        ("Campo numérico no finito", "NON_FINITE_NUMERIC_FIELD"),
        ("debe ser positivo en posición", "NON_POSITIVE_POSITION_VALUE"),
        ("stop loss", "INVALID_STOP_LOSS"),
        ("Stop loss inválido", "INVALID_STOP_LOSS"),
    )
    for marker, code in known_failures:
        if marker in message:
            return code
    if type(exc).__name__ == "EtoroAPIError":
        return "ETORO_API_VALIDATION_ERROR"
    return type(exc).__name__.upper()


def _fee_class(instrument_type: Any) -> str | None:
    if not isinstance(instrument_type, str):
        return None
    normalized = instrument_type.strip().casefold().replace(" ", "")
    if normalized in {"stock", "stocks"}:
        return "STOCK"
    if normalized in {"etf", "exchange-tradedfund", "exchangetradedfund"}:
        return "ETF"
    if normalized in {"crypto", "cryptocurrency", "cryptocurrencies"}:
        return "CRYPTO"
    return None


def run_audit(
    client: EtoroReadOnlyClient,
    assets: Mapping[str, Mapping[str, Any]],
    account_currency: str,
    now_utc: datetime,
) -> dict[str, Any]:
    """Run account schema, exact instrument and realtime quote checks."""
    snapshot = None
    account_snapshot_status = "NOT_VALIDATED"
    account_snapshot_missing_fields: list[str] = []
    try:
        snapshot = client.get_account_snapshot(account_currency)
        account_valid = True
        account_snapshot_status = "VALIDATED"
    except Exception as exc:
        account_valid = False
        account_snapshot_status = _account_snapshot_status(exc)
        account_snapshot_missing_fields = _account_snapshot_missing_fields(exc)

    enabled_assets = {
        symbol: asset
        for symbol, asset in assets.items()
        if isinstance(asset, Mapping) and asset.get("enabled") is True
    }

    resolved: dict[str, Mapping[str, Any]] = {}
    resolution_status: dict[str, str] = {}
    for symbol, asset in enabled_assets.items():
        item, status = _resolve_asset(client, symbol, asset)
        resolution_status[symbol] = status
        if item is not None:
            resolved[symbol] = item

    fee_classes_by_instrument_id: dict[int, str] = {}
    for symbol, item in resolved.items():
        actual_fee_class = _fee_class(item.get("instrumentType"))
        expected_fee_class = enabled_assets[symbol].get("type")
        if (
            actual_fee_class is not None
            and isinstance(expected_fee_class, str)
            and actual_fee_class == expected_fee_class.strip().upper()
        ):
            fee_classes_by_instrument_id[item["instrumentId"]] = actual_fee_class

    gross_position_risk_validated = False
    complete_position_risk_validated = False
    position_risk_diagnostic_status = "NOT_CHECKED"
    if account_valid:
        try:
            risk_diagnostic = calculate_account_stop_risk_from_client(
                client,
                snapshot,
                now_utc,
                fee_classes_by_instrument_id,
            )
            gross_position_risk_validated = risk_diagnostic["status"] in {
                "MODELED_UNCONFIRMED",
                "VALIDATED_STOP_RISK",
                "COMPLETE_NO_OPEN_POSITIONS",
            }
            complete_position_risk_validated = risk_diagnostic["complete"]
            position_risk_diagnostic_status = risk_diagnostic["status"]
        except Exception as exc:
            gross_position_risk_validated = False
            complete_position_risk_validated = False
            position_risk_diagnostic_status = _resolution_error_status(exc)

    eligibility_valid = False
    eligibility_status = "NOT_CHECKED"
    quotes_valid = False
    quotes_status = "NOT_CHECKED"
    if resolved and len(resolved) == len(enabled_assets):
        instrument_ids = [item["instrumentId"] for item in resolved.values()]
        try:
            eligibility = client.get_validated_instrument_eligibility(
                instrument_ids,
                currency="USD",
            )
            eligibility_valid = (
                set(eligibility) == set(instrument_ids)
                and all(item.supports_long for item in eligibility.values())
            )
            eligibility_status = (
                "VALIDATED" if eligibility_valid else "LONG_NOT_SUPPORTED_OR_INCOMPLETE"
            )
        except Exception as exc:
            eligibility_valid = False
            eligibility_status = _resolution_error_status(exc)
        try:
            client.get_realtime_quotes(
                instrument_ids,
                now_utc,
                max_age_seconds=30,
            )
            quotes_valid = True
            quotes_status = "VALIDATED"
        except Exception as exc:
            quotes_valid = False
            quotes_status = _resolution_error_status(exc)

    instrument_report = [
        {
            "symbol": symbol,
            "expected_asset_type": enabled_assets[symbol].get("type"),
            "candidate_found": symbol in resolved,
            "resolution_status": resolution_status[symbol],
            "instrument_id": (
                resolved[symbol]["instrumentId"] if symbol in resolved else None
            ),
            "instrument_type": (
                resolved[symbol]["instrumentType"] if symbol in resolved else None
            ),
            "display_name": (
                resolved[symbol]["displayname"] if symbol in resolved else None
            ),
        }
        for symbol in enabled_assets
    ]
    overall = (
        account_valid
        and len(resolved) == len(enabled_assets)
        and eligibility_valid
        and quotes_valid
        and gross_position_risk_validated
    )
    return {
        "audit_scope": "read-only diagnostics; not authorization to trade",
        "position_risk_scope": "eToro real-account positions only; separate from the bot's virtual paper portfolio",
        "account_snapshot_schema_valid": account_valid,
        "account_snapshot_status": account_snapshot_status,
        "account_snapshot_missing_fields": account_snapshot_missing_fields,
        "instrument_resolution": instrument_report,
        "instrument_eligibility_validated": eligibility_valid,
        "instrument_eligibility_status": eligibility_status,
        "complete_realtime_quotes_validated": quotes_valid,
        "realtime_quotes_status": quotes_status,
        "gross_position_stop_risk_diagnostic_validated": gross_position_risk_validated,
        "position_risk_diagnostic_status": position_risk_diagnostic_status,
        "complete_position_risk_validated": complete_position_risk_validated,
        "portfolio_risk_admission_ready": False,
        "overall_pass": overall,
    }


def main() -> int:
    if not os.getenv("ETORO_API_KEY") or not os.getenv("ETORO_USER_KEY"):
        print("Audit aborted: read-only eToro credentials are not configured.")
        return 2

    try:
        client = EtoroReadOnlyClient()
    except EtoroConfigurationError:
        print("Audit aborted: read-only eToro credentials are unavailable.")
        return 2

    report = run_audit(
        client,
        ASSETS,
        ACCOUNT_CURRENCY,
        datetime.now(timezone.utc),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
