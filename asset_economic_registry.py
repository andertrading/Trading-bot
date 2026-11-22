"""Per-instrument economic evidence registry for the daily MVP.

The registry is a manual evidence ledger. It never reads broker accounts,
balances, holdings, positions or orders. Missing evidence fails closed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from config import ASSETS, ACCOUNT_CURRENCY

REGISTRY_PATH = Path(__file__).with_name("asset_economic_registry.json")
REQUIRED_ASSET_KEYS = set(ASSETS)
VALID_EXECUTION_TYPES = {"REAL", "CFD"}


def load_registry(path: Path | str = REGISTRY_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        registry = json.load(handle)
    validate_registry(registry)
    return registry


def _valid_number(value, *, positive=False):
    if isinstance(value, bool) or value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and (number > 0 if positive else number >= 0)


def validate_registry(registry: dict) -> None:
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        raise ValueError("unsupported economic registry schema")
    if registry.get("policy") != "DAILY_LONG_ONLY_UNLEVERAGED_REAL":
        raise ValueError("economic registry policy mismatch")
    assets = registry.get("assets")
    if not isinstance(assets, dict) or set(assets) != REQUIRED_ASSET_KEYS:
        raise ValueError("economic registry must cover exactly the configured assets")
    for symbol, item in assets.items():
        if not isinstance(item, dict):
            raise ValueError(f"{symbol}: registry entry must be an object")
        for section in ("instrument", "execution", "fees", "spread", "price_basis", "minimum_order", "fx"):
            if not isinstance(item.get(section), dict):
                raise ValueError(f"{symbol}: missing {section} evidence section")
        instrument = item["instrument"]
        configured = ASSETS[symbol]
        if instrument.get("currency") != configured.get("currency"):
            raise ValueError(f"{symbol}: quote currency differs from config")
        if instrument.get("type") != configured.get("type"):
            raise ValueError(f"{symbol}: instrument type differs from config")
        if not isinstance(item["execution"].get("confirmed"), bool):
            raise ValueError(f"{symbol}: execution confirmation must be boolean")
        if not isinstance(item["fees"].get("confirmed"), bool):
            raise ValueError(f"{symbol}: fee confirmation must be boolean")
        if not isinstance(item["spread"].get("confirmed"), bool):
            raise ValueError(f"{symbol}: spread confirmation must be boolean")
        if not isinstance(item["price_basis"].get("confirmed"), bool):
            raise ValueError(f"{symbol}: price-basis confirmation must be boolean")
        if not isinstance(item["minimum_order"].get("confirmed"), bool):
            raise ValueError(f"{symbol}: minimum-order confirmation must be boolean")
        if not isinstance(item["fx"].get("confirmed"), bool):
            raise ValueError(f"{symbol}: FX confirmation must be boolean")


def valid_fee_side(side: dict) -> bool:
    if not isinstance(side, dict):
        return False
    model, unit, value = side.get("model"), side.get("unit"), side.get("value")
    if model not in {"FIXED", "PERCENT_NOTIONAL", "ZERO"}:
        return False
    if model == "ZERO":
        return not isinstance(value, bool) and value == 0 and unit in {"USD", "PERCENT"}
    expected_unit = "USD" if model == "FIXED" else "PERCENT"
    return unit == expected_unit and _valid_number(value)


def classify_registry_asset(symbol: str, registry: dict | None = None) -> dict:
    registry = registry if registry is not None else load_registry()
    validate_registry(registry)
    if symbol not in registry["assets"]:
        return {"asset": symbol, "status": "INELIGIBLE", "blockers": ["asset_not_configured"]}
    item = registry["assets"][symbol]
    blockers = []
    execution = item["execution"]
    execution_type = str(execution.get("type") or "").upper()
    if not execution.get("confirmed") or not execution.get("source") or not execution.get("observed_at"):
        blockers.append("execution_type_evidence_missing")
    elif execution_type != "REAL":
        blockers.append("mvp_requires_REAL_execution")

    fees = item["fees"]
    if (
        not fees.get("confirmed") or not fees.get("source") or not fees.get("observed_at")
        or not valid_fee_side(fees.get("entry"))
        or not valid_fee_side(fees.get("exit"))
    ):
        blockers.append("round_trip_fee_evidence_missing")

    spread = item["spread"]
    if (
        not spread.get("confirmed") or not spread.get("source") or not spread.get("observed_at")
        or not _valid_number(spread.get("bid_ask_bps"))
    ):
        blockers.append("observed_bid_ask_spread_evidence_missing")

    basis = item["price_basis"]
    if not basis.get("confirmed") or not basis.get("source") or not basis.get("observed_at"):
        blockers.append("execution_price_basis_evidence_missing")

    minimum = item["minimum_order"]
    if (
        not minimum.get("confirmed") or not minimum.get("source") or not minimum.get("observed_at")
        or not _valid_number(minimum.get("minimum_notional"), positive=True)
        or not isinstance(minimum.get("fractional_allowed"), bool)
    ):
        blockers.append("minimum_order_rules_evidence_missing")

    fx = item["fx"]
    if not fx.get("confirmed"):
        blockers.append("fx_conversion_evidence_missing")
    elif fx.get("required"):
        if (
            not fx.get("source") or not fx.get("observed_at")
            or not _valid_number(fx.get("conversion_fee_pct"))
            or not _valid_number(fx.get("quote_to_account_rate"), positive=True)
        ):
            blockers.append("fx_conversion_evidence_missing")

    return {
        "asset": symbol,
        "status": "ELIGIBLE" if not blockers else (
            "INELIGIBLE" if "mvp_requires_REAL_execution" in blockers else "COSTS_UNVERIFIED"
        ),
        "blockers": blockers,
        "execution_type": execution_type or None,
        "account_currency": ACCOUNT_CURRENCY,
        "account_portfolio_data_accessed": False,
    }


def build_registry_report(registry: dict | None = None) -> dict:
    registry = registry if registry is not None else load_registry()
    validate_registry(registry)
    results = [classify_registry_asset(symbol, registry) for symbol in sorted(registry["assets"])]
    return {
        "schema_version": 1,
        "policy": registry["policy"],
        "account_portfolio_data_accessed": False,
        "summary": {
            status: sum(row["status"] == status for row in results)
            for status in ("ELIGIBLE", "INELIGIBLE", "COSTS_UNVERIFIED")
        },
        "assets": results,
    }


if __name__ == "__main__":
    print(json.dumps(build_registry_report(), indent=2, sort_keys=True))
