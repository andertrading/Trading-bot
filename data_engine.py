
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

    if not all(col in df.columns for col in COLUMNS):
        return None

    df = df[COLUMNS].copy()

    for col in COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df[df["Open"] > 0]
    df = df[df["High"] > 0]
    df = df[df["Low"] > 0]
    df = df[df["Close"] > 0]

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
        cutoff = pd.Timestamp(now - timedelta(minutes=15))
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

    symbol = str(config.get("ticker", asset)).strip()

    if asset in {"BTC", "ETH", "SOL", "XLM", "XRP"}:
        symbol = symbol.replace("-USD", "/USD").replace("-USDT", "/USD")

        if "/" not in symbol:
            symbol = f"{symbol}/USD"

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
            timeout=25,
        )

        response.raise_for_status()
        payload = response.json()

        if "values" not in payload:
            logger.error(
                "Twelve Data error para %s: %s",
                asset,
                payload.get("message", payload),
            )
            return None

        df = pd.DataFrame(payload["values"])

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
            return None

        df["datetime"] = pd.to_datetime(
            df["datetime"],
            utc=True,
            errors="coerce",
        )

        df = df.set_index("datetime")
        df = _normalise_dataframe(df)

        if df is None:
            return None

        df = _remove_incomplete_candle(df, interval)

        return df if len(df) >= 220 else None

    except requests.RequestException as exc:
        logger.error("Error de conexión Twelve Data (%s): %s", asset, exc)
        return None

    except Exception:
        logger.exception("Error procesando Twelve Data para %s", asset)
        return None


def _download_yahoo(asset, config, interval):
    ticker = config.get("ticker")

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
        )

        df = _normalise_dataframe(raw)

        if df is None:
            return None

        df = _remove_incomplete_candle(df, interval)

        return df if len(df) >= 220 else None

    except Exception:
        logger.exception("Error descargando Yahoo Finance para %s", asset)
        return None


def download_data(asset, interval="15m"):
    if interval not in INTERVAL_MAP:
        logger.error("Temporalidad no admitida: %s", interval)
        return None

    config = _asset_config(asset)

    if config is None:
        logger.warning("Activo desconocido o desactivado: %s", asset)
        return None

    provider = PROVIDER_MAP.get(asset)

    if provider == "twelvedata":
        return _download_twelvedata(asset, config, interval)

    if provider == "yahoo":
        return _download_yahoo(asset, config, interval)

    logger.error("No hay proveedor definido para %s", asset)
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
    age = (now - timestamp).total_seconds() / 60

    return 0 <= age <= max_age_minutes
