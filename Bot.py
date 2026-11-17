
import os
import time
import threading
import requests
import yfinance as yf
import pandas as pd
from flask import Flask, jsonify

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Activos iniciales: acciones, índices, Forex,
# materias primas y criptomonedas.
ASSETS = {
    "S&P 500": "^GSPC",
    "Nasdaq 100": "^NDX",
    "Apple": "AAPL",
    "Microsoft": "MSFT",
    "Nvidia": "NVDA",
    "Tesla": "TSLA",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "Oro": "GC=F",
    "Petróleo": "CL=F",
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
    "Solana": "SOL-USD"
}

sent_signals = set()


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("Faltan las variables de Telegram")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    try:
        requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": message},
            timeout=15
        ).raise_for_status()
    except Exception as error:
        print("Error Telegram:", error)


def calculate_signal(name, ticker, interval):
    try:
        period = "5d" if interval == "15m" else "6mo"

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

        close = df["Close"].dropna()

        if len(close) < 60:
            return None

        high = df["High"].reindex(close.index)
        low = df["Low"].reindex(close.index)

        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(
            alpha=1/14, adjust=False
        ).mean()
        loss = (-delta.clip(upper=0)).ewm(
            alpha=1/14, adjust=False
        ).mean()

        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))

        macd = (
            close.ewm(span=12, adjust=False).mean()
            - close.ewm(span=26, adjust=False).mean()
        )
        macd_signal = macd.ewm(
            span=9, adjust=False
        ).mean()

        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs()
            ],
            axis=1
        ).max(axis=1)

        atr = true_range.rolling(14).mean()

        price = float(close.iloc[-1])
        current_rsi = float(rsi.iloc[-1])
        current_atr = float(atr.iloc[-1])

        if pd.isna(current_rsi) or pd.isna(current_atr):
            return None

        bullish = (
            price > ema20.iloc[-1]
            and ema20.iloc[-1] > ema50.iloc[-1]
            and 50 < current_rsi < 68
            and macd.iloc[-1] > macd_signal.iloc[-1]
        )

        bearish = (
            price < ema20.iloc[-1]
            and ema20.iloc[-1] < ema50.iloc[-1]
            and 32 < current_rsi < 50
            and macd.iloc[-1] < macd_signal.iloc[-1]
        )

        if bullish:
            direction = "COMPRA"
            stop = price - (1.5 * current_atr)
            target = price + (3 * current_atr)
        elif bearish:
            direction = "VENTA"
            stop = price + (1.5 * current_atr)
            target = price - (3 * current_atr)
        else:
            return None

        signal_id = f"{ticker}-{interval}-{direction}-{close.index[-1]}"

        if signal_id in sent_signals:
            return None

        sent_signals.add(signal_id)

        return (
            f"📊 SEÑAL DE TRADING\n\n"
            f"Activo: {name}\n"
            f"Tipo: {direction}\n"
            f"Temporalidad: {interval}\n"
            f"Precio de referencia: {price:.5f}\n"
            f"Stop orientativo: {stop:.5f}\n"
            f"Objetivo orientativo: {target:.5f}\n"
            f"RSI: {current_rsi:.1f}\n\n"
            f"Indicadores: EMA + RSI + MACD\n"
            f"Riesgo/beneficio teórico: 1:2\n"
            f"⚠️ Señal informativa. Comprueba el precio "
            f"y los costes en tu plataforma antes de operar."
        )

    except Exception as error:
        print(f"Error analizando {ticker} ({interval}): {error}")
        return None


def scan_market():
    print("Iniciando análisis del mercado...")

    for name, ticker in ASSETS.items():
        for interval in ["15m", "1d"]:
            signal = calculate_signal(name, ticker, interval)

            if signal:
                send_telegram(signal)

            time.sleep(2)

    print("Análisis terminado.")


def monitor_market():
    while True:
        try:
            scan_market()
        except Exception as error:
            print("Error general:", error)

        time.sleep(900)


@app.route("/")
def home():
    return "Trading Bot activo - análisis técnico habilitado"


@app.route("/scan")
def manual_scan():
    threading.Thread(target=scan_market, daemon=True).start()
    return jsonify({"status": "Análisis iniciado"})


@app.route("/health")
def health():
    return jsonify({"status": "activo"})


threading.Thread(target=monitor_market, daemon=True).start()

