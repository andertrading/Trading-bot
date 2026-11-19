
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from config import ASSETS

logger = logging.getLogger(__name__)

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

PROVIDER_MAP = {
    "CSPX": "yahoo",
    "CNDX": "yahoo",
    "TSLA": "twelvedata",
    "NVDA": "twelvedata",
    "AAPL": "twelvedata",
    "MSFT": "twelvedata",
    "TTWO": "twelvedata",
    "BTC": "twelvedata",
    "ETH": "twelvedata",
    "SOL": "twelvedata",
    "XLM": "twelvedata",
    "XRP": "twelvedata",
}

INTERVAL_MAP = {
    "15m": "15min",
    "1d": "1day",
}

YAHOO_INTERVAL_MAP = {
    "15m": "15m",
    "1d": "1d",
}

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

CRYPTO_ASSETS = {"BTC", "ETH", "SOL", "XLM", "XRP"}

TWELVE_DATA_MIN_INTERVAL = 8.5

CACHE_TTL = {
    "15m": 300,
    "1d": 1800,
}

_data_cache = {}
_cache_lock = threading.Lock()

_api_lock = threading.Lock()
_last_api_request = 0.0


def _asset_config(asset):
    config = ASSETS.get(asset)

    if not isinstance(config, dict):
        return None

    if not config.get("enabled", True):
        return None

    return config


def _get_cached_data(asset, interval):
    key = (asset, interval)

    with _cache_lock:
        cached = _data_cache.get(key)

        if cached is None:
            return None

        dataframe, saved_at = cached
        age = time.monotonic() - saved_at

        if age > CACHE_TTL[interval]:
            del _data_cache[key]
            return None

        logger.info("Caché utilizada: %s %s", asset, interval)

        return dataframe.copy()


def _save_cached_data(asset, interval, dataframe):
    key = (asset, interval)

    with _cache_lock:
        _data_cache[key] = (
            dataframe.copy(),
            time.monotonic(),
        )


def _normalise_dataframe(df):
    if df is None or df.empty:
        return None

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.columns = [
        str(col).strip().title()
        for col in df.columns
    ]

    required = ["Open", "High", "Low", "Close"]

    if not all(col in df.columns for col in required):
        logger.error("Faltan columnas OHLC")
        return None

    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df = df[COLUMNS].copy()

    for col in COLUMNS:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=["Open", "High", "Low", "Close"]
    )

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(
            df.index,
            utc=True,
            errors="coerce",
        )

    df = df[~df.index.isna()]

    if df.empty:
        return None

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()

    df = df[
        (df["Open"] > 0)
        & (df["High"] > 0)
        & (df["Low"] > 0)
        & (df["Close"] > 0)
    ]

    valid_ohlc = (
        (df["High"] >= df["Low"])
        & (df["High"] >= df["Open"])
        & (df["High"] >= df["Close"])
        & (df["Low"] <= df["Open"])
        & (df["Low"] <= df["Close"])
    )

    df = df[valid_ohlc]

    return df if not df.empty else None


def _remove_incomplete_candle(df, interval):
    if df is None or df.empty:
        return df

    now = datetime.now(timezone.utc)

    if interval == "15m":
        # Las velas llevan como timestamp su hora de inicio.
        # Conservamos únicamente velas cuyo periodo de 15m
        # ya ha terminado.
        current_period_start = now.replace(
            minute=(now.minute // 15) * 15,
            second=0,
            microsecond=0,
        )

        last_completed_start = pd.Timestamp(
            current_period_start - timedelta(minutes=15)
        )

        return df[df.index <= last_completed_start]

    if interval == "1d":
        # Las velas diarias están etiquetadas a las 00:00 UTC.
        # Excluimos la vela del día que todavía está en curso.
        today = now.date()
        return df[df.index.date < today]

    return df


def _twelvedata_request(params):
    global _last_api_request

    with _api_lock:
        elapsed = time.monotonic() - _last_api_request
        wait_time = TWELVE_DATA_MIN_INTERVAL - elapsed

        if wait_time > 0:
            logger.info(
                "Límite Twelve Data: esperando %.1f segundos",
                wait_time,
            )
            time.sleep(wait_time)

        _last_api_request = time.monotonic()

        return requests.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=30,
        )


def _download_twelvedata(asset, config, interval):
    api_key = os.getenv("TWELVE_DATA_API_KEY")

    if not api_key:
        logger.error("Falta TWELVE_DATA_API_KEY")
        return None

    params = {
        "symbol": asset,
        "interval": INTERVAL_MAP[interval],
        "outputsize": 5000,
        "timezone": "UTC",
        "apikey": api_key,
    }

    try:
        response = _twelvedata_request(params)
        response.raise_for_status()

        payload = response.json()

        if "values" not in payload:
            logger.error(
                "Twelve Data rechazó %s: %s",
                asset,
                payload.get("message", payload.get("code", payload)),
            )
            return None

        df = pd.DataFrame(payload["values"])

        if df.empty or "datetime" not in df.columns:
            logger.error(
                "Twelve Data devolvió datos vacíos o sin fechas para %s",
                asset,
            )
            return None

        df = df.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )

        df["datetime"] = pd.to_datetime(
            df["datetime"],
            utc=True,
            errors="coerce",
        )

        df = df.dropna(subset=["datetime"])
        df = df.set_index("datetime")

        df = _normalise_dataframe(df)
        df = _remove_incomplete_candle(df, interval)

        if df is None or len(df) < 220:
            logger.warning(
                "Histórico insuficiente para %s %s",
                asset,
                interval,
            )
            return None

        logger.info(
            "Twelve Data OK: %s %s, %s velas",
            asset,
            interval,
            len(df),
        )

        return df

    except requests.RequestException as exc:
        logger.error(
            "Error de conexión Twelve Data para %s: %s",
            asset,
            exc,
        )
        return None

    except Exception:
        logger.exception(
            "Error procesando Twelve Data para %s",
            asset,
        )
        return None


