
import math

from indicators import add_indicators


def evaluate_signal(df, asset, timeframe):
    """
    Evalúa una posible señal de compra.
    Devuelve NO TRADE si no hay suficientes condiciones.
    """

    if df is None or len(df) < 220:
        return {
            "action": "NO TRADE",
            "reason": "Histórico insuficiente",
            "asset": asset,
            "timeframe": timeframe,
        }

    if timeframe not in ("15m", "1d"):
        return {
            "action": "NO TRADE",
            "reason": "Temporalidad no admitida",
            "asset": asset,
            "timeframe": timeframe,
        }

    data = add_indicators(df)

    if data is None or data.empty:
        return {
            "action": "NO TRADE",
            "reason": "No se pudieron calcular indicadores",
            "asset": asset,
            "timeframe": timeframe,
        }

    row = data.iloc[-1]

    required = [
        "Close", "EMA20", "EMA50", "EMA200",
        "RSI", "MACD", "MACD_SIGNAL",
        "ATR", "ATR_PERCENT", "HIGH20",
    ]

    for name in required:
        if name not in row.index:
            return {
                "action": "NO TRADE",
                "reason": f"Falta indicador: {name}",
                "asset": asset,
                "timeframe": timeframe,
            }

        try:
            if not math.isfinite(float(row[name])):
                return {
                    "action": "NO TRADE",
                    "reason": f"Indicador no válido: {name}",
                    "asset": asset,
                    "timeframe": timeframe,
                }
        except (TypeError, ValueError):
            return {
                "action": "NO TRADE",
                "reason": f"Indicador no numérico: {name}",
                "asset": asset,
                "timeframe": timeframe,
            }

    price = float(row["Close"])
    atr = float(row["ATR"])
    rsi = float(row["RSI"])
    atr_pct = float(row["ATR_PERCENT"])

    # El volumen es opcional: si no está disponible,
    # no se conceden puntos por confirmación de volumen.
    try:
        relative_volume = float(row["RELATIVE_VOLUME"])
        volume_valid = math.isfinite(relative_volume)
    except (KeyError, TypeError, ValueError):
        relative_volume = 0.0
        volume_valid = False

    if price <= 0 or atr <= 0:
        return {
            "action": "NO TRADE",
            "reason": "Precio o volatilidad no válidos",
            "asset": asset,
            "timeframe": timeframe,
        }

    if atr_pct > 5:
        return {
            "action": "NO TRADE",
            "reason": "Volatilidad excesiva",
            "asset": asset,
            "timeframe": timeframe,
        }

    score = 0
    reasons = []

    # Tendencia.
    if price > row["EMA20"]:
        score += 15
        reasons.append("Precio sobre EMA20")

    if row["EMA20"] > row["EMA50"]:
        score += 15
        reasons.append("EMA20 sobre EMA50")

    if row["EMA50"] > row["EMA200"]:
        score += 20
        reasons.append("Tendencia principal alcista")

    # Momentum.
    if 50 <= rsi <= 68:
        score += 15
        reasons.append("RSI favorable")

    if row["MACD"] > row["MACD_SIGNAL"]:
        score += 15
        reasons.append("MACD alcista")

    # Ruptura de máximos anteriores.
    if price > row["HIGH20"]:
        score += 10
        reasons.append("Ruptura de máximos de 20 velas")

    # Confirmación de volumen.
    if volume_valid and relative_volume >= 1.2:
        score += 10
        reasons.append("Volumen superior a la media")

    if score < 70:
        return {
            "action": "NO TRADE",
            "reason": "Calidad insuficiente",
            "score": score,
            "asset": asset,
            "timeframe": timeframe,
        }

    stop = price - (1.5 * atr)
    target = price + (3.0 * atr)

    if stop <= 0 or target <= price:
        return {
            "action": "NO TRADE",
            "reason": "Niveles de riesgo no válidos",
            "asset": asset,
            "timeframe": timeframe,
        }

    risk = price - stop
    reward = target - price
    risk_reward = reward / risk

    if risk_reward < 2:
        return {
            "action": "NO TRADE",
            "reason": "Relación riesgo/beneficio insuficiente",
            "score": score,
            "asset": asset,
            "timeframe": timeframe,
        }

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
