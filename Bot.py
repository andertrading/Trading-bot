
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify

from config import (
    BOT_VERSION,
    ASSETS,
    INTRADAY_INTERVAL,
    SWING_INTERVAL,
)

from data_engine import (
    download_data,
    get_data_timestamp,
    is_data_fresh,
)

from strategies import evaluate_signal


app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def no_trade(asset, timeframe, reason):
    return {
        "action": "NO TRADE",
        "asset": asset,
        "timeframe": timeframe,
        "reason": reason,
        "mode": "PAPER_ONLY",
    }


def analyze_asset(asset, timeframe):
    try:
        # 1. Descargar datos
        df = download_data(asset, timeframe)

        if df is None or df.empty:
            return no_trade(
                asset,
                timeframe,
                "Datos no disponibles",
            )

        # 2. Comprobar fecha de la última vela
        timestamp = get_data_timestamp(df)

        if timestamp is None:
            return no_trade(
                asset,
                timeframe,
                "No se pudo determinar la fecha de los datos",
            )

        now = datetime.now(timezone.utc)

        age_minutes = (
            now - timestamp.to_pydatetime()
        ).total_seconds() / 60

        if age_minutes < 0:
            return no_trade(
                asset,
                timeframe,
                "Los datos tienen una fecha futura",
            )

        # 3. Validar antigüedad según el activo y temporalidad.
        # Se evita imponer un límite universal que bloquee
        # acciones y ETF durante cierres normales del mercado.
        if not is_data_fresh(
            df,
            asset=asset,
            interval=timeframe,
        ):
            logger.warning(
                "Datos desactualizados: %s %s | última vela: %s",
                asset,
                timeframe,
                timestamp,
            )

            return no_trade(
                asset,
                timeframe,
                "Datos desactualizados",
            )

        # 4. Evaluar estrategia
        result = evaluate_signal(
            df,
            asset,
            timeframe,
        )

        if not isinstance(result, dict):
            return no_trade(
                asset,
                timeframe,
                "La estrategia devolvió un resultado no válido",
            )

        # 5. Añadir información de control
        result["asset"] = asset
        result["timeframe"] = timeframe
        result["data_timestamp"] = timestamp.isoformat()
        result["data_age_minutes"] = round(age_minutes, 2)
        result["mode"] = "PAPER_ONLY"

        return result

    except Exception:
        logger.exception(
            "Error analizando %s %s",
            asset,
            timeframe,
        )

        return no_trade(
            asset,
            timeframe,
            "Error interno de análisis",
        )


def run_scan():
    results = []

    for asset, settings in ASSETS.items():

        if not settings.get("enabled", False):
            continue

        for timeframe in (
            INTRADAY_INTERVAL,
            SWING_INTERVAL,
        ):

            result = analyze_asset(
                asset,
                timeframe,
            )

            results.append(result)

    candidates = [
        result
        for result in results
        if result.get("action") == "BUY CANDIDATE"
    ]

    no_trades = [
        result
        for result in results
        if result.get("action") == "NO TRADE"
    ]

    return {
        "version": BOT_VERSION,
        "status": "analysis_completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "analyses_completed": len(results),
        "candidates_found": len(candidates),
        "no_trade_count": len(no_trades),
        "results": results,
        "mode": "PAPER_ONLY",
        "telegram_alerts_enabled": False,
        "automatic_execution": False,
        "real_trading_authorized": False,
    }


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "name": "Trading Bot",
        "version": BOT_VERSION,
        "status": "online",
        "mode": "PAPER_ONLY",
        "automatic_execution": False,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "version": BOT_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "PAPER_ONLY",
    })


@app.route("/scan", methods=["GET"])
def scan():
    return jsonify(run_scan())


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
    )
