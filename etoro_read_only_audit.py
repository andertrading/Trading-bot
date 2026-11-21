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
from etoro_position_risk import calculate_account_gross_stop_risk_from_client


def _asset_candidates(symbol: str, asset: Mapping[str, Any]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for value in (symbol, asset.get("yahoo")):
        if isinstance(value, str) and value.strip():
            candidate = ("internalSymbolFull", value.strip())
            if candidate not in candidates:
                candidates.append(candidate)
    name = asset.get("name")
    if isinstance(name, str) and name.strip():
        candidate = ("displayname", name.strip())
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _resolve_asset(client: EtoroReadOnlyClient, symbol: str, asset: Mapping[str, Any]):
    for field, value in _asset_candidates(symbol, asset):
        try:
            response = client.search_instruments({field: value})
        except Exception:
            return None
        try:
            return resolve_unique_instrument(response, value)
        except Exception:
            continue
    return None


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
    try:
        snapshot = client.get_account_snapshot(account_currency)
        account_valid = True
    except Exception:
        account_valid = False

    enabled_assets = {
        symbol: asset
        for symbol, asset in assets.items()
        if isinstance(asset, Mapping) and asset.get("enabled") is True
    }

    resolved: dict[str, Mapping[str, Any]] = {}
    for symbol, asset in enabled_assets.items():
        item = _resolve_asset(client, symbol, asset)
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
            risk_diagnostic = calculate_account_gross_stop_risk_from_client(
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
            "expected_asset_type": asset.get("type"),
            "candidate_found": symbol in resolved,
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
