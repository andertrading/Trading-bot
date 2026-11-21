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
    resolve_unique_instrument,
)
from etoro_position_risk import calculate_account_stop_risk_from_client


def _resolution_error_status(exc: Exception) -> str:
    message = str(exc)
    if "HTTP " in message:
        suffix = message.split("HTTP ", 1)[1][:3]
        if suffix.isdigit():
            return f"HTTP_{suffix}"
    known = (
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
    )
    for marker, code in known:
        if marker in message:
            return code
    if type(exc).__name__ == "EtoroAPIError":
        return "ETORO_SEARCH_VALIDATION_ERROR"
    return type(exc).__name__.upper()


def _resolve_asset(
    client: EtoroReadOnlyClient,
    symbol: str,
    asset: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, str]:
    """Resolve a ticker through eToro's exact-symbol v2 search endpoint."""
    try:
        response = client.search_instrument_by_symbol(symbol, page_size=100)
        item = resolve_unique_instrument_by_symbol(response, symbol)
    except Exception as exc:
        return None, _resolution_error_status(exc)

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
        except Exception:
            gross_position_risk_validated = False
            complete_position_risk_validated = False

    eligibility_valid = False
    quotes_valid = False
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
        except Exception:
            eligibility_valid = False
        try:
            client.get_realtime_quotes(
                instrument_ids,
                now_utc,
                max_age_seconds=30,
            )
            quotes_valid = True
        except Exception:
            quotes_valid = False

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
        "account_snapshot_schema_valid": account_valid,
        "account_snapshot_status": account_snapshot_status,
        "account_snapshot_missing_fields": account_snapshot_missing_fields,
        "instrument_resolution": instrument_report,
        "instrument_eligibility_validated": eligibility_valid,
        "complete_realtime_quotes_validated": quotes_valid,
        "gross_position_stop_risk_diagnostic_validated": gross_position_risk_validated,
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
