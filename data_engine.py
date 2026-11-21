
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
import yfinance as yf

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

        if original.weekday() == 5:
            holidays.add(original + timedelta(days=2))
        elif original.weekday() == 6:
            # New Year's Day substitutes on Monday when 1 January is Sunday.
            # Christmas and Boxing Day use the next available weekdays.
            offset = 1 if (month, day) == (1, 1) else 2
            holidays.add(original + timedelta(days=offset))
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

    # Exceptional UK exchange closures in the five-year history window.
    if year == 2022:
        # Spring bank holiday moved for the Platinum Jubilee; 3 June was added.
        holidays.discard(date(2022, 5, 30))
        holidays.update({
            date(2022, 6, 2),
            date(2022, 6, 3),
            date(2022, 9, 19),  # State funeral of Queen Elizabeth II.
        })
    elif year == 2023:
        holidays.add(date(2023, 5, 8))  # Coronation bank holiday.

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


def _next_trading_day(day, asset):
    candidate = day + timedelta(days=1)

    while not _is_trading_day(candidate, asset):
        candidate += timedelta(days=1)

    return candidate


def _previous_weekday(day):
    candidate = day - timedelta(days=1)

    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)

    return candidate


def _us_early_close_dates(year):
    """
    Fechas habituales de cierre anticipado de los mercados estadounidenses.

    Incluye el viernes posterior a Thanksgiving, el 3 de julio cuando
    realmente es sesión bursátil y Nochebuena cuando es sesión bursátil.
    Los festivos observados siguen excluidos por _is_trading_day().
    """
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {
        thanksgiving + timedelta(days=1),
        date(year, 7, 3),
        date(year, 12, 24),
    }
    return {
        day for day in candidates
        if day.weekday() < 5 and day not in _us_holidays(year)
    }


def _market_close_time(asset, session_date):
    """
    Devuelve la hora de cierre aplicable a una sesión concreta.

    Para Estados Unidos contempla cierres anticipados habituales:
    viernes posterior a Thanksgiving, 3 de julio y Nochebuena.
    """

    _, _, regular_close = _session_for_asset(asset)

    if asset in CRYPTO_ASSETS:
        return regular_close

    if asset not in LONDON_ASSETS:
        if session_date in _us_early_close_dates(session_date.year):
            return datetime_time(13, 0)

    return regular_close


def _filter_out_of_session_intraday(df, asset):
    """
    Excluye velas intradía etiquetadas fuera de la sesión regular.

    Los proveedores pueden devolver barras de pre/posmercado o marcas
    posteriores al cierre anticipado. Solo se excluyen en días que el
    calendario reconoce como sesiones; las velas en festivos se conservan
    para que la validación estricta las rechace.
    """
    if (
        df is None
        or df.empty
        or asset in CRYPTO_ASSETS
        or not isinstance(df.index, pd.DatetimeIndex)
        or df.index.tz is None
    ):
        return df

    market_tz, regular_open, _ = _session_for_asset(asset)
    keep = []
    excluded = []

    for timestamp in df.index:
        local = timestamp.tz_convert(market_tz)
        session_date = local.date()

        if not _is_trading_day(session_date, asset):
            keep.append(True)
            continue

        close_time = _market_close_time(asset, session_date)
        local_time = local.time().replace(tzinfo=None)
        in_regular_session = regular_open <= local_time < close_time
        keep.append(in_regular_session)

        if not in_regular_session:
            excluded.append(local.isoformat())

    if not excluded:
        return df

    logger.warning(
        "VELAS FUERA DE SESIÓN EXCLUIDAS | %s | cantidad=%s | ejemplos=%s",
        asset,
        len(excluded),
        excluded[:5],
    )

    return df.loc[keep].copy()


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


