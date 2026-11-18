
import numpy as np
import pandas as pd


def calculate_rsi(series, period=14):
    """Calcula RSI mediante suavizado de Wilder."""

    delta = series.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss

    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.mask(
        (avg_loss == 0) & (avg_gain > 0),
        100
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss > 0),
        0
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss == 0),
        50
    )

    return rsi


def calculate_atr(df, period=14):
    """Calcula Average True Range."""

    previous_close = df["Close"].shift(1)

    ranges = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - previous_close).abs(),
            (df["Low"] - previous_close).abs(),
        ],
        axis=1
    )

    true_range = ranges.max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()


def add_indicators(df):
    """
    Añade indicadores técnicos al histórico.
    No modifica el DataFrame original.
    """

    if df is None or df.empty:
        return None

    result = df.copy()

    required = ["Open", "High", "Low", "Close", "Volume"]

    if not all(column in result.columns for column in required):
        return None

    close = result["Close"]

    # Medias móviles exponenciales.
    result["EMA20"] = close.ewm(
        span=20,
        adjust=False
    ).mean()

    result["EMA50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    result["EMA200"] = close.ewm(
        span=200,
        adjust=False
    ).mean()

    # RSI.
    result["RSI"] = calculate_rsi(close, 14)

    # MACD.
    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    result["MACD"] = ema12 - ema26

    result["MACD_SIGNAL"] = result["MACD"].ewm(
        span=9,
        adjust=False
    ).mean()

    result["MACD_HIST"] = (
        result["MACD"] - result["MACD_SIGNAL"]
    )

    # Volatilidad.
    result["ATR"] = calculate_atr(result, 14)

    result["ATR_PERCENT"] = (
        result["ATR"] / close
    ) * 100

    # Volumen relativo.
    result["VOLUME_AVG20"] = (
        result["Volume"].rolling(20).mean()
    )

    result["RELATIVE_VOLUME"] = (
        result["Volume"] / result["VOLUME_AVG20"].replace(0, np.nan)
    )

    # Máximos y mínimos de las 20 velas anteriores.
    # Se desplazan una vela para evitar anticipar información.
    result["HIGH20"] = (
        result["High"].rolling(20).max().shift(1)
    )

    result["LOW20"] = (
        result["Low"].rolling(20).min().shift(1)
    )

    # Tendencia y estructura.
    result["TREND_BULLISH"] = (
        (result["EMA20"] > result["EMA50"])
        & (result["EMA50"] > result["EMA200"])
    )

    result["TREND_BEARISH"] = (
        (result["EMA20"] < result["EMA50"])
        & (result["EMA50"] < result["EMA200"])
    )

    return result
