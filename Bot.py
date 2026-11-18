
import logging
from flask import Flask, jsonify

from config import (
    BOT_VERSION,
    ASSETS,
    INTRADAY_INTERVAL,
    SWING_INTERVAL,
)
from data_engine import download_data
from indicators import add_indicators
from strategies import evaluate_signal
from notifier import send_telegram, format_signal

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def analyze_asset(asset, timeframe):
    """Descarga datos y evalúa un activo."""

    try:
        df = download_data(asset, timeframe)

        if df is None or df.empty:
            return {
                "action": "NO TRADE",
                "asset": asset,
                "timeframe": timeframe,
                "reason": "Datos no disponibles o insuficientes",
            }

        return evaluate_signal(df, asset, timeframe)

    except Exception as exc:
        logger.exception("Error analizando %s", asset)

        return {
            "action": "NO TRADE",
            "asset": asset,
            "timeframe": timeframe,
            "reason": "Error interno de análisis",
        }


def run_scan(send_alerts=False):
    """Analiza todos los activos habilitados."""

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

            # Las alertas son únicamente candidatos informativos.
            # No son órdenes ni recomendaciones validadas.
            if (
                send_alerts
                and result.get("action") == "BUY CANDIDATE"
            ):
                send_telegram(format_signal(result))

    candidates = [
        result for result in results
        if result.get("action") == "BUY CANDIDATE"
    ]

    return {
        "version": BOT_VERSION,
        "status": "analysis_completed",
        "assets_analyzed": len(
            [
                asset for asset in ASSETS.values()
                if asset.get("enabled", False)
            ]
        ),
        "analyses_completed": len(results),
        "candidates_found": len(candidates),
        "results": results,
        "trading_mode": "PAPER_ONLY",
        "execution_enabled": False,
        "validated_for_real_trading": False,
    }


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "bot": "Trading Bot",
        "version": BOT_VERSION,
        "status": "running",
        "mode": "PAPER_ONLY",
        "automatic_execution": False,
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
        results = run_scan(send_alerts=True)
        return jsonify(results)

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
