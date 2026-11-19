
import logging
import faulthandler
import threading
import sys
import platform
import math
import html
import io

import pandas as pd
import numpy as np
import requests

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    TIMEZONE,
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
chart_data_lock = threading.Lock()

TELEGRAM_ENABLED = bool(
    TELEGRAM_TOKEN and TELEGRAM_CHAT_ID
)

TELEGRAM_API_URL = (
    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    if TELEGRAM_TOKEN
    else None
)

try:
    LOCAL_TIMEZONE = ZoneInfo(TIMEZONE)
except Exception:
    logger.exception(
        "INVALID TIMEZONE CONFIGURATION | Using Europe/Madrid"
    )
    LOCAL_TIMEZONE = ZoneInfo("Europe/Madrid")


# Los DataFrames se conservan temporalmente en memoria
# para generar los gráficos de las candidatas.
# No se incluyen en las respuestas JSON.
telegram_chart_data = {}

scan_state = {
    "status": "idle",
    "scan_id": None,
    "started_at": None,
    "finished_at": None,
    "analyses_completed": 0,
    "total_analyses": 0,
    "results": [],
    "error": None,
    "telegram_alerts_enabled": TELEGRAM_ENABLED,
    "telegram_notification_status": "not_sent",
}


def local_timestamp(value=None):
    """
    Convierte una fecha a la zona horaria configurada.
    """

    try:
        if value is None:
            dt = datetime.now(timezone.utc)
        elif isinstance(value, datetime):
            dt = value
        else:
            text = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(LOCAL_TIMEZONE)

    except (TypeError, ValueError, OverflowError):
        return datetime.now(LOCAL_TIMEZONE)


def format_timestamp(value=None):
    dt = local_timestamp(value)

    return (
        dt.strftime("%d/%m/%Y · %H:%M:%S")
        + f" ({dt.tzname()})"
    )


def format_number(value, decimals=4):
    try:
        number = float(value)

        if not math.isfinite(number):
            return "N/D"

        return f"{number:,.{decimals}f}"

    except (TypeError, ValueError):
        return "N/D"


def format_risk(value):
    try:
        number = float(value)

        if not math.isfinite(number):
            return "N/D"

        return f"{number * 100:.2f}%"

    except (TypeError, ValueError):
        return "N/D"


def escape_html(value):
    return html.escape(str(value))


def send_telegram_message(message):
    """
    Envía un mensaje HTML a Telegram.
    Nunca ejecuta operaciones.
    """

    if not TELEGRAM_ENABLED:
        logger.warning(
            "TELEGRAM DISABLED | Missing credentials"
        )
        return False

    try:
        response = requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok", False):
            logger.error(
                "TELEGRAM API ERROR | %s",
                data.get("description", "Unknown error"),
            )
            return False

        logger.info("TELEGRAM MESSAGE SENT")
        return True

    except requests.RequestException:
        logger.exception("TELEGRAM CONNECTION ERROR")
        return False

    except ValueError:
        logger.exception("TELEGRAM INVALID RESPONSE")
        return False

    except Exception:
        logger.exception("TELEGRAM UNEXPECTED ERROR")
        return False


def send_telegram_photo(image_bytes, caption):
    """
    Envía un gráfico PNG directamente a Telegram.
    """

    if not TELEGRAM_ENABLED:
        return False

    if not image_bytes:
        return False

    try:
        response = requests.post(
            f"{TELEGRAM_API_URL}/sendPhoto",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption[:1024],
                "parse_mode": "HTML",
            },
            files={
                "photo": (
                    "trading_chart.png",
                    image_bytes,
                    "image/png",
                )
            },
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok", False):
            logger.error(
                "TELEGRAM PHOTO ERROR | %s",
                data.get("description", "Unknown error"),
            )
            return False

        logger.info("TELEGRAM CHART SENT")
        return True

    except requests.RequestException:
        logger.exception("TELEGRAM PHOTO CONNECTION ERROR")
        return False

    except ValueError:
        logger.exception("TELEGRAM PHOTO INVALID RESPONSE")
        return False

    except Exception:
        logger.exception("TELEGRAM PHOTO UNEXPECTED ERROR")
        return False


def get_candidate_entry(candidate):
    """
    Utiliza entry_reference, con entry como alternativa.
    """

    value = candidate.get("entry_reference")

    if value is None:
        value = candidate.get("entry")

    return value


def find_dataframe_column(df, possible_names):
    """
    Busca una columna sin distinguir mayúsculas,
    minúsculas, espacios o guiones bajos.
    """

    normalized = {}

    for column in df.columns:
        key = (
            str(column)
            .lower()
            .replace("_", "")
            .replace(" ", "")
            .replace("-", "")
        )
        normalized[key] = column

    for name in possible_names:
        key = (
            name.lower()
            .replace("_", "")
            .replace(" ", "")
            .replace("-", "")
        )

        if key in normalized:
            return normalized[key]

    return None


