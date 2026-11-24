
import math

from indicators import add_indicators


MIN_BARS = 220
SUPPORTED_TIMEFRAMES = ("15m", "1d")

STOP_ATR = 1.5
TARGET_ATR = 3.0
MIN_SCORE = 70
MAX_ATR_PERCENT = 5.0
MIN_RISK_REWARD = 2.0


def _is_dataframe(value):
    return (
        value is not None
        and hasattr(value, "columns")
        and hasattr(value, "iloc")
    )


def _normalize_arguments(data, asset):
    """
    Admite ambos órdenes:
    data, asset
    asset, data
    """

    if not _is_dataframe(data) and _is_dataframe(asset):
        return asset, data

    return data, asset


def _no_trade(asset, timeframe, reason, score=None):
    result = {
        "action": "NO TRADE",
        "reason": reason,
        "asset": asset,
        "timeframe": timeframe,
        "tradable": False,
        "validated": False,
    }

    if score is not None:
        result["score"] = score

    return result


def _valid_number(value):
    if isinstance(value, bool):
        return False

    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def evaluate_signal_at(data, asset, timeframe, index=-1):
    """
    Evalúa una vela concreta utilizando indicadores ya calculados.

    Admite tanto:
        (data, asset, timeframe)
        (asset, data, timeframe)

    Se utiliza tanto en el análisis habitual como en el backtest.

    No ejecuta operaciones ni calcula tamaño de posición.
    """

    data, asset = _normalize_arguments(data, asset)

    if timeframe not in SUPPORTED_TIMEFRAMES:
        return _no_trade(
            asset,
            timeframe,
            "Temporalidad no admitida",
        )

    if data is None:
        return _no_trade(
            asset,
            timeframe,
            "Datos no disponibles",
        )

    if not _is_dataframe(data):
        return _no_trade(
            asset,
            timeframe,
            "Estructura de datos no válida",
        )

    try:
        if len(data) < MIN_BARS:
            return _no_trade(
                asset,
                timeframe,
                "Histórico insuficiente",
            )
    except (TypeError, ValueError):
        return _no_trade(
            asset,
            timeframe,
            "Estructura de datos no válida",
        )

    if isinstance(index, bool) or not isinstance(index, int):
        return _no_trade(
            asset,
            timeframe,
            "Índice de vela no válido",
        )

    try:
        row = data.iloc[index]
    except (IndexError, TypeError, KeyError):
        return _no_trade(
            asset,
            timeframe,
            "Vela no disponible",
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
                asset,
                timeframe,
                f"Falta indicador: {name}",
            )

        if not _valid_number(row[name]):
            return _no_trade(
                asset,
                timeframe,
                f"Indicador no válido: {name}",
            )

    price = float(row["Close"])
    ema20 = float(row["EMA20"])
    ema50 = float(row["EMA50"])
    ema200 = float(row["EMA200"])
    rsi = float(row["RSI"])
    macd = float(row["MACD"])
    macd_signal = float(row["MACD_SIGNAL"])
    atr = float(row["ATR"])
    atr_pct = float(row["ATR_PERCENT"])
    high20 = float(row["HIGH20"])

    if any(
        value <= 0
        for value in (
            price,
            ema20,
            ema50,
            ema200,
            atr,
            high20,
        )
    ):
        return _no_trade(
            asset,
            timeframe,
            "Precio o indicadores de tendencia no válidos",
        )

    if not 0 <= rsi <= 100:
        return _no_trade(
            asset,
            timeframe,
            "RSI fuera de rango",
        )

    if atr_pct < 0 or atr_pct > MAX_ATR_PERCENT:
        return _no_trade(
            asset,
            timeframe,
            "Volatilidad excesiva",
        )

    # FILTRO OBLIGATORIO DE TENDENCIA PRINCIPAL.
    #
    # Antes esta condición solamente sumaba 20 puntos.
    # Ahora es imprescindible para permitir una candidata.
    #
    # EMA50 debe estar estrictamente por encima de EMA200.

    if ema50 <= ema200:
        return _no_trade(
            asset,
            timeframe,
            "Tendencia principal no alcista: EMA50 no supera EMA200",
        )

    score = 0
    reasons = []

    # Tendencia y posición del precio.

    if price > ema20:
        score += 15
        reasons.append("Precio sobre EMA20")

    if ema20 > ema50:
        score += 15
        reasons.append("EMA20 sobre EMA50")

    # Esta condición también puntúa, pero ya es obligatoria.
    if ema50 > ema200:
        score += 20
        reasons.append("Tendencia principal alcista")

    # Momentum.

    if 50 <= rsi <= 68:
        score += 15
        reasons.append("RSI favorable")

    if macd > macd_signal:
        score += 15
        reasons.append("MACD alcista")

    # Ruptura de máximos anteriores.
    # HIGH20 debe calcularse desplazando una vela.

    if price > high20:
        score += 10
        reasons.append("Ruptura de máximos de 20 velas")

    # El volumen continúa siendo opcional. Solo puede mejorar la
    # puntuación cuando el histórico conserva procedencia explícita y
    # válida desde la normalización OHLCV. Un volumen zero-filled por
    # ausencia o valores inválidos nunca debe generar este bonus.
    volume_metadata = getattr(data, "attrs", {})
    volume_quality = volume_metadata.get("volume_quality")
    volume_column_present = volume_metadata.get("volume_column_present")
    volume_invalid_count = volume_metadata.get(
        "volume_invalid_count_before_normalization"
    )
    volume_positive_count = volume_metadata.get(
        "volume_positive_count_before_normalization"
    )
    volume_zero_count = volume_metadata.get(
        "volume_zero_count_before_normalization"
    )

    volume_provenance_valid = (
        volume_quality in {"available", "contains_zero_bars"}
        and volume_column_present is True
        and type(volume_invalid_count) is int
        and volume_invalid_count == 0
        and type(volume_positive_count) is int
        and volume_positive_count > 0
        and type(volume_zero_count) is int
        and volume_zero_count >= 0
        and volume_zero_count + volume_positive_count == len(data)
    )

    relative_volume = row.get(
        "RELATIVE_VOLUME",
        row.get("relative_volume", float("nan")),
    )

    if (
        volume_provenance_valid
        and _valid_number(relative_volume)
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

    # Cálculo de niveles de riesgo.

    stop = price - STOP_ATR * atr
    target = price + TARGET_ATR * atr

    if not all(
        _valid_number(value)
        for value in (stop, target)
    ):
        return _no_trade(
            asset,
            timeframe,
            "Niveles de riesgo no válidos",
            score,
        )

    if stop <= 0 or stop >= price or target <= price:
        return _no_trade(
            asset,
            timeframe,
            "Niveles de riesgo no válidos",
            score,
        )

    risk = price - stop
    reward = target - price

    if not _valid_number(risk) or not _valid_number(reward):
        return _no_trade(
            asset,
            timeframe,
            "Riesgo o beneficio no válido",
            score,
        )

    if risk <= 0 or reward <= 0:
        return _no_trade(
            asset,
            timeframe,
            "Riesgo o beneficio no válido",
            score,
        )

    risk_reward = reward / risk

    if (
        not _valid_number(risk_reward)
        or risk_reward < MIN_RISK_REWARD
    ):
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
        "tradable": False,
        "validated": False,
    }