def _download_yahoo(asset, config, interval):
    ticker = config.get("yahoo")

    if not ticker:
        logger.error("No hay ticker de Yahoo para %s", asset)
        return None

    now = datetime.now(timezone.utc)

    if interval == "15m":
        start = now - timedelta(days=60)
    else:
        start = now - timedelta(days=3650)

    params = {
        "period1": int(start.timestamp()),
        "period2": int(now.timestamp()),
        "interval": YAHOO_INTERVAL_MAP[interval],
        "events": "history",
        "includeAdjustedClose": "true",
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(
            f"{YAHOO_CHART_URL}/{ticker}",
            params=params,
            headers=headers,
            timeout=25,
        )

        if response.status_code == 429:
            logger.error(
                "Yahoo limitó las peticiones para %s (HTTP 429)",
                ticker,
            )
            return None

        response.raise_for_status()

        payload = response.json()
        chart = payload.get("chart", {})

        if chart.get("error"):
            logger.error(
                "Yahoo rechazó %s: %s",
                ticker,
                chart["error"],
            )
            return None

        results = chart.get("result")

        if not results:
            logger.error(
                "Yahoo no devolvió resultados para %s",
                ticker,
            )
            return None

        result = results[0]
        timestamps = result.get("timestamp", [])
        indicators = result.get("indicators", {})
        quotes = indicators.get("quote", [])

        if not timestamps or not quotes:
            logger.error(
                "Yahoo devolvió datos incompletos para %s",
                ticker,
            )
            return None

        quote = quotes[0]

        df = pd.DataFrame({
            "Open": quote.get("open", []),
            "High": quote.get("high", []),
            "Low": quote.get("low", []),
            "Close": quote.get("close", []),
            "Volume": quote.get("volume", []),
        }, index=pd.to_datetime(
            timestamps,
            unit="s",
            utc=True,
        ))

        df = _normalise_dataframe(df)
        df = _remove_incomplete_candle(df, interval)

        if df is None or len(df) < 220:
            logger.warning(
                "Histórico insuficiente en Yahoo para %s %s",
                ticker,
                interval,
            )
            return None

        logger.info(
            "Yahoo OK: %s %s, %s velas",
            ticker,
            interval,
            len(df),
        )

        return df

    except requests.RequestException as exc:
        logger.error(
            "Error HTTP de Yahoo para %s: %s",
            ticker,
            exc,
        )
        return None

    except Exception:
        logger.exception(
            "Error procesando Yahoo Finance para %s",
            ticker,
        )
        return None


def download_data(asset, interval="15m"):
    if interval not in INTERVAL_MAP:
        logger.error(
            "Temporalidad no admitida: %s",
            interval,
        )
        return None

    config = _asset_config(asset)

    if config is None:
        logger.warning(
            "Activo desconocido o desactivado: %s",
            asset,
        )
        return None

    cached = _get_cached_data(asset, interval)

    if cached is not None:
        return cached

    provider = PROVIDER_MAP.get(asset)

    if provider == "twelvedata":
        dataframe = _download_twelvedata(
            asset,
            config,
            interval,
        )

    elif provider == "yahoo":
        dataframe = _download_yahoo(
            asset,
            config,
            interval,
        )

    else:
        logger.error(
            "No hay proveedor definido para %s",
            asset,
        )
        return None

    if dataframe is not None and not dataframe.empty:
        _save_cached_data(
            asset,
            interval,
            dataframe,
        )

    return dataframe


def get_data_timestamp(df):
    if df is None or df.empty:
        return None

    timestamp = df.index[-1]

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")

    return timestamp


def is_data_fresh(
    df,
    max_age_minutes=30,
    asset=None,
    interval="15m",
):
    timestamp = get_data_timestamp(df)

    if timestamp is None:
        logger.warning(
            "DATA FRESHNESS REJECTED | %s | %s | sin timestamp",
            asset,
            interval,
        )
        return False

    now = pd.Timestamp.now(tz="UTC")

    age_minutes = (
        now - timestamp
    ).total_seconds() / 60

    if age_minutes < 0:
        logger.warning(
            "DATA FRESHNESS REJECTED | %s | %s | timestamp futuro: %s",
            asset,
            interval,
            timestamp,
        )
        return False

    if asset in CRYPTO_ASSETS:
        # El timestamp representa el inicio de la vela.
        # Damos margen para el cierre y la publicación del proveedor.
        limit = 45 if interval == "15m" else 48 * 60
    else:
        # Los mercados tradicionales pueden estar cerrados.
        limit = max(max_age_minutes, 72 * 60)

    is_fresh = age_minutes <= limit

    logger.info(
        "DATA FRESHNESS | %s | %s | timestamp=%s | age=%.1f min | limit=%.1f min | fresh=%s",
        asset,
        interval,
        timestamp.isoformat(),
        age_minutes,
        limit,
        is_fresh,
    )

    return is_fresh
