
import math

from indicators import add_indicators


MIN_BARS = 220
SUPPORTED_TIMEFRAMES = ("15m", "1d")

STOP_ATR = 1.5
TARGET_ATR = 3.0
MIN_SCORE = 70
MAX_ATR_PERCENT = 5.0


def _no_trade(asset, timeframe, reason, score=None):
    result = {
        "action": "NO TRADE",
        "reason": reason,
        "asset": asset,
        "timeframe": timeframe,
    }

    if score is not None:
        result["score"] = score

    return result


def _valid_number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def evaluate_signal_at(data, asset, timeframe, index=-1):
    """
    Evalúa una vela concreta utilizando indicadores ya calculados.

    Esta función es compartida por el análisis habitual y el backtest.
    No ejecuta operaciones ni calcula tamaño de posición.
    """

    if timeframe not in SUPPORTED_TIMEFRAMES:
        return _no_trade(
            asset, timeframe, "Temporalidad no admitida"
        )

    if data is None or len(data) < MIN_BARS:
        return _no_trade(
            asset, timeframe, "Histórico insuficiente"
        )

    try:
        row = data.iloc[index]
    except (IndexError, TypeError):
        return _no_trade(
            asset, timeframe, "Vela no disponible"
        )

    required = [
        "Close",
        "EMA20",
        "EMA50",
        "EMA200",
        "RSI",
        "MACD",
        "MACD_SIGNAL",
        "ATR",
        "ATR_PERCENT",
        "HIGH20",
    ]

    for name in required:
        if name not in row.index:
            return _no_trade(
                asset, timeframe, f"Falta indicador: {name}"
            )

        if not _valid_number(row[name]):
            return _no_trade(
                asset, timeframe, f"Indicador no válido: {name}"
            )

    price = float(row["Close"])
    atr = float(row["ATR"])
    rsi = float(row["RSI"])
    atr_pct = float(row["ATR_PERCENT"])

    if price <= 0 or atr <= 0:
        return _no_trade(
            asset, timeframe, "Precio o volatilidad no válidos"
        )

    if atr_pct < 0 or atr_pct > MAX_ATR_PERCENT:
        return _no_trade(
            asset, timeframe, "Volatilidad excesiva"
        )

    score = 0
    reasons = []

    if price > float(row["EMA20"]):
        score += 15
        reasons.append("Precio sobre EMA20")

    if float(row["EMA20"]) > float(row["EMA50"]):
        score += 15
        reasons.append("EMA20 sobre EMA50")

    if float(row["EMA50"]) > float(row["EMA200"]):
        score += 20
        reasons.append("Tendencia principal alcista")

    if 50 <= rsi <= 68:
        score += 15
        reasons.append("RSI favorable")

    if float(row["MACD"]) > float(row["MACD_SIGNAL"]):
        score += 15
        reasons.append("MACD alcista")

    if price > float(row["HIGH20"]):
        score += 10
        reasons.append("Ruptura de máximos de 20 velas")

    # El volumen es opcional. Si no existe o no es válido,
    # no se conceden los 10 puntos de confirmación.
    relative_volume = row.get("RELATIVE_VOLUME", float("nan"))

    if (
        _valid_number(relative_volume)
        and float(relative_volume) >= 1.2
    ):
        score += 10
        reasons.append("Volumen superior a la media")

    if score < MIN_SCORE:
        return _no_trade(
            asset,
            timeframe,
            "Calidad insuficiente",
            score,
        )

    stop = price - STOP_ATR * atr
    target = price + TARGET_ATR * atr

    if not all(_valid_number(value) for value in (stop, target)):
        return _no_trade(
            asset, timeframe, "Niveles de riesgo no válidos", score
        )

    if stop <= 0 or target <= price:
        return _no_trade(
            asset, timeframe, "Niveles de riesgo no válidos", score
        )

    risk = price - stop
    reward = target - price
    risk_reward = reward / risk

    if not _valid_number(risk_reward) or risk_reward < 2:
        return _no_trade(
            asset,
            timeframe,
            "Relación riesgo/beneficio insuficiente",
            score,
        )

    return {
        "action": "BUY CANDIDATE",
        "asset": asset,
        "timeframe": timeframe,
        "entry_reference": round(price, 6),
        "stop": round(stop, 6),
        "target": round(target, 6),
        "risk_reward": round(risk_reward, 2),
        "score": score,
        "reasons": reasons,
        "duration_estimate": None,
        "status": "PENDING_VALIDATION",
    }


def evaluate_signal(df, asset, timeframe):
    """
    Evalúa la última vela disponible para el escáner habitual.
    """

    if df is None or len(df) < MIN_BARS:
        return _no_trade(
            asset, timeframe, "Histórico insuficiente"
        )

    if timeframe not in SUPPORTED_TIMEFRAMES:
        return _no_trade(
            asset, timeframe, "Temporalidad no admitida"
        )

    data = add_indicators(df)

    if data is None or data.empty:
        return _no_trade(
            asset, timeframe, "No se pudieron calcular indicadores"
        )

    return evaluate_signal_at(
        data=data,
        asset=asset,
        timeframe=timeframe,
        index=-1,
    )