def generate_candidate_chart(candidate):
    """
    Genera un gráfico PNG en memoria.

    Incluye:
    - Evolución del precio.
    - EMA 20 y EMA 50 si están disponibles.
    - Entrada técnica.
    - Stop técnico.
    - Objetivo técnico.
    """

    asset = candidate.get("asset", "N/D")
    timeframe = candidate.get("timeframe", "N/D")

    with chart_data_lock:
        df = telegram_chart_data.get(
            (asset, timeframe)
        )

        if df is not None:
            df = df.copy()

    if df is None or df.empty:
        logger.warning(
            "CHART DATA NOT AVAILABLE | %s | %s",
            asset,
            timeframe,
        )
        return None

    try:
        import matplotlib

        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        close_column = find_dataframe_column(
            df,
            ["Close", "Adj Close", "close"],
        )

        if close_column is None:
            logger.warning(
                "CHART CLOSE COLUMN NOT FOUND | %s",
                asset,
            )
            return None

        plot_df = df.tail(120).copy()

        close = pd.to_numeric(
            plot_df[close_column],
            errors="coerce",
        )

        valid = close.notna()

        plot_df = plot_df.loc[valid]
        close = close.loc[valid]

        if len(close) < 2:
            return None

        entry = get_candidate_entry(candidate)
        stop = candidate.get("stop")
        target = candidate.get("target")

        fig, ax = plt.subplots(
            figsize=(12, 7),
            dpi=150,
        )

        fig.patch.set_facecolor("#111827")
        ax.set_facecolor("#111827")

        ax.plot(
            plot_df.index,
            close,
            label="Precio",
            linewidth=2.2,
            color="#60A5FA",
        )

        ema20_column = find_dataframe_column(
            plot_df,
            ["EMA20", "EMA_20", "ema20"],
        )

        ema50_column = find_dataframe_column(
            plot_df,
            ["EMA50", "EMA_50", "ema50"],
        )

        if ema20_column is not None:
            ema20 = pd.to_numeric(
                plot_df[ema20_column],
                errors="coerce",
            )

            if ema20.notna().any():
                ax.plot(
                    plot_df.index,
                    ema20,
                    label="EMA 20",
                    linewidth=1.5,
                    color="#FBBF24",
                )

        if ema50_column is not None:
            ema50 = pd.to_numeric(
                plot_df[ema50_column],
                errors="coerce",
            )

            if ema50.notna().any():
                ax.plot(
                    plot_df.index,
                    ema50,
                    label="EMA 50",
                    linewidth=1.5,
                    color="#A78BFA",
                )

        levels = [
            (entry, "Entrada", "#34D399"),
            (stop, "Stop-loss", "#F87171"),
            (target, "Objetivo", "#FBBF24"),
        ]

        for value, label, color in levels:
            try:
                number = float(value)

                if math.isfinite(number):
                    ax.axhline(
                        number,
                        color=color,
                        linestyle="--",
                        linewidth=1.5,
                        label=f"{label}: {number:.4f}",
                    )

            except (TypeError, ValueError):
                continue

        ax.set_title(
            f"{asset} · {timeframe}",
            color="white",
            fontsize=17,
            fontweight="bold",
            pad=18,
        )

        ax.set_xlabel(
            "Fecha",
            color="#D1D5DB",
        )

        ax.set_ylabel(
            "Precio",
            color="#D1D5DB",
        )

        ax.tick_params(
            axis="both",
            colors="#D1D5DB",
        )

        ax.grid(
            True,
            alpha=0.18,
            linestyle=":",
        )

        for spine in ax.spines.values():
            spine.set_color("#374151")

        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%d/%m")
        )

        legend = ax.legend(
            loc="best",
            facecolor="#1F2937",
            edgecolor="#374151",
            fontsize=8,
        )

        for text_item in legend.get_texts():
            text_item.set_color("white")

        fig.autofmt_xdate()
        fig.tight_layout()

        buffer = io.BytesIO()

        fig.savefig(
            buffer,
            format="png",
            facecolor=fig.get_facecolor(),
            bbox_inches="tight",
        )

        plt.close(fig)

        buffer.seek(0)

        return buffer.getvalue()

    except Exception:
        logger.exception(
            "CHART GENERATION ERROR | %s | %s",
            asset,
            timeframe,
        )
        return None


