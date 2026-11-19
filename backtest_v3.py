
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

from config import ASSETS
from indicators import add_indicators
from strategies import evaluate_signal_at


VERSION = "3.7"

PERIODS = {
    "15m": {"period": "60d", "max_bars": 32},
    "1d": {"period": "2y", "max_bars": 60},
}

MIN_BARS = 220

DOWNLOAD_ERRORS = {}

# Registro de velas incoherentes detectadas.
# Si aparece alguna, se rechaza el histórico completo.
EXCLUDED_OHLC_CANDLES = []


def get_assets():
    """Obtiene los activos habilitados en config.py."""

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


def _normalize_columns(data):
    """Normaliza columnas de Yahoo Finance."""

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
    """Identifica velas con relaciones OHLC imposibles."""

    return (
        (data["High"] < data["Low"])
        | (data["High"] < data["Open"])
        | (data["High"] < data["Close"])
        | (data["Low"] > data["Open"])
        | (data["Low"] > data["Close"])
    )


def _ohlc_validation_error(data):
    """Valida los precios OHLC y devuelve el motivo del error."""

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
            return f"No se puede convertir {column} a números: {exc}"

        if not np.isfinite(values).all():
            return f"La columna {column} contiene valores no finitos"

        if (values <= 0).any():
            return f"La columna {column} contiene precios cero o negativos"

    invalid_count = int(_ohlc_incoherent_mask(data).sum())

    if invalid_count:
        return (
            f"Se detectaron {invalid_count} velas OHLC incoherentes. "
            "Se rechaza el histórico completo para evitar "
            "operaciones a través de huecos."
        )

    return None


def _valid_ohlc(data):
    """Comprueba que los precios OHLC sean válidos."""

    return _ohlc_validation_error(data) is None


def download_history(ticker, interval):
    """
    Descarga el histórico.

    No elimina velas OHLC incoherentes: rechaza el histórico
    completo para evitar crear discontinuidades artificiales.
    """

    key = (ticker, interval)
    DOWNLOAD_ERRORS.pop(key, None)

    if interval not in PERIODS:
        DOWNLOAD_ERRORS[key] = "Temporalidad no admitida"
        return None

    settings = PERIODS[interval]

    try:
        data = yf.download(
            ticker,
            period=settings["period"],
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
        )

    except Exception as exc:
        error = (
            f"Excepción al descargar datos de Yahoo Finance: "
            f"{type(exc).__name__}: {exc}"
        )

        DOWNLOAD_ERRORS[key] = error
        warnings.warn(f"{ticker} ({interval}): {error}")
        return None

    if data is None:
        DOWNLOAD_ERRORS[key] = "Yahoo Finance devolvió None"
        return None

    if data.empty:
        DOWNLOAD_ERRORS[key] = (
            "Yahoo Finance devolvió un histórico vacío"
        )
        return None

    raw_rows = len(data)

    try:
        data = _normalize_columns(data)

    except Exception as exc:
        DOWNLOAD_ERRORS[key] = (
            f"Error normalizando columnas: "
            f"{type(exc).__name__}: {exc}"
        )
        return None

    required = ["Open", "High", "Low", "Close"]

    missing = [
        column
        for column in required
        if column not in data.columns
    ]

    if missing:
        DOWNLOAD_ERRORS[key] = (
            f"Columnas ausentes: {missing}. "
            f"Columnas recibidas: {list(data.columns)}"
        )
        return None

    columns = required.copy()

    if "Volume" in data.columns:
        columns.append("Volume")

    try:
        data = data[columns].copy()

        # Convertimos OHLC sin eliminar silenciosamente
        # las filas que contengan valores ausentes.
        for column in required:
            data[column] = pd.to_numeric(
                data[column],
                errors="coerce",
            )

        if "Volume" in data.columns:
            data["Volume"] = pd.to_numeric(
                data["Volume"],
                errors="coerce",
            )

        # Un OHLC ausente o no numérico invalida el histórico.
        for column in required:
            if data[column].isna().any():
                missing_count = int(data[column].isna().sum())

                DOWNLOAD_ERRORS[key] = (
                    f"La columna {column} contiene "
                    f"{missing_count} valores ausentes o no numéricos. "
                    "Se rechaza el histórico para evitar huecos."
                )
                return None

        data = data.replace(
            [np.inf, -np.inf],
            np.nan,
        )

        for column in required:
            if data[column].isna().any():
                DOWNLOAD_ERRORS[key] = (
                    f"La columna {column} contiene valores no finitos"
                )
                return None

        if data.index.hasnans:
            DOWNLOAD_ERRORS[key] = (
                "El índice contiene fechas no válidas"
            )
            return None

        # Los duplicados temporales se resuelven conservando
        # el último registro de cada timestamp.
        data = data[
            ~data.index.duplicated(keep="last")
        ]

        data = data.sort_index()

    except Exception as exc:
        DOWNLOAD_ERRORS[key] = (
            f"Error limpiando histórico: "
            f"{type(exc).__name__}: {exc}"
        )
        return None

    if data.empty:
        DOWNLOAD_ERRORS[key] = (
            "No quedan velas después de la limpieza"
        )
        return None

    # Los precios cero, negativos o no finitos son errores.
    for column in required:
        values = data[column].to_numpy(dtype=float)

        if not np.isfinite(values).all():
            DOWNLOAD_ERRORS[key] = (
                f"La columna {column} contiene valores no finitos"
            )
            return None

        if (values <= 0).any():
            DOWNLOAD_ERRORS[key] = (
                f"La columna {column} contiene precios cero o negativos"
            )
            return None

    # No borramos velas incoherentes.
    # Si aparece alguna, rechazamos toda la serie.
    invalid_mask = _ohlc_incoherent_mask(data)
    invalid_rows = data.loc[invalid_mask].copy()

    invalid_count = len(invalid_rows)

    if invalid_count:
        for timestamp, candle in invalid_rows.iterrows():
            EXCLUDED_OHLC_CANDLES.append({
                "ticker": ticker,
                "timeframe": interval,
                "fecha": str(timestamp),
                "open": float(candle["Open"]),
                "high": float(candle["High"]),
                "low": float(candle["Low"]),
                "close": float(candle["Close"]),
                "motivo": "Histórico rechazado por OHLC incoherente",
            })

        DOWNLOAD_ERRORS[key] = (
            f"Histórico rechazado: {invalid_count} "
            "velas OHLC incoherentes detectadas. "
            "No se han eliminado individualmente."
        )

        print(
            f"  {ticker} ({interval}): "
            f"HISTÓRICO RECHAZADO. "
            f"{invalid_count} velas OHLC incoherentes."
        )

        return None

    validation_error = _ohlc_validation_error(data)

    if validation_error:
        DOWNLOAD_ERRORS[key] = (
            f"Histórico OHLC inválido: {validation_error}"
        )
        return None

    # Excluimos la última vela recibida para evitar utilizar
    # una vela que pudiera estar incompleta.
    if len(data) > 1:
        data = data.iloc[:-1].copy()

    final_rows = len(data)

    if final_rows < MIN_BARS:
        DOWNLOAD_ERRORS[key] = (
            f"Histórico insuficiente: se necesitan "
            f"{MIN_BARS} velas y quedan {final_rows}. "
            f"Filas originales: {raw_rows}; "
            f"periodo solicitado: {settings['period']}; "
            f"intervalo: {interval}"
        )
        return None

    DOWNLOAD_ERRORS.pop(key, None)

    return data


