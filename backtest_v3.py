
import hashlib
import json
import os
import platform
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from config import (
    ASSETS,
    INITIAL_CAPITAL_USD,
    ETORO_EXECUTION_TYPES,
    ETORO_MARKET_SPREAD_BPS,
)
from data_engine import (
    _is_trading_day,
    _market_for_asset,
    download_data,
    PROVIDER_MAP,
    LONDON_ASSETS,
)
from indicators import add_indicators
from strategies import evaluate_signal_at
from risk_manager import risk_pct_for_score
from transaction_costs import estimate_etoro_trade_costs


VERSION = "3.18"

PERIODS = {
    "15m": {"period": "60d", "max_bars": 32},
    "1d": {"period": "2y", "max_bars": 60},
}

MIN_BARS = 220
BACKTEST_DAILY_LOOKBACK_DAYS = 730

MIN_EXECUTION_RR = 2.0
RR_TOLERANCE = 1e-6

# ---------------------------------------------------------
# REPRODUCIBILIDAD
# ---------------------------------------------------------

BACKTEST_DATA_MODE = os.getenv(
    "BACKTEST_DATA_MODE",
    "replay",
).strip().lower()

SNAPSHOT_DIR = Path(
    os.getenv(
        "BACKTEST_SNAPSHOT_DIR",
        "historicos_backtest_v3",
    )
)

if BACKTEST_DATA_MODE not in ("download", "replay"):
    raise ValueError(
        "BACKTEST_DATA_MODE debe ser 'download' o 'replay'"
    )

SNAPSHOT_MANIFEST = []

# ---------------------------------------------------------
# REGISTROS
# ---------------------------------------------------------

DOWNLOAD_ERRORS = {}
EXCLUDED_OHLC_CANDLES = []
RR_REJECTIONS = []
SIGNAL_DIAGNOSTICS = []

RR_REJECTION_COLUMNS = [
    "asset",
    "ticker",
    "timeframe",
    "signal_time",
    "entry_time",
    "signal_reference",
    "entry",
    "stop",
    "target",
    "risk_per_unit",
    "reward_per_unit",
    "actual_rr",
    "minimum_rr",
    "reason",
]

SIGNAL_DIAGNOSTIC_COLUMNS = [
    "asset",
    "ticker",
    "timeframe",
    "signal_time",
    "signal_status",
    "rejection_reason",
    "score",
    "signal_reference",
    "signal_close",
    "ema20",
    "ema50",
    "ema200",
    "rsi",
    "macd",
    "macd_signal",
    "high20",
    "relative_volume",
    "atr",
    "atr_percent",
    "entry_time",
    "entry",
    "entry_gap_pct",
    "stop",
    "target",
    "initial_risk_per_unit",
    "actual_reward_per_unit",
    "actual_rr",
    "minimum_rr",
    "exit_time",
    "active_trade_exit_time",
    "exit",
    "exit_reason",
    "result_R",
]

OVERLAPPING_SIGNAL_COLUMNS = [
    "asset",
    "ticker",
    "timeframe",
    "signal_time",
    "entry_time",
    "signal_score",
    "signal_reference",
    "entry",
    "stop",
    "target",
    "actual_rr",
    "active_trade_exit_time",
    "reason",
]

# ---------------------------------------------------------
# CONFIGURACIÓN TEMPORAL
# ---------------------------------------------------------

GAP_TOLERANCE_MULTIPLIER = 1.5


# ---------------------------------------------------------
# FUNCIONES AUXILIARES
# ---------------------------------------------------------

def _safe_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if not np.isfinite(result):
        return None

    return result


def _first_row_value(row, names):
    for name in names:
        try:
            value = row.get(name)
        except AttributeError:
            try:
                value = row[name]
            except (KeyError, TypeError, IndexError):
                continue

        if value is not None:
            return _safe_float(value)

    return None


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _snapshot_paths(asset, interval):
    base = SNAPSHOT_DIR / f"{asset}__{interval}"

    return (
        base.with_suffix(".csv"),
        base.with_suffix(".json"),
    )


def _sha256_file(path):
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _provider_for_asset(asset, interval):
    if asset in LONDON_ASSETS and interval == "1d":
        return "eodhd"
    return PROVIDER_MAP.get(asset)


def _snapshot_metadata(
    asset,
    ticker,
    interval,
    data,
    csv_path,
    digest,
    provider,
):
    return {
        "bot_version": VERSION,
        "asset": asset,
        "ticker": ticker,
        "timeframe": interval,
        "provider": provider,
        "period_requested": (
            "2y" if interval == "1d" else "provider_maximum_available"
        ),
        "interval_requested": interval,
        "auto_adjust": None,
        "adjustment_policy": data.attrs.get(
            "price_adjustment_policy",
            "unknown_unverified",
        ),
        "price_adjustment_verified": bool(
            data.attrs.get("price_adjustment_verified", False)
        ),
        "closed_candle_policy": (
            "El motor live excluye velas incompletas antes de devolver el histórico"
        ),
        "downloaded_at_utc": _utc_now(),
        "first_timestamp": str(data.index[0]),
        "last_timestamp": str(data.index[-1]),
        "row_count": int(len(data)),
        "sha256": digest,
        "csv_file": csv_path.name,
        "python_version": platform.python_version(),
        "pandas_version": pd.__version__,
        "numpy_version": np.__version__,
        "yfinance_version": getattr(
            yf,
            "__version__",
            "unknown",
        ),
    }


def _save_snapshot(asset, ticker, interval, data):
    """Guarda las velas exactas utilizadas por el backtest."""

    try:
        SNAPSHOT_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        csv_path, metadata_path = _snapshot_paths(
            asset,
            interval,
        )

        temporary_csv = csv_path.with_suffix(".csv.tmp")
        temporary_metadata = metadata_path.with_suffix(".json.tmp")

        export = data.copy()
        export.index.name = "timestamp"

        export.to_csv(
            temporary_csv,
            index=True,
            index_label="timestamp",
            float_format="%.12g",
        )

        digest = _sha256_file(temporary_csv)

        metadata = _snapshot_metadata(
            asset,
            ticker,
            interval,
            data,
            csv_path,
            digest,
            _provider_for_asset(asset, interval),
        )

        with open(
            temporary_metadata,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                metadata,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temporary_csv, csv_path)
        os.replace(temporary_metadata, metadata_path)

        SNAPSHOT_MANIFEST.append(metadata)

        print(
            f"  Histórico guardado: {csv_path} "
            f"| SHA256={digest[:16]}..."
        )

        return metadata

    except Exception as exc:
        raise RuntimeError(
            f"No se pudo guardar el snapshot de "
            f"{asset} ({interval}): {exc}"
        ) from exc


