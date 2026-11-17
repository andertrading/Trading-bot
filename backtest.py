
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

INITIAL_CAPITAL = 10000
RISK_PER_TRADE = 0.01
COMMISSION = 0.001
SLIPPAGE = 0.0005


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

    return df.dropna()


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


def run_backtest(name, ticker, interval):
    df = get_data(ticker, interval)

    if df is None or len(df) < 60:
        return None

    df = prepare_indicators(df)

    capital = INITIAL_CAPITAL
    trades = []

    position = None
    entry = 0
    stop = 0
    target = 0
    quantity = 0

    for i in range(55, len(df) - 1):

        row = df.iloc[i]
        next_row = df.iloc[i + 1]

        bullish = (
            row["Close"] > row["EMA20"]
            and row["EMA20"] > row["EMA50"]
            and row["EMA50"] > df["EMA50"].iloc[i-5]
            and 52 < row["RSI"] < 65
            and row["MACD"] > row["MACD_SIGNAL"]
            and row["MACD"] > df["MACD"].iloc[i-1]
        )

        bearish = (
            row["Close"] < row["EMA20"]
            and row["EMA20"] < row["EMA50"]
            and row["EMA50"] < df["EMA50"].iloc[i-5]
            and 35 < row["RSI"] < 48
            and row["MACD"] < row["MACD_SIGNAL"]
            and row["MACD"] < df["MACD"].iloc[i-1]
        )

        if position is None:

            if not bullish and not bearish:
                continue

            direction = "LONG" if bullish else "SHORT"

            entry = float(next_row["Open"])

            atr = float(row["ATR"])

            if not np.isfinite(atr) or atr <= 0:
                continue

            if direction == "LONG":
                stop = entry - 1.5 * atr
                target = entry + 3 * atr
                risk_per_unit = entry - stop
            else:
                stop = entry + 1.5 * atr
                target = entry - 3 * atr
                risk_per_unit = stop - entry

            risk_amount = capital * RISK_PER_TRADE

            quantity = risk_amount / risk_per_unit

            position = direction

            continue

        exit_price = None
        reason = None

        if position == "LONG":

            if next_row["Low"] <= stop:
                exit_price = stop
                reason = "STOP"

            elif next_row["High"] >= target:
                exit_price = target
                reason = "TARGET"

        elif position == "SHORT":

            if next_row["High"] >= stop:
                exit_price = stop
                reason = "STOP"

            elif next_row["Low"] <= target:
                exit_price = target
                reason = "TARGET"

        if exit_price is None:
            continue

        if position == "LONG":
            pnl = (exit_price - entry) * quantity
        else:
            pnl = (entry - exit_price) * quantity

        costs = (
            abs(entry * quantity) + abs(exit_price * quantity)
        ) * (COMMISSION + SLIPPAGE)

        pnl -= costs

        capital += pnl

        trades.append({
            "activo": name,
            "temporalidad": interval,
            "direccion": position,
            "entrada": entry,
            "salida": exit_price,
            "resultado": pnl,
            "motivo": reason,
            "capital": capital
        })

        position = None

    if not trades:
        return {
            "activo": name,
            "temporalidad": interval,
            "operaciones": 0,
            "ganadoras": 0,
            "perdedoras": 0,
            "rentabilidad_%": 0,
            "drawdown_%": 0
        }

    results = pd.DataFrame(trades)

    wins = (results["resultado"] > 0).sum()
    losses = (results["resultado"] <= 0).sum()

    equity = results["capital"]
    peak = equity.cummax()
    drawdown = ((equity - peak) / peak) * 100

    return {
        "activo": name,
        "temporalidad": interval,
        "operaciones": len(results),
        "ganadoras": int(wins),
        "perdedoras": int(losses),
        "acierto_%": round(wins / len(results) * 100, 2),
        "rentabilidad_%": round(
            (capital / INITIAL_CAPITAL - 1) * 100, 2
        ),
        "drawdown_%": round(abs(drawdown.min()), 2)
    }


def main():
    all_results = []

    for name, ticker in ASSETS.items():
        for interval in ["15m", "1d"]:

            print(f"Analizando {name} - {interval}")

            try:
                result = run_backtest(name, ticker, interval)

                if result:
                    all_results.append(result)

            except Exception as error:
                print("Error:", error)

    if not all_results:
        print("No hay resultados disponibles")
        return

    report = pd.DataFrame(all_results)

    print("\n===== RESULTADOS BACKTEST =====")
    print(report.to_string(index=False))

    report.to_csv("resultados_backtest.csv", index=False)

    print("\nArchivo resultados_backtest.csv creado")


if __name__ == "__main__":
    main()
