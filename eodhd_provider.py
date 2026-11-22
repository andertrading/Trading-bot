"""EODHD daily data adapter for the verified CSPX and CNDX London listings.

The free EODHD tier supplies end-of-day history only. This module is
intentionally restricted to the two verified USD ETF listings.
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import exchange_calendars as xcals

logger = logging.getLogger(__name__)

EODHD_EOD_URL = "https://eodhd.com/api/eod"
EODHD_SPLITS_URL = "https://eodhd.com/api/splits"
EODHD_SYMBOLS = {
    # Verified by ISIN in the EODHD listing diagnostic:
    # CSPX: IE00B5BMR087; CNDX: IE00B53SZB19.
    "CSPX": "CSPX.LSE",
    "CNDX": "CNDX.LSE",
}
CACHE_TTL_SECONDS = 6 * 60 * 60
MINIMUM_DAILY_BARS = 220
_cache = {}
_split_cache = {}
_cache_lock = threading.Lock()


def _cached(asset):
    with _cache_lock:
        entry = _cache.get(asset)
        if entry is None:
            return None
        frame, saved_at = entry
        if time.monotonic() - saved_at > CACHE_TTL_SECONDS:
            del _cache[asset]
            return None
        return frame.copy()


def _save_cache(asset, frame):
    with _cache_lock:
        _cache[asset] = (frame.copy(), time.monotonic())


def _download_split_events(asset, symbol, start_date, end_date, api_key):
    """Fetch and validate split events for split-adjusted daily OHLC."""
    with _cache_lock:
        cached = _split_cache.get(asset)
        if cached and time.monotonic() - cached[1] <= CACHE_TTL_SECONDS:
            return list(cached[0])

    try:
        response = requests.get(
            f"{EODHD_SPLITS_URL}/{symbol}",
            params={
                "api_token": api_key,
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
                "fmt": "json",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        logger.error("Error HTTP al descargar splits EODHD para %s", asset)
        return None
    except (ValueError, TypeError):
        logger.error("Respuesta de splits EODHD inválida para %s", asset)
        return None

    if not isinstance(payload, list):
        logger.error("Formato de splits EODHD inválido para %s", asset)
        return None

    events = []
    for item in payload:
        if not isinstance(item, dict):
            logger.error("Evento de split inválido para %s", asset)
            return None
        try:
            event_date = pd.Timestamp(item["date"])
            if pd.isna(event_date):
                raise ValueError("invalid split date")
            ratio = str(item["split"]).strip()
            numerator_text, denominator_text = ratio.split("/", 1)
            numerator = float(numerator_text)
            denominator = float(denominator_text)
            if (
                event_date.tzinfo is not None
                or not np.isfinite(numerator)
                or not np.isfinite(denominator)
                or numerator <= 0
                or denominator <= 0
            ):
                raise ValueError("invalid split fields")
            events.append((event_date.date(), numerator, denominator))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            logger.error("Ratio o fecha de split inválidos para %s", asset)
            return None

    events.sort(key=lambda event: event[0])
    with _cache_lock:
        _split_cache[asset] = (list(events), time.monotonic())
    return events


def _apply_split_adjustment(frame, events):
    """Convert historical OHLC to current share units, excluding dividends."""
    adjusted = frame.copy()
    for event_date, new_shares, old_shares in events:
        price_factor = old_shares / new_shares
        before_split = adjusted.index.date < event_date
        if before_split.any():
            adjusted.loc[before_split, ["Open", "High", "Low", "Close"]] *= price_factor
    adjusted.attrs["price_adjustment_policy"] = "split_adjusted_ohlc_no_dividend_adjustment"
    adjusted.attrs["split_events_applied"] = len(events)
    return adjusted

def download_eodhd_daily(asset, configured_currency, remove_incomplete, has_daily_gaps):
    """Download, normalize and validate one ETF daily history.

    The API token is never included in logs or exception text. No search
    endpoint is called at runtime: the exact listings were independently
    resolved by ISIN and are pinned above to conserve the free-tier quota.
    """
    if asset not in EODHD_SYMBOLS:
        logger.error("EODHD no admite el activo solicitado: %s", asset)
        return None

    if str(configured_currency).upper().strip() != "USD":
        logger.error("Moneda configurada incompatible con EODHD para %s", asset)
        return None

    cached = _cached(asset)
    if cached is not None:
        return cached

    api_key = os.getenv("EODHD_API_KEY")
    if not api_key:
        logger.error("Falta EODHD_API_KEY; se bloquea el histórico diario de %s", asset)
        return None

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=370)
    params = {
        "api_token": api_key,
        "from": start_date.isoformat(),
        "to": today.isoformat(),
        "period": "d",
        "fmt": "json",
    }
    symbol = EODHD_SYMBOLS[asset]

    try:
        response = requests.get(
            f"{EODHD_EOD_URL}/{symbol}",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        # Do not log the exception: request URLs may contain the API token.
        logger.error("Error HTTP de EODHD para %s", asset)
        return None
    except (ValueError, TypeError):
        logger.error("Respuesta JSON inválida de EODHD para %s", asset)
        return None

    if not isinstance(payload, list) or not payload:
        logger.error("EODHD no devolvió histórico diario para %s", asset)
        return None

    frame = pd.DataFrame(payload)
    required = ["date", "open", "high", "low", "close"]
    if any(column not in frame.columns for column in required):
        logger.error("EODHD devolvió campos OHLC incompletos para %s", asset)
        return None

    frame = frame.rename(columns={
        "date": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    })
    frame["Date"] = pd.to_datetime(frame["Date"], utc=True, errors="coerce")
    if frame["Date"].isna().any():
        logger.error("EODHD devolvió fechas inválidas para %s", asset)
        return None
    frame = frame.set_index("Date").sort_index()

    # EODHD can return stale/synthetic bars on UK exchange holidays.
    # These are not executable LSE sessions and must never enter indicators.
    calendar = xcals.get_calendar("XLON")
    session_mask = [
        bool(calendar.is_session(pd.Timestamp(timestamp.date())))
        for timestamp in frame.index
    ]
    non_session_dates = [
        str(timestamp.date())
        for timestamp, is_session in zip(frame.index, session_mask)
        if not is_session
    ]
    if non_session_dates:
        logger.warning(
            "EODHD NON-SESSION DAILY BARS EXCLUDED | %s | dates=%s",
            asset,
            non_session_dates,
        )
        frame = frame.loc[session_mask]

    if frame.index.has_duplicates:
        logger.error("EODHD devolvió fechas duplicadas para %s", asset)
        return None

    for column in ("Open", "High", "Low", "Close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        values = frame[column].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            logger.error("EODHD devolvió precios inválidos en %s para %s", column, asset)
            return None

    frame["Volume"] = pd.to_numeric(
        frame.get("Volume", pd.Series(0.0, index=frame.index)),
        errors="coerce",
    ).fillna(0.0)
    frame.loc[frame["Volume"] < 0, "Volume"] = 0.0

    split_events = _download_split_events(
        asset, symbol, start_date, today, api_key
    )
    if split_events is None:
        return None
    frame = _apply_split_adjustment(frame, split_events)

    valid_ohlc = (
        (frame["High"] >= frame["Low"])
        & (frame["High"] >= frame["Open"])
        & (frame["High"] >= frame["Close"])
        & (frame["Low"] <= frame["Open"])
        & (frame["Low"] <= frame["Close"])
    )
    if not valid_ohlc.all():
        logger.error("EODHD devolvió velas OHLC incoherentes para %s", asset)
        return None

    frame = frame[["Open", "High", "Low", "Close", "Volume"]]
    frame = remove_incomplete(frame, "1d", asset)
    if frame is None or len(frame) < MINIMUM_DAILY_BARS:
        logger.error(
            "Histórico EODHD insuficiente para %s: mínimo=%s",
            asset,
            MINIMUM_DAILY_BARS,
        )
        return None

    if has_daily_gaps(frame, asset):
        logger.error("Histórico EODHD rechazado por huecos para %s", asset)
        return None

    _save_cache(asset, frame)
    logger.info(
        "EODHD OK: %s (%s), %s velas diarias; splits=%s",
        asset,
        symbol,
        len(frame),
        len(split_events),
    )
    return frame.copy()
