"""One-shot, read-only probe of eToro historical market candles.

Requests only the documented public Market Data candle endpoint. No account,
portfolio, order, trading, or cost endpoints are called. Manual invocation only.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Mapping
from etoro_read_only import EtoroAPIError, EtoroReadOnlyClient

API_PATH_RE = re.compile(r"^/api/v1/market-data/instruments/[1-9][0-9]*/history/candles/(asc|desc)/(FifteenMinutes|OneDay)/([1-9][0-9]{0,2}|1000)$")
INTERVALS = ("FifteenMinutes", "OneDay")
DEFAULT_COUNT = 1000

class EtoroHistoricalMarketDataClient(EtoroReadOnlyClient):
    """Narrow client that permits only the historical market-candle GET route."""
    def _request(self, method: str, path: str, **kwargs: Any):
        if method != "GET" or API_PATH_RE.fullmatch(path) is None:
            raise EtoroAPIError("Endpoint bloqueado: el probe solo permite velas históricas de Market Data.")
        return super()._request(method, path, **kwargs)

    def get_candle_history(self, instrument_id: int, interval: str, candles_count: int = DEFAULT_COUNT, direction: str = "asc"):
        if isinstance(instrument_id, bool) or not isinstance(instrument_id, int) or instrument_id <= 0:
            raise ValueError("instrument_id debe ser un entero positivo")
        if interval not in INTERVALS:
            raise ValueError("interval no permitido")
        if direction not in {"asc", "desc"}:
            raise ValueError("direction no permitido")
        if isinstance(candles_count, bool) or not isinstance(candles_count, int) or not 1 <= candles_count <= 1000:
            raise ValueError("candles_count debe estar entre 1 y 1000")
        path = f"/api/v1/market-data/instruments/{instrument_id}/history/candles/{direction}/{interval}/{candles_count}"
        return self._request("GET", path)

def _number(value: Any, label: str, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise EtoroAPIError(f"Valor numérico inválido: {label}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise EtoroAPIError(f"Valor numérico inválido: {label}") from None
    if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
        raise EtoroAPIError(f"Valor numérico fuera de rango: {label}")
    return number

def normalize_candle_history(response: Any, instrument_id: int, expected_interval: str) -> list[dict[str, Any]]:
    """Validate the documented grouped response; reject partial/mismatched data."""
    if response.status_code != 200 or not isinstance(response.data, Mapping):
        raise EtoroAPIError("Histórico no válido: se esperaba HTTP 200 y objeto JSON")
    if response.data.get("interval") != expected_interval:
        raise EtoroAPIError("El intervalo devuelto no coincide con el solicitado")
    groups = response.data.get("candles")
    if not isinstance(groups, list):
        raise EtoroAPIError("Falta la lista de grupos de velas")
    if not groups:
        return []
    if len(groups) != 1:
        raise EtoroAPIError("Respuesta ambigua: se esperaba un único grupo de instrumento")
    group = groups[0]
    if not isinstance(group, Mapping) or group.get("instrumentId") != instrument_id:
        raise EtoroAPIError("El grupo de velas corresponde a otro instrumento")
    raw_candles = group.get("candles")
    if not isinstance(raw_candles, list):
        raise EtoroAPIError("Falta la lista anidada de velas")
    normalized = []
    seen = set()
    for index, item in enumerate(raw_candles):
        if not isinstance(item, Mapping) or item.get("instrumentID") != instrument_id:
            raise EtoroAPIError(f"Vela {index}: instrumentID ausente o inesperado")
        timestamp = item.get("fromDate")
        if not isinstance(timestamp, str):
            raise EtoroAPIError(f"Vela {index}: fromDate ausente")
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            raise EtoroAPIError(f"Vela {index}: fromDate inválido") from None
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise EtoroAPIError(f"Vela {index}: timestamp sin UTC explícito")
        if parsed in seen:
            raise EtoroAPIError(f"Vela duplicada: {timestamp}")
        seen.add(parsed)
        o = _number(item.get("open"), f"{index}.open")
        h = _number(item.get("high"), f"{index}.high")
        low = _number(item.get("low"), f"{index}.low")
        c = _number(item.get("close"), f"{index}.close")
        if h < max(o, low, c) or low > min(o, h, c):
            raise EtoroAPIError(f"Vela {index}: OHLC inconsistente")
        volume_raw = item.get("volume")
        volume = None if volume_raw is None else _number(volume_raw, f"{index}.volume", allow_zero=True)
        normalized.append({"timestamp": parsed.isoformat(), "open": str(o), "high": str(h), "low": str(low), "close": str(c), "volume": None if volume is None else str(volume)})
    timestamps = [datetime.fromisoformat(item["timestamp"]) for item in normalized]
    if timestamps != sorted(timestamps):
        raise EtoroAPIError("Las velas no están ordenadas cronológicamente")
    return normalized

def load_instrument_ids(registry_path: Path) -> dict[str, int]:
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assets = payload.get("assets")
    if not isinstance(assets, Mapping) or not assets:
        raise ValueError("El registro económico no contiene assets")
    result = {}
    for symbol, item in assets.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"Registro de instrumento inválido para {symbol}")
        instrument = item.get("instrument")
        if not isinstance(instrument, Mapping) or instrument.get("confirmed") is not True:
            raise ValueError(f"Metadatos de instrumento no confirmados para {symbol}")
        iid = instrument.get("etoro_instrument_id")
        if isinstance(iid, bool) or not isinstance(iid, int) or iid <= 0:
            raise ValueError(f"instrumentId no confirmado para {symbol}")
        result[symbol] = iid
    if len(set(result.values())) != len(result):
        raise ValueError("El registro contiene instrumentIds duplicados")
    return result

def run_probe(client: EtoroHistoricalMarketDataClient, instruments: Mapping[str, int], candles_count: int) -> dict[str, Any]:
    report = {
        "schema_version": 1, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "eToro Public API Market Data historical candles",
        "mode": "READ_ONLY_MARKET_DATA_ONLY", "status": "COMPLETE",
        "account_or_portfolio_data_accessed": False,
        "orders_or_trades_submitted": False, "provider_credits_used": False,
        "requested_candles_per_asset_interval": candles_count, "intervals": list(INTERVALS),
        "assets": {}, "errors": [], "limitations": [
            "OHLCV history is not assumed to represent executable bid/ask prices.",
            "Corporate-action adjustment basis is not established by this probe.",
            "Volume may be zero or unavailable and is not presumed comparable.",
            "A successful response does not establish complete historical coverage.",
            "No account, portfolio, position, balance, PnL, order, or trade-history endpoint is called."
        ]
    }
    for symbol, instrument_id in instruments.items():
        item = {"instrument_id": instrument_id, "intervals": {}}
        report["assets"][symbol] = item
        for interval in INTERVALS:
            try:
                response = client.get_candle_history(instrument_id, interval, candles_count, "asc")
                candles = normalize_candle_history(response, instrument_id, interval)
            except EtoroAPIError as exc:
                error = {
                    "asset": symbol,
                    "instrument_id": instrument_id,
                    "interval": interval,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }
                report["errors"].append(error)
                item["intervals"][interval] = {
                    "status": "ERROR",
                    "returned_candles": None,
                    "earliest_timestamp": None,
                    "latest_timestamp": None,
                    "zero_volume_candles": None,
                    "missing_volume_candles": None,
                    "error_type": error["error_type"],
                    "error": error["message"],
                }
                continue

            volumes = [candle["volume"] for candle in candles]
            timestamps = [candle["timestamp"] for candle in candles]
            item["intervals"][interval] = {
                "status": "DATA_RETURNED" if candles else "EMPTY",
                "returned_candles": len(candles),
                "earliest_timestamp": timestamps[0] if timestamps else None,
                "latest_timestamp": timestamps[-1] if timestamps else None,
                "zero_volume_candles": sum(value == "0" for value in volumes),
                "missing_volume_candles": sum(value is None for value in volumes),
                "candles": candles
            }
    if report["errors"]:
        report["status"] = "PARTIAL"
    return report

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="asset_economic_registry.json")
    parser.add_argument("--output", default="artifacts/etoro_historical_market_probe.json")
    parser.add_argument("--candles-count", type=int, default=DEFAULT_COUNT)
    args = parser.parse_args()
    if not 1 <= args.candles_count <= 1000:
        parser.error("--candles-count debe estar entre 1 y 1000")
    instruments = load_instrument_ids(Path(args.registry))
    client = EtoroHistoricalMarketDataClient()
    report = run_probe(client, instruments, args.candles_count)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {symbol: {interval: {key: value.get(key) for key in ("status", "returned_candles", "earliest_timestamp", "latest_timestamp")} for interval, value in item["intervals"].items()} for symbol, item in report["assets"].items()}
    print(json.dumps({"output": str(output), "status": report["status"], "errors": len(report["errors"]), "summary": summary}, indent=2))
    return 1 if report["errors"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
