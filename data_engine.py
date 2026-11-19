
import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf

from config import ASSETS

logger = logging.getLogger(__name__)

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"

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

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

CRYPTO_ASSETS = {"BTC", "ETH", "SOL", "XLM", "XRP"}


def _asset_config(asset):
    config = ASSETS.get(asset)

    if not isinstance(config, dict):
        return None

    if not config.get("enabled", True):
        return None

    return config


def _normalise_dataframe(df):
    if df is None or df.empty:
        return None

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.columns = [str(col).strip().title() for col in df.columns]

    required = ["Open", "High", "Low", "Close"]

    if not all(col in df.columns for col in required):
        logger.error("Faltan columnas OHLC")
        return None

    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df = df[COLUMNS].copy()

    for col in COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce")

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
        cutoff = pd.Timestamp(
            now - timedelta(minutes=15)
        )

        return df[df.index <= cutoff]

    if interval == "1d":
        today = now.date()

        return df[df.index.date < today]

    return df


def _download_twelvedata(asset, config, interval):
    api_key = os.getenv("TWELVE_DATA_API_KEY")

    if not api_key:
        logger.error("Falta TWELVE_DATA_API_KEY")
        return None

    # Los símbolos se consultan directamente por su ticker.
    # En nuestra prueba anterior estos símbolos funcionaron.
    symbol = asset

    params = {
        "symbol": symbol,
        "interval": INTERVAL_MAP[interval],
        "outputsize": 5000,
        "timezone": "UTC",
        "apikey": api_key,
    }

    try:
        response = requests.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=30,
        )

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

        if df.empty:
            logger.error("Twelve Data devolvió datos vacíos para %s", asset)
            return None

        df = df.rename(
            columns={
                "datetime": "datetime",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )

        if "datetime" not in df.columns:
            logger.error("No hay fechas en los datos de %s", asset)
            return None

        df["datetime"] = pd.to_datetime(
            df["datetime"],
            utc=True,
            errors="coerce",
        )

        df = df.dropna(subset=["datetime"])
        df = df.set_index("datetime")

        df = _normalise_dataframe(df)

        if df is None:
            return None

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
    # En config.py el campo correcto es yahoo, no ticker.
    ticker = config.get("yahoo")

    if not ticker:
        logger.error("No hay ticker de Yahoo para %s", asset)
        return None

    period = "60d" if interval == "15m" else "10y"

    try:
        raw = yf.download(
            ticker,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=30,
        )

        if raw is None or raw.empty:
            logger.error(
                "Yahoo no devolvió datos para %s (%s)",
                asset,
                ticker,
            )
            return None

        df = _normalise_dataframe(raw)

        if df is None:
            logger.error(
                "Datos de Yahoo no válidos para %s",
                asset,
            )
            return None

        df = _remove_incomplete_candle(df, interval)

        if df is None or len(df) < 220:
            logger.warning(
                "Histórico insuficiente en Yahoo para %s %s",
                asset,
                interval,
            )
            return None

        logger.info(
            "Yahoo OK: %s (%s) %s, %s velas",
            asset,
            ticker,
            interval,
            len(df),
        )

        return df

    except Exception:
        logger.exception(
            "Error descargando Yahoo Finance para %s",
            asset,
        )
        return None


def download_data(asset, interval="15m"):
    if interval not in INTERVAL_MAP:
        logger.error("Temporalidad no admitida: %s", interval)
        return None

    config = _asset_config(asset)

    if config is None:
        logger.warning(
            "Activo desconocido o desactivado: %s",
            asset,
        )
        return None

    provider = PROVIDER_MAP.get(asset)

    if provider == "twelvedata":
        return _download_twelvedata(
            asset,
            config,
            interval,
        )

    if provider == "yahoo":
        return _download_yahoo(
            asset,
            config,
            interval,
        )

    logger.error(
        "No hay proveedor definido para %s",
        asset,
    )

    return None


def get_data_timestamp(df):
    if df is None or df.empty:
        return None

    timestamp = df.index[-1]

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")

    return timestamp


def is_data_fresh(df, max_age_minutes=30):
    timestamp = get_data_timestamp(df)

    if timestamp is None:
        return False

    now = pd.Timestamp.now(tz="UTC")

    age_minutes = (
        now - timestamp
    ).total_seconds() / 60

    if age_minutes < 0:
        return False

    return age_minutes <= max_age_minutes
