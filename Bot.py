
import logging
import faulthandler
import threading
import sys
import platform

import pandas as pd
import numpy as np

from datetime import datetime, timezone
from uuid import uuid4

from flask import Flask, jsonify

from config import (
    BOT_VERSION,
    ASSETS,
    INTRADAY_INTERVAL,
    SWING_INTERVAL,
    ACCOUNT_CURRENCY,
    INITIAL_CAPITAL_USD,
    MAX_RISK_PER_TRADE,
    MAX_PORTFOLIO_RISK,
    ALLOW_POSITION_SIZING,
    CLOSE_FEES_CONFIRMED,
    SPREADS_CONFIRMED,
    FX_CONVERSION_CONFIRMED,
)

from data_engine import (
    download_data,
    get_data_timestamp,
    is_data_fresh,
)

from strategies import evaluate_signal
from risk_manager import risk_pct_for_score
from portfolio import get_portfolio_status


faulthandler.enable(all_threads=True)

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
        "tradable": False,
    }


def get_validation_blockers(portfolio_status):
    blockers = []

    if not ALLOW_POSITION_SIZING:
        blockers.append(
            "Cálculo de tamaño de posición desactivado"
        )

    if not CLOSE_FEES_CONFIRMED:
        blockers.append(
            "Comisiones de cierre pendientes de confirmar"
        )

    if not SPREADS_CONFIRMED:
        blockers.append(
            "Spreads pendientes de confirmar"
        )

    if not FX_CONVERSION_CONFIRMED:
        blockers.append(
            "Conversión de divisa pendiente de confirmar"
        )

    if not isinstance(portfolio_status, dict):
        blockers.append(
            "Estado de cartera no disponible"
        )
    elif portfolio_status.get("status") != "OK":
        blockers.append(
            "Cartera no confirmada o riesgo de cartera bloqueado"
        )

    return blockers


def apply_validation_gate(result, portfolio_status):
    """
    Conserva las candidatas técnicas, pero impide
    considerarlas operaciones validadas.
    """

    result = result.copy()

    if result.get("action") != "BUY CANDIDATE":
        result["tradable"] = False
        return result

    score = result.get("score")

    try:
        provisional_risk_pct = risk_pct_for_score(score)
    except Exception:
        logger.exception("RISK MANAGER ERROR")
        provisional_risk_pct = 0.0

    result["strategy_status"] = result.get("status")
    result["provisional_risk_pct"] = provisional_risk_pct

    blockers = get_validation_blockers(portfolio_status)

    if (
        not isinstance(provisional_risk_pct, (int, float))
        or provisional_risk_pct <= 0
        or provisional_risk_pct > MAX_RISK_PER_TRADE
    ):
        blockers.append(
            "Nivel de riesgo provisional no válido"
        )

    # No se calcula tamaño ni riesgo monetario de una
    # posición sin costes y datos de cartera confirmados.
    result["validation_status"] = "BLOCKED"
    result["status"] = "BLOCKED_VALIDATION"
    result["tradable"] = False
    result["validated"] = False
    result["validation_reasons"] = blockers

    logger.warning(
        "CANDIDATE BLOCKED | %s | %s | reasons=%s",
        result.get("asset"),
        result.get("timeframe"),
        blockers,
    )

    return result