def build_telegram_messages(scan_result):
    """
    Construye un resumen visual y un mensaje por candidata.
    """

    timestamp = scan_result.get(
        "timestamp",
        datetime.now(timezone.utc).isoformat(),
    )

    analyses = scan_result.get(
        "analyses_completed",
        0,
    )

    technical = scan_result.get(
        "technical_candidates_found",
        0,
    )

    blocked = scan_result.get(
        "candidates_blocked",
        0,
    )

    validated = scan_result.get(
        "validated_candidates",
        0,
    )

    no_trade_count = scan_result.get(
        "no_trade_count",
        0,
    )

    portfolio = scan_result.get(
        "portfolio_status",
        {},
    )

    portfolio_status = (
        portfolio.get("status", "UNKNOWN")
        if isinstance(portfolio, dict)
        else "UNKNOWN"
    )

    summary = (
        "📊 <b>TRADING BOT 3.0</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>ANÁLISIS COMPLETADO</b>\n\n"
        f"🕒 {escape_html(format_timestamp(timestamp))}\n\n"
        "📈 <b>RESULTADOS</b>\n"
        f"• Análisis realizados: <b>{analyses}</b>\n"
        f"• Candidatas técnicas: <b>{technical}</b>\n"
        f"• Candidatas bloqueadas: <b>{blocked}</b>\n"
        f"• Candidatas validadas: <b>{validated}</b>\n"
        f"• Sin operación (NO TRADE): <b>{no_trade_count}</b>\n\n"
        "💼 <b>CARTERA</b>\n"
        f"Estado: <b>{escape_html(portfolio_status)}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🛡️ <b>SEGURIDAD</b>\n"
        "Modo: <b>PAPER ONLY</b>\n"
        "Ejecución automática: <b>DESACTIVADA</b>\n"
        "Operativa real: <b>NO AUTORIZADA</b>"
    )

    messages = [summary]

    blocked_candidates = [
        item
        for item in scan_result.get("results", [])
        if (
            item.get("action") == "BUY CANDIDATE"
            and item.get("validation_status") == "BLOCKED"
        )
    ]

    if not blocked_candidates:
        messages.append(
            "🔎 <b>RESUMEN DE OPORTUNIDADES</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "No se han detectado candidatas técnicas "
            "bloqueadas en este análisis."
        )

        return messages

    for candidate in blocked_candidates:
        asset = escape_html(
            candidate.get("asset", "N/D")
        )

        timeframe = escape_html(
            candidate.get("timeframe", "N/D")
        )

        score = escape_html(
            candidate.get("score", "N/D")
        )

        entry = format_number(
            get_candidate_entry(candidate),
            4,
        )

        stop = format_number(
            candidate.get("stop"),
            4,
        )

        target = format_number(
            candidate.get("target"),
            4,
        )

        rr = format_number(
            candidate.get("risk_reward"),
            2,
        )

        risk = format_risk(
            candidate.get("provisional_risk_pct")
        )

        reasons = candidate.get(
            "validation_reasons",
            [],
        )

        if not isinstance(reasons, list):
            reasons = [str(reasons)]

        reasons_text = "\n".join(
            f"• {escape_html(reason)}"
            for reason in reasons
        )

        if len(reasons_text) > 550:
            reasons_text = reasons_text[:550] + "..."

        message = (
            "⚠️ <b>CANDIDATA TÉCNICA DETECTADA</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🚫 <b>BLOQUEADA — NO OPERAR</b>\n\n"
            f"📌 <b>{asset}</b>  |  {timeframe}\n"
            f"⭐ Score: <b>{score}</b>\n\n"
            "💰 <b>NIVELES TÉCNICOS</b>\n"
            f"🟢 Entrada: <code>{entry}</code>\n"
            f"🔴 Stop-loss: <code>{stop}</code>\n"
            f"🟡 Objetivo: <code>{target}</code>\n"
            f"⚖️ Ratio riesgo/beneficio: <b>{rr}</b>\n"
            f"📉 Riesgo provisional: <b>{risk}</b>\n\n"
            "🔒 <b>MOTIVOS DEL BLOQUEO</b>\n"
            f"{reasons_text}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "❌ Estado: <b>NO VALIDADA</b>\n"
            "Este análisis no constituye una orden.\n"
            "<b>NO OPERAR.</b>"
        )

        messages.append(message)

    return messages


def send_telegram_notifications(scan_result):
    """
    Envía el resumen y las candidatas.
    Si hay una candidata con datos disponibles,
    adjunta también su gráfico.
    """

    if not TELEGRAM_ENABLED:
        return "disabled"

    try:
        messages = build_telegram_messages(scan_result)

        sent = 0
        failed = 0

        candidates = [
            item
            for item in scan_result.get("results", [])
            if (
                item.get("action") == "BUY CANDIDATE"
                and item.get("validation_status") == "BLOCKED"
            )
        ]

        # Primer mensaje: resumen general.
        if messages:
            if send_telegram_message(messages[0]):
                sent += 1
            else:
                failed += 1

        # Mensajes siguientes: candidatas y gráficos.
        for index, message in enumerate(messages[1:]):
            if index < len(candidates):
                candidate = candidates[index]

                chart = generate_candidate_chart(
                    candidate
                )

                if chart is not None:
                    success = send_telegram_photo(
                        chart,
                        message,
                    )
                else:
                    success = send_telegram_message(
                        message
                    )

            else:
                success = send_telegram_message(
                    message
                )

            if success:
                sent += 1
            else:
                failed += 1

        if failed == 0:
            logger.info(
                "TELEGRAM NOTIFICATIONS COMPLETED | sent=%s",
                sent,
            )
            return f"sent:{sent}"

        logger.warning(
            "TELEGRAM NOTIFICATIONS PARTIAL | sent=%s failed=%s",
            sent,
            failed,
        )

        if sent > 0:
            return f"partial:{sent}/{failed}"

        return "failed"

    except Exception:
        logger.exception(
            "TELEGRAM NOTIFICATION BUILD ERROR"
        )
        return "failed"


