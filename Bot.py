
import logging
from flask import Flask, jsonify

from config import (
    BOT_VERSION,
    ASSETS,
    INTRADAY_INTERVAL,
    SWING_INTERVAL,
)
from data_engine import download_data
from strategies import evaluate_signal

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def analyze_asset(asset, timeframe):
    try:
        df = download_data(asset, timeframe)

        if df is None or df.empty:
            return {
                "action": "NO TRADE",
                "asset": asset,
                "timeframe": timeframe,
                "reason": "Datos no disponibles",
            }

        return evaluate_signal(df, asset, timeframe)

    except Exception:
        logger.exception("Error analizando %s", asset)

        return {
            "action": "NO TRADE",
            "asset": asset,
            "timeframe": timeframe,
            "reason": "Error interno de análisis",
        }


def run_scan():
    results = []

    for asset, settings in ASSETS.items():
        if not settings.get("enabled", False):
            continue

        for timeframe in (
            INTRADAY_INTERVAL,
            SWING_INTERVAL,
        ):
            result = analyze_asset(asset, timeframe)
            results.append(result)

    candidates = [
        result for result in results
        if result.get("action") == "BUY CANDIDATE"
    ]

    return {
        "version": BOT_VERSION,
        "status": "analysis_completed",
        "analyses_completed": len(results),
        "candidates_found": len(candidates),
        "results": results,
        "mode": "PAPER_ONLY",
        "telegram_alerts_enabled": False,
        "automatic_execution": False,
        "real_trading_authorized": False,
    }


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "bot": "Trading Bot",
        "version": BOT_VERSION,
        "status": "running",
        "mode": "PAPER_ONLY",
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "version": BOT_VERSION,
    })


@app.route("/scan", methods=["GET"])
def scan():
    try:
        return jsonify(run_scan())
    except Exception:
        logger.exception("Error general durante el análisis")
        return jsonify({
            "status": "error",
            "message": "No se pudo completar el análisis",
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
    )
