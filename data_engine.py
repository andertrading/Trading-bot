
import logging
import os
import threading
import time
from datetime import datetime, timedelta, time as datetime_time, timezone
from zoneinfo import ZoneInfo

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
LONDON_ASSETS = {"CSPX", "CNDX"}

CRYPTO_SYMBOLS = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "XLM": "XLM/USD",
    "XRP": "XRP/USD",
}

MARKET_CONFIG = {
    "US": {
        "timezone": "America/New_York",
        "open": datetime_time(9, 30),
        "close": datetime_time(16, 0),
    },
    "LONDON": {
        "timezone": "Europe/London",
        "open": datetime_time(8, 0),
        "close": datetime_time(16, 30),
    },
}

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


def _market_for_asset(asset):
    if asset in LONDON_ASSETS:
        return MARKET_CONFIG["LONDON"]

    return MARKET_CONFIG["US"]


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


def _previous_weekday(day):
    day = day - timedelta(days=1)

    while day.weekday() >= 5:
        day -= timedelta(days=1)

    return day


def _session_for_asset(asset):
    market = _market_for_asset(asset)

    return (
        ZoneInfo(market["timezone"]),
        market["open"],
        market["close"],
    )


def _last_expected_intraday_timestamp(asset, now_utc):
    tz, opening, closing = _session_for_asset(asset)

    now_local = now_utc.astimezone(tz)
    session_date = now_local.date()

    open_dt = datetime.combine(
        session_date,
        opening,
        tzinfo=tz,
    )

    close_dt = datetime.combine(
        session_date,
        closing,
        tzinfo=tz,
    )

    if now_local.weekday() >= 5 or now_local < open_dt:
        previous_day = _previous_weekday(session_date)

        while True:
            previous_close = datetime.combine(
                previous_day,
                closing,
                tzinfo=tz,
            )

            if previous_close.weekday() < 5:
                return previous_close - timedelta(minutes=15)

            previous_day = _previous_weekday(previous_day)

    if now_local >= close_dt:
        return close_dt - timedelta(minutes=15)

    elapsed = (now_local - open_dt).total_seconds()

    completed_intervals = int(elapsed // 900)

    if completed_intervals <= 0:
        previous_day = _previous_weekday(session_date)

        previous_close = datetime.combine(
            previous_day,
            closing,
            tzinfo=tz,
        )

        return previous_close - timedelta(minutes=15)

    expected = (
        open_dt
        + timedelta(minutes=15 * (completed_intervals - 1))
    )

    return expected


def _remove_incomplete_candle(df, interval, asset=None):
    if df is None or df.empty:
        return df

    now = datetime.now(timezone.utc)

    if interval == "15m":
        if asset in CRYPTO_ASSETS:
            current_period_start = now.replace(
                minute=(now.minute // 15) * 15,
                second=0,
                microsecond=0,
            )

            last_completed_start = pd.Timestamp(
                current_period_start - timedelta(minutes=15)
            )

        else:
            if asset is None:
                return df

            expected = _last_expected_intraday_timestamp(
                asset,
                now,
            )

            last_completed_start = pd.Timestamp(
                expected.astimezone(timezone.utc)
            )

        return df[df.index <= last_completed_start]

    if interval == "1d":
        if asset is None:
            return df

        # Comparación diaria consistente en UTC.
        # Se excluye la vela del día UTC actual.
        return df[df.index.date < now.date()]

    return df


def _log_dataframe_timestamps(provider, asset, interval, df):
    if df is None or df.empty:
        logger.warning(
            "DATA TIMESTAMPS | %s | %s | %s | sin datos",
            provider,
            asset,
            interval,
        )
        return

    first_timestamp = df.index[0]
    last_timestamp = df.index[-1]

    age_minutes = (
        pd.Timestamp.now(tz="UTC") - last_timestamp
    ).total_seconds() / 60

    logger.info(
        "DATA TIMESTAMPS | %s | %s | %s | first=%s | last=%s | age=%.1f min | rows=%s",
        provider,
        asset,
        interval,
        first_timestamp.isoformat(),
        last_timestamp.isoformat(),
        age_minutes,
        len(df),
    )


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

    symbol = (
        config.get("twelvedata_symbol")
        or CRYPTO_SYMBOLS.get(asset, asset)
    )

    params = {
        "symbol": symbol,
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
                "Twelve Data rechazó %s (%s): %s",
                asset,
                symbol,
                payload.get("message", payload.get("code", payload)),
            )
            return None

        meta = payload.get("meta")

        if not isinstance(meta, dict):
            logger.error(
                "MONEDA NO VERIFICABLE | %s | %s | falta metadata",
                asset,
                symbol,
            )
            return None

        # Comprobar que Twelve Data devuelve el símbolo solicitado.
        provider_symbol = meta.get("symbol")

        if not provider_symbol:
            logger.error(
                "SÍMBOLO NO VERIFICABLE | %s | %s | falta meta.symbol",
                asset,
                symbol,
            )
            return None

        normalised_provider_symbol = (
            str(provider_symbol)
            .upper()
            .replace(" ", "")
            .strip()
        )

        normalised_requested_symbol = (
            str(symbol)
            .upper()
            .replace(" ", "")
            .strip()
        )

        if normalised_provider_symbol != normalised_requested_symbol:
            logger.error(
                "SÍMBOLO INCOMPATIBLE | %s | solicitado=%s | recibido=%s",
                asset,
                normalised_requested_symbol,
                normalised_provider_symbol,
            )
            return None

        configured_currency = config.get("currency")

        if not configured_currency:
            logger.error(
                "MONEDA NO VERIFICABLE | %s | %s | falta moneda configurada",
                asset,
                symbol,
            )
            return None

        configured_currency = (
            str(configured_currency).upper().strip()
        )

        # Twelve Data utiliza metadatos distintos para acciones y criptomonedas.
        if asset in CRYPTO_ASSETS:
            provider_currency = meta.get("currency_quote")

            if not provider_currency:
                logger.error(
                    "MONEDA NO VERIFICABLE | %s | %s | "
                    "meta.currency_quote ausente | claves_metadata=%s",
                    asset,
                    symbol,
                    sorted(meta.keys()),
                )
                return None

            provider_currency = (
                str(provider_currency).upper().strip()
            )

            # Normalizar las denominaciones habituales de USD.
            currency_aliases = {
                "USD": "USD",
                "US DOLLAR": "USD",
                "UNITED STATES DOLLAR": "USD",
            }

            provider_currency = currency_aliases.get(
                provider_currency,
                provider_currency,
            )

            currency_field = "currency_quote"

        else:
            provider_currency = meta.get("currency")

            if not provider_currency:
                logger.error(
                    "MONEDA NO VERIFICABLE | %s | %s | "
                    "meta.currency ausente",
                    asset,
                    symbol,
                )
                return None

            provider_currency = (
                str(provider_currency).upper().strip()
            )

            currency_field = "currency"

        logger.info(
            "TWELVE DATA METADATA | %s | symbol=%s | %s=%s | configured=%s",
            asset,
            symbol,
            currency_field,
            provider_currency,
            configured_currency,
        )

        if provider_currency != configured_currency:
            logger.error(
                "MONEDA INCOMPATIBLE | %s | %s | "
                "Twelve Data=%s | Config=%s",
                asset,
                symbol,
                provider_currency,
                configured_currency,
            )
            return None

        values = payload["values"]

        if not values:
            logger.warning(
                "Twelve Data devolvió una lista vacía para %s (%s)",
                asset,
                symbol,
            )
            return None

        df = pd.DataFrame(values)

        if "datetime" not in df.columns:
            logger.error(
                "Twelve Data no devolvió fechas para %s (%s)",
                asset,
                symbol,
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

        if df is None:
            logger.warning(
                "No se pudieron normalizar los datos de %s",
                asset,
            )
            return None

        _log_dataframe_timestamps(
            "Twelve Data RAW",
            asset,
            interval,
            df,
        )

        df = _remove_incomplete_candle(
            df,
            interval,
            asset,
        )

        if df is None or len(df) < 220:
            logger.warning(
                "Histórico insuficiente para %s %s",
                asset,
                interval,
            )
            return None

        _log_dataframe_timestamps(
            "Twelve Data FINAL",
            asset,
            interval,
            df,
        )

        logger.info(
            "Twelve Data OK: %s (%s) %s, %s velas",
            asset,
            symbol,
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
        meta = result.get("meta", {})

        yahoo_currency = meta.get("currency")
        configured_currency = config.get("currency")

        logger.info(
            "YAHOO METADATA | %s | currency=%s | exchange=%s | timezone=%s",
            asset,
            yahoo_currency,
            meta.get("fullExchangeName", meta.get("exchangeName")),
            meta.get("exchangeTimezoneName"),
        )

        if not yahoo_currency or not configured_currency:
            logger.error(
                "Moneda no verificable para %s: Yahoo=%s config=%s",
                asset,
                yahoo_currency,
                configured_currency,
            )
            return None

        if yahoo_currency.upper() != configured_currency.upper():
            logger.error(
                "MONEDA INCOMPATIBLE | %s | Yahoo=%s | Config=%s",
                asset,
                yahoo_currency,
                configured_currency,
            )
            return None

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

        df = pd.DataFrame(
            {
                "Open": quote.get("open", []),
                "High": quote.get("high", []),
                "Low": quote.get("low", []),
                "Close": quote.get("close", []),
                "Volume": quote.get("volume", []),
            },
            index=pd.to_datetime(
                timestamps,
                unit="s",
                utc=True,
            ),
        )

        df = _normalise_dataframe(df)

        if df is None:
            logger.warning(
                "Yahoo no devolvió datos válidos para %s",
                ticker,
            )
            return None

        _log_dataframe_timestamps(
            "Yahoo RAW",
            asset,
            interval,
            df,
        )

        df = _remove_incomplete_candle(
            df,
            interval,
            asset,
        )

        if df is None or len(df) < 220:
            logger.warning(
                "Histórico insuficiente en Yahoo para %s %s",
                ticker,
                interval,
            )
            return None

        _log_dataframe_timestamps(
            "Yahoo FINAL",
            asset,
            interval,
            df,
        )

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

    if timestamp is None or asset is None:
        logger.warning(
            "DATA FRESHNESS REJECTED | %s | %s | sin timestamp o activo",
            asset,
            interval,
        )
        return False

    now = datetime.now(timezone.utc)

    timestamp_dt = timestamp.to_pydatetime()

    if timestamp_dt > now:
        logger.warning(
            "DATA FRESHNESS REJECTED | %s | %s | timestamp futuro",
            asset,
            interval,
        )
        return False

    age_minutes = (
        now - timestamp_dt
    ).total_seconds() / 60

    if asset in CRYPTO_ASSETS:
        limit = 45 if interval == "15m" else 48 * 60

    else:
        if interval == "15m":
            expected = _last_expected_intraday_timestamp(
                asset,
                now,
            )

            expected_utc = expected.astimezone(timezone.utc)

            difference_minutes = abs(
                (timestamp_dt - expected_utc).total_seconds()
            ) / 60

            is_fresh = difference_minutes <= 45

            logger.info(
                "DATA FRESHNESS | %s | %s | timestamp=%s | expected=%s | difference=%.1f min | fresh=%s",
                asset,
                interval,
                timestamp.isoformat(),
                expected_utc.isoformat(),
                difference_minutes,
                is_fresh,
            )

            return is_fresh

        if interval == "1d":
            expected_date = _previous_weekday(now.date())
            timestamp_utc_date = timestamp_dt.date()

            is_fresh = timestamp_utc_date == expected_date

            logger.info(
                "DATA FRESHNESS | %s | %s | timestamp=%s | timestamp_utc_date=%s | expected_date=%s | fresh=%s",
                asset,
                interval,
                timestamp.isoformat(),
                timestamp_utc_date,
                expected_date,
                is_fresh,
            )

            return is_fresh

        limit = max_age_minutes

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
