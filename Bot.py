
import os
import time
import threading
import sqlite3
import requests
import yfinance as yf
import pandas as pd
from flask import Flask, jsonify

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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

DB_FILE = "signals.db"
scan_lock = threading.Lock()


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_signals (
                signal_id TEXT PRIMARY KEY
            )
        """)


def already_sent(signal_id):
    with sqlite3.connect(DB_FILE) as conn:
        result = conn.execute(
            "SELECT 1 FROM sent_signals WHERE signal_id = ?",
            (signal_id,)
        ).fetchone()
        return result is not None


def save_signal(signal_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_signals VALUES (?)",
            (signal_id,)
        )


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("Faltan las variables de Telegram")
        return False

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": message},
            timeout=15
        )
        response.raise_for_status()
        return True
    except Exception as error:
        print("Error Telegram:", error)
        return False


def download_data(ticker, interval):
    try:
        period = "5d" if interval == "15m" else "1y"

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

        # Utilizamos velas cerradas, no la vela en formación.
        df = df.iloc[:-1].copy()

        if len(df) < 60:
            return None

        return df.dropna(subset=["Close"])

    except Exception as error:
        print(f"Error descargando {ticker}: {error}")
        return None


def indicators(df):
    close = df["Close"].dropna()

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
    macd_signal = macd.ewm(span=9, adjust=False).mean()

    high = df["High"].reindex(close.index)
    low = df["Low"].reindex(close.index)
    previous_close = close.shift(1)

    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs()
    ], axis=1).max(axis=1)

    atr = true_range.rolling(14).mean()

    return close, ema20, ema50, rsi, macd, macd_signal, atr


def get_daily_trend(ticker):
    df = download_data(ticker, "1d")

    if df is None:
        return None

    close, ema20, ema50, rsi, macd, macd_signal, atr = indicators(df)

    if len(close) < 60:
        return None

    if ema50.iloc[-1] > ema50.iloc[-5] and ema20.iloc[-1] > ema50.iloc[-1]:
        return "COMPRA"

    if ema50.iloc[-1] < ema50.iloc[-5] and ema20.iloc[-1] < ema50.iloc[-1]:
        return "VENTA"

    return None


def calculate_signal(name, ticker, interval):
    try:
        df = download_data(ticker, interval)

        if df is None:
            return None

        close, ema20, ema50, rsi, macd, macd_signal, atr = indicators(df)

        price = float(close.iloc[-1])
        current_rsi = float(rsi.iloc[-1])
        current_atr = float(atr.iloc[-1])

        if pd.isna(current_rsi) or pd.isna(current_atr) or current_atr <= 0:
            return None

        # Filtro de tendencia superior para señales de 15 minutos.
        if interval == "15m":
            daily_trend = get_daily_trend(ticker)
            if daily_trend is None:
                return None
        else:
            daily_trend = None

        rising_ema = ema50.iloc[-1] > ema50.iloc[-5]
        falling_ema = ema50.iloc[-1] < ema50.iloc[-5]

        rising_macd = macd.iloc[-1] > macd.iloc[-2]
        falling_macd = macd.iloc[-1] < macd.iloc[-2]

        bullish = (
            price > ema20.iloc[-1]
            and ema20.iloc[-1] > ema50.iloc[-1]
            and rising_ema
            and 52 < current_rsi < 65
            and macd.iloc[-1] > macd_signal.iloc[-1]
            and rising_macd
        )

        bearish = (
            price < ema20.iloc[-1]
            and ema20.iloc[-1] < ema50.iloc[-1]
            and falling_ema
            and 35 < current_rsi < 48
            and macd.iloc[-1] < macd_signal.iloc[-1]
            and falling_macd
        )

        if interval == "15m":
            bullish = bullish and daily_trend == "COMPRA"
            bearish = bearish and daily_trend == "VENTA"

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

        candle_time = str(close.index[-1])
        signal_id = f"{ticker}-{interval}-{direction}-{candle_time}"

        if already_sent(signal_id):
            return None

        message = (
            f"📊 SEÑAL DE TRADING 2.0\n\n"
            f"Activo: {name}\n"
            f"Tipo: {direction}\n"
            f"Temporalidad: {interval}\n"
            f"Precio de referencia: {price:.5f}\n"
            f"Stop orientativo: {stop:.5f}\n"
            f"Objetivo orientativo: {target:.5f}\n"
            f"RSI: {current_rsi:.1f}\n\n"
            f"Filtros: EMA + RSI + MACD\n"
            f"Tendencia: confirmada\n"
            f"Riesgo/beneficio teórico: 1:2\n\n"
            f"⚠️ Señal informativa. Datos de Yahoo Finance. "
            f"Comprueba cotización, costes y condiciones en eToro. "
            f"No es una recomendación de inversión."
        )

        if send_telegram(message):
            save_signal(signal_id)

        return message

    except Exception as error:
        print(f"Error analizando {ticker}: {error}")
        return None


def scan_market():
    if not scan_lock.acquire(blocking=False):
        return "Ya hay un análisis en curso"

    try:
        print("Iniciando Trading Bot 2.0")

        for name, ticker in ASSETS.items():
            for interval in ["15m", "1d"]:
                calculate_signal(name, ticker, interval)
                time.sleep(2)

        print("Análisis terminado")
        return "Análisis terminado"

    finally:
        scan_lock.release()


@app.route("/")
def home():
    return "Trading Bot 2.0 activo"


@app.route("/health")
def health():
    return jsonify({
        "status": "activo",
        "version": "2.0",
        "assets": len(ASSETS)
    })


@app.route("/scan")
def manual_scan():
    if scan_lock.locked():
        return jsonify({"status": "Ya hay un análisis en curso"})

    threading.Thread(target=scan_market, daemon=True).start()
    return jsonify({"status": "Análisis iniciado"})


init_db()