def _metadata_timestamp(value, field_name, metadata_path):
    """Convierte un timestamp del manifiesto a UTC."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Metadata inválida en {metadata_path}: "
            f"{field_name} ausente o no textual"
        )

    try:
        timestamp = pd.Timestamp(value)
    except Exception as exc:
        raise ValueError(
            f"Metadata inválida en {metadata_path}: "
            f"{field_name} no es una fecha válida"
        ) from exc

    if pd.isna(timestamp):
        raise ValueError(
            f"Metadata inválida en {metadata_path}: "
            f"{field_name} contiene NaT"
        )

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")

    return timestamp


def _load_snapshot(asset, ticker, interval):
    """Carga un histórico guardado y verifica su integridad."""

    csv_path, metadata_path = _snapshot_paths(
        asset,
        interval,
    )

    if not csv_path.is_file():
        raise FileNotFoundError(
            f"No existe el histórico guardado: {csv_path}"
        )

    if not metadata_path.is_file():
        raise FileNotFoundError(
            f"No existe el manifiesto: {metadata_path}"
        )

    with open(
        metadata_path,
        "r",
        encoding="utf-8",
    ) as file:
        metadata = json.load(file)

    if not isinstance(metadata, dict):
        raise ValueError(
            f"El manifiesto no contiene un objeto JSON: {metadata_path}"
        )

    expected = {
        "asset": asset,
        "ticker": ticker,
        "timeframe": interval,
        "provider": _provider_for_asset(asset, interval),
        "period_requested": (
            "2y" if interval == "1d" else "provider_maximum_available"
        ),
        "auto_adjust": None,
        "adjustment_policy": (
            "raw_unadjusted_intraday"
            if interval == "15m"
            else "split_adjusted_ohlc_no_dividend_adjustment"
        ),
        "price_adjustment_verified": True,
    }

    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(
                f"Metadatos incompatibles en {metadata_path}: "
                f"{key} esperado={value}, "
                f"recibido={metadata.get(key)}"
            )

    actual_hash = _sha256_file(csv_path)

    if actual_hash != metadata.get("sha256"):
        raise ValueError(
            f"SHA-256 incorrecto en {csv_path}. "
            "El archivo puede haber sido modificado."
        )

    data = pd.read_csv(
        csv_path,
        parse_dates=["timestamp"],
    )

    if "timestamp" not in data.columns:
        raise ValueError(
            f"El snapshot no contiene timestamp: {csv_path}"
        )

    data = data.set_index("timestamp")

    data.index = pd.DatetimeIndex(
        pd.to_datetime(
            data.index,
            utc=True,
            errors="coerce",
        )
    )

    if data.index.hasnans:
        raise ValueError(
            f"El snapshot contiene timestamps inválidos: {csv_path}"
        )

    # No se repara silenciosamente un snapshot desordenado.
    if not data.index.is_monotonic_increasing:
        raise ValueError(
            f"El snapshot contiene timestamps desordenados: {csv_path}"
        )

    if data.index.has_duplicates:
        raise ValueError(
            f"El snapshot contiene timestamps duplicados: {csv_path}"
        )

    required = [
        "Open",
        "High",
        "Low",
        "Close",
    ]

    for column in required:
        if column not in data.columns:
            raise ValueError(
                f"Falta {column} en el snapshot {csv_path}"
            )

        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    if "Volume" in data.columns:
        data["Volume"] = pd.to_numeric(
            data["Volume"],
            errors="coerce",
        )

    row_count = metadata.get("row_count")

    if (
        isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count != len(data)
    ):
        raise ValueError(
            f"Número de filas incompatible en {csv_path}: "
            f"metadata={row_count}, real={len(data)}"
        )

    metadata_first = _metadata_timestamp(
        metadata.get("first_timestamp"),
        "first_timestamp",
        metadata_path,
    )

    metadata_last = _metadata_timestamp(
        metadata.get("last_timestamp"),
        "last_timestamp",
        metadata_path,
    )

    if data.empty:
        raise ValueError(
            f"El snapshot está vacío: {csv_path}"
        )

    if data.index[0] != metadata_first:
        raise ValueError(
            f"first_timestamp no coincide con el CSV en {csv_path}: "
            f"metadata={metadata_first}, real={data.index[0]}"
        )

    if data.index[-1] != metadata_last:
        raise ValueError(
            f"last_timestamp no coincide con el CSV en {csv_path}: "
            f"metadata={metadata_last}, real={data.index[-1]}"
        )

    validation_error = _ohlc_validation_error(data)

    if validation_error:
        raise ValueError(
            f"Snapshot OHLC inválido: {validation_error}"
        )

    if len(data) < MIN_BARS:
        raise ValueError(
            f"Snapshot insuficiente: {len(data)} velas"
        )

    SNAPSHOT_MANIFEST.append(metadata)

    print(
        f"  Histórico reproducido: {csv_path} "
        f"| SHA256={actual_hash[:16]}..."
    )

    return data


def _write_snapshot_manifest():
    if not SNAPSHOT_MANIFEST:
        return

    SNAPSHOT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = SNAPSHOT_DIR / "manifest.json"

    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            SNAPSHOT_MANIFEST,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ---------------------------------------------------------
# DIAGNÓSTICO DE SEÑALES
# ---------------------------------------------------------

def _start_signal_diagnostic(
    asset,
    ticker,
    interval,
    signal_time,
    signal_row,
    signal,
):
    diagnostic = {
        "asset": asset,
        "ticker": ticker,
        "timeframe": interval,
        "signal_time": str(signal_time),
        "signal_status": "PENDING",
        "rejection_reason": None,
        "score": None,
        "signal_reference": None,
        "signal_close": _first_row_value(
            signal_row,
            ["Close"],
        ),
        "ema20": _first_row_value(
            signal_row,
            ["EMA20", "ema20", "EMA_20", "ema_20"],
        ),
        "ema50": _first_row_value(
            signal_row,
            ["EMA50", "ema50", "EMA_50", "ema_50"],
        ),
        "ema200": _first_row_value(
            signal_row,
            ["EMA200", "ema200", "EMA_200", "ema_200"],
        ),
        "rsi": _first_row_value(
            signal_row,
            ["RSI", "rsi"],
        ),
        "macd": _first_row_value(
            signal_row,
            ["MACD", "macd"],
        ),
        "macd_signal": _first_row_value(
            signal_row,
            [
                "MACD_SIGNAL",
                "MACD_SIGNAL_LINE",
                "macd_signal",
                "macd_signal_line",
                "MACD Signal",
            ],
        ),
        "high20": _first_row_value(
            signal_row,
            ["HIGH20", "high20", "HIGH_20", "high_20"],
        ),
        "relative_volume": _first_row_value(
            signal_row,
            [
                "RELATIVE_VOLUME",
                "relative_volume",
                "REL_VOLUME",
            ],
        ),
        "atr": _first_row_value(
            signal_row,
            ["ATR", "atr", "ATR14", "atr14"],
        ),
        "atr_percent": _first_row_value(
            signal_row,
            ["ATR_PERCENT", "atr_percent", "ATR_PCT"],
        ),
        "entry_time": None,
        "entry": None,
        "entry_gap_pct": None,
        "stop": None,
        "target": None,
        "initial_risk_per_unit": None,
        "actual_reward_per_unit": None,
        "actual_rr": None,
        "minimum_rr": MIN_EXECUTION_RR,
        "exit_time": None,
        "active_trade_exit_time": None,
        "exit": None,
        "exit_reason": None,
        "result_R": None,
    }

    try:
        diagnostic["score"] = int(signal["score"])
    except (KeyError, TypeError, ValueError, OverflowError):
        pass

    diagnostic["signal_reference"] = _safe_float(
        signal.get("entry_reference")
    )

    diagnostic["stop"] = _safe_float(
        signal.get("stop")
    )

    diagnostic["target"] = _safe_float(
        signal.get("target")
    )

    SIGNAL_DIAGNOSTICS.append(diagnostic)

    return diagnostic


def _finish_signal_diagnostic(
    diagnostic,
    status,
    reason=None,
):
    if diagnostic is None:
        return

    diagnostic["signal_status"] = status
    diagnostic["rejection_reason"] = reason


def _update_signal_diagnostic(diagnostic, **values):
    if diagnostic is None:
        return

    for key, value in values.items():
        if key in SIGNAL_DIAGNOSTIC_COLUMNS:
            diagnostic[key] = value


# ---------------------------------------------------------
# ACTIVOS
# ---------------------------------------------------------

def get_assets():
    result = []

    for name, settings in ASSETS.items():
        if not isinstance(settings, dict):
            continue

        if not settings.get("enabled", False):
            continue

        ticker = settings.get("yahoo") or settings.get("ticker")

        if ticker:
            result.append((name, ticker))

    return result


def _get_asset_type(asset):
    settings = ASSETS.get(asset, {})

    if not isinstance(settings, dict):
        return "UNKNOWN"

    return str(
        settings.get("type", "UNKNOWN")
    ).upper()


# ---------------------------------------------------------
# CALENDARIO Y VALIDACIÓN TEMPORAL
# ---------------------------------------------------------

def _calendar_asset(asset_type, asset=None):
    """
    Devuelve el identificador que utiliza data_engine
    para seleccionar el calendario de mercado.

    Si no se proporciona el activo, conserva compatibilidad
    con las llamadas antiguas de los tests.
    """

    if asset:
        return str(asset).upper()

    asset_type = str(asset_type).upper()

    if asset_type == "CRYPTO":
        return "BTC"

    return "UNKNOWN"


def _has_missing_trading_day(
    previous_date,
    current_date,
    calendar_asset,
):
    """
    Comprueba si existe una sesión bursátil completa ausente
    entre dos fechas representadas en el histórico.

    Los fines de semana y festivos oficiales del calendario
    correspondiente no se consideran sesiones ausentes.
    """

    if current_date <= previous_date:
        return True

    missing_date = previous_date + pd.Timedelta(days=1)

    while missing_date < current_date:
        if _is_trading_day(
            missing_date,
            calendar_asset,
        ):
            return True

        missing_date += pd.Timedelta(days=1)

    return False


def _has_unexpected_gap(
    previous_timestamp,
    current_timestamp,
    interval,
    asset_type,
    asset=None,
):
    try:
        previous = pd.Timestamp(previous_timestamp)
        current = pd.Timestamp(current_timestamp)
    except (TypeError, ValueError):
        return True

    if pd.isna(previous) or pd.isna(current):
        return True

    elapsed = current - previous

    if elapsed <= pd.Timedelta(0):
        return True

    asset_type = str(asset_type).upper()

    if asset_type == "CRYPTO":
        if interval == "15m":
            expected = pd.Timedelta(minutes=15)
        elif interval == "1d":
            expected = pd.Timedelta(days=1)
        else:
            return True

        maximum = expected * GAP_TOLERANCE_MULTIPLIER

        return elapsed > maximum

    if asset_type not in ("STOCK", "ETF"):
        return True

    calendar_asset = _calendar_asset(
        asset_type,
        asset,
    )

    if interval == "15m":
        market = _market_for_asset(calendar_asset)
        market_tz = market["timezone"]

        try:
            if previous.tzinfo is None:
                previous = previous.tz_localize("UTC")
            else:
                previous = previous.tz_convert("UTC")

            if current.tzinfo is None:
                current = current.tz_localize("UTC")
            else:
                current = current.tz_convert("UTC")

            previous_local = previous.tz_convert(market_tz)
            current_local = current.tz_convert(market_tz)

        except Exception:
            return True

        # Dentro de una misma sesión, las velas deben ser
        # consecutivas con una tolerancia estricta.
        if previous_local.date() == current_local.date():
            expected = pd.Timedelta(minutes=15)
            maximum = expected * GAP_TOLERANCE_MULTIPLIER

            return elapsed > maximum

        # Entre sesiones puede haber cierres nocturnos,
        # fines de semana y festivos. Se rechazan únicamente
        # los días bursátiles completos ausentes.
        return _has_missing_trading_day(
            previous_local.date(),
            current_local.date(),
            calendar_asset,
        )

    if interval == "1d":
        # Yahoo Finance representa las velas diarias con
        # timestamps UTC asociados a la fecha de sesión.
        # Por eso se utiliza la fecha UTC del timestamp,
        # no la fecha local obtenida al convertir la hora.
        previous_date = previous.date()
        current_date = current.date()

        return _has_missing_trading_day(
            previous_date,
            current_date,
            calendar_asset,
        )

    return True


# ---------------------------------------------------------
# VALIDACIÓN OHLC
# ---------------------------------------------------------

def _normalize_columns(data):
    if not isinstance(data.columns, pd.MultiIndex):
        return data

    fields = {
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    }

    for level in range(data.columns.nlevels):
        values = set(data.columns.get_level_values(level))

        if fields.intersection(values):
            data.columns = data.columns.get_level_values(level)
            return data

    return data


def _ohlc_incoherent_mask(data):
    return (
        (data["High"] < data["Low"])
        | (data["High"] < data["Open"])
        | (data["High"] < data["Close"])
        | (data["Low"] > data["Open"])
        | (data["Low"] > data["Close"])
    )


def _ohlc_validation_error(data):
    if data is None:
        return "El histórico es None"

    if not isinstance(data, pd.DataFrame):
        return "El histórico no es un DataFrame"

    if data.empty:
        return "El histórico está vacío"

    required = ["Open", "High", "Low", "Close"]

    missing = [
        column
        for column in required
        if column not in data.columns
    ]

    if missing:
        return f"Faltan columnas OHLC: {missing}"

    for column in required:
        try:
            values = data[column].to_numpy(dtype=float)
        except (TypeError, ValueError) as exc:
            return f"No se puede convertir {column}: {exc}"

        if not np.isfinite(values).all():
            return f"La columna {column} contiene valores no finitos"

        if (values <= 0).any():
            return f"La columna {column} contiene precios no positivos"

    invalid_count = int(_ohlc_incoherent_mask(data).sum())

    if invalid_count:
        return (
            f"Se detectaron {invalid_count} velas OHLC incoherentes"
        )

    return None


def _valid_ohlc(data):
    return _ohlc_validation_error(data) is None


# ---------------------------------------------------------
# DESCARGA / REPRODUCCIÓN DE HISTÓRICOS
# ---------------------------------------------------------

def download_history(ticker, interval, asset=None):
    """Usa el mismo motor de proveedores y validación que el escáner."""
    key = (ticker, interval)
    DOWNLOAD_ERRORS.pop(key, None)
    if interval not in PERIODS:
        DOWNLOAD_ERRORS[key] = "Temporalidad no admitida"
        return None
    if asset is None:
        asset = next((name for name, settings in get_assets() if settings == ticker), None)
    if asset is None:
        DOWNLOAD_ERRORS[key] = "No se pudo identificar el activo del histórico"
        return None
    if BACKTEST_DATA_MODE == "replay":
        try:
            return _load_snapshot(asset, ticker, interval)
        except Exception as exc:
            DOWNLOAD_ERRORS[key] = f"Error reproduciendo snapshot: {type(exc).__name__}: {exc}"
            warnings.warn(f"{asset} ({interval}): {DOWNLOAD_ERRORS[key]}")
            return None
    try:
        data = download_data(asset, interval)
    except Exception as exc:
        DOWNLOAD_ERRORS[key] = f"Motor de datos: {type(exc).__name__}: {exc}"
        warnings.warn(f"{asset} ({interval}): {DOWNLOAD_ERRORS[key]}")
        return None
    if data is None or not isinstance(data, pd.DataFrame) or data.empty:
        DOWNLOAD_ERRORS[key] = "El motor de datos no devolvió un histórico válido"
        return None
    try:
        expected_provider = _provider_for_asset(asset, interval)
        expected_adjustment = (
            "raw_unadjusted_intraday"
            if interval == "15m"
            else "split_adjusted_ohlc_no_dividend_adjustment"
        )
        if data.attrs.get("data_provider") != expected_provider:
            raise ValueError("El motor no confirma el proveedor del histórico")
        if data.attrs.get("price_adjustment_policy") != expected_adjustment:
            raise ValueError("La política de ajuste de precios no coincide")
        if data.attrs.get("price_adjustment_verified") is not True:
            raise ValueError("El ajuste de precios no está verificado")
        data = _normalize_columns(data.copy())
        required = ["Open", "High", "Low", "Close"]
        missing = [column for column in required if column not in data.columns]
        if missing:
            raise ValueError(f"Columnas OHLC ausentes: {missing}")
        columns = required + (["Volume"] if "Volume" in data.columns else [])
        data = data[columns].copy()
        if not isinstance(data.index, pd.DatetimeIndex):
            data.index = pd.to_datetime(data.index, utc=True, errors="coerce")
        elif data.index.tz is None:
            data.index = data.index.tz_localize("UTC")
        else:
            data.index = data.index.tz_convert("UTC")
        if data.index.hasnans: raise ValueError("timestamps inválidos")
        if not data.index.is_monotonic_increasing: raise ValueError("timestamps desordenados")
        if data.index.has_duplicates: raise ValueError("timestamps duplicados")
        if interval == "1d":
            daily_cutoff = (
                data.index[-1]
                - pd.Timedelta(days=BACKTEST_DAILY_LOOKBACK_DAYS)
            )
            data = data.loc[data.index >= daily_cutoff].copy()
        for column in columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
        for column in required:
            values = data[column].to_numpy(dtype=float)
            if not np.isfinite(values).all() or (values <= 0).any():
                raise ValueError(f"{column} contiene precios ausentes, no finitos o no positivos")
        invalid_mask = _ohlc_incoherent_mask(data)
        if invalid_mask.any():
            invalid_rows = data.loc[invalid_mask]
            for timestamp, candle in invalid_rows.iterrows():
                EXCLUDED_OHLC_CANDLES.append({
                    "ticker": ticker, "timeframe": interval, "fecha": str(timestamp),
                    "open": float(candle["Open"]), "high": float(candle["High"]),
                    "low": float(candle["Low"]), "close": float(candle["Close"]),
                    "motivo": "Histórico rechazado por OHLC incoherente",
                })
            raise ValueError(f"{len(invalid_rows)} velas OHLC incoherentes")
        validation_error = _ohlc_validation_error(data)
        if validation_error: raise ValueError(validation_error)
        if len(data) < MIN_BARS:
            raise ValueError(f"Histórico insuficiente: {len(data)} filas; mínimo {MIN_BARS}")
        provider = _provider_for_asset(asset, interval)
        if not provider: raise ValueError("No hay proveedor configurado para el activo")
        _save_snapshot(asset, ticker, interval, data)
        DOWNLOAD_ERRORS.pop(key, None)
        print(f"  Fuente live: {provider} | {len(data)} velas | {data.index[0]} → {data.index[-1]}")
        return data
    except Exception as exc:
        DOWNLOAD_ERRORS[key] = f"Histórico rechazado: {type(exc).__name__}: {exc}"
        warnings.warn(f"{asset} ({interval}): {DOWNLOAD_ERRORS[key]}")
        return None
def _validate_execution_gaps(
    df,
    start_index,
    end_index,
    interval,
    asset_type,
    asset=None,
):
    if start_index < 0 or end_index >= len(df):
        return "Índices fuera del histórico al comprobar huecos"

    if start_index >= end_index:
        return None

    for index in range(start_index + 1, end_index + 1):
        previous_timestamp = df.index[index - 1]
        current_timestamp = df.index[index]

        if _has_unexpected_gap(
            previous_timestamp,
            current_timestamp,
            interval,
            asset_type,
            asset=asset,
        ):
            elapsed = (
                pd.Timestamp(current_timestamp)
                - pd.Timestamp(previous_timestamp)
            )

            return (
                f"Hueco temporal anómalo en "
                f"{asset_type} ({interval}): "
                f"{previous_timestamp} -> {current_timestamp} "
                f"(duración: {elapsed})"
            )

    return None


# ---------------------------------------------------------
# RECHAZOS RR
# ---------------------------------------------------------

def _record_rr_rejection(
    asset,
    ticker,
    interval,
    signal_time,
    entry_time,
    entry_reference,
    entry,
    stop,
    target,
    initial_risk,
    actual_reward,
    actual_rr,
):
    RR_REJECTIONS.append({
        "asset": asset,
        "ticker": ticker,
        "timeframe": interval,
        "signal_time": str(signal_time),
        "entry_time": str(entry_time),
        "signal_reference": round(entry_reference, 6),
        "entry": round(entry, 6),
        "stop": round(stop, 6),
        "target": round(target, 6),
        "risk_per_unit": round(initial_risk, 6),
        "reward_per_unit": round(actual_reward, 6),
        "actual_rr": round(actual_rr, 6),
        "minimum_rr": MIN_EXECUTION_RR,
        "reason": "RR real inferior al mínimo",
    })


# ---------------------------------------------------------
# PREPARACIÓN DE CANDIDATAS
# ---------------------------------------------------------

def _prepare_candidate(
    asset,
    ticker,
    interval,
    df,
    index,
    signal,
    asset_type,
):
    """Evalúa los datos de entrada y RR de una candidata."""

    signal_row = df.iloc[index]
    entry_index = index + 1

    diagnostic = _start_signal_diagnostic(
        asset=asset,
        ticker=ticker,
        interval=interval,
        signal_time=df.index[index],
        signal_row=signal_row,
        signal=signal,
    )

    _update_signal_diagnostic(
        diagnostic,
        entry_time=str(df.index[entry_index]),
    )

    gap_error = _validate_execution_gaps(
        df,
        index,
        entry_index,
        interval,
        asset_type,
        asset=asset,
    )

    if gap_error:
        _finish_signal_diagnostic(
            diagnostic,
            "BACKTEST_ABORTED_GAP",
            gap_error,
        )
        return None, gap_error

    try:
        signal_close = float(signal_row["Close"])
        entry_reference = float(signal["entry_reference"])
        stop = float(signal["stop"])
        target = float(signal["target"])
        entry = float(df.iloc[entry_index]["Open"])
        atr = float(signal_row["ATR"])
        score = int(signal["score"])

    except (
        TypeError,
        ValueError,
        KeyError,
        OverflowError,
    ):
        _finish_signal_diagnostic(
            diagnostic,
            "INVALID_SIGNAL_DATA",
            "Faltan datos o no se pueden convertir a números",
        )
        return None, None

    entry_gap_pct = (
        (entry / entry_reference - 1) * 100
        if entry_reference > 0
        else None
    )

    _update_signal_diagnostic(
        diagnostic,
        signal_reference=entry_reference,
        signal_close=signal_close,
        entry=entry,
        stop=stop,
        target=target,
        score=score,
        atr=atr,
        entry_gap_pct=entry_gap_pct,
    )

    values = (
        signal_close,
        entry_reference,
        stop,
        target,
        entry,
        atr,
    )

    if not all(np.isfinite(value) for value in values):
        _finish_signal_diagnostic(
            diagnostic,
            "NON_FINITE_SIGNAL_DATA",
            "La señal contiene valores no finitos",
        )
        return None, None

    if (
        signal_close <= 0
        or entry_reference <= 0
        or entry <= 0
        or atr <= 0
        or stop <= 0
        or target <= 0
    ):
        _finish_signal_diagnostic(
            diagnostic,
            "INVALID_PRICE_LEVELS",
            "La señal contiene precios cero o negativos",
        )
        return None, None

    if not stop < entry_reference < target:
        _finish_signal_diagnostic(
            diagnostic,
            "INVALID_SIGNAL_LEVELS",
            "Los niveles deben cumplir stop < referencia < objetivo",
        )
        return None, None

    initial_risk = entry - stop

    _update_signal_diagnostic(
        diagnostic,
        initial_risk_per_unit=initial_risk,
    )

    if not np.isfinite(initial_risk) or initial_risk <= 0:
        _finish_signal_diagnostic(
            diagnostic,
            "ENTRY_AT_OR_BELOW_STOP",
            "La entrada real está en el stop o por debajo",
        )
        return None, None

    if entry >= target:
        _finish_signal_diagnostic(
            diagnostic,
            "ENTRY_AT_OR_ABOVE_TARGET",
            "La entrada real ya ha alcanzado o superado el objetivo",
        )
        return None, None

    actual_reward = target - entry

    _update_signal_diagnostic(
        diagnostic,
        actual_reward_per_unit=actual_reward,
    )

    if not np.isfinite(actual_reward) or actual_reward <= 0:
        _finish_signal_diagnostic(
            diagnostic,
            "INVALID_REWARD",
            "La recompensa potencial no es positiva",
        )
        return None, None

    actual_rr = actual_reward / initial_risk

    _update_signal_diagnostic(
        diagnostic,
        actual_rr=actual_rr,
    )

    if not np.isfinite(actual_rr):
        _finish_signal_diagnostic(
            diagnostic,
            "INVALID_RR",
            "El ratio riesgo/recompensa no es finito",
        )
        return None, None

    if actual_rr < (MIN_EXECUTION_RR - RR_TOLERANCE):
        _record_rr_rejection(
            asset=asset,
            ticker=ticker,
            interval=interval,
            signal_time=df.index[index],
            entry_time=df.index[entry_index],
            entry_reference=entry_reference,
            entry=entry,
            stop=stop,
            target=target,
            initial_risk=initial_risk,
            actual_reward=actual_reward,
            actual_rr=actual_rr,
        )

        _finish_signal_diagnostic(
            diagnostic,
            "RR_REJECTED",
            (
                f"RR real {actual_rr:.6f} inferior "
                f"al mínimo {MIN_EXECUTION_RR:.2f}"
            ),
        )
        return None, None

    candidate = {
        "index": index,
        "entry_index": entry_index,
        "signal_time": df.index[index],
        "entry_time": df.index[entry_index],
        "signal_close": signal_close,
        "entry_reference": entry_reference,
        "entry": entry,
        "stop": stop,
        "target": target,
        "atr": atr,
        "score": score,
        "initial_risk": initial_risk,
        "actual_reward": actual_reward,
        "actual_rr": actual_rr,
        "diagnostic": diagnostic,
    }

    return candidate, None


def _estimate_backtest_cost_fields(asset, trade):
    """Adds a per-trade cost scenario using the configured reference capital."""
    try:
        entry = float(trade["entry"])
        exit_price = float(trade["exit"])
        stop = float(trade["stop"])
        score = float(trade["score"])
        risk_per_unit = entry - stop
        risk_pct = float(risk_pct_for_score(score))
        capital = float(INITIAL_CAPITAL_USD)
        if (
            not np.isfinite(risk_per_unit)
            or risk_per_unit <= 0
            or not np.isfinite(risk_pct)
            or risk_pct <= 0
            or not np.isfinite(capital)
            or capital <= 0
        ):
            raise ValueError("No se puede estimar el tamaño hipotético")
        risk_budget_target = capital * risk_pct
        quantity = min(
            risk_budget_target / risk_per_unit,
            capital / entry,
        )
        actual_risk_budget = quantity * risk_per_unit
        if quantity <= 0 or actual_risk_budget <= 0:
            raise ValueError("Tamaño o riesgo hipotético no válido")

        entry_time = pd.Timestamp(trade["entry_time"])
        exit_time = pd.Timestamp(trade["exit_time"])
        held_days = max(
            0.0,
            (exit_time - entry_time).total_seconds() / 86400.0,
        )
        execution_type = ETORO_EXECUTION_TYPES.get(asset, "UNKNOWN")
        overnight_units = None if execution_type == "CFD" and held_days > 0 else 0.0

        costs = estimate_etoro_trade_costs(
            asset,
            entry,
            exit_price,
            quantity,
            execution_type=execution_type,
            market_spread_bps=ETORO_MARKET_SPREAD_BPS.get(asset),
            overnight_charge_units=overnight_units,
        )
        gross_pnl = (exit_price - entry) * quantity
        known_costs = float(costs.get("known_costs_total", 0.0))
        known_fee_adjusted_pnl = gross_pnl - known_costs

        return {
            "hypothetical_quantity": round(quantity, 10),
            "hypothetical_risk_budget_usd": round(actual_risk_budget, 6),
            "hypothetical_gross_pnl_usd": round(gross_pnl, 6),
            "commission_cost_usd": round(float(costs.get("commission_cost", 0.0)), 6),
            "market_spread_cost_usd": (
                round(float(costs["market_spread_cost"]), 6)
                if costs.get("market_spread_cost") is not None else None
            ),
            "fx_conversion_cost_usd": round(float(costs.get("fx_conversion_cost", 0.0)), 6),
            "overnight_cost_usd": (
                round(float(costs["overnight_cost"]), 6)
                if costs.get("overnight_cost") is not None else None
            ),
            "known_costs_total_usd": round(known_costs, 6),
            "known_fee_adjusted_pnl_usd": round(known_fee_adjusted_pnl, 6),
            "result_R_after_known_fees": round(
                known_fee_adjusted_pnl / actual_risk_budget, 4
            ),
            "costs_complete": bool(costs.get("complete", False)),
            "unpriced_costs": ";".join(costs.get("unpriced_costs", [])),
            "cost_model_execution_type": execution_type,
            "cost_model_assumptions": "; ".join(costs.get("assumptions", [])),
        }
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return {
            "hypothetical_quantity": None,
            "hypothetical_risk_budget_usd": None,
            "hypothetical_gross_pnl_usd": None,
            "commission_cost_usd": None,
            "market_spread_cost_usd": None,
            "fx_conversion_cost_usd": None,
            "overnight_cost_usd": None,
            "known_costs_total_usd": None,
            "known_fee_adjusted_pnl_usd": None,
            "result_R_after_known_fees": None,
            "costs_complete": False,
            "unpriced_costs": "cost_model_calculation_failed",
            "cost_model_execution_type": ETORO_EXECUTION_TYPES.get(asset, "UNKNOWN"),
            "cost_model_assumptions": "",
        }


# ---------------------------------------------------------
# SIMULACIÓN DE UNA OPERACIÓN
# ---------------------------------------------------------

def _simulate_candidate(
    asset,
    ticker,
    interval,
    df,
    candidate,
    max_bars,
    asset_type,
):
    index = candidate["index"]
    entry_index = candidate["entry_index"]

    entry = candidate["entry"]
    stop = candidate["stop"]
    target = candidate["target"]
    initial_risk = candidate["initial_risk"]
    actual_rr = candidate["actual_rr"]

    diagnostic = candidate["diagnostic"]

    last_index = entry_index + max_bars - 1

    exit_price = None
    exit_reason = None
    exit_index = last_index

    for j in range(entry_index, last_index + 1):
        if j > entry_index:
            gap_error = _validate_execution_gaps(
                df,
                j - 1,
                j,
                interval,
                asset_type,
                asset=asset,
            )

            if gap_error:
                _finish_signal_diagnostic(
                    diagnostic,
                    "BACKTEST_ABORTED_GAP",
                    gap_error,
                )
                return None, gap_error

        candle = df.iloc[j]

        try:
            candle_open = float(candle["Open"])
            candle_high = float(candle["High"])
            candle_low = float(candle["Low"])

        except (TypeError, ValueError, KeyError):
            error = (
                f"Vela inválida durante la ejecución "
                f"en el índice {j}"
            )

            _finish_signal_diagnostic(
                diagnostic,
                "INVALID_EXECUTION_CANDLE",
                error,
            )

            return None, error

        if not all(
            np.isfinite(value)
            for value in (
                candle_open,
                candle_high,
                candle_low,
            )
        ):
            error = (
                f"Valores OHLC no finitos "
                f"en el índice {j}"
            )

            _finish_signal_diagnostic(
                diagnostic,
                "NON_FINITE_EXECUTION_CANDLE",
                error,
            )

            return None, error

        if candle_open <= stop:
            exit_price = candle_open
            exit_reason = "STOP_GAP"
            exit_index = j
            break

        if candle_open >= target:
            exit_price = target
            exit_reason = "TARGET_GAP"
            exit_index = j
            break

        # Si stop y objetivo aparecen en la misma vela,
        # se prioriza el stop por prudencia.
        if candle_low <= stop:
            exit_price = stop
            exit_reason = "STOP"
            exit_index = j
            break

        if candle_high >= target:
            exit_price = target
            exit_reason = "TARGET"
            exit_index = j
            break

    if exit_price is None:
        try:
            exit_price = float(
                df.iloc[last_index]["Close"]
            )
        except (TypeError, ValueError, KeyError):
            error = (
                "No se pudo obtener el cierre "
                "de la última vela"
            )

            _finish_signal_diagnostic(
                diagnostic,
                "INVALID_MAX_HOLD_CLOSE",
                error,
            )

            return None, error

        exit_reason = "MAX_HOLD"

    if not np.isfinite(exit_price) or exit_price <= 0:
        error = "El precio de salida no es válido"

        _finish_signal_diagnostic(
            diagnostic,
            "INVALID_EXIT_PRICE",
            error,
        )

        return None, error

    pnl_per_unit = exit_price - entry
    result_r = pnl_per_unit / initial_risk

    if not np.isfinite(result_r):
        error = "El resultado R no es finito"

        _finish_signal_diagnostic(
            diagnostic,
            "INVALID_RESULT_R",
            error,
        )

        return None, error

    duration_bars = exit_index - entry_index + 1

    trade = {
        "asset": asset,
        "ticker": ticker,
        "timeframe": interval,
        "signal_time": str(candidate["signal_time"]),
        "entry_time": str(candidate["entry_time"]),
        "exit_time": str(df.index[exit_index]),
        "score": candidate["score"],
        "signal_reference": round(
            candidate["entry_reference"],
            6,
        ),
        "signal_close": round(
            candidate["signal_close"],
            6,
        ),
        "entry": round(entry, 6),
        "entry_gap_pct": round(
            (
                entry / candidate["entry_reference"] - 1
            ) * 100,
            4,
        ),
        "atr_at_signal": round(
            candidate["atr"],
            6,
        ),
        "initial_risk_per_unit": round(
            initial_risk,
            6,
        ),
        "stop": round(stop, 6),
        "target": round(target, 6),
        "execution_rr": round(actual_rr, 6),
        "exit": round(exit_price, 6),
        "exit_reason": exit_reason,
        "duration_bars": duration_bars,
        "pnl_per_unit": round(pnl_per_unit, 6),
        "result_R": round(result_r, 4),
        "data_source": _provider_for_asset(asset, interval),
        "price_adjustment_policy": (
            "raw_unadjusted_intraday"
            if interval == "15m"
            else "split_adjusted_ohlc_no_dividend_adjustment"
        ),
        "costs_included": False,
        "known_fee_cost_model_applied": True,
        "position_sizing_included": False,
    }

    trade.update(_estimate_backtest_cost_fields(asset, trade))

    _update_signal_diagnostic(
        diagnostic,
        exit_time=str(df.index[exit_index]),
        exit=round(exit_price, 6),
        exit_reason=exit_reason,
        result_R=round(result_r, 4),
    )

    _finish_signal_diagnostic(
        diagnostic,
        "EXECUTED",
        None,
    )

    return {
        "trade": trade,
        "exit_index": exit_index,
    }, None


# ---------------------------------------------------------
# BACKTEST
# ---------------------------------------------------------

def run_backtest(asset, ticker, interval):
    """
    Diagnostica todas las candidatas de cada vela evaluable.

    La simulación sigue siendo long-only y no permite
    operaciones solapadas.
    """

    if interval not in PERIODS:
        return [], "Temporalidad no admitida"

    data = download_history(
        ticker,
        interval,
        asset=asset,
    )

    if data is None:
        return [], DOWNLOAD_ERRORS.get(
            (ticker, interval),
            "Causa desconocida durante la descarga",
        )

    validation_error = _ohlc_validation_error(data)

    if validation_error:
        return [], (
            f"Histórico rechazado antes de calcular indicadores: "
            f"{validation_error}"
        )

    try:
        df = add_indicators(data)
    except Exception as exc:
        return [], (
            f"Error calculando indicadores: "
            f"{type(exc).__name__}: {exc}"
        )

    if df is None or not isinstance(df, pd.DataFrame):
        return [], (
            "El cálculo de indicadores no devolvió un DataFrame válido"
        )

    if len(df) < MIN_BARS:
        return [], (
            f"Histórico insuficiente: {len(df)} filas; "
            f"mínimo {MIN_BARS}"
        )

    validation_error = _ohlc_validation_error(df)

    if validation_error:
        return [], (
            f"Datos inválidos después de calcular indicadores: "
            f"{validation_error}"
        )

    if not isinstance(df.index, pd.DatetimeIndex):
        return [], "El índice temporal no es un DatetimeIndex"

    if df.index.hasnans:
        return [], "El índice temporal contiene fechas inválidas"

    if not df.index.is_monotonic_increasing:
        return [], "El índice temporal no está ordenado"

    if df.index.has_duplicates:
        return [], "El índice temporal contiene duplicados"

    max_bars = PERIODS[interval]["max_bars"]
    asset_type = _get_asset_type(asset)

    # -----------------------------------------------------
    # FASE 1: DETECTAR Y DIAGNOSTICAR CANDIDATAS
    # -----------------------------------------------------

    candidates = []

    last_evaluable_index = len(df) - max_bars - 1

    for i in range(MIN_BARS - 1, last_evaluable_index + 1):
        signal = evaluate_signal_at(
            data=df,
            asset=asset,
            timeframe=interval,
            index=i,
        )

        if not isinstance(signal, dict):
            continue

        if signal.get("action") != "BUY CANDIDATE":
            continue

        candidate, error = _prepare_candidate(
            asset=asset,
            ticker=ticker,
            interval=interval,
            df=df,
            index=i,
            signal=signal,
            asset_type=asset_type,
        )

        if error:
            return [], error

        if candidate is not None:
            candidates.append(candidate)

    # -----------------------------------------------------
    # FASE 2: SIMULAR SIN SOLAPAR OPERACIONES
    # -----------------------------------------------------

    trades = []
    last_exit_index = -1

    for candidate in candidates:
        entry_index = candidate["entry_index"]
        diagnostic = candidate["diagnostic"]

        if entry_index <= last_exit_index:

            if 0 <= last_exit_index < len(df):
                active_trade_exit_time = str(
                    df.index[last_exit_index]
                )
            else:
                active_trade_exit_time = None

            _update_signal_diagnostic(
                diagnostic,
                active_trade_exit_time=active_trade_exit_time,
            )

            _finish_signal_diagnostic(
                diagnostic,
                "SKIPPED_OVERLAPPING_TRADE",
                (
                    "La entrada prevista coincide con la vela "
                    "de salida de una operación anterior "
                    "o se produce antes de ella"
                ),
            )

            continue

        simulation, error = _simulate_candidate(
            asset=asset,
            ticker=ticker,
            interval=interval,
            df=df,
            candidate=candidate,
            max_bars=max_bars,
            asset_type=asset_type,
        )

        if error:
            return [], error

        trades.append(simulation["trade"])
        last_exit_index = simulation["exit_index"]

    return trades, None


# ---------------------------------------------------------
# MÉTRICAS
# ---------------------------------------------------------

def _empty_metrics():
    return {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate_pct": 0,
        "total_R": 0,
        "average_R": 0,
        "profit_factor_R": None,
        "max_drawdown_R": 0,
        "average_duration_bars": 0,
        "known_fee_adjusted_total_R": 0,
        "known_fee_adjusted_average_R": 0,
        "trades_with_complete_costs": 0,
        "trades_with_unpriced_costs": 0,
    }


def _calculate_drawdown(results):
    if len(results) == 0:
        return 0.0

    equity = np.cumsum(results)

    peaks = np.maximum.accumulate(
        np.maximum(equity, 0)
    )

    drawdown = peaks - equity

    return float(drawdown.max())


def _is_finite_metric(value):
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError, OverflowError):
        return False


def calculate_metrics(trades):
    if trades is None:
        return _empty_metrics()

    if isinstance(trades, pd.DataFrame):
        if trades.empty or "result_R" not in trades.columns:
            return _empty_metrics()

        records = trades.to_dict("records")
    else:
        try:
            records = list(trades)
        except (TypeError, ValueError):
            return _empty_metrics()

    valid_trades = []

    for trade in records:
        if not isinstance(trade, dict):
            continue

        try:
            result = float(trade["result_R"])
            duration = float(trade["duration_bars"])
        except (
            TypeError,
            ValueError,
            KeyError,
            OverflowError,
        ):
            continue

        if not np.isfinite(result) or not np.isfinite(duration):
            continue

        if duration <= 0:
            continue

        valid_trades.append((result, duration))

    if not valid_trades:
        return _empty_metrics()

    results = np.array(
        [item[0] for item in valid_trades],
        dtype=float,
    )

    durations = np.array(
        [item[1] for item in valid_trades],
        dtype=float,
    )

    wins = results[results > 0]
    losses = results[results < 0]

    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else None
    )

    return {
        "trades": len(results),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(
            len(wins) / len(results) * 100,
            2,
        ),
        "total_R": round(float(results.sum()), 4),
        "average_R": round(float(results.mean()), 4),
        "profit_factor_R": (
            round(profit_factor, 4)
            if profit_factor is not None
            else None
        ),
        "max_drawdown_R": round(
            _calculate_drawdown(results),
            4,
        ),
        "average_duration_bars": round(
            float(durations.mean()),
            2,
        ),
        "known_fee_adjusted_total_R": round(
            sum(
                float(trade["result_R_after_known_fees"])
                for trade in records
                if isinstance(trade, dict)
                and _is_finite_metric(trade.get("result_R_after_known_fees"))
            ),
            4,
        ),
        "known_fee_adjusted_average_R": (
            round(
                np.mean([
                    float(trade["result_R_after_known_fees"])
                    for trade in records
                    if isinstance(trade, dict)
                    and _is_finite_metric(trade.get("result_R_after_known_fees"))
                ]),
                4,
            )
            if any(
                isinstance(trade, dict)
                and _is_finite_metric(trade.get("result_R_after_known_fees"))
                for trade in records
            )
            else 0
        ),
        "trades_with_complete_costs": sum(
            1 for trade in records
            if isinstance(trade, dict) and trade.get("costs_complete") is True
        ),
        "trades_with_unpriced_costs": sum(
            1 for trade in records
            if isinstance(trade, dict) and trade.get("costs_complete") is False
        ),
    }


# ---------------------------------------------------------
# RESUMEN DE OPERACIONES
# ---------------------------------------------------------

def generate_operations_summary(trades):
    columns = [
        "tipo",
        "activo",
        "temporalidad",
        "periodo",
        "operaciones",
        "ganadoras",
        "perdedoras",
        "acierto_pct",
        "resultado_R",
        "media_R",
        "media_ganadoras_R",
        "media_perdedoras_R",
        "mejor_operacion_R",
        "peor_operacion_R",
        "profit_factor",
        "drawdown_max_R",
        "racha_perdedora_max",
        "STOP",
        "STOP_GAP",
        "TARGET",
        "TARGET_GAP",
        "MAX_HOLD",
    ]

    if trades is None:
        return pd.DataFrame(columns=columns)

    if isinstance(trades, pd.DataFrame):
        if trades.empty:
            return pd.DataFrame(columns=columns)

        df = trades.copy()
    else:
        try:
            df = pd.DataFrame(list(trades))
        except (TypeError, ValueError):
            return pd.DataFrame(columns=columns)

    required = [
        "result_R",
        "exit_time",
        "asset",
        "timeframe",
        "exit_reason",
    ]

    if not all(column in df.columns for column in required):
        return pd.DataFrame(columns=columns)

    df["result_R"] = pd.to_numeric(
        df["result_R"],
        errors="coerce",
    )

    df["exit_time"] = pd.to_datetime(
        df["exit_time"],
        format="mixed",
        errors="coerce",
        utc=True,
    )

    df = df.dropna(
        subset=[
            "result_R",
            "exit_time",
            "asset",
            "timeframe",
        ]
    )

    df = df[
        np.isfinite(
            df["result_R"].to_numpy(dtype=float)
        )
    ]

    if df.empty:
        return pd.DataFrame(columns=columns)

    df = df.sort_values("exit_time").copy()

    summaries = []

    def summarize(
        group,
        summary_type,
        asset,
        timeframe,
        period,
    ):
        group = group.sort_values("exit_time")
        results = group["result_R"].to_numpy(dtype=float)

        if len(results) == 0:
            return

        wins = results[results > 0]
        losses = results[results < 0]

        max_losing_streak = 0
        current_losing_streak = 0

        for result in results:
            if result < 0:
                current_losing_streak += 1
                max_losing_streak = max(
                    max_losing_streak,
                    current_losing_streak,
                )
            else:
                current_losing_streak = 0

        reasons = group["exit_reason"].value_counts()

        gross_profit = float(wins.sum())
        gross_loss = abs(float(losses.sum()))

        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else None
        )

        summaries.append({
            "tipo": summary_type,
            "activo": asset,
            "temporalidad": timeframe,
            "periodo": period,
            "operaciones": len(results),
            "ganadoras": len(wins),
            "perdedoras": len(losses),
            "acierto_pct": round(
                len(wins) / len(results) * 100,
                2,
            ),
            "resultado_R": round(
                float(results.sum()),
                4,
            ),
            "media_R": round(
                float(results.mean()),
                4,
            ),
            "media_ganadoras_R": (
                round(float(wins.mean()), 4)
                if len(wins) else None
            ),
            "media_perdedoras_R": (
                round(float(losses.mean()), 4)
                if len(losses) else None
            ),
            "mejor_operacion_R": round(
                float(results.max()),
                4,
            ),
            "peor_operacion_R": round(
                float(results.min()),
                4,
            ),
            "profit_factor": (
                round(profit_factor, 4)
                if profit_factor is not None
                else None
            ),
            "drawdown_max_R": round(
                _calculate_drawdown(results),
                4,
            ),
            "racha_perdedora_max": max_losing_streak,
            "STOP": int(reasons.get("STOP", 0)),
            "STOP_GAP": int(reasons.get("STOP_GAP", 0)),
            "TARGET": int(reasons.get("TARGET", 0)),
            "TARGET_GAP": int(reasons.get("TARGET_GAP", 0)),
            "MAX_HOLD": int(reasons.get("MAX_HOLD", 0)),
        })

    for (asset, timeframe), group in df.groupby(
        ["asset", "timeframe"]
    ):
        summarize(
            group,
            "ACTIVO_TEMPORALIDAD",
            asset,
            timeframe,
            "TOTAL",
        )

    df["month"] = df["exit_time"].dt.strftime("%Y-%m")

    for (asset, timeframe, month), group in df.groupby(
        ["asset", "timeframe", "month"]
    ):
        summarize(
            group,
            "MENSUAL",
            asset,
            timeframe,
            month,
        )

    return pd.DataFrame(
        summaries,
        columns=columns,
    )


# ---------------------------------------------------------
# EJECUCIÓN PRINCIPAL
# ---------------------------------------------------------

def main():
    all_trades = []
    summaries = []
    errors = []
    overlapping_signals = []

    DOWNLOAD_ERRORS.clear()
    EXCLUDED_OHLC_CANDLES.clear()
    RR_REJECTIONS.clear()
    SIGNAL_DIAGNOSTICS.clear()
    SNAPSHOT_MANIFEST.clear()

    assets = get_assets()

    print(f"Trading Bot {VERSION} - Backtest")
    print(f"Activos habilitados: {len(assets)}")
    print("Modo: histórico, compras únicamente")
    print("Estrategia: sin cambios")
    print("Datos: mismo motor y proveedores que el escáner (snapshots guardados)")
    print(f"Modo de datos: {BACKTEST_DATA_MODE}")
    print(f"Directorio de históricos: {SNAPSHOT_DIR}")
    print("Costes: comisiones públicas modeladas parcialmente; spread real/financiación sin confirmar")
    print("Sizing: solo tamaño hipotético por operación para estimar costes; no es simulación de cartera")
    print("Entrada: apertura de la vela siguiente")
    print("Stop y objetivo: niveles originales")
    print(f"RR mínimo: {MIN_EXECUTION_RR:.2f}:1")
    print(f"Tolerancia RR: {RR_TOLERANCE}")
    print("Diagnóstico: todas las candidatas")
    print("Simulación: sin operaciones solapadas")
    print("Calendario: festivos y sesiones por mercado")
    print("Proveedores: Twelve Data / Yahoo Finance / EODHD según motor live")
    print("Ajuste de precios: nativo del proveedor; equivalencia no verificada")
    print("Snapshots: hash, proveedor, filas, orden y timestamps verificados")

    if BACKTEST_DATA_MODE == "replay":
        print("SEGURIDAD: no se descargarán ni sobrescribirán snapshots.")
    else:
        print("ATENCIÓN: el modo download puede sustituir snapshots existentes.")

    print("-" * 60)

    for asset, ticker in assets:
        for interval in ("15m", "1d"):
            print(f"Analizando {asset} ({interval})...")

            try:
                trades, error = run_backtest(
                    asset,
                    ticker,
                    interval,
                )

                if error:
                    errors.append({
                        "asset": asset,
                        "ticker": ticker,
                        "timeframe": interval,
                        "error": error,
                    })

                    print(f"  Sin resultados: {error}")
                    continue

                all_trades.extend(trades)

                metrics = calculate_metrics(trades)

                summaries.append({
                    "asset": asset,
                    "timeframe": interval,
                    **metrics,
                })

                print(
                    f"  Operaciones: {metrics['trades']} | "
                    f"Resultado bruto: {metrics['total_R']} R"
                )

            except Exception as exc:
                error_message = (
                    f"{type(exc).__name__}: {exc}"
                )

                errors.append({
                    "asset": asset,
                    "ticker": ticker,
                    "timeframe": interval,
                    "error": error_message,
                })

                print(f"  Error: {error_message}")

    # -----------------------------------------------------
    # SEÑALES SOLAPADAS
    # -----------------------------------------------------

    for diagnostic in SIGNAL_DIAGNOSTICS:
        if diagnostic["signal_status"] != "SKIPPED_OVERLAPPING_TRADE":
            continue

        overlapping_signals.append({
            "asset": diagnostic["asset"],
            "ticker": diagnostic["ticker"],
            "timeframe": diagnostic["timeframe"],
            "signal_time": diagnostic["signal_time"],
            "entry_time": diagnostic["entry_time"],
            "signal_score": diagnostic["score"],
            "signal_reference": diagnostic["signal_reference"],
            "entry": diagnostic["entry"],
            "stop": diagnostic["stop"],
            "target": diagnostic["target"],
            "actual_rr": diagnostic["actual_rr"],
            "active_trade_exit_time": (
                diagnostic["active_trade_exit_time"]
            ),
            "reason": diagnostic["rejection_reason"],
        })

    # -----------------------------------------------------
    # EXPORTACIÓN DE RESULTADOS
    # -----------------------------------------------------

    pd.DataFrame(all_trades).to_csv(
        "operaciones_backtest_v3.csv",
        index=False,
    )

    cost_columns = [
        "asset",
        "timeframe",
        "entry_time",
        "exit_time",
        "cost_model_execution_type",
        "hypothetical_quantity",
        "hypothetical_risk_budget_usd",
        "hypothetical_gross_pnl_usd",
        "commission_cost_usd",
        "market_spread_cost_usd",
        "fx_conversion_cost_usd",
        "overnight_cost_usd",
        "known_costs_total_usd",
        "known_fee_adjusted_pnl_usd",
        "result_R",
        "result_R_after_known_fees",
        "costs_complete",
        "unpriced_costs",
        "cost_model_assumptions",
    ]
    pd.DataFrame(
        [
            {column: trade.get(column) for column in cost_columns}
            for trade in all_trades
        ],
        columns=cost_columns,
    ).to_csv(
        "costes_backtest_v3.csv",
        index=False,
    )

    pd.DataFrame(summaries).to_csv(
        "resultados_backtest_v3.csv",
        index=False,
    )

    pd.DataFrame(errors).to_csv(
        "errores_backtest_v3.csv",
        index=False,
    )

    operations_summary = generate_operations_summary(
        all_trades
    )

    operations_summary.to_csv(
        "resumen_operaciones_v3.csv",
        index=False,
    )

    excluded_columns = [
        "ticker",
        "timeframe",
        "fecha",
        "open",
        "high",
        "low",
        "close",
        "motivo",
    ]

    pd.DataFrame(
        EXCLUDED_OHLC_CANDLES,
        columns=excluded_columns,
    ).to_csv(
        "velas_ohlc_excluidas_v3.csv",
        index=False,
    )

    pd.DataFrame(
        RR_REJECTIONS,
        columns=RR_REJECTION_COLUMNS,
    ).to_csv(
        "rechazos_rr_backtest_v3.csv",
        index=False,
    )

    pd.DataFrame(
        SIGNAL_DIAGNOSTICS,
        columns=SIGNAL_DIAGNOSTIC_COLUMNS,
    ).to_csv(
        "diagnostico_senales_v3.csv",
        index=False,
    )

    pd.DataFrame(
        overlapping_signals,
        columns=OVERLAPPING_SIGNAL_COLUMNS,
    ).to_csv(
        "senales_solapadas_v3.csv",
        index=False,
    )

    if BACKTEST_DATA_MODE == "download":
        try:
            _write_snapshot_manifest()
        except Exception as exc:
            errors.append({
                "asset": "SYSTEM",
                "ticker": "",
                "timeframe": "",
                "error": (
                    f"No se pudo escribir el manifiesto: {exc}"
                ),
            })

            pd.DataFrame(errors).to_csv(
                "errores_backtest_v3.csv",
                index=False,
            )
    else:
        print("Manifiesto original conservado; no se ha sobrescrito.")

    # -----------------------------------------------------
    # COMPROBACIÓN DEL REGISTRO DE SOLAPAMIENTOS
    # -----------------------------------------------------

    missing_overlap_times = sum(
        1
        for signal in overlapping_signals
        if not signal["active_trade_exit_time"]
    )

    print("-" * 60)
    print(f"Versión: {VERSION}")
    print(f"Modo de datos: {BACKTEST_DATA_MODE}")
    print(f"Operaciones ejecutadas: {len(all_trades)}")
    print(
        "Señales candidatas registradas: "
        f"{len(SIGNAL_DIAGNOSTICS)}"
    )
    print(f"Rechazos por RR: {len(RR_REJECTIONS)}")
    print(
        "Señales solapadas no simuladas: "
        f"{len(overlapping_signals)}"
    )
    print(
        "Solapamientos sin hora de salida: "
        f"{missing_overlap_times}"
    )
    print(f"RR mínimo: {MIN_EXECUTION_RR:.2f}:1")
    print(f"Errores: {len(errors)}")
    print(
        "Velas OHLC incoherentes: "
        f"{len(EXCLUDED_OHLC_CANDLES)}"
    )
    print(
        f"Históricos reproducidos/registrados: "
        f"{len(SNAPSHOT_MANIFEST)}"
    )
    print("Archivos CSV generados.")
    print(
        "IMPORTANTE: result_R es bruto. result_R_after_known_fees "
        "solo resta comisiones conocidas estimadas; no incluye spread "
        "de mercado ni costes no verificados. No es una simulación de cartera."
    )

    if BACKTEST_DATA_MODE == "replay":
        print(
            "Los snapshots originales no se han modificado."
        )


if __name__ == "__main__":
    main()