def evaluate_signal(df, asset, timeframe):
    """
    Evalúa la última vela disponible para el escáner habitual.

    Admite tanto:
        (df, asset, timeframe)
        (asset, df, timeframe)
    """

    df, asset = _normalize_arguments(df, asset)

    if timeframe not in SUPPORTED_TIMEFRAMES:
        return _no_trade(
            asset,
            timeframe,
            "Temporalidad no admitida",
        )

    if df is None:
        return _no_trade(
            asset,
            timeframe,
            "Datos no disponibles",
        )

    if not _is_dataframe(df):
        return _no_trade(
            asset,
            timeframe,
            "Estructura de datos no válida",
        )

    try:
        if len(df) < MIN_BARS:
            return _no_trade(
                asset,
                timeframe,
                "Histórico insuficiente",
            )
    except (TypeError, ValueError):
        return _no_trade(
            asset,
            timeframe,
            "Estructura de datos no válida",
        )

    try:
        data = add_indicators(df)
    except Exception:
        return _no_trade(
            asset,
            timeframe,
            "Error al calcular indicadores",
        )

    if data is None or data.empty:
        return _no_trade(
            asset,
            timeframe,
            "No se pudieron calcular indicadores",
        )

    return evaluate_signal_at(
        data=data,
        asset=asset,
        timeframe=timeframe,
        index=-1,
    )