def is_intraday_market_open(asset, now=None):
    """Return whether completed intraday bars are available to scan.

    Includes a five-minute post-close grace period so the final session candle
    can be fetched after the exchange closes and the provider publishes it.
    """
    if _asset_config(asset) is None:
        return False
    if asset in CRYPTO_ASSETS:
        return True

    if now is None:
        now_utc = datetime.now(timezone.utc)
    elif isinstance(now, str):
        now_utc = datetime.fromisoformat(now.replace("Z", "+00:00"))
    else:
        now_utc = now

    if now_utc.tzinfo is None:
        raise ValueError("now must include a timezone")
    now_utc = now_utc.astimezone(timezone.utc)

    market_tz, opening, _ = _session_for_asset(asset)
    now_local = now_utc.astimezone(market_tz)
    session_date = now_local.date()

    if not _is_trading_day(session_date, asset):
        return False

    closing = _market_close_time(asset, session_date)
    first_completed_bar = datetime.combine(
        session_date, opening, tzinfo=market_tz
    ) + timedelta(minutes=15)

    close_dt = datetime.combine(
        session_date, closing, tzinfo=market_tz
    )
    return first_completed_bar <= now_local < (
        close_dt + timedelta(minutes=5)
    )


def signal_expiry_at(
    asset,
    interval,
    generated_at=None,
    data_timestamp=None,
    validity_bars=2,
):
    """Compute expiry after a fixed number of *completed* bars.

    Daily equity/ETF expiry uses the existing US/London holiday and early-close
    calendars. Crypto daily bars use UTC day boundaries.
    """
    if isinstance(validity_bars, bool) or not isinstance(validity_bars, int):
        raise ValueError("validity_bars must be a positive integer")
    if validity_bars <= 0:
        raise ValueError("validity_bars must be a positive integer")
    if _asset_config(asset) is None:
        raise ValueError("unknown or disabled asset")

    def to_utc(value):
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, str):
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            value = pd.Timestamp(value).to_pydatetime()
        if value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value.astimezone(timezone.utc)

    generated = to_utc(generated_at)

    if interval == "15m":
        candle_start = to_utc(data_timestamp) if data_timestamp is not None else generated
        if asset in CRYPTO_ASSETS:
            return candle_start + timedelta(minutes=15 * (validity_bars + 1))

        # Equities/ETFs do not print overnight, on weekends, or on exchange
        # holidays. Count only future session bars so an alert near the close
        # cannot remain enterable while its market is shut.
        market_tz, opening, _ = _session_for_asset(asset)
        local_start = candle_start.astimezone(market_tz)
        session_date = local_start.date()
        if not _is_trading_day(session_date, asset):
            raise ValueError("data_timestamp must fall within a trading session")
        session_open = datetime.combine(session_date, opening, tzinfo=market_tz)
        session_close = datetime.combine(
            session_date, _market_close_time(asset, session_date), tzinfo=market_tz
        )
        if (
            local_start < session_open
            or local_start + timedelta(minutes=15) > session_close
            or (local_start - session_open).total_seconds() % 900 != 0
        ):
            raise ValueError("data_timestamp must identify a completed 15-minute session bar")

        next_bar = local_start + timedelta(minutes=15)
        for bar_index in range(validity_bars):
            while True:
                if not _is_trading_day(next_bar.date(), asset):
                    next_day = _next_trading_day(next_bar.date(), asset)
                    next_bar = datetime.combine(next_day, opening, tzinfo=market_tz)
                    continue
                day_open = datetime.combine(next_bar.date(), opening, tzinfo=market_tz)
                day_close = datetime.combine(
                    next_bar.date(), _market_close_time(asset, next_bar.date()),
                    tzinfo=market_tz,
                )
                if next_bar < day_open:
                    next_bar = day_open
                if next_bar + timedelta(minutes=15) <= day_close:
                    break
                next_day = _next_trading_day(next_bar.date(), asset)
                next_bar = datetime.combine(next_day, opening, tzinfo=market_tz)
            if bar_index < validity_bars - 1:
                next_bar += timedelta(minutes=15)

        return (next_bar + timedelta(minutes=15)).astimezone(timezone.utc)

    if interval != "1d":
        raise ValueError("unsupported signal timeframe")

    if asset in CRYPTO_ASSETS:
        last_completed = _last_completed_daily_session_date(asset, generated)
        expiry_date = last_completed + timedelta(days=validity_bars + 1)
        return datetime.combine(expiry_date, datetime_time.min, tzinfo=timezone.utc)

    last_completed = _last_completed_daily_session_date(asset, generated)
    next_session = last_completed
    for _ in range(validity_bars):
        next_session += timedelta(days=1)
        while not _is_trading_day(next_session, asset):
            next_session += timedelta(days=1)

    market_tz, _, _ = _session_for_asset(asset)
    closing = _market_close_time(asset, next_session)
    expiry = datetime.combine(next_session, closing, tzinfo=market_tz)
    return (expiry + timedelta(minutes=30)).astimezone(timezone.utc)


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
    extended_daily_backtest = os.getenv(
        "BACKTEST_EXTENDED_DAILY_HISTORY", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    recent_cutoff = (
        session_dates[0]
        if extended_daily_backtest
        else session_dates[-1] - timedelta(days=730)
    )

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


def _download_twelvedata(
    asset, config, interval, start_date=None, end_date=None
):
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
        "timezone": "UTC",
        # Explicitly pin the documented corporate-action policy:
        # daily bars are split-adjusted; intraday bars remain raw.
        "adjust": "splits" if interval == "1d" else "none",
        "apikey": api_key,
    }

    if start_date is None and end_date is None:
        params["outputsize"] = 5000
    elif start_date is not None and end_date is not None:
        params["start_date"] = start_date
        params["end_date"] = end_date
    else:
        raise ValueError("start_date and end_date must be supplied together")

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

        if interval == "15m":
            df = _filter_out_of_session_intraday(df, asset)

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


