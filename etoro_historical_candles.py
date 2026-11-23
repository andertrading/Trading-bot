"""Read-only eToro historical market-candle capture; no account or trading endpoints."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from config import ASSETS
from etoro_market_evidence import _instrument_type_matches
from etoro_read_only import (
    EtoroAPIError,
    EtoroReadOnlyClient,
    resolve_unique_instrument,
    resolve_unique_instrument_by_symbol,
)


class EtoroHistoricalCandleClient(EtoroReadOnlyClient):
    """Market-only client restricted to exact instrument search and candle history."""

    SEARCH_ENDPOINTS = frozenset({
        ("GET", "/api/v2/market-data/instruments"),
        ("GET", "/api/v1/market-data/search"),
    })
    HISTORY_PATH = re.compile(
        r"^/api/v1/market-data/instruments/[1-9][0-9]*/history/"
        r"candles/(?:asc|desc)/(?:OneMinute|FiveMinutes|TenMinutes|FifteenMinutes|"
        r"ThirtyMinutes|OneHour|FourHours|OneDay|OneWeek)/"
        r"(?:[1-9][0-9]{0,2}|1000)$"
    )

    def _request(self, method: str, path: str, **kwargs: Any):
        if (method, path) not in self.SEARCH_ENDPOINTS and not (
            method == "GET" and self.HISTORY_PATH.fullmatch(path)
        ):
            raise EtoroAPIError(
                "Endpoint bloqueado: el recolector solo permite búsqueda de instrumentos "
                "y velas históricas de mercado; no accede a cartera, saldos, posiciones, "
                "PnL, órdenes ni historial de operaciones."
            )
        return super()._request(method, path, **kwargs)


def _resolve_instrument(client: Any, symbol: str) -> Mapping[str, Any]:
    try:
        response = client.search_instrument_by_symbol(symbol)
        return resolve_unique_instrument_by_symbol(response, symbol)
    except EtoroAPIError as exc:
        message = str(exc)
        if "HTTP 404" not in message and "encontrados: 0" not in message:
            raise
        response = client.search_instruments({"internalSymbolFull": symbol})
        return resolve_unique_instrument(response, symbol)


def _as_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise EtoroAPIError(f"Campo numérico inválido: {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise EtoroAPIError(f"Campo numérico inválido: {field}") from None
    if not result.is_finite():
        raise EtoroAPIError(f"Campo numérico no finito: {field}")
    return result


def _as_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EtoroAPIError("La vela no contiene fromDate")
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        raise EtoroAPIError("fromDate de vela inválido") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EtoroAPIError("fromDate debe incluir zona horaria")
    return parsed.astimezone(timezone.utc)


def _extract_candle_items(data: Any, instrument_id: int) -> list[Mapping[str, Any]]:
    if not isinstance(data, Mapping):
        raise EtoroAPIError("La respuesta de velas no es un objeto JSON")
    groups = data.get("candles")
    if not isinstance(groups, list):
        raise EtoroAPIError("La respuesta de velas no contiene una lista candles")
    if not groups:
        return []

    grouped = [
        item for item in groups
        if isinstance(item, Mapping)
        and item.get("instrumentId", item.get("instrumentID")) == instrument_id
    ]
    if len(grouped) > 1:
        raise EtoroAPIError("La respuesta contiene grupos duplicados para el instrumento")
    group_identity_confirmed = False
    if grouped:
        group_identity_confirmed = True
        items = grouped[0].get("candles")
    elif len(groups) == 1 and isinstance(groups[0], Mapping) and "candles" in groups[0]:
        group_id = groups[0].get("instrumentId", groups[0].get("instrumentID"))
        if group_id is not None and group_id != instrument_id:
            raise EtoroAPIError("La respuesta de velas pertenece a otro instrumento")
        group_identity_confirmed = group_id == instrument_id
        items = groups[0].get("candles")
    elif all(isinstance(item, Mapping) and "fromDate" in item for item in groups):
        items = groups
    else:
        raise EtoroAPIError("No se pudo resolver el grupo de velas del instrumento")
    if not isinstance(items, list):
        raise EtoroAPIError("El grupo de velas no contiene una lista candles")
    if not all(isinstance(item, Mapping) for item in items):
        raise EtoroAPIError("La lista de velas contiene elementos inválidos")
    if not group_identity_confirmed and any(
        item.get("instrumentID", item.get("instrumentId")) is None
        for item in items
    ):
        raise EtoroAPIError("No se pudo confirmar el instrumentId de cada vela")
    return items


def normalize_candles(
    data: Any,
    instrument_id: int,
    *,
    count: int,
    now_utc: datetime,
    diagnostics: dict[str, Any] | None = None,
    allow_conflicts: bool = False,
) -> list[dict[str, Any]]:
    items = _extract_candle_items(data, instrument_id)
    if len(items) > count:
        raise EtoroAPIError("eToro devolvió más velas que las solicitadas")

    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.setdefault("identical_duplicate_rows_removed", 0)
    diagnostics.setdefault("conflicting_duplicate_rows", 0)
    diagnostics.setdefault("duplicate_timestamp_conflicts", [])
    diagnostics["source_candle_count"] = len(items)
    by_timestamp: dict[str, dict[str, Any]] = {}
    conflicted_timestamps: set[str] = set()
    for index, item in enumerate(items):
        candle_id = item.get("instrumentID", item.get("instrumentId"))
        if candle_id is not None and candle_id != instrument_id:
            raise EtoroAPIError(f"La vela {index} pertenece a otro instrumento")
        timestamp = _as_utc_timestamp(item.get("fromDate"))
        if timestamp > now_utc:
            raise EtoroAPIError(f"La vela {index} tiene una fecha futura")

        prices = {
            key: _as_decimal(item.get(key), f"candles[{index}].{key}")
            for key in ("open", "high", "low", "close")
        }
        if any(value <= 0 for value in prices.values()):
            raise EtoroAPIError(f"La vela {index} contiene precios no positivos")
        if (
            prices["low"] > min(prices["open"], prices["close"])
            or prices["high"] < max(prices["open"], prices["close"])
            or prices["low"] > prices["high"]
        ):
            raise EtoroAPIError(f"La vela {index} contiene OHLC incoherente")

        volume = item.get("volume")
        if volume is not None:
            volume_value = _as_decimal(volume, f"candles[{index}].volume")
            if volume_value < 0:
                raise EtoroAPIError(f"La vela {index} contiene volumen negativo")
            volume = format(volume_value, "f")

        normalized_item = {
            "timestamp": timestamp.isoformat(),
            "open": format(prices["open"], "f"),
            "high": format(prices["high"], "f"),
            "low": format(prices["low"], "f"),
            "close": format(prices["close"], "f"),
            "volume": volume,
        }
        key = normalized_item["timestamp"]
        if key in conflicted_timestamps:
            diagnostics["conflicting_duplicate_rows"] += 1
            diagnostics["duplicate_timestamp_conflicts"].append({
                "timestamp": key,
                "additional": normalized_item,
                "additional_source_fromDate": item.get("fromDate"),
                "additional_source_fields": sorted(str(field) for field in item.keys()),
            })
            continue
        previous = by_timestamp.get(key)
        if previous is not None:
            if previous != normalized_item:
                diagnostics["conflicting_duplicate_rows"] += 1
                diagnostics["duplicate_timestamp_conflicts"].append({
                    "timestamp": key,
                    "first": previous,
                    "conflicting": normalized_item,
                    "first_source_fromDate": next(
                        (
                            prior.get("fromDate")
                            for prior in items[:index]
                            if _as_utc_timestamp(prior.get("fromDate")).isoformat() == key
                        ),
                        None,
                    ),
                    "conflicting_source_fromDate": item.get("fromDate"),
                    "source_fields": sorted(str(field) for field in item.keys()),
                })
                by_timestamp.pop(key, None)
                conflicted_timestamps.add(key)
                continue
            diagnostics["identical_duplicate_rows_removed"] += 1
            continue
        by_timestamp[key] = normalized_item

    diagnostics["conflicting_duplicate_timestamps"] = len(conflicted_timestamps)
    if conflicted_timestamps and not allow_conflicts:
        first = sorted(conflicted_timestamps)[0]
        raise EtoroAPIError(
            f"Fecha de vela duplicada con valores distintos: {first}"
        )
    normalized = list(by_timestamp.values())
    normalized.sort(key=lambda item: item["timestamp"])
    return normalized


def collect_historical_candles(
    client: Any,
    *,
    symbols: Mapping[str, Mapping[str, Any]] | None = None,
    now_utc: datetime | None = None,
    interval: str = "1d",
    count: int = 1000,
    min_candles: int = 220,
) -> dict[str, Any]:
    symbols = ASSETS if symbols is None else symbols
    now_utc = datetime.now(timezone.utc) if now_utc is None else now_utc
    if now_utc.tzinfo is None or now_utc.utcoffset() != timezone.utc.utcoffset(now_utc):
        raise ValueError("now_utc debe estar expresado en UTC")
    if interval not in {"1m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1w"}:
        raise ValueError("intervalo de velas no permitido")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
        raise ValueError("count debe estar entre 1 y 1000")
    if isinstance(min_candles, bool) or not isinstance(min_candles, int) or not 1 <= min_candles <= count:
        raise ValueError("min_candles debe estar entre 1 y count")

    report = {
        "schema_version": 1,
        "captured_at": now_utc.isoformat(),
        "source": "eToro Public API",
        "mode": "READ_ONLY_HISTORICAL_MARKET_CANDLES",
        "account_data_accessed": False,
        "order_actions_performed": False,
        "interval": interval,
        "direction_requested": "desc",
        "requested_candles_per_asset": count,
        "minimum_candles_for_sufficient_history": min_candles,
        "price_basis_scope": "HISTORICAL_CHART_OHLC; BID_ASK_AND_EXECUTION_BASIS_UNSPECIFIED",
        "price_adjustment_scope": "UNSPECIFIED; NOT_ASSUMED_SPLIT_OR_DIVIDEND_ADJUSTED",
        "assets": {},
    }

    resolved = 0
    sufficient = 0
    failures = 0
    for asset, config in symbols.items():
        record = {
            "configured_asset": asset,
            "instrument": None,
            "historical_candles": None,
            "errors": [],
        }
        report["assets"][asset] = record
        try:
            symbol = config.get("etoro_symbol", asset)
            if not isinstance(symbol, str) or not symbol.strip():
                raise EtoroAPIError("etoro_symbol debe ser un símbolo no vacío")
            instrument = _resolve_instrument(client, symbol.strip())
            if not _instrument_type_matches(config.get("type"), instrument.get("instrumentType")):
                raise EtoroAPIError(
                    "eToro instrument type does not match configured asset type"
                )
            instrument_id = instrument["instrumentId"]
            resolved += 1
            record["instrument"] = {
                "instrument_id": instrument_id,
                "symbol": instrument["internalSymbolFull"],
                "display_name": instrument["displayname"],
                "instrument_type": instrument["instrumentType"],
            }
            response = client.get_candles(
                instrument_id, "desc", interval, count
            )
            if getattr(response, "status_code", None) != 200:
                raise EtoroAPIError("La descarga de velas no devolvió HTTP 200")
            candle_diagnostics: dict[str, Any] = {}
            candles = normalize_candles(
                response.data,
                instrument_id,
                count=count,
                now_utc=now_utc,
                diagnostics=candle_diagnostics,
                allow_conflicts=True,
            )
            enough = (
                len(candles) >= min_candles
                and candle_diagnostics["conflicting_duplicate_timestamps"] == 0
            )
            if enough:
                sufficient += 1
            record["historical_candles"] = {
                "interval": interval,
                "requested_count": count,
                "source_returned_count": candle_diagnostics["source_candle_count"],
                "returned_count": len(candles),
                "identical_duplicate_rows_removed": candle_diagnostics[
                    "identical_duplicate_rows_removed"
                ],
                "conflicting_duplicate_timestamps": candle_diagnostics[
                    "conflicting_duplicate_timestamps"
                ],
                "duplicate_timestamp_conflicts": candle_diagnostics[
                    "duplicate_timestamp_conflicts"
                ],
                "sufficient_history": enough,
                "history_usable": enough,
                "first_timestamp": candles[0]["timestamp"] if candles else None,
                "last_timestamp": candles[-1]["timestamp"] if candles else None,
                "source": "eToro historical chart candles",
                "bid_ask_history_available": False,
                "execution_price_basis_confirmed": False,
                "price_adjustment_status": "UNSPECIFIED",
                "candles": candles,
            }
            if candle_diagnostics["conflicting_duplicate_timestamps"]:
                record["errors"].append(
                    "conflicting_duplicate_timestamps: "
                    + str(candle_diagnostics["conflicting_duplicate_timestamps"])
                )
            elif not enough:
                record["errors"].append(
                    f"insufficient_history: returned {len(candles)}; minimum {min_candles}"
                )
        except Exception as exc:
            record["errors"].append(
                f"{type(exc).__name__}: {exc}"
            )
        if record["errors"]:
            failures += 1

    report["summary"] = {
        "configured_assets": len(symbols),
        "resolved_instruments": resolved,
        "assets_with_sufficient_history": sufficient,
        "assets_with_errors": failures,
        "status": (
            "COMPLETE"
            if failures == 0 and resolved == len(symbols) and sufficient == len(symbols)
            else "PARTIAL"
        ),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Captura OHLC histórico eToro de solo lectura."
    )
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--min-candles", type=int, default=220)
    args = parser.parse_args()

    client = EtoroHistoricalCandleClient()
    report = collect_historical_candles(
        client,
        interval=args.interval,
        count=args.count,
        min_candles=args.min_candles,
    )
    output = Path(os.getenv(
        "ETORO_HISTORICAL_EVIDENCE_OUTPUT",
        "artifacts/etoro_historical_candles.json",
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"Informe guardado: {output}")
    return 0 if report["summary"]["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