def run_backtest(asset, ticker, interval):
    """Backtest con los indicadores y reglas de señal actuales."""

    if interval not in PERIODS:
        return [], "Temporalidad no admitida"

    data = download_history(ticker, interval)

    if data is None:
        diagnostic = DOWNLOAD_ERRORS.get(
            (ticker, interval),
            "Causa desconocida durante la descarga",
        )

        return [], diagnostic

    # Segunda validación: protege también frente a datos
    # proporcionados por otras fuentes o pruebas.
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

    if df is None:
        return [], "El cálculo de indicadores devolvió None"

    if not isinstance(df, pd.DataFrame):
        return [], "El cálculo de indicadores no devolvió un DataFrame"

    if len(df) < MIN_BARS:
        return [], (
            f"Indicadores o histórico insuficientes: "
            f"{len(df)} filas disponibles; "
            f"mínimo requerido: {MIN_BARS}"
        )

    # Comprobamos que los indicadores no hayan alterado
    # las velas OHLC ni introducido valores inválidos.
    validation_error = _ohlc_validation_error(df)

    if validation_error:
        return [], (
            f"Datos inválidos después de calcular indicadores: "
            f"{validation_error}"
        )

    if not df.index.is_monotonic_increasing:
        return [], "El índice temporal no está ordenado"

    if df.index.has_duplicates:
        return [], "El índice temporal contiene duplicados"

    max_bars = PERIODS[interval]["max_bars"]

    trades = []
    i = MIN_BARS - 1

    while i + max_bars < len(df):

        signal = evaluate_signal_at(
            data=df,
            asset=asset,
            timeframe=interval,
            index=i,
        )

        if not isinstance(signal, dict):
            i += 1
            continue

        if signal.get("action") != "BUY CANDIDATE":
            i += 1
            continue

        signal_row = df.iloc[i]
        entry_index = i + 1
        entry_row = df.iloc[entry_index]

        try:
            signal_close = float(signal_row["Close"])
            entry = float(entry_row["Open"])
            atr = float(signal_row["ATR"])
            score = int(signal["score"])

        except (TypeError, ValueError, KeyError, OverflowError):
            i += 1
            continue

        if not all(
            np.isfinite(value)
            for value in (signal_close, entry, atr)
        ):
            i += 1
            continue

        if signal_close <= 0 or entry <= 0 or atr <= 0:
            i += 1
            continue

        # Riesgo y objetivo originales.
        stop = entry - 1.5 * atr
        target = entry + 3.0 * atr

        initial_risk = entry - stop

        if (
            not np.isfinite(stop)
            or not np.isfinite(target)
            or initial_risk <= 0
            or stop <= 0
            or target <= entry
        ):
            i += 1
            continue

        last_index = entry_index + max_bars - 1

        exit_price = None
        exit_reason = None
        exit_index = last_index

        for j in range(entry_index, last_index + 1):

            candle = df.iloc[j]

            try:
                candle_open = float(candle["Open"])
                candle_high = float(candle["High"])
                candle_low = float(candle["Low"])

            except (TypeError, ValueError, KeyError):
                return [], (
                    f"Vela inválida durante la ejecución "
                    f"en el índice {j}"
                )

            if not all(
                np.isfinite(value)
                for value in (
                    candle_open,
                    candle_high,
                    candle_low,
                )
            ):
                return [], (
                    f"Valores OHLC no finitos durante la ejecución "
                    f"en el índice {j}"
                )

            # Gap bajista: la salida se produce al precio
            # de apertura, aunque la pérdida supere -1R.
            if candle_open <= stop:
                exit_price = candle_open
                exit_reason = "STOP_GAP"
                exit_index = j
                break

            # Gap alcista: se mantiene la ejecución conservadora
            # en el precio objetivo.
            if candle_open >= target:
                exit_price = target
                exit_reason = "TARGET_GAP"
                exit_index = j
                break

            # Si stop y objetivo se alcanzan en la misma vela,
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
                i = last_index + 1
                continue

            exit_reason = "MAX_HOLD"

        if not np.isfinite(exit_price) or exit_price <= 0:
            i = exit_index + 1
            continue

        pnl_per_unit = exit_price - entry

        # R se calcula siempre respecto al riesgo inicial.
        # No se limita artificialmente a -1R.
        result_r = pnl_per_unit / initial_risk

        if not np.isfinite(result_r):
            i = exit_index + 1
            continue

        duration_bars = exit_index - entry_index + 1

        trades.append({
            "asset": asset,
            "ticker": ticker,
            "timeframe": interval,
            "signal_time": str(df.index[i]),
            "entry_time": str(df.index[entry_index]),
            "exit_time": str(df.index[exit_index]),
            "score": score,
            "signal_reference": round(signal_close, 6),
            "entry": round(entry, 6),
            "entry_gap_pct": round(
                (entry / signal_close - 1) * 100,
                4,
            ),
            "atr_at_signal": round(atr, 6),
            "initial_risk_per_unit": round(initial_risk, 6),
            "stop": round(stop, 6),
            "target": round(target, 6),
            "exit": round(exit_price, 6),
            "exit_reason": exit_reason,
            "duration_bars": duration_bars,
            "pnl_per_unit": round(pnl_per_unit, 6),
            "result_R": round(result_r, 4),
            "data_source": "Yahoo Finance",
            "costs_included": False,
            "position_sizing_included": False,
        })

        i = exit_index + 1

    return trades, None


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
    }