def no_trade(asset, timeframe, reason):
    return {
        "action": "NO TRADE",
        "asset": asset,
        "timeframe": timeframe,
        "reason": reason,
        "mode": "PAPER_ONLY",
        "tradable": False,
        "validated": False,
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

    if ACCOUNT_CURRENCY != "USD":
        blockers.append(
            "La divisa de la cuenta no está configurada en USD"
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
    Conserva las candidatas técnicas, pero bloquea todas
    las operaciones hasta completar la validación integral.
    """

    result = result.copy()

    if result.get("action") != "BUY CANDIDATE":
        result["tradable"] = False
        result["validated"] = False
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

    try:
        risk_is_valid = (
            isinstance(provisional_risk_pct, (int, float))
            and not isinstance(provisional_risk_pct, bool)
            and math.isfinite(provisional_risk_pct)
            and provisional_risk_pct > 0
            and provisional_risk_pct <= MAX_RISK_PER_TRADE
            and provisional_risk_pct <= 0.01
        )
    except (TypeError, ValueError):
        risk_is_valid = False

    if not risk_is_valid:
        blockers.append(
            "Nivel de riesgo provisional no válido"
        )

    # El cálculo de tamaño y la validación integral
    # todavía no están conectados a este flujo.
    # Por tanto, ninguna candidata puede aprobarse.
    blockers.append(
        "Validación integral y cálculo de posición pendientes"
    )

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

        # Guardamos únicamente los datos de candidatas.
        # Se usarán después para generar el gráfico de Telegram.
        if result.get("action") == "BUY CANDIDATE":
            with chart_data_lock:
                telegram_chart_data[(asset, timeframe)] = (
                    df.tail(120).copy()
                )

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
    try:
        portfolio_status = get_portfolio_status(
            open_positions=None,
            positions_confirmed=False,
            capital_usd=INITIAL_CAPITAL_USD,
        )

        if not isinstance(portfolio_status, dict):
            raise ValueError(
                "El gestor de cartera devolvió un resultado no válido"
            )

    except Exception:
        logger.exception("PORTFOLIO STATUS ERROR")

        portfolio_status = {
            "status": "UNKNOWN",
            "current_portfolio_risk_usd": None,
            "max_portfolio_risk_usd": (
                INITIAL_CAPITAL_USD
                * min(MAX_PORTFOLIO_RISK, 0.03)
            ),
            "remaining_risk_usd": None,
            "currency": "USD",
            "error": "No se pudo confirmar el estado de la cartera",
        }

    logger.info(
        "PORTFOLIO STATUS | %s",
        portfolio_status.get("status"),
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
        and result.get("validated") is True
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
        "portfolio_risk_limit": min(MAX_PORTFOLIO_RISK, 0.03),
        "max_risk_per_trade": min(MAX_RISK_PER_TRADE, 0.01),
        "currency": ACCOUNT_CURRENCY,
        "results": results,
        "mode": "PAPER_ONLY",
        "telegram_alerts_enabled": TELEGRAM_ENABLED,
        "automatic_execution": False,
        "real_trading_authorized": False,
    }


def _scan_worker():
    logger.info("SCAN WORKER START")

    try:
        result = run_scan()

        # Las notificaciones se envían al terminar el análisis.
        # Si Telegram falla, el análisis no se pierde.
        telegram_status = send_telegram_notifications(result)

        result["telegram_alerts_enabled"] = TELEGRAM_ENABLED
        result["telegram_notification_status"] = telegram_status

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
            "telegram_alerts_enabled": TELEGRAM_ENABLED,
            "telegram_notification_status": "pending",
        })

    # Evita reutilizar gráficos de un escaneo anterior.
    with chart_data_lock:
        telegram_chart_data.clear()

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
        "telegram_alerts_enabled": TELEGRAM_ENABLED,
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
        "telegram_alerts_enabled": TELEGRAM_ENABLED,
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
        "telegram_alerts_enabled": TELEGRAM_ENABLED,
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
