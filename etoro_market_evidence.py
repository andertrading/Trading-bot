"""Read-only eToro market evidence capture. Never accesses account portfolio or submits orders."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any, Mapping

from config import ASSETS
from etoro_read_only import (
    EtoroAPIError, EtoroConfigurationError, EtoroCostEstimate,
    EtoroInstrumentEligibility, EtoroQuote, EtoroReadOnlyClient,
    resolve_unique_instrument,
    resolve_unique_instrument_by_symbol,
)

class EtoroMarketEvidenceClient(EtoroReadOnlyClient):
    """Narrower client: only market search, quotes, eligibility and hypothetical open costs."""
    MARKET_ONLY_ENDPOINTS = frozenset({
        ("GET", "/api/v2/market-data/instruments"),
        ("GET", "/api/v1/market-data/search"),
        ("GET", "/api/v1/market-data/instruments/rates"),
        ("POST", "/api/v2/trading/info/costs"),
        ("POST", "/api/v2/trading/info/eligibility"),
    })

    def _request(self, method: str, path: str, **kwargs: Any):
        if (method, path) not in self.MARKET_ONLY_ENDPOINTS:
            raise EtoroAPIError("Endpoint bloqueado: este recolector no accede a cartera, saldos, posiciones, órdenes ni historial.")
        return super()._request(method, path, **kwargs)


def _instrument_type_matches(configured_type: Any, etoro_type: Any) -> bool:
    configured = str(configured_type or "").strip().upper()
    actual = "".join(
        character for character in str(etoro_type or "").casefold()
        if character.isalnum()
    )
    accepted = {
        "STOCK": {"stock", "stocks"},
        "ETF": {"etf", "etfs", "exchangetradedfund"},
        "CRYPTO": {"crypto", "cryptocurrency"},
    }
    return actual in accepted.get(configured, set())


def _resolve_instrument(client: Any, symbol: str) -> Mapping[str, Any]:
    """Resolve an exact eToro symbol, falling back to the documented v1 search."""
    try:
        response = client.search_instrument_by_symbol(symbol)
        return resolve_unique_instrument_by_symbol(response, symbol)
    except EtoroAPIError as exc:
        message = str(exc)
        # The v2 exact-symbol endpoint may not index every listed ETF. Retry
        # only a not-found/empty-result case through eToro's v1 exact filter;
        # never fuzzy-match or silently substitute another instrument.
        if "HTTP 404" not in message and "encontrados: 0" not in message:
            raise
        response = client.search_instruments({"internalSymbolFull": symbol})
        return resolve_unique_instrument(response, symbol)


def _real_unleveraged_long_config(item: EtoroInstrumentEligibility) -> Mapping[str, Any] | None:
    if not item.allow_open_position:
        return None
    matches = [
        cfg for cfg in item.leverage_configs
        if isinstance(cfg, Mapping)
        and str(cfg.get("settlementType", "")).casefold() == "real"
        and cfg.get("direction") == "LONG"
        and cfg.get("isPotential") is False
        and isinstance(cfg.get("leverageValues"), list)
        and 1 in cfg["leverageValues"]
    ]
    return matches[0] if len(matches) == 1 else None


def collect_market_evidence(client: Any, *, symbols: Mapping[str, Mapping[str, Any]] | None = None,
                            now_utc: datetime | None = None,
                            sample_amount_usd: Decimal = Decimal("100")) -> dict[str, Any]:
    symbols = ASSETS if symbols is None else symbols
    clock_is_injected = now_utc is not None
    now_utc = datetime.now(timezone.utc) if now_utc is None else now_utc
    if now_utc.tzinfo is None or now_utc.utcoffset() != timezone.utc.utcoffset(now_utc):
        raise ValueError("now_utc debe estar expresado en UTC")
    if not isinstance(sample_amount_usd, Decimal) or not sample_amount_usd.is_finite() or sample_amount_usd <= 0:
        raise ValueError("sample_amount_usd debe ser un Decimal positivo")

    report = {
        "schema_version": 1, "captured_at": now_utc.isoformat(),
        "source": "eToro Public API", "mode": "READ_ONLY_MARKET_EVIDENCE",
        "account_data_accessed": False, "order_actions_performed": False,
        "sample_amount_usd_default": str(sample_amount_usd),
        "cost_scope": "HYPOTHETICAL OPEN ONLY; NOT A ROUND-TRIP COST",
        "assets": {},
    }
    resolved = {}
    for asset in symbols:
        record = {"configured_asset": asset, "instrument": None, "eligibility": None,
                  "quote": None, "hypothetical_open_cost": None, "errors": []}
        report["assets"][asset] = record
        try:
            etoro_symbol = symbols[asset].get("etoro_symbol", asset)
            if not isinstance(etoro_symbol, str) or not etoro_symbol.strip():
                raise EtoroAPIError("etoro_symbol debe ser un símbolo no vacío")
            instrument = _resolve_instrument(client, etoro_symbol.strip())
            expected_type = symbols[asset].get("type")
            if not _instrument_type_matches(
                expected_type, instrument.get("instrumentType")
            ):
                raise EtoroAPIError(
                    "instrument_resolution: eToro instrument type does not match "
                    f"configured asset type ({expected_type!r} vs "
                    f"{instrument.get('instrumentType')!r})"
                )
            resolved[asset] = instrument
            record["instrument"] = {
                "instrument_id": instrument["instrumentId"],
                "symbol": instrument["internalSymbolFull"],
                "display_name": instrument["displayname"],
                "instrument_type": instrument["instrumentType"],
            }
        except Exception as exc:
            record["errors"].append(f"instrument_resolution: {type(exc).__name__}: {exc}")

    instrument_owners = {}
    for asset, instrument in resolved.items():
        instrument_owners.setdefault(instrument["instrumentId"], []).append(asset)
    duplicate_assets = set()
    for instrument_id, owners in instrument_owners.items():
        if len(owners) > 1:
            duplicate_assets.update(owners)
            message = (
                f"instrument_resolution: duplicate eToro instrument ID {instrument_id} "
                f"resolved for configured assets {', '.join(sorted(owners))}"
            )
            for asset in owners:
                report["assets"][asset]["errors"].append(message)
    if duplicate_assets:
        resolved = {
            asset: instrument for asset, instrument in resolved.items()
            if asset not in duplicate_assets
        }

    # Isolate eligibility parsing per instrument. A single malformed
    # leverage configuration must not erase valid records for every other
    # instrument in the batch.
    eligibility_by_id = {}
    for asset, instrument in resolved.items():
        iid = instrument["instrumentId"]
        try:
            eligibility = client.get_validated_instrument_eligibility([iid]).get(iid)
            if eligibility is None:
                raise EtoroAPIError(
                    "La respuesta no contiene la elegibilidad del instrumento solicitado"
                )
            eligibility_by_id[iid] = eligibility
        except Exception as exc:
            report["assets"][asset]["errors"].append(
                f"eligibility: {type(exc).__name__}: {exc}"
            )

    for asset, instrument in resolved.items():
        record = report["assets"][asset]
        iid = instrument["instrumentId"]
        eligibility = eligibility_by_id.get(iid)
        if eligibility is None and not any(
            error.startswith("eligibility:") for error in record["errors"]
        ):
            record["errors"].append(
                "eligibility: no validated eligibility record returned for resolved instrument"
            )
        real_cfg = _real_unleveraged_long_config(eligibility) if eligibility else None
        minimums = [sample_amount_usd]
        if eligibility and eligibility.min_position_exposure is not None:
            minimums.append(eligibility.min_position_exposure)
        if real_cfg and real_cfg.get("minPositionAmount") is not None:
            minimums.append(Decimal(str(real_cfg["minPositionAmount"])))
        sample_amount = max(minimums)

        if eligibility:
            record["eligibility"] = {
                "allow_open_position": eligibility.allow_open_position,
                "supports_long_any_settlement": eligibility.supports_long,
                "supports_real_unleveraged_long": real_cfg is not None,
                "minimum_position_exposure_usd": str(eligibility.min_position_exposure) if eligibility.min_position_exposure is not None else None,
                "minimum_real_order_amount_usd": str(real_cfg.get("minPositionAmount")) if real_cfg else None,
                "maximum_units_per_order": str(eligibility.max_units_per_order) if eligibility.max_units_per_order is not None else None,
                "units_quantity_type": eligibility.units_quantity_type,
                "allowed_order_quantity_type": eligibility.allowed_order_quantity_type,
                "order_fill_behavior_type": eligibility.order_fill_behavior_type,
                "trade_unit_type": eligibility.trade_unit_type,
                "real_long_leverage_values": real_cfg.get("leverageValues") if real_cfg else [],
            }

        try:
            # Use a fresh reference immediately before each network request.
            # The API can return timestamps several seconds after the report's
            # initial captured_at; reusing that initial timestamp falsely marks
            # valid crypto quotes and cost estimates as future-dated.
            quote_reference_now = (
                now_utc if clock_is_injected else datetime.now(timezone.utc)
            )
            quote = client.get_realtime_quotes(
                [iid], quote_reference_now,
                asset_calendars={iid: symbols[asset].get("market_calendar", "24/7")},
            )[iid]
            midpoint = (quote.bid + quote.ask) / Decimal("2")
            spread_absolute = quote.ask - quote.bid
            spread_bps = (
                spread_absolute / midpoint * Decimal("10000")
                if midpoint > 0 else None
            )
            quote_age_reference = (
                now_utc if clock_is_injected else datetime.now(timezone.utc)
            )
            quote_age_seconds = (quote_age_reference - quote.timestamp).total_seconds()
            record["quote"] = {
                "bid": str(quote.bid),
                "ask": str(quote.ask),
                "midpoint": str(midpoint),
                "spread_absolute": str(spread_absolute),
                "spread_bps": format(spread_bps.normalize(), "f") if spread_bps is not None else None,
                "spread_evidence_scope": "CURRENT_SNAPSHOT_ONLY",
                "timestamp": quote.timestamp.isoformat(),
                "age_seconds": quote_age_seconds,
                "quote_type": quote.quote_type,
            }
        except Exception as exc:
            record["errors"].append(f"quote: {type(exc).__name__}: {exc}")

        if eligibility is None:
            continue
        if real_cfg is None:
            record["hypothetical_open_cost"] = {
                "status": "SKIPPED",
                "reason": "No confirmed REAL, LONG, 1x configuration for this account.",
            }
            continue

        payload = {
            "action": "open", "transaction": "buy", "instrumentId": iid,
            "settlementType": "real", "orderType": "mkt", "leverage": 1,
            "amount": str(sample_amount), "orderCurrency": "usd",
        }
        try:
            cost_reference_now = (
                now_utc if clock_is_injected else datetime.now(timezone.utc)
            )
            estimate: EtoroCostEstimate = client.get_validated_what_if_costs(
                payload, cost_reference_now
            )
            components = {key: str(value) for key, value in sorted(estimate.costs.items())}
            record["hypothetical_open_cost"] = {
                "scope": "HYPOTHETICAL_OPEN_ONLY", "sample_amount_usd": str(sample_amount),
                "instrument_id": estimate.instrument_id, "symbol": estimate.symbol,
                "cost_components_usd": components,
                "total_reported_open_cost_usd": str(sum(estimate.costs.values(), Decimal("0"))),
                "last_updated": estimate.last_updated.isoformat(),
                "round_trip_cost_confirmed": False,
            }
        except Exception as exc:
            record["errors"].append(f"hypothetical_open_cost: {type(exc).__name__}: {exc}")

    failures = sum(bool(item["errors"]) for item in report["assets"].values())
    report["summary"] = {
        "configured_assets": len(report["assets"]), "resolved_instruments": len(resolved),
        "assets_with_errors": failures,
        "status": "COMPLETE" if failures == 0 and len(resolved) == len(report["assets"]) else "PARTIAL",
        "net_performance_validation": "NOT_VALIDATED",
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Captura eToro de solo lectura.")
    parser.add_argument("--output", default=os.getenv("ETORO_EVIDENCE_OUTPUT", "artifacts/etoro_market_evidence.json"))
    parser.add_argument("--sample-amount-usd", default="100")
    args = parser.parse_args()
    try:
        client = EtoroMarketEvidenceClient()
        report = collect_market_evidence(client, sample_amount_usd=Decimal(args.sample_amount_usd))
    except (EtoroConfigurationError, EtoroAPIError, ValueError) as exc:
        print(f"Captura no iniciada/completada: {type(exc).__name__}: {exc}")
        return 2
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"Informe guardado: {output}")
    return 0 if report["summary"]["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
