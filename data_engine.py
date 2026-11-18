
import logging
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from config import ASSETS

logger = logging.getLogger(__name__)


def download_data(asset, interval="15m"):
    """
    Descarga datos históricos de Yahoo Finance.
    Devuelve únicamente velas cerradas y datos validados.
    """

    if asset not in ASSETS:
        logger.warning("Activo desconocido: %s", asset)
        return None

    config = ASSETS[asset]

    if not config.get("enabled", False):
        logger.info("Activo deshabilitado: %s", asset)
        return None

    ticker = config["yahoo"]

    if interval == "15m":
        period = "60d"
    elif interval == "1d":
        period = "2y"
    else:
        logger.warning("Temporalidad no admitida: %s", interval)
        return None

    try:
        df = yf.download(
            ticker,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
        )

        if df is None or df.empty:
            logger.warning("Sin datos para %s (%s)", asset, interval)
            return None

        # Yahoo puede devolver columnas MultiIndex.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        required = ["Open", "High", "Low", "Close", "Volume"]

        if not all(column in df.columns for column in required):
            logger.warning("Faltan columnas en %s", asset)
            return None

        df = df[required].copy()

        # Convertir columnas a valores numéricos.
        for column in required:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        df = df.dropna(subset=required)
        df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()

        # Eliminar la última vela para evitar utilizar una vela
        # que todavía pueda estar formándose.
        if len(df) > 1:
            df = df.iloc[:-1].copy()

        if len(df) < 60:
            logger.warning("Histórico insuficiente para %s", asset)
            return None

        # Comprobar coherencia básica de precios.
        invalid = (
            (df["High"] < df["Low"])
            | (df["High"] < df["Open"])
            | (df["High"] < df["Close"])
            | (df["Low"] > df["Open"])
            | (df["Low"] > df["Close"])
            | (df[["Open", "High", "Low", "Close"]] <= 0).any(axis=1)
        )

        df = df.loc[~invalid].copy()

        if len(df) < 60:
            return None

        df.index.name = "Datetime"

        return df

    except Exception as exc:
        logger.exception(
            "Error descargando %s (%s): %s",
            asset,
            interval,
            exc,
        )
        return None


def get_data_timestamp(df):
    """Devuelve la fecha de la última vela disponible."""

    if df is None or df.empty:
        return None

    timestamp = df.index[-1]

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")

    return timestamp.to_pydatetime()


def is_data_fresh(df, max_age_minutes=30):
    """
    Comprueba la antigüedad de la última vela.
    Una sesión cerrada puede producir datos antiguos;
    por tanto, esta comprobación es conservadora.
    """

    timestamp = get_data_timestamp(df)

    if timestamp is None:
        return False

    now = datetime.now(timezone.utc)
    age_minutes = (now - timestamp).total_seconds() / 60

    return 0 <= age_minutes <= max_age_minutes