def analyze_asset(asset, timeframe):
    logger.info(
        "ANALYSIS START | %s | %s",
        asset,
        timeframe,
    )

    try:
        logger.info(
            "DATA DOWNLOAD START | %s | %s",
            asset,
            timeframe,
        )

        df = download_data(asset, timeframe)

        logger.info(
            "DATA DOWNLOAD END | %s | %s | rows=%s",
            asset,
            timeframe,
            0 if df is None else len(df),
        )

        if df is None or df.empty:
            return no_trade(
                asset,
                timeframe,
                "Datos no disponibles",
            )

        timestamp = get_data_timestamp(df)

        if timestamp is None:
            return no_trade(
                asset,
                timeframe,
                "No se pudo determinar la fecha de los datos",
            )

        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")

        timestamp = timestamp.tz_convert("UTC")

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

        logger.info(
            "STRATEGY START | %s | %s",
            asset,
            timeframe,
        )

        result = evaluate_signal(
            df,
            asset,
            timeframe,
        )

        logger.info(
            "STRATEGY END | %s | %s",
            asset,
            timeframe,
        )

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
        result["currency"] = ACCOUNT_CURRENCY

        logger.info(
            "ANALYSIS END | %s | %s | action=%s",
            asset,
            timeframe,
            result.get("action"),
        )

        return result

    except Exception:
        logger.exception(
            "PYTHON ERROR | %s | %s",
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

    logger.info("SCAN LOOP START")

    # No existe conexión ni confirmación automática
    # de las posiciones abiertas en eToro.
    portfolio_status = get_portfolio_status(
        open_positions=None,
        positions_confirmed=False,
        capital_usd=INITIAL_CAPITAL_USD,
    )

    logger.info(
        "PORTFOLIO STATUS | %s",
        portfolio_status.get("status")
        if isinstance(portfolio_status, dict)
        else "INVALID",
    )

    for asset, settings in ASSETS.items():
        if not settings.get("enabled", False):
            continue

        for timeframe in (
            INTRADAY_INTERVAL,
            SWING_INTERVAL,
        ):
            result = analyze_asset(asset, timeframe)

            result = apply_validation_gate(
                result,
                portfolio_status,
            )

            results.append(result)

            with state_lock:
                scan_state["results"] = list(results)
                scan_state["analyses_completed"] = len(results)

            logger.info(
                "SCAN PROGRESS | %s/%s",
                len(results),
                scan_state["total_analyses"],
            )

    technical_candidates = [
        result for result in results
        if result.get("action") == "BUY CANDIDATE"
    ]

    blocked_candidates = [
        result for result in technical_candidates
        if result.get("validation_status") == "BLOCKED"
    ]

    validated_candidates = [
        result for result in technical_candidates
        if result.get("validation_status") == "APPROVED"
        and result.get("tradable") is True
    ]

    no_trades = [
        result for result in results
        if result.get("action") == "NO TRADE"
    ]

    logger.info("SCAN LOOP END")

    return {
        "version": BOT_VERSION,
        "status": "analysis_completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "analyses_completed": len(results),
        "technical_candidates_found": len(technical_candidates),
        "candidates_blocked": len(blocked_candidates),
        "validated_candidates": len(validated_candidates),
        "candidates_found": len(validated_candidates),
        "no_trade_count": len(no_trades),
        "portfolio_status": portfolio_status,
        "portfolio_risk_limit": MAX_PORTFOLIO_RISK,
        "max_risk_per_trade": MAX_RISK_PER_TRADE,
        "currency": ACCOUNT_CURRENCY,
        "results": results,
        "mode": "PAPER_ONLY",
        "telegram_alerts_enabled": False,
        "automatic_execution": False,
        "real_trading_authorized": False,
    }


def _scan_worker():
    logger.info("SCAN WORKER START")

    try:
        result = run_scan()

        with state_lock:
            scan_state.update(result)
            scan_state["status"] = "completed"
            scan_state["finished_at"] = datetime.now(
                timezone.utc
            ).isoformat()

        logger.info("SCAN WORKER COMPLETED")

    except Exception as exc:
        logger.exception("SCAN WORKER PYTHON ERROR")

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
            asset
            for asset, settings in ASSETS.items()
            if settings.get("enabled", False)
        ]

        scan_state.update({
            "status": "running",
            "scan_id": str(uuid4()),
            "started_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "finished_at": None,
            "analyses_completed": 0,
            "total_analyses": len(enabled_assets) * 2,
            "results": [],
            "error": None,
        })

    try:
        logger.info("SCAN REQUEST RECEIVED")

        thread = threading.Thread(
            target=_scan_worker,
            daemon=True,
            name="trading-scan-worker",
        )

        thread.start()

        logger.info("SCAN THREAD STARTED")

        return True

    except Exception as exc:
        logger.exception("SCAN THREAD START ERROR")

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
        "real_trading_authorized": False,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "version": BOT_VERSION,
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
        "mode": "PAPER_ONLY",
    })


@app.route("/diagnostics", methods=["GET"])
def diagnostics():
    return jsonify({
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "pandas_version": pd.__version__,
        "numpy_version": np.__version__,
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
    app.run(
        host="0.0.0.0",
        port=5000,
    )