def _calculate_drawdown(results):
    """Drawdown de la secuencia de operaciones cerradas."""

    if len(results) == 0:
        return 0.0

    equity = np.cumsum(results)

    peaks = np.maximum.accumulate(
        np.maximum(equity, 0)
    )

    drawdown = peaks - equity

    return float(drawdown.max())


def calculate_metrics(trades):
    """Calcula métricas de una secuencia independiente."""

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

        except (TypeError, ValueError, KeyError, OverflowError):
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
    }


def generate_operations_summary(trades):
    """Genera un resumen por activo, temporalidad y mes."""

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


def main():
    all_trades = []
    summaries = []
    errors = []

    DOWNLOAD_ERRORS.clear()
    EXCLUDED_OHLC_CANDLES.clear()

    assets = get_assets()

    print(f"Trading Bot {VERSION} - Backtest")
    print(f"Activos habilitados: {len(assets)}")
    print("Modo: histórico, compras únicamente")
    print("Estrategia: compartida con el análisis en directo")
    print("Datos: Yahoo Finance")
    print("Costes: no incluidos ni verificados")
    print("Sizing: desactivado")
    print("Validación: históricos OHLC incoherentes rechazados")
    print("-" * 50)

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

    pd.DataFrame(all_trades).to_csv(
        "operaciones_backtest_v3.csv",
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

    print("-" * 50)
    print(f"Operaciones totales: {len(all_trades)}")
    print(f"Combinaciones analizadas: {len(summaries)}")
    print(f"Errores o datos insuficientes: {len(errors)}")
    print(
        f"Velas OHLC incoherentes detectadas: "
        f"{len(EXCLUDED_OHLC_CANDLES)}"
    )
    print(
        f"Filas del resumen detallado: {len(operations_summary)}"
    )
    print("Archivos CSV generados.")
    print(
        "IMPORTANTE: resultados brutos, sin costes, "
        "sin sizing y sin simulación de cartera."
    )


if __name__ == "__main__":
    main()
