
import yfinance as yf
import pandas as pd
import numpy as np

ASSETS = {
    "S&P 500": "^GSPC",
    "Nasdaq 100": "^NDX",
    "Tesla": "TSLA",
    "Nvidia": "NVDA",
    "Apple": "AAPL",
    "Microsoft": "MSFT",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "Oro": "GC=F",
    "Petróleo": "CL=F",
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
    "Solana": "SOL-USD",
    "XLM": "XLM-USD"
}

INITIAL_CAPITAL = 10000.0
RISK_PER_TRADE = 0.01

# Hipótesis configurables, no costes reales de eToro.
COMMISSION_RATE = 0.001
SLIPPAGE_RATE = 0.0005

ATR_STOP = 1.5
ATR_TARGET = 3.0


def get_data(ticker, interval):
    period = "60d" if interval == "15m" else "2y"

    df = yf.download(
        ticker,
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False
    )

    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close"]
    if not all(column in df.columns for column in required):
        return None

    return df.dropna(subset=required).copy()


def prepare_indicators(df):
    df = df.copy()
    close = df["Close"]

    df["EMA20"] = close.ewm(
        span=20, adjust=False
    ).mean()

    df["EMA50"] = close.ewm(
        span=50, adjust=False
    ).mean()

    delta = close.diff()

    gain = delta.clip(lower=0).ewm(
        alpha=1/14, adjust=False
    ).mean()

    loss = (-delta.clip(upper=0)).ewm(
        alpha=1/14, adjust=False
    ).mean()

    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    macd = (
        close.ewm(span=12, adjust=False).mean()
        - close.ewm(span=26, adjust=False).mean()
    )

    df["MACD"] = macd
    df["MACD_SIGNAL"] = macd.ewm(
        span=9, adjust=False
    ).mean()

    previous_close = close.shift(1)

    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - previous_close).abs(),
        (df["Low"] - previous_close).abs()
    ], axis=1).max(axis=1)

    df["ATR"] = tr.rolling(14).mean()

    return df


def get_daily_trend(ticker):
    daily = get_data(ticker, "1d")

    if daily is None or len(daily) < 60:
        return None

    daily = prepare_indicators(daily)

    row = daily.iloc[-1]

    if (
        row["EMA50"] > daily["EMA50"].iloc[-5]
        and row["EMA20"] > row["EMA50"]
    ):
        return "LONG"

    if (
        row["EMA50"] < daily["EMA50"].iloc[-5]
        and row["EMA20"] < row["EMA50"]
    ):
        return "SHORT"

    return None


def get_signal(df, i, daily_trend=None, interval="1d"):
    row = df.iloc[i]

    bullish = (
        row["Close"] > row["EMA20"]
        and row["EMA20"] > row["EMA50"]
        and row["EMA50"] > df["EMA50"].iloc[i - 5]
        and 52 < row["RSI"] < 65
        and row["MACD"] > row["MACD_SIGNAL"]
        and row["MACD"] > df["MACD"].iloc[i - 1]
    )

    bearish = (
        row["Close"] < row["EMA20"]
        and row["EMA20"] < row["EMA50"]
        and row["EMA50"] < df["EMA50"].iloc[i - 5]
        and 35 < row["RSI"] < 48
        and row["MACD"] < row["MACD_SIGNAL"]
        and row["MACD"] < df["MACD"].iloc[i - 1]
    )

    if interval == "15m":
        bullish = bullish and daily_trend == "LONG"
        bearish = bearish and daily_trend == "SHORT"

    if bullish:
        return "LONG"

    if bearish:
        return "SHORT"

    return None


def apply_cost(price, direction, is_entry):
    if direction == "LONG":
        adverse = 1 if is_entry else -1
    else:
        adverse = -1 if is_entry else 1

    return price * (1 + adverse * SLIPPAGE_RATE)


def close_trade(position, raw_exit, reason, capital, timestamp):
    direction = position["direction"]

    exit_price = apply_cost(
        raw_exit,
        direction,
        is_entry=False
    )

    if direction == "LONG":
        gross = (
            exit_price - position["entry"]
        ) * position["quantity"]
    else:
        gross = (
            position["entry"] - exit_price
        ) * position["quantity"]

    entry_cost = (
        position["entry"]
        * position["quantity"]
        * COMMISSION_RATE
    )

    exit_cost = (
        abs(exit_price)
        * position["quantity"]
        * COMMISSION_RATE
    )

    pnl = gross - entry_cost - exit_cost
    new_capital = capital + pnl

    return {
        "fecha_entrada": str(position["timestamp"]),
        "fecha_salida": str(timestamp),
        "direccion": direction,
        "entrada": position["entry"],
        "salida": exit_price,
        "resultado": pnl,
        "motivo": reason,
        "capital": new_capital
    }


