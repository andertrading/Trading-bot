
import os
import math
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from config import ASSETS

warnings.filterwarnings("ignore")

VERSION = "3.0"
PERIODS = {
    "15m": {"period": "60d", "max_bars": 32},
    "1d": {"period": "2y", "max_bars": 60},
}

STOP_ATR = 1.5
TARGET_ATR = 3.0
MIN_BARS = 220


def get_assets():
    """Obtiene únicamente los activos habilitados en config.py."""
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
    """Descarga datos históricos de Yahoo Finance."""
    settings = PERIODS[interval]

    data = yf.download(
        ticker,
        period=settings["period"],
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False
    )

    if data is None or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close", "Volume"]

    if not all(column in data.columns for column in required):
        return None

    data = data[required].copy()

    for column in required:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    data = data.dropna()
    data = data[~data.index.duplicated(keep="last")]
    data = data.sort_index()

    # Excluimos la última vela para evitar utilizar una vela posiblemente incompleta.
    if len(data) > 1:
        data = data.iloc[:-1]

    if len(data) < MIN_BARS:
        return None

    return data


def add_indicators(data):
    """Calcula indicadores utilizando únicamente datos disponibles."""
    df = data.copy()

    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()

    delta = df["Close"].diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()

    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()

    previous_close = df["Close"].shift(1)

    true_range = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - previous_close).abs(),
            (df["Low"] - previous_close).abs()
        ],
        axis=1
    ).max(axis=1)

    df["ATR"] = true_range.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()

    df["VOLUME_AVG"] = df["Volume"].rolling(20).mean()
    df["HIGH20"] = df["High"].shift(1).rolling(20).max()

    return df.replace([np.inf, -np.inf], np.nan)


def signal_score(row):
    """Puntuación objetiva de una posible entrada larga."""
    conditions = [
        row["Close"] > row["EMA200"],
        row["EMA20"] > row["EMA50"],
        row["RSI"] >= 50,
        row["RSI"] <= 70,
        row["MACD"] > row["MACD_SIGNAL"],
        row["Volume"] >= row["VOLUME_AVG"] * 0.8,
        row["Close"] > row["HIGH20"],
    ]

    return sum(conditions) * 100 / len(conditions)


def run_backtest(asset, ticker, interval):
    data = download_history(ticker, interval)

    if data is None:
        return [], "Datos insuficientes o no disponibles"

    df = add_indicators(data)
    max_bars = PERIODS[interval]["max_bars"]

    trades = []
    i = MIN_BARS

    while i < len(df) - 1:
        row = df.iloc[i]

        required = [
            row["Close"], row["EMA20"], row["EMA50"],
            row["EMA200"], row["RSI"], row["MACD"],
            row["MACD_SIGNAL"], row["ATR"],
            row["VOLUME_AVG"], row["HIGH20"]
        ]

        if not all(pd.notna(value) and math.isfinite(float(value))
                   for value in required):
            i += 1
            continue

        score = signal_score(row)

        # No se fuerza ninguna operación.
        if score < 70:
            i += 1
            continue

        entry_index = i + 1
        entry_row = df.iloc[entry_index]

        entry = float(entry_row["Open"])
        atr = float(row["ATR"])

        if not math.isfinite(entry) or entry <= 0 or atr <= 0:
            i += 1
            continue

        stop = entry - STOP_ATR * atr
        target = entry + TARGET_ATR * atr
        initial_risk = entry - stop

        if initial_risk <= 0:
            i += 1
            continue

        last_index = min(
            entry_index + max_bars - 1,
            len(df) - 1
        )

        exit_price = None
        exit_reason = None
        exit_index = last_index

        for j in range(entry_index, last_index + 1):
            candle = df.iloc[j]

            # Si ambos niveles se alcanzan en la misma vela,
            # se aplica el escenario más conservador: STOP.
            if float(candle["Low"]) <= stop:
                exit_price = stop
                exit_reason = "STOP"
                exit_index = j
                break

            if float(candle["High"]) >= target:
                exit_price = target
                exit_reason = "TARGET"
                exit_index = j
                break

        if exit_price is None:
            exit_price = float(df.iloc[last_index]["Close"])
            exit_reason = "MAX_HOLD"

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
            "score": round(score, 2),
            "entry": round(entry, 6),
            "stop": round(stop, 6),
            "target": round(target, 6),
            "exit": round(exit_price, 6),
            "exit_reason": exit_reason,
            "duration_bars": duration_bars,
            "pnl_per_unit": round(pnl_per_unit, 6),
            "result_R": round(result_r, 4),
            "costs_included": False,
        })

        # Una sola operación simultánea por activo y temporalidad.
        i = exit_index + 1

    return trades, None


def calculate_metrics(trades):
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0,
            "total_R": 0,
            "average_R": 0,
            "profit_factor_R": 0,
            "max_drawdown_R": 0,
            "average_duration_bars": 0,
        }

    results = np.array([t["result_R"] for t in trades], dtype=float)

    wins = results[results > 0]
    losses = results[results < 0]

    equity = np.cumsum(results)
    peaks = np.maximum.accumulate(np.maximum(equity, 0))
    drawdown = peaks - equity

    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0 else None
    )

    return {
        "trades": len(results),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(results) * 100, 2),
        "total_R": round(float(results.sum()), 4),
        "average_R": round(float(results.mean()), 4),
        "profit_factor_R": (
            round(profit_factor, 4)
            if profit_factor is not None else None
        ),
        "max_drawdown_R": round(float(drawdown.max()), 4),
        "average_duration_bars": round(
            float(np.mean([t["duration_bars"] for t in trades])), 2
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
    print("Costes: no incluidos ni verificados")
    print("-" * 50)

    for asset, ticker in assets:
        for interval in ["15m", "1d"]:
            print(f"Analizando {asset} ({interval})...")

            try:
                trades, error = run_backtest(
                    asset, ticker, interval
                )

                if error:
                    errors.append({
                        "asset": asset,
                        "timeframe": interval,
                        "error": error
                    })
                    print(f"  Sin resultados: {error}")
                    continue

                all_trades.extend(trades)

                metrics = calculate_metrics(trades)
                summaries.append({
                    "asset": asset,
                    "timeframe": interval,
                    **metrics
                })

                print(
                    f"  Operaciones: {metrics['trades']} | "
                    f"Resultado: {metrics['total_R']} R"
                )

            except Exception as exc:
                errors.append({
                    "asset": asset,
                    "timeframe": interval,
                    "error": str(exc)
                })
                print(f"  Error: {exc}")

    pd.DataFrame(all_trades).to_csv(
        "operaciones_backtest_v3.csv", index=False
    )

    pd.DataFrame(summaries).to_csv(
        "resultados_backtest_v3.csv", index=False
    )

    pd.DataFrame(errors).to_csv(
        "errores_backtest_v3.csv", index=False
    )

    print("-" * 50)
    print(f"Operaciones totales: {len(all_trades)}")
    print(f"Combinaciones analizadas: {len(summaries)}")
    print(f"Errores o datos insuficientes: {len(errors)}")
    print("Archivos CSV generados.")
    print("IMPORTANTE: resultados brutos, sin costes ni sizing real.")


if __name__ == "__main__":
    main()
