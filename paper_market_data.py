"""Append-only, research-only storage for validated LSE ETF daily bars.

This module is deliberately separate from the live signal and broker paths.
It stores public EODHD market data in a dedicated Upstash key, never account data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

PAPER_MARKET_DATA_SCHEMA_VERSION = 1
PAPER_MARKET_DATA_KEY = "tradingbot:etf_daily_paper_data:v1"
PAPER_MARKET_ASSETS = ("CSPX", "CNDX")
OHLCV_FIELDS = ("open", "high", "low", "close", "volume")


class MarketDataRevisionError(ValueError):
    """Raised when a provider revises a previously captured daily bar."""


def _finite(value, *, positive=False):
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and (number > 0 if positive else number >= 0)


def empty_market_data_state():
    return {
        "schema_version": PAPER_MARKET_DATA_SCHEMA_VERSION,
        "mode": "PUBLIC_MARKET_DATA_PAPER_ONLY",
        "source": "EODHD",
        "calendar": "XLON",
        "assets": {asset: {"symbol": f"{asset}.LSE", "bars": []}
                   for asset in PAPER_MARKET_ASSETS},
        "created_at": None,
        "updated_at": None,
    }


def validate_bar(bar):
    if not isinstance(bar, dict):
        raise ValueError("bar must be an object")
    day = bar.get("date")
    if not isinstance(day, str) or len(day) != 10:
        raise ValueError("bar date must use YYYY-MM-DD")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("bar date is invalid") from exc
    values = {}
    for field in OHLCV_FIELDS:
        value = bar.get(field)
        if not _finite(value, positive=field != "volume"):
            raise ValueError(f"bar {field} is invalid")
        values[field] = float(value)
    if (
        values["high"] < max(values["open"], values["close"], values["low"])
        or values["low"] > min(values["open"], values["close"], values["high"])
    ):
        raise ValueError("bar OHLC envelope is inconsistent")
    return {"date": day, **values}


def merge_asset_bars(state, asset, incoming_bars, *, captured_at):
    """Append new bars; never silently overwrite a previously captured bar."""
    if asset not in PAPER_MARKET_ASSETS:
        raise ValueError("asset is outside the ETF paper-data universe")
    if not isinstance(state, dict) or state.get("schema_version") != PAPER_MARKET_DATA_SCHEMA_VERSION:
        raise ValueError("unsupported paper market-data schema")
    if state.get("mode") != "PUBLIC_MARKET_DATA_PAPER_ONLY":
        raise ValueError("market-data state mode is invalid")
    if not isinstance(state.get("assets"), dict) or asset not in state["assets"]:
        raise ValueError("market-data asset state is invalid")
    if not isinstance(incoming_bars, list) or not incoming_bars:
        raise ValueError("incoming bars must be a non-empty list")
    if not isinstance(captured_at, str) or not captured_at.strip():
        raise ValueError("captured_at is required")

    existing = state["assets"][asset].get("bars")
    if not isinstance(existing, list):
        raise ValueError("stored bars are invalid")
    existing_by_date = {}
    for item in existing:
        validated = validate_bar(item)
        if validated["date"] in existing_by_date:
            raise ValueError("stored market data contains duplicate dates")
        existing_by_date[validated["date"]] = validated

    incoming_by_date = {}
    for item in incoming_bars:
        validated = validate_bar(item)
        day = validated["date"]
        if day in incoming_by_date:
            raise ValueError("incoming market data contains duplicate dates")
        incoming_by_date[day] = validated

    additions = []
    revisions = []
    for day, bar in sorted(incoming_by_date.items()):
        prior = existing_by_date.get(day)
        if prior is None:
            additions.append(bar)
            continue
        for field in OHLCV_FIELDS:
            if not math.isclose(
                float(prior[field]), float(bar[field]),
                rel_tol=1e-10, abs_tol=1e-10,
            ):
                revisions.append({"date": day, "field": field})
    if revisions:
        raise MarketDataRevisionError(
            f"{asset}: provider revised previously captured bars: {revisions[:5]}"
        )

    updated = {
        **state,
        "assets": {
            **state["assets"],
            asset: {
                **state["assets"][asset],
                "symbol": f"{asset}.LSE",
                "bars": sorted(
                    [*existing_by_date.values(), *additions],
                    key=lambda item: item["date"],
                ),
            },
        },
        "created_at": state.get("created_at") or captured_at,
        "updated_at": captured_at,
    }
    return updated, len(additions)


def validate_market_data_state(state):
    if not isinstance(state, dict) or state.get("schema_version") != PAPER_MARKET_DATA_SCHEMA_VERSION:
        raise ValueError("unsupported paper market-data schema")
    if state.get("mode") != "PUBLIC_MARKET_DATA_PAPER_ONLY":
        raise ValueError("market-data state mode is invalid")
    if state.get("source") != "EODHD" or state.get("calendar") != "XLON":
        raise ValueError("market-data source or exchange calendar is invalid")
    assets = state.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(PAPER_MARKET_ASSETS):
        raise ValueError("market-data asset universe is invalid")
    for asset in PAPER_MARKET_ASSETS:
        asset_state = assets[asset]
        if (
            not isinstance(asset_state, dict)
            or asset_state.get("symbol") != f"{asset}.LSE"
        ):
            raise ValueError(f"{asset} listing metadata is invalid")
        bars = asset_state.get("bars")
        if not isinstance(bars, list):
            raise ValueError(f"{asset} bars are invalid")
        dates = [validate_bar(bar)["date"] for bar in bars]
        if dates != sorted(set(dates)):
            raise ValueError(f"{asset} bars must be unique and ordered")
    return state