def twelvedata_history_windows(asset, end=None, lookback_days=700):
    """Return contiguous date windows that stay below Twelve Data's 5,000-bar cap."""
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int):
        raise ValueError("lookback_days must be a positive integer")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be a positive integer")

    if end is None:
        end_utc = datetime.now(timezone.utc)
    elif isinstance(end, str):
        end_utc = datetime.fromisoformat(end.replace("Z", "+00:00"))
    else:
        end_utc = end

    if end_utc.tzinfo is None:
        raise ValueError("end must include a timezone")
    end_utc = end_utc.astimezone(timezone.utc)
    # Align the endpoint to a completed 15-minute boundary.
    end_utc = end_utc.replace(
        minute=(end_utc.minute // 15) * 15,
        second=0,
        microsecond=0,
    )
    start_utc = end_utc - timedelta(days=lookback_days)

    # 50 calendar days produce at most 4,800 crypto 15m bars.
    # 180 calendar days stay comfortably below 5,000 US-equity session bars.
    chunk_days = 50 if asset in CRYPTO_ASSETS else 180
    windows = []
    cursor = start_utc

    while cursor < end_utc:
        chunk_end = min(cursor + timedelta(days=chunk_days), end_utc)
        windows.append((cursor, chunk_end))
        cursor = chunk_end

    return windows


def _download_twelvedata_extended(asset, config, interval):
    """Backtest-only historical backfill using contiguous date-range requests."""
    if interval != "15m":
        return _download_twelvedata(asset, config, interval)

    try:
        lookback_days = int(
            os.getenv("BACKTEST_INTRADAY_LOOKBACK_DAYS", "700")
        )
    except (TypeError, ValueError):
        logger.error("BACKTEST_INTRADAY_LOOKBACK_DAYS no es válido")
        return None

    try:
        windows = twelvedata_history_windows(
            asset,
            lookback_days=lookback_days,
        )
    except ValueError:
        logger.exception("No se pudieron construir ventanas históricas | %s", asset)
        return None

    frames = []
    for index, (start, end) in enumerate(windows, start=1):
        logger.info(
            "TWELVE DATA BACKFILL | %s | %s/%s | %s -> %s",
            asset,
            index,
            len(windows),
            start.isoformat(),
            end.isoformat(),
        )
        chunk = _download_twelvedata(
            asset,
            config,
            interval,
            start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if chunk is None or chunk.empty:
            logger.error(
                "BACKFILL INCOMPLETO | %s | ventana %s/%s",
                asset,
                index,
                len(windows),
            )
            return None
        frames.append(chunk)

    combined = pd.concat(frames).sort_index()
    combined = combined.loc[~combined.index.duplicated(keep="last")]
    combined = _normalise_dataframe(combined)

    if combined is None or len(combined) < 220:
        logger.error("BACKFILL INSUFICIENTE | %s", asset)
        return None

    if _has_intraday_gaps(combined, asset):
        logger.error(
            "BACKFILL RECHAZADO POR HUECOS | %s | %s velas",
            asset,
            len(combined),
        )
        return None

    logger.info(
        "TWELVE DATA EXTENDED HISTORY OK | %s | %s velas | %s -> %s",
        asset,
        len(combined),
        combined.index[0].isoformat(),
        combined.index[-1].isoformat(),
    )
    return combined


# ---------------------------------------------------------------------
# YAHOO FINANCE
# ---------------------------------------------------------------------

def _yahoo_repair_enabled(interval):
    return (
        interval == "1d"
        and os.getenv(
            "BACKTEST_EXTENDED_DAILY_HISTORY", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
    )


YAHOO_EXTENDED_DAILY_REPAIR_TICKERS = {
    # Same USD accumulating share classes / ISINs, alternate SIX listings.
    "CSPX": "CSSPX.SW",
    "CNDX": "CSNDX.SW",
}
MAX_ALTERNATE_OHLC_REPAIRS_FRACTION = 0.01
MAX_ALTERNATE_OHLC_REPAIRS_MINIMUM = 10
MAX_ALTERNATE_CLOSE_DEVIATION_PCT = 0.5


def _invalid_ohlc_mask(dataframe):
    required = ["Open", "High", "Low", "Close"]
    if dataframe is None or dataframe.empty or any(
        column not in dataframe.columns for column in required
    ):
        return pd.Series(True, index=getattr(dataframe, "index", None))
    values = dataframe[required].apply(pd.to_numeric, errors="coerce")
    finite = np.isfinite(values.to_numpy(dtype=float)).all(axis=1)
    positive = (values > 0).all(axis=1)
    coherent = (
        (values["High"] >= values["Low"])
        & (values["High"] >= values["Open"])
        & (values["High"] >= values["Close"])
        & (values["Low"] <= values["Open"])
        & (values["Low"] <= values["Close"])
    )
    return pd.Series(~(finite & positive & coherent), index=dataframe.index)


def _download_yahoo_alternate_daily(
    asset, start, end, expected_currency
):
    """Fetch the same USD ETF share class from its SIX listing for repairs."""
    ticker = YAHOO_EXTENDED_DAILY_REPAIR_TICKERS.get(asset)
    if not ticker:
        logger.error("No hay cotización alternativa verificada para %s", asset)
        return None

    params = {
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "1d",
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
            logger.error("Yahoo limitó la reparación alternativa de %s", asset)
            return None
        response.raise_for_status()
        payload = response.json()
        chart = payload.get("chart", {})
        if chart.get("error"):
            logger.error("Yahoo rechazó la cotización alternativa de %s", asset)
            return None
        results = chart.get("result") or []
        if not results:
            logger.error("Yahoo no devolvió cotización alternativa para %s", asset)
            return None
        result = results[0]
        meta = result.get("meta", {})
        currency = str(meta.get("currency") or "").upper()
        exchange = str(
            meta.get("fullExchangeName") or meta.get("exchangeName") or ""
        )
        timezone_name = meta.get("exchangeTimezoneName")
        if currency != str(expected_currency or "").upper():
            logger.error(
                "Moneda incompatible en reparación alternativa | %s | %s",
                asset,
                currency or "sin verificar",
            )
            return None
        if not timezone_name or "swiss" not in exchange.lower():
            logger.error(
                "Mercado alternativo no verificable para %s | exchange=%s",
                asset,
                exchange or "sin verificar",
            )
            return None

        dataframe = _download_yahoo_daily_history(
            ticker,
            start,
            end,
        )
        if dataframe is None or dataframe.empty:
            logger.error("Histórico alternativo vacío para %s", asset)
            return None
        dataframe = dataframe[
            ~dataframe.index.duplicated(keep="last")
        ].sort_index()
        logger.info(
            "YAHOO ALTERNATE OK | %s | %s | %s velas | currency=%s",
            asset,
            ticker,
            len(dataframe),
            currency,
        )
        return dataframe, ticker
    except requests.RequestException as exc:
        status_code = (
            exc.response.status_code
            if getattr(exc, "response", None) is not None
            else "sin respuesta HTTP"
        )
        logger.error(
            "Error de descarga de reparación alternativa | %s | status=%s",
            asset,
            status_code,
        )
        return None
    except Exception:
        logger.exception("Error procesando reparación alternativa para %s", asset)
        return None


def _patch_incoherent_daily_ohlc_from_alternate(
    dataframe, alternate_dataframe, asset, alternate_ticker
):
    """Patch only defective LSE bars from the same-date, same-ISIN USD SIX listing."""
    if dataframe is None or dataframe.empty:
        return None, None

    invalid = _invalid_ohlc_mask(dataframe)
    invalid_index = list(dataframe.index[invalid])
    if not invalid_index:
        return dataframe.copy(), {
            "price_source_detail": "Yahoo LSE primary; no alternate OHLC repairs required",
            "ohlc_repair_count": 0,
            "ohlc_repair_source_ticker": None,
            "ohlc_repair_max_close_deviation_pct": None,
        }

    maximum_repairs = max(
        MAX_ALTERNATE_OHLC_REPAIRS_MINIMUM,
        int(np.ceil(len(dataframe) * MAX_ALTERNATE_OHLC_REPAIRS_FRACTION)),
    )
    if len(invalid_index) > maximum_repairs:
        logger.error(
            "Demasiadas velas defectuosas para reparación alternativa | %s | %s/%s",
            asset,
            len(invalid_index),
            maximum_repairs,
        )
        return None, None

    if (
        alternate_dataframe is None
        or alternate_dataframe.empty
        or alternate_dataframe.index.has_duplicates
    ):
        logger.error("Histórico alternativo no válido para reparar %s", asset)
        return None, None

    patched = dataframe.copy()
    maximum_deviation = 0.0
    for timestamp in invalid_index:
        if timestamp not in alternate_dataframe.index:
            logger.error(
                "La cotización alternativa no contiene la fecha defectuosa | %s | %s",
                asset,
                timestamp,
            )
            return None, None
        primary_close = pd.to_numeric(
            pd.Series([patched.at[timestamp, "Close"]]), errors="coerce"
        ).iloc[0]
        alternate_row = alternate_dataframe.loc[timestamp]
        if isinstance(alternate_row, pd.DataFrame):
            logger.error("Fecha alternativa duplicada al reparar %s", asset)
            return None, None
        if _invalid_ohlc_mask(alternate_dataframe.loc[[timestamp]]).iloc[0]:
            logger.error("Vela alternativa defectuosa | %s | %s", asset, timestamp)
            return None, None
        alternate_close = float(alternate_row["Close"])
        if (
            not np.isfinite(primary_close)
            or float(primary_close) <= 0
            or not np.isfinite(alternate_close)
            or alternate_close <= 0
        ):
            logger.error("No se puede verificar el precio de reparación | %s", asset)
            return None, None
        deviation = abs(float(primary_close) / alternate_close - 1.0) * 100.0
        if deviation > MAX_ALTERNATE_CLOSE_DEVIATION_PCT:
            logger.error(
                "Divergencia entre listings superior al límite | %s | %s | %.4f%%",
                asset,
                timestamp,
                deviation,
            )
            return None, None
        maximum_deviation = max(maximum_deviation, deviation)
        for column in ("Open", "High", "Low", "Close"):
            patched.at[timestamp, column] = float(alternate_row[column])

    if _invalid_ohlc_mask(patched).any():
        logger.error("Persisten velas OHLC defectuosas tras reparar %s", asset)
        return None, None

    metadata = {
        "price_source_detail": (
            "Yahoo LSE primary; defective OHLC bars patched from the same-date "
            "SIX USD listing of the same ISIN"
        ),
        "ohlc_repair_count": len(invalid_index),
        "ohlc_repair_source_ticker": alternate_ticker,
        "ohlc_repair_max_close_deviation_pct": round(maximum_deviation, 6),
    }
    logger.warning(
        "YAHOO ALTERNATE OHLC REPAIR | %s | repaired=%s | source=%s | max_close_deviation=%.4f%%",
        asset,
        len(invalid_index),
        alternate_ticker,
        maximum_deviation,
    )
    return patched, metadata


def _download_yahoo_daily_history(ticker, start, end):
    """Download split-adjusted daily OHLC without yfinance's repair heuristics."""
    try:
        dataframe = yf.Ticker(ticker).history(
            start=start,
            end=end,
            interval="1d",
            auto_adjust=False,
            actions=True,
            repair=False,
            timeout=25,
        )
    except Exception:
        logger.exception("Yahoo repair falló para %s", ticker)
        return None

    if dataframe is None or dataframe.empty:
        logger.error("Yahoo repair no devolvió datos para %s", ticker)
        return None

    index = pd.DatetimeIndex(pd.to_datetime(dataframe.index, errors="coerce"))
    if index.isna().any():
        logger.error("Yahoo repair devolvió timestamps inválidos para %s", ticker)
        return None

    # Normalize the exchange-local daily date to UTC midnight, matching the
    # daily timestamp convention used by the chart API and other providers.
    if index.tz is not None:
        index = index.tz_localize(None)
    index = index.normalize().tz_localize("UTC")

    dataframe = dataframe.copy()
    dataframe.index = index
    dataframe.index.name = "Datetime"
    required = ["Open", "High", "Low", "Close"]
    if any(column not in dataframe.columns for column in required):
        logger.error("Yahoo repair devolvió columnas OHLC incompletas para %s", ticker)
        return None
    if "Volume" not in dataframe.columns:
        dataframe["Volume"] = 0.0
    return dataframe[["Open", "High", "Low", "Close", "Volume"]]


def _drop_invalid_ohlc_rows(dataframe, asset, interval):
    """
    Extended daily backtests may discard Yahoo rows with missing OHLC.
    The normal daily-gap validator still rejects any missing trading session.
    Live data and intraday data retain the original strict behavior.
    """
    extended_daily_backtest = (
        interval == "1d"
        and os.getenv(
            "BACKTEST_EXTENDED_DAILY_HISTORY", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
    )
    if not extended_daily_backtest or dataframe is None or dataframe.empty:
        return dataframe

    required = ["Open", "High", "Low", "Close"]
    numeric = dataframe[required].apply(pd.to_numeric, errors="coerce")
    valid_rows = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
    dropped = int((~valid_rows).sum())

    if dropped:
        logger.warning(
            "YAHOO EXTENDED DAILY | %s | descartadas=%s velas con OHLC incompleto",
            asset,
            dropped,
        )

    return dataframe.loc[valid_rows].copy()


def _extended_daily_yahoo_start(now):
    """Use the exact requested daily backtest lookback, never an implicit 10y window."""
    raw_days = os.getenv("BACKTEST_DAILY_LOOKBACK_DAYS", "1825")
    try:
        lookback_days = int(raw_days)
    except (TypeError, ValueError) as exc:
        raise ValueError("BACKTEST_DAILY_LOOKBACK_DAYS no es válido") from exc
    if not 365 <= lookback_days <= 3650:
        raise ValueError("BACKTEST_DAILY_LOOKBACK_DAYS fuera de rango")
    return now - timedelta(days=lookback_days)


def _download_yahoo(asset, config, interval):
    ticker = config.get("yahoo")

    if not ticker:
        logger.error("No hay ticker de Yahoo para %s", asset)
        return None

    now = datetime.now(timezone.utc)

    if interval == "15m":
        start = now - timedelta(days=60)
    elif _yahoo_repair_enabled(interval):
        try:
            start = _extended_daily_yahoo_start(now)
        except ValueError as error:
            logger.error("Ventana diaria extendida inválida: %s", error)
            return None
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

        # Yahoo puede rechazar con HTTP 422 el límite exacto de 60 días
        # para intervalos intradía. Reintenta una sola vez con 55 días.
        if response.status_code == 422 and interval == "15m":
            logger.warning(
                "Yahoo HTTP 422 para %s; reintentando con ventana intradía de 55 días",
                ticker,
            )
            retry_params = dict(params)
            retry_params["period1"] = int(
                (now - timedelta(days=55)).timestamp()
            )
            response = requests.get(
                f"{YAHOO_CHART_URL}/{ticker}",
                params=retry_params,
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

        repair_metadata = None
        if _yahoo_repair_enabled(interval):
            df = _download_yahoo_daily_history(
                ticker,
                start,
                now,
            )
            if df is None:
                return None
            if _invalid_ohlc_mask(df).any():
                alternate = _download_yahoo_alternate_daily(
                    asset,
                    start,
                    now,
                    configured_currency,
                )
                if alternate is None:
                    return None
                alternate_dataframe, alternate_ticker = alternate
                df, repair_metadata = _patch_incoherent_daily_ohlc_from_alternate(
                    df,
                    alternate_dataframe,
                    asset,
                    alternate_ticker,
                )
                if df is None:
                    return None
            else:
                _, repair_metadata = _patch_incoherent_daily_ohlc_from_alternate(
                    df, None, asset, None
                )
                if repair_metadata is None:
                    return None
        else:
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

        df = _drop_invalid_ohlc_rows(
            df,
            asset,
            interval,
        )

        df = _normalise_dataframe(df)

        if df is not None and repair_metadata is not None:
            df.attrs.update(repair_metadata)

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
    extended_daily_backtest = (
        interval == "1d"
        and os.getenv(
            "BACKTEST_EXTENDED_DAILY_HISTORY", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
    )
    provider = (
        "yahoo"
        if asset in LONDON_ASSETS and interval == "1d" and extended_daily_backtest
        else (
            "eodhd"
            if asset in LONDON_ASSETS and interval == "1d"
            else PROVIDER_MAP.get(asset)
        )
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

    provider = PROVIDER_MAP.get(asset)
    extended_backtest = (
        provider == "twelvedata"
        and interval == "15m"
        and os.getenv(
            "BACKTEST_EXTENDED_INTRADAY_HISTORY", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
    )
    extended_daily_backtest = (
        interval == "1d"
        and os.getenv(
            "BACKTEST_EXTENDED_DAILY_HISTORY", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
    )

    cached = (
        None
        if extended_backtest or extended_daily_backtest
        else _get_cached_data(asset, interval)
    )

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

    if asset in LONDON_ASSETS and interval == "1d":
        if extended_daily_backtest:
            # Backtests only: Yahoo provides a longer daily history than
            # the EODHD free tier's one-year window. Live scans keep EODHD.
            dataframe = _download_yahoo(
                asset,
                config,
                interval,
            )
        else:
            dataframe = download_eodhd_daily(
                asset,
                config.get("currency"),
                _remove_incomplete_candle,
                _has_daily_gaps,
            )

    elif provider == "twelvedata":
        if extended_backtest:
            dataframe = _download_twelvedata_extended(
                asset,
                config,
                interval,
            )
        else:
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
