
import logging
import os
import threading
import time
from datetime import (
    date,
    datetime,
    timedelta,
    time as datetime_time,
    timezone,
)
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from config import ASSETS
from eodhd_provider import download_eodhd_daily

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


# ---------------------------------------------------------------------
# CALENDARIOS DE MERCADO
# ---------------------------------------------------------------------

def _nth_weekday(year, month, weekday, occurrence):
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year, month, weekday):
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)

    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year):
    """Calcula el domingo de Pascua mediante el algoritmo gregoriano."""

    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1

    return date(year, month, day)


def _observed_us_holiday(day):
    if day.weekday() == 5:
        return day - timedelta(days=1)

    if day.weekday() == 6:
        return day + timedelta(days=1)

    return day


def _us_holidays(year):
    holidays = {
        _observed_us_holiday(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_us_holiday(date(year, 6, 19)),
        _observed_us_holiday(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_us_holiday(date(year, 12, 25)),
    }

    if year < 2022:
        holidays.discard(
            _observed_us_holiday(date(year, 6, 19))
        )

    # Cierre extraordinario por el funeral de Estado del presidente
    # Jimmy Carter (9 de enero de 2025).
    if year == 2025:
        holidays.add(date(2025, 1, 9))

    return holidays


def _uk_substitute_holidays(year):
    holidays = set()

    for month, day in ((1, 1), (12, 25), (12, 26)):
        original = date(year, month, day)

        if original.weekday() in (5, 6):
            holidays.add(original + timedelta(days=2))
        else:
            holidays.add(original)

    return holidays


def _uk_holidays(year):
    easter = _easter_sunday(year)

    holidays = _uk_substitute_holidays(year)
    holidays.update({
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        _nth_weekday(year, 5, 0, 1),
        _last_weekday(year, 5, 0),
        _last_weekday(year, 8, 0),
    })

    return holidays


def _market_for_asset(asset):
    if asset in LONDON_ASSETS:
        return MARKET_CONFIG["LONDON"]

    return MARKET_CONFIG["US"]


def _session_for_asset(asset):
    market = _market_for_asset(asset)

    return (
        ZoneInfo(market["timezone"]),
        market["open"],
        market["close"],
    )


def _is_market_holiday(day, asset):
    if asset in CRYPTO_ASSETS:
        return False

    if asset in LONDON_ASSETS:
        return day in _uk_holidays(day.year)

    return day in _us_holidays(day.year)


def _is_trading_day(day, asset):
    if asset in CRYPTO_ASSETS:
        return True

    return (
        day.weekday() < 5
        and not _is_market_holiday(day, asset)
    )


def _previous_trading_day(day, asset):
    candidate = day - timedelta(days=1)

    while not _is_trading_day(candidate, asset):
        candidate -= timedelta(days=1)

    return candidate


def _previous_weekday(day):
    candidate = day - timedelta(days=1)

    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)

    return candidate


def _us_early_close_dates(year):
    """
    Devuelve las fechas estadounidenses con cierre anticipado
    que se contemplan expresamente en este calendario.

    Incluye el viernes posterior a Thanksgiving.
    """

    thanksgiving = _nth_weekday(year, 11, 3, 4)

    return {
        thanksgiving + timedelta(days=1),
    }


def _market_close_time(asset, session_date):
    """
    Devuelve la hora de cierre aplicable a una sesión concreta.

    Para Estados Unidos contempla el cierre anticipado del viernes
    posterior a Thanksgiving.
    """

    _, _, regular_close = _session_for_asset(asset)

    if asset in CRYPTO_ASSETS:
        return regular_close

    if asset not in LONDON_ASSETS:
        if session_date in _us_early_close_dates(session_date.year):
            return datetime_time(13, 0)

    return regular_close


# ---------------------------------------------------------------------
# CONFIGURACION Y CACHÉ
# ---------------------------------------------------------------------

def _asset_config(asset):
    config = ASSETS.get(asset)

    if not isinstance(config, dict):
        return None

    if config.get("enabled") is not True:
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


# ---------------------------------------------------------------------
# NORMALIZACION E INTEGRIDAD OHLCV
# ---------------------------------------------------------------------

def _normalise_dataframe(df):
    """
    Normaliza OHLCV.

    Política estricta:
    - Una sola vela OHLC inválida rechaza el histórico.
    - Un timestamp inválido rechaza el histórico.
    - Duplicados idénticos se consolidan.
    - Duplicados contradictorios rechazan el histórico.
    - El volumen ausente o inválido se sustituye por cero.
    """

    if df is None or not isinstance(df, pd.DataFrame):
        return None

    if df.empty:
        return None

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        required_fields = {
            "Open",
            "High",
            "Low",
            "Close",
        }

        selected_level = None

        for level in range(df.columns.nlevels):
            values = {
                str(value).strip().title()
                for value in df.columns.get_level_values(level)
            }

            if required_fields.issubset(values):
                selected_level = level
                break

        if selected_level is None:
            logger.error(
                "No se pudo identificar el nivel OHLC de las columnas"
            )
            return None

        df.columns = df.columns.get_level_values(selected_level)

    df.columns = [
        str(col).strip().title()
        for col in df.columns
    ]

    if pd.Index(df.columns).duplicated().any():
        logger.error("Columnas duplicadas tras normalización")
        return None

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

    df["Volume"] = df["Volume"].replace(
        [np.inf, -np.inf],
        np.nan,
    )
    df["Volume"] = df["Volume"].fillna(0.0)
    df.loc[df["Volume"] < 0, "Volume"] = 0.0

    for col in required:
        values = df[col].to_numpy(dtype=float)

        if not np.isfinite(values).all():
            logger.error(
                "HISTÓRICO RECHAZADO: precios ausentes o no finitos en %s",
                col,
            )
            return None

        if (values <= 0).any():
            logger.error(
                "HISTÓRICO RECHAZADO: precios no positivos en %s",
                col,
            )
            return None

    try:
        if not isinstance(df.index, pd.DatetimeIndex):
            parsed_index = pd.to_datetime(
                df.index,
                utc=True,
                errors="coerce",
            )
            df.index = parsed_index
        elif df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

    except Exception:
        logger.exception("No se pudo normalizar el índice temporal")
        return None

    if df.index.isna().any():
        logger.error(
            "HISTÓRICO RECHAZADO: timestamps inválidos"
        )
        return None

    duplicated = df.index.duplicated(keep=False)

    if duplicated.any():
        duplicate_rows = df.loc[duplicated]

        for timestamp, group in duplicate_rows.groupby(level=0):
            if len(group.drop_duplicates()) > 1:
                logger.error(
                    "HISTÓRICO RECHAZADO: timestamp duplicado conflictivo %s",
                    timestamp,
                )
                return None

        df = df[~df.index.duplicated(keep="last")]

    df = df.sort_index()

    valid_ohlc = (
        (df["High"] >= df["Low"])
        & (df["High"] >= df["Open"])
        & (df["High"] >= df["Close"])
        & (df["Low"] <= df["Open"])
        & (df["Low"] <= df["Close"])
    )

    if not valid_ohlc.all():
        invalid_count = int((~valid_ohlc).sum())

        logger.error(
            "HISTÓRICO RECHAZADO: %s velas OHLC incoherentes",
            invalid_count,
        )
        return None

    return df[COLUMNS]


# ---------------------------------------------------------------------
# VELAS INCOMPLETAS Y HORARIOS
# ---------------------------------------------------------------------

def _last_expected_intraday_timestamp(asset, now_utc):
    tz, opening, regular_closing = _session_for_asset(asset)

    now_local = now_utc.astimezone(tz)
    session_date = now_local.date()

    if (
        now_local.weekday() >= 5
        or _is_market_holiday(session_date, asset)
    ):
        previous_day = _previous_trading_day(
            session_date,
            asset,
        )

        previous_close = _market_close_time(
            asset,
            previous_day,
        )

        previous_close_dt = datetime.combine(
            previous_day,
            previous_close,
            tzinfo=tz,
        )

        return previous_close_dt - timedelta(minutes=15)

    closing = _market_close_time(
        asset,
        session_date,
    )

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

    if now_local < open_dt:
        previous_day = _previous_trading_day(
            session_date,
            asset,
        )

        previous_close = _market_close_time(
            asset,
            previous_day,
        )

        previous_close_dt = datetime.combine(
            previous_day,
            previous_close,
            tzinfo=tz,
        )

        return previous_close_dt - timedelta(minutes=15)

    if now_local >= close_dt:
        return close_dt - timedelta(minutes=15)

    elapsed = (now_local - open_dt).total_seconds()
    completed_intervals = int(elapsed // 900)

    if completed_intervals <= 0:
        previous_day = _previous_trading_day(
            session_date,
            asset,
        )

        previous_close = _market_close_time(
            asset,
            previous_day,
        )

        previous_close_dt = datetime.combine(
            previous_day,
            previous_close,
            tzinfo=tz,
        )

        return previous_close_dt - timedelta(minutes=15)

    expected = (
        open_dt
        + timedelta(minutes=15 * (completed_intervals - 1))
    )

    last_session_candle = close_dt - timedelta(minutes=15)

    return min(expected, last_session_candle)


def _last_completed_daily_session_date(asset, now_utc):
    """
    Determina la última sesión diaria que puede considerarse completa.

    Acciones y ETF:
    - Antes del cierre: última sesión bursátil anterior.
    - Después del cierre: sesión de hoy, si hoy es hábil.

    Criptomonedas:
    - La vela diaria UTC actual no se considera completa hasta
      el siguiente cambio de día UTC.
    """

    if asset in CRYPTO_ASSETS:
        return now_utc.date() - timedelta(days=1)

    market_tz, _, _ = _session_for_asset(asset)
    now_local = now_utc.astimezone(market_tz)
    market_today = now_local.date()

    if not _is_trading_day(market_today, asset):
        return _previous_trading_day(
            market_today,
            asset,
        )

    closing = _market_close_time(
        asset,
        market_today,
    )

    if now_local.time() >= closing:
        return market_today

    return _previous_trading_day(
        market_today,
        asset,
    )


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
            return df[df.index.date < now.date()]

        last_completed_date = _last_completed_daily_session_date(
            asset,
            now,
        )

        return df[
            df.index.date <= last_completed_date
        ]

    return df


# ---------------------------------------------------------------------
# DETECCION DE HUECOS INTRADIA
# ---------------------------------------------------------------------

def _has_intraday_gaps(df, asset):
    """
    Comprueba la continuidad de velas de 15 minutos.

    Cripto:
    - Comprueba continuidad de 15 minutos durante todo el histórico.

    Acciones y ETF:
    - Rechaza sesiones con una sola vela.
    - Comprueba intervalos de 15 minutos dentro de cada sesión.
    - Detecta sesiones bursátiles completas ausentes entre fechas.
    - Comprueba apertura cuando puede determinarse que la sesión
      está completa.
    - Comprueba cierre en sesiones históricas completas.
    - No exige que el primer día de un histórico multidía esté
      completo, porque el proveedor puede haber empezado a mitad
      de esa sesión.
    - No exige el cierre de la sesión actual si todavía está abierta.
    """

    if df is None or df.empty:
        return True

    timestamps = df.index.sort_values()

    # Criptomonedas: mercado continuo.
    if asset in CRYPTO_ASSETS:
        differences = timestamps.to_series().diff().dropna()

        if not (
            differences == pd.Timedelta(minutes=15)
        ).all():
            logger.error(
                "HUECO INTRADIA | %s | mercado cripto",
                asset,
            )
            return True

        return False

    # Acciones y ETF.
    tz, opening, _ = _session_for_asset(asset)

    local_index = timestamps.tz_convert(tz)

    local_dates = pd.Series(
        local_index.date,
        index=timestamps,
    )

    sessions = list(local_dates.groupby(local_dates))

    if not sessions:
        return True

    session_dates = [
        session_date
        for session_date, _ in sessions
    ]

    first_session_date = session_dates[0]

    now_local = (
        datetime.now(timezone.utc).astimezone(tz)
    )

    current_session_date = now_local.date()

    # Detectar sesiones bursátiles completas ausentes.
    for previous_date, current_date in zip(
        session_dates,
        session_dates[1:],
    ):
        missing_date = previous_date + timedelta(days=1)

        while missing_date < current_date:
            if _is_trading_day(missing_date, asset):
                logger.error(
                    "SESIÓN BURSÁTIL AUSENTE | %s | fecha=%s | anterior=%s | siguiente=%s",
                    asset,
                    missing_date,
                    previous_date,
                    current_date,
                )
                return True

            missing_date += timedelta(days=1)

    # Validar cada sesión representada.
    for session_date, group in sessions:

        if not _is_trading_day(session_date, asset):
            logger.error(
                "HISTÓRICO RECHAZADO: vela en día no hábil | %s | %s",
                asset,
                session_date,
            )
            return True

        session_timestamps = pd.DatetimeIndex(
            group.index
        ).sort_values()

        # Una sesión con una única vela no permite verificar
        # la continuidad intradía y se considera incompleta.
        if len(session_timestamps) < 2:
            logger.error(
                "HISTÓRICO RECHAZADO: sesión con una sola vela | %s | %s",
                asset,
                session_date,
            )
            return True

        # Comprobar continuidad entre velas.
        differences = (
            session_timestamps.to_series()
            .diff()
            .dropna()
        )

        if not (
            differences == pd.Timedelta(minutes=15)
        ).all():
            logger.error(
                "HUECO INTRADIA | %s | sesión=%s",
                asset,
                session_date,
            )
            return True

        local_first = session_timestamps[0].tz_convert(tz)
        local_last = session_timestamps[-1].tz_convert(tz)

        closing = _market_close_time(
            asset,
            session_date,
        )

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

        # Comprobar que las velas están dentro del horario.
        if (
            local_first < open_dt
            or local_last >= close_dt
        ):
            logger.error(
                "HORARIO INTRADIA INVÁLIDO | %s | %s",
                asset,
                session_date,
            )
            return True

        # Comprobar apertura.
        # El primer día de un histórico multidía puede estar
        # parcialmente descargado por el límite del proveedor.
        check_open = (
            session_date != first_session_date
            or len(sessions) == 1
        )

        if check_open and local_first != open_dt:
            logger.error(
                "FALTA LA PRIMERA VELA DE SESIÓN | %s | %s | expected=%s | received=%s",
                asset,
                session_date,
                open_dt,
                local_first,
            )
            return True

        # Comprobar cierre en sesiones completas.
        session_is_complete = (
            session_date < current_session_date
            or (
                session_date == current_session_date
                and now_local.time() >= closing
            )
        )

        if session_is_complete:
            expected_last = (
                close_dt - timedelta(minutes=15)
            )

            if local_last != expected_last:
                logger.error(
                    "FALTA LA ÚLTIMA VELA DE SESIÓN | %s | %s | expected=%s | received=%s",
                    asset,
                    session_date,
                    expected_last,
                    local_last,
                )
                return True

    return False


# ---------------------------------------------------------------------
# DETECCIÓN DE HUECOS DIARIOS
# ---------------------------------------------------------------------

def _has_daily_gaps(df, asset):
    """
    Comprueba que el histórico diario no omite sesiones esperadas.

    Cripto:
    - Exige una vela por cada día UTC consecutivo.

    Acciones y ETF:
    - Permite fines de semana y festivos del mercado correspondiente.
    - Rechaza cualquier sesión bursátil completa ausente.
    """
    if df is None or df.empty or asset is None:
        return True

    timestamps = pd.DatetimeIndex(df.index).sort_values()

    if timestamps.has_duplicates:
        logger.error(
            "HISTÓRICO DIARIO RECHAZADO: fechas duplicadas | %s",
            asset,
        )
        return True

    session_dates = [timestamp.date() for timestamp in timestamps]

    if any(
        current <= previous
        for previous, current in zip(session_dates, session_dates[1:])
    ):
        logger.error(
            "HISTÓRICO DIARIO RECHAZADO: fechas no crecientes | %s",
            asset,
        )
        return True

    # El motor utiliza como máximo dos años para los indicadores
    # operativos. Comprobar huecos solo dentro de esa ventana evita
    # rechazar históricos por discontinuidades anteriores al periodo
    # que realmente puede utilizar el sistema.
    recent_cutoff = session_dates[-1] - timedelta(days=730)

    if asset in CRYPTO_ASSETS:
        recent_timestamps = timestamps[
            timestamps.date >= recent_cutoff
        ]
        differences = recent_timestamps.to_series().diff().dropna()

        invalid_gaps = differences[
            differences != pd.Timedelta(days=1)
        ]
        if not invalid_gaps.empty:
            gap_position = differences.index.get_loc(invalid_gaps.index[0])
            previous_timestamp = recent_timestamps[gap_position]
            current_timestamp = recent_timestamps[gap_position + 1]
            logger.error(
                "HUECO DIARIO | %s | mercado cripto | anterior=%s | siguiente=%s | días_transcurridos=%.1f",
                asset,
                previous_timestamp.isoformat(),
                current_timestamp.isoformat(),
                invalid_gaps.iloc[0].total_seconds() / 86400,
            )
            return True

        return False

    recent_dates = [
        session_date
        for session_date in session_dates
        if session_date >= recent_cutoff
    ]

    for previous_date, current_date in zip(
        recent_dates,
        recent_dates[1:],
    ):
        missing_date = previous_date + timedelta(days=1)

        while missing_date < current_date:
            if _is_trading_day(missing_date, asset):
                logger.error(
                    "SESIÓN DIARIA AUSENTE | %s | fecha=%s | anterior=%s | siguiente=%s",
                    asset,
                    missing_date,
                    previous_date,
                    current_date,
                )
                return True

            missing_date += timedelta(days=1)

    return False


# ---------------------------------------------------------------------
# REGISTRO DE DATOS
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# TWELVE DATA
# ---------------------------------------------------------------------

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
                "MONEDA NO VERIFICABLE | %s | falta metadata",
                asset,
            )
            return None

        provider_symbol = meta.get("symbol")

        if not provider_symbol:
            logger.error(
                "SÍMBOLO NO VERIFICABLE | %s | falta meta.symbol",
                asset,
            )
            return None

        normalised_provider_symbol = (
            str(provider_symbol).upper().replace(" ", "").strip()
        )
        normalised_requested_symbol = (
            str(symbol).upper().replace(" ", "").strip()
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
                "MONEDA NO VERIFICABLE | %s | falta moneda configurada",
                asset,
            )
            return None

        configured_currency = str(configured_currency).upper().strip()

        if asset in CRYPTO_ASSETS:
            provider_currency = meta.get("currency_quote")
            currency_field = "currency_quote"
        else:
            provider_currency = meta.get("currency")
            currency_field = "currency"

        if not provider_currency:
            logger.error(
                "MONEDA NO VERIFICABLE | %s | metadata=%s",
                asset,
                sorted(meta.keys()),
            )
            return None

        provider_currency = str(provider_currency).upper().strip()

        currency_aliases = {
            "US DOLLAR": "USD",
            "UNITED STATES DOLLAR": "USD",
        }

        provider_currency = currency_aliases.get(
            provider_currency,
            provider_currency,
        )

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
                "MONEDA INCOMPATIBLE | %s | Twelve Data=%s | Config=%s",
                asset,
                provider_currency,
                configured_currency,
            )
            return None

        values = payload["values"]

        if not values:
            logger.warning(
                "Twelve Data devolvió una lista vacía para %s",
                asset,
            )
            return None

        df = pd.DataFrame(values)

        if "datetime" not in df.columns:
            logger.error(
                "Twelve Data no devolvió fechas para %s",
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

        df = df.set_index("datetime")

        df = _normalise_dataframe(df)

        if df is None:
            logger.error(
                "HISTÓRICO RECHAZADO | Twelve Data | %s",
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

        has_gaps = (
            _has_intraday_gaps(df, asset)
            if interval == "15m"
            else _has_daily_gaps(df, asset)
        )
        if has_gaps:
            logger.error(
                "HISTÓRICO RECHAZADO POR HUECOS | %s | %s",
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

    except requests.RequestException:
        logger.error(
            "Error de conexión Twelve Data para %s",
            asset,
        )
        return None

    except Exception:
        logger.exception(
            "Error procesando datos Twelve Data para %s",
            asset,
        )
        return None


# ---------------------------------------------------------------------
# YAHOO FINANCE
# ---------------------------------------------------------------------

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
                "Moneda no verificable para %s",
                asset,
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

        try:
            index = pd.to_datetime(
                timestamps,
                unit="s",
                utc=True,
                errors="coerce",
            )
        except Exception:
            logger.exception(
                "Yahoo devolvió timestamps inválidos para %s",
                ticker,
            )
            return None

        if index.isna().any():
            logger.error(
                "Yahoo devolvió timestamps inválidos para %s",
                ticker,
            )
            return None

        df = pd.DataFrame(
            {
                "Open": quote.get("open", []),
                "High": quote.get("high", []),
                "Low": quote.get("low", []),
                "Close": quote.get("close", []),
                "Volume": quote.get("volume", []),
            },
            index=index,
        )

        # Yahoo puede incluir una vela todavía abierta con OHLC nulo.
        # Se excluye por timestamp ANTES de validar precios; las velas
        # ya cerradas siguen sometidas a la validación OHLC estricta.
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

        df = _normalise_dataframe(df)

        if df is None:
            logger.error(
                "HISTÓRICO RECHAZADO | Yahoo | %s",
                ticker,
            )
            return None

        if len(df) < 220:
            logger.warning(
                "Histórico insuficiente en Yahoo para %s %s",
                ticker,
                interval,
            )
            return None

        has_gaps = (
            _has_intraday_gaps(df, asset)
            if interval == "15m"
            else _has_daily_gaps(df, asset)
        )
        if has_gaps:
            logger.error(
                "HISTÓRICO RECHAZADO POR HUECOS | %s | %s",
                asset,
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

    except requests.HTTPError as error:
        status_code = (
            error.response.status_code
            if error.response is not None
            else "desconocido"
        )
        logger.error(
            "Error HTTP de Yahoo para %s | status=%s",
            ticker,
            status_code,
        )
        return None

    except requests.RequestException as error:
        # No registrar la URL completa ni el texto de la excepción.
        # El código de estado basta para diagnosticar el fallo sin
        # arriesgar la exposición de parámetros sensibles.
        status_code = (
            error.response.status_code
            if getattr(error, "response", None) is not None
            else "sin respuesta HTTP"
        )
        logger.error(
            "Error de conexión de Yahoo para %s | status=%s",
            ticker,
            status_code,
        )
        return None

    except Exception:
        logger.exception(
            "Error procesando Yahoo Finance para %s",
            ticker,
        )
        return None

# ---------------------------------------------------------------------
# DESCARGA PRINCIPAL
# ---------------------------------------------------------------------

def _attach_market_data_metadata(dataframe, asset, interval):
    """Attach explicit, provider-verified OHLC adjustment metadata."""
    provider = (
        "eodhd"
        if asset in LONDON_ASSETS and interval == "1d"
        else PROVIDER_MAP.get(asset)
    )
    if interval == "15m":
        policy = "raw_unadjusted_intraday"
    else:
        policy = "split_adjusted_ohlc_no_dividend_adjustment"

    dataframe = dataframe.copy()
    dataframe.attrs["data_provider"] = provider
    dataframe.attrs["price_adjustment_policy"] = policy
    dataframe.attrs["price_adjustment_verified"] = True
    return dataframe


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
        validated = _normalise_dataframe(cached)

        if validated is not None:
            if interval == "15m":
                has_gaps = _has_intraday_gaps(validated, asset)
            else:
                has_gaps = _has_daily_gaps(validated, asset)

            if not has_gaps:
                return _attach_market_data_metadata(
                    validated, asset, interval
                )

        logger.warning(
            "Caché rechazada por validación | %s | %s",
            asset,
            interval,
        )

        with _cache_lock:
            _data_cache.pop((asset, interval), None)

    provider = PROVIDER_MAP.get(asset)

    if asset in LONDON_ASSETS and interval == "1d":
        dataframe = download_eodhd_daily(
            asset,
            config.get("currency"),
            _remove_incomplete_candle,
            _has_daily_gaps,
        )

    elif provider == "twelvedata":
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
        dataframe = _attach_market_data_metadata(
            dataframe, asset, interval
        )
        _save_cached_data(
            asset,
            interval,
            dataframe,
        )

    return dataframe


# ---------------------------------------------------------------------
# TIMESTAMP Y FRESCURA
# ---------------------------------------------------------------------

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

    if interval not in INTERVAL_MAP:
        return False

    if _asset_config(asset) is None:
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

    if interval == "15m":
        if asset in CRYPTO_ASSETS:
            is_fresh = age_minutes <= 45
        else:
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

    elif interval == "1d":
        expected_date = _last_completed_daily_session_date(
            asset,
            now,
        )

        timestamp_date = timestamp_dt.date()

        is_fresh = timestamp_date == expected_date

        logger.info(
            "DATA FRESHNESS | %s | %s | timestamp=%s | expected_session=%s | fresh=%s",
            asset,
            interval,
            timestamp.isoformat(),
            expected_date,
            is_fresh,
        )

        return is_fresh

    else:
        is_fresh = age_minutes <= max_age_minutes

    logger.info(
        "DATA FRESHNESS | %s | %s | timestamp=%s | age=%.1f min | fresh=%s",
        asset,
        interval,
        timestamp.isoformat(),
        age_minutes,
        is_fresh,
    )

    return is_fresh
