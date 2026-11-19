
import logging
import threading
from datetime import datetime, timezone
from uuid import uuid4

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

state_lock = threading.Lock()

scan_state = {
    "status": "idle",
    "scan_id": None,
    "started_at": None,
    "finished_at": None,
    "analyses_completed": 0,
    "total_analyses": 0,
    "results": [],
    "error": None,
}


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
        df = download_data(asset, timeframe)

        if df is None or df.empty:
            return no_trade(asset, timeframe, "Datos no disponibles")

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

        if not is_data_fresh(
            df,
            asset=asset,
            interval=timeframe,
        ):
            return no_trade(
                asset,
                timeframe,
                "Datos desactualizados",
            )

        result = evaluate_signal(df, asset, timeframe)

        if not isinstance(result, dict):
            return no_trade(
                asset,
                timeframe,
                "La estrategia devolvió un resultado no válido",
            )

        result = result.copy()
        result["asset"] = asset
        result["timeframe"] = timeframe
        result["data_timestamp"] = timestamp.isoformat()
        result["data_age_minutes"] = round(age_minutes, 2)
        result["mode"] = "PAPER_ONLY"

        return result

    except Exception:
        logger.exception("Error analizando %s %s", asset, timeframe)
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

        for timeframe in (INTRADAY_INTERVAL, SWING_INTERVAL):
            result = analyze_asset(asset, timeframe)
            results.append(result)

            with state_lock:
                scan_state["results"] = list(results)
                scan_state["analyses_completed"] = len(results)

    candidates = [
        result for result in results
        if result.get("action") == "BUY CANDIDATE"
    ]

    no_trades = [
        result for result in results
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


def _scan_worker():
    try:
        result = run_scan()

        with state_lock:
            scan_state.update(result)
            scan_state["status"] = "completed"
            scan_state["finished_at"] = datetime.now(
                timezone.utc
            ).isoformat()

    except Exception as exc:
        logger.exception("Error durante el escaneo")

        with state_lock:
            scan_state["status"] = "error"
            scan_state["error"] = str(exc)
            scan_state["finished_at"] = datetime.now(
                timezone.utc
            ).isoformat()


def start_scan():
    with state_lock:
        if scan_state["status"] == "running":
            return False

        enabled_assets = [
            asset for asset, settings in ASSETS.items()
            if settings.get("enabled", False)
        ]

        scan_state.update({
            "status": "running",
            "scan_id": str(uuid4()),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "analyses_completed": 0,
            "total_analyses": len(enabled_assets) * 2,
            "results": [],
            "error": None,
        })

    try:
        thread = threading.Thread(
            target=_scan_worker,
            daemon=True,
        )
        thread.start()
        return True

    except Exception as exc:
        logger.exception("No se pudo iniciar el escaneo")

        with state_lock:
            scan_state["status"] = "error"
            scan_state["error"] = str(exc)
            scan_state["finished_at"] = datetime.now(
                timezone.utc
            ).isoformat()

        return False


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
    started = start_scan()

    with state_lock:
        response = dict(scan_state)

    response["scan_started"] = started
    response["automatic_execution"] = False
    response["real_trading_authorized"] = False

    return jsonify(response), 202 if started else 200


@app.route("/scan/status", methods=["GET"])
def scan_status():
    with state_lock:
        response = dict(scan_state)

    response["automatic_execution"] = False
    response["real_trading_authorized"] = False

    return jsonify(response)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
