
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

from config import ASSETS
from indicators import add_indicators
from strategies import evaluate_signal_at


VERSION = "3.1"

PERIODS = {
    "15m": {"period": "60d", "max_bars": 32},
    "1d": {"period": "2y", "max_bars": 60},
}

MIN_BARS = 220


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


def download_history(ticker, interval):
    """Descarga histórico de Yahoo Finance."""
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
        warnings.warn(f"Error descargando {ticker}: {exc}")
        return None

    if data is None or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close", "Volume"]

    if not all(column in data.columns for column in required):
        return None

    data = data[required].copy()

    for column in required:
        data[column] = pd.to_numeric(
            data[column], errors="coerce"
        )

    data = data.replace([np.inf, -np.inf], np.nan)
    data = data.dropna(subset=required)
    data = data[~data.index.duplicated(keep="last")]
    data = data.sort_index()

    # Conservador: excluimos la última vela por si está incompleta.
    if len(data) > 1:
        data = data.iloc[:-1]

    if len(data) < MIN_BARS:
        return None

    return data


def run_backtest(asset, ticker, interval):
    """
    Backtest con los mismos indicadores y reglas de señal
    que utiliza el análisis en directo.
    """

    data = download_history(ticker, interval)

    if data is None:
        return [], "Datos insuficientes o no disponibles"

    try:
        df = add_indicators(data)
    except Exception as exc:
        return [], f"Error calculando indicadores: {exc}"

    if df is None or len(df) < MIN_BARS:
        return [], "Indicadores o histórico insuficientes"

    max_bars = PERIODS[interval]["max_bars"]

    trades = []
    i = MIN_BARS - 1

    while i < len(df) - 1:
        signal = evaluate_signal_at(
            data=df,
            asset=asset,
            timeframe=interval,
            index=i,
        )

        if signal.get("action") != "BUY CANDIDATE":
            i += 1
            continue

        signal_row = df.iloc[i]
        entry_index = i + 1
        entry_row = df.iloc[entry_index]

        try:
            entry = float(entry_row["Open"])
            atr = float(signal_row["ATR"])
        except (TypeError, ValueError, KeyError):
            i += 1
            continue

        if (
            not np.isfinite(entry)
            or not np.isfinite(atr)
            or entry <= 0
            or atr <= 0
        ):
            i += 1
            continue

        # La entrada se produce en la apertura de la vela siguiente.
        stop = entry - 1.5 * atr
        target = entry + 3.0 * atr
        initial_risk = entry - stop

        if initial_risk <= 0 or stop <= 0:
            i += 1
            continue

        last_index = min(
            entry_index + max_bars - 1,
            len(df) - 1,
        )

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
                continue

            if not all(
                np.isfinite(value)
                for value in (
                    candle_open,
                    candle_high,
                    candle_low,
                )
            ):
                continue

            # Un hueco bajista puede producir una pérdida superior a 1R.
            if candle_open <= stop:
                exit_price = candle_open
                exit_reason = "STOP_GAP"
                exit_index = j
                break

            # Objetivo alcanzado al abrir: ejecución conservadora al objetivo.
            if candle_open >= target:
                exit_price = target
                exit_reason = "TARGET_GAP"
                exit_index = j
                break

            # Si ambos niveles se alcanzan dentro de la misma vela,
            # se prioriza siempre el stop.
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
            exit_price = float(df.iloc[last_index]["Close"])
            exit_reason = "MAX_HOLD"

        if not np.isfinite(exit_price):
            i += 1
            continue

        pnl_per_unit = exit_price - entry
        result_r = pnl_per_unit / initial_risk
        duration_bars = exit_index - entry_index + 1

        trades.append({
            "asset": asset,
            "ticker": ticker,
            "timeframe": interval,
            "signal_time": str(df.index[i]),
            "entry_time": str(df.index[entry_index]),
            "exit_time": str(df.index[exit_index]),
            "score": signal.get("score"),
            "entry": round(entry, 6),
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

        # Una sola operación simultánea por activo y temporalidad.
        i = exit_index + 1

    return trades, None


def calculate_metrics(trades):
    """Calcula métricas de una secuencia independiente de operaciones."""

    if not trades:
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

    results = np.array(
        [trade["result_R"] for trade in trades],
        dtype=float,
    )

    results = results[np.isfinite(results)]

    if len(results) == 0:
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

    wins = results[results > 0]
    losses = results[results < 0]

    equity = np.cumsum(results)
    peaks = np.maximum.accumulate(
        np.maximum(equity, 0)
    )
    drawdown = peaks - equity

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
            len(wins) / len(results) * 100, 2
        ),
        "total_R": round(float(results.sum()), 4),
        "average_R": round(float(results.mean()), 4),
        "profit_factor_R": (
            round(profit_factor, 4)
            if profit_factor is not None
            else None
        ),
        "max_drawdown_R": round(
            float(drawdown.max()), 4
        ),
        "average_duration_bars": round(
            float(np.mean([
                trade["duration_bars"]
                for trade in trades
            ])),
            2,
        ),
    }


def main():
    all_trades = []
    summaries = []
    errors = []

    assets = get_assets()

    print(f"Trading Bot {VERSION} - Backtest")
    print(f"Activos habilitados: {len(assets)}")
    print("Modo: histórico, compras únicamente")
    print("Estrategia: compartida con el análisis en directo")
    print("Datos: Yahoo Finance")
    print("Costes: no incluidos ni verificados")
    print("Sizing: desactivado")
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
                errors.append({
                    "asset": asset,
                    "timeframe": interval,
                    "error": str(exc),
                })
                print(f"  Error: {exc}")

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

    print("-" * 50)
    print(f"Operaciones totales: {len(all_trades)}")
    print(f"Combinaciones analizadas: {len(summaries)}")
    print(f"Errores o datos insuficientes: {len(errors)}")
    print("Archivos CSV generados.")
    print(
        "IMPORTANTE: resultados brutos, sin costes, "
        "sin sizing y sin simulación de cartera."
    )


if __name__ == "__main__":
    main()