def run_backtest(name, ticker, interval):
    df = get_data(ticker, interval)

    if df is None or len(df) < 100:
        return None, []

    df = prepare_indicators(df)

    daily_trend = None
    if interval == "15m":
        daily_trend = get_daily_trend(ticker)

        if daily_trend is None:
            return None, []

    capital = INITIAL_CAPITAL
    position = None
    trades = []

    for i in range(55, len(df) - 1):
        row = df.iloc[i]
        next_row = df.iloc[i + 1]

        if position is None:
            signal = get_signal(
                df, i, daily_trend, interval
            )

            if signal is None:
                continue

            raw_entry = float(next_row["Open"])

            entry = apply_cost(
                raw_entry, signal, is_entry=True
            )

            atr = float(row["ATR"])

            if not np.isfinite(atr) or atr <= 0:
                continue

            if signal == "LONG":
                stop = entry - ATR_STOP * atr
                target = entry + ATR_TARGET * atr
                risk_per_unit = entry - stop
            else:
                stop = entry + ATR_STOP * atr
                target = entry - ATR_TARGET * atr
                risk_per_unit = stop - entry

            if risk_per_unit <= 0:
                continue

            risk_amount = capital * RISK_PER_TRADE
            quantity = risk_amount / risk_per_unit

            position = {
                "direction": signal,
                "entry": entry,
                "stop": stop,
                "target": target,
                "quantity": quantity,
                "timestamp": next_row.name
            }

            # No se comprueba stop/objetivo en la misma
            # vela de entrada: la entrada ocurre en su apertura.
            continue

        direction = position["direction"]

        if direction == "LONG":
            stop_hit = float(next_row["Low"]) <= position["stop"]
            target_hit = float(next_row["High"]) >= position["target"]
        else:
            stop_hit = float(next_row["High"]) >= position["stop"]
            target_hit = float(next_row["Low"]) <= position["target"]

        exit_price = None
        reason = None

        if stop_hit:
            exit_price = position["stop"]
            reason = "STOP"
        elif target_hit:
            exit_price = position["target"]
            reason = "TARGET"

        if exit_price is not None:
            trade = close_trade(
                position,
                exit_price,
                reason,
                capital,
                next_row.name
            )

            capital = trade["capital"]
            trades.append(trade)
            position = None

    # Cierre al final del histórico.
    if position is not None:
        last = df.iloc[-1]

        trade = close_trade(
            position,
            float(last["Close"]),
            "FIN_HISTORICO",
            capital,
            last.name
        )

        capital = trade["capital"]
        trades.append(trade)

    if not trades:
        return {
            "activo": name,
            "temporalidad": interval,
            "operaciones": 0,
            "acierto_%": 0,
            "rentabilidad_%": 0,
            "drawdown_%": 0
        }, []

    results = pd.DataFrame(trades)

    wins = int((results["resultado"] > 0).sum())
    losses = int((results["resultado"] <= 0).sum())

    equity = pd.Series(
        [INITIAL_CAPITAL] + results["capital"].tolist()
    )

    peak = equity.cummax()
    drawdown = (equity / peak - 1) * 100

    summary = {
        "activo": name,
        "temporalidad": interval,
        "operaciones": len(results),
        "ganadoras": wins,
        "perdedoras": losses,
        "acierto_%": round(
            wins / len(results) * 100, 2
        ),
        "rentabilidad_%": round(
            (capital / INITIAL_CAPITAL - 1) * 100, 2
        ),
        "drawdown_%": round(
            abs(drawdown.min()), 2
        )
    }

    return summary, trades


def main():
    summaries = []
    all_trades = []

    for name, ticker in ASSETS.items():
        for interval in ["15m", "1d"]:
            print(f"Analizando {name} - {interval}")

            try:
                summary, trades = run_backtest(
                    name, ticker, interval
                )

                if summary is not None:
                    summaries.append(summary)

                    for trade in trades:
                        trade["activo"] = name
                        trade["temporalidad"] = interval

                    all_trades.extend(trades)

            except Exception as error:
                print(
                    f"Error en {name} {interval}: {error}"
                )

    if not summaries:
        print("No hay resultados disponibles")
        return

    pd.DataFrame(summaries).to_csv(
        "resultados_backtest.csv",
        index=False
    )

    pd.DataFrame(all_trades).to_csv(
        "operaciones_backtest.csv",
        index=False
    )

    print("\n===== RESULTADOS BACKTEST =====")
    print(pd.DataFrame(summaries).to_string(index=False))

    print("\nArchivos CSV generados.")


ASSETS = {
    "S&P 500": "^GSPC",
    "Nasdaq 100": "^NDX",
    "Tesla": "TSLA",
    "Nvidia": "NVDA",
    "Apple": "AAPL",
    "Microsoft": "MSFT",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "Oro": "GC=F",
    "Petróleo": "CL=F",
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
    "Solana": "SOL-USD",
    "XLM": "XLM-USD"
}


if __name__ == "__main__":
    main()
