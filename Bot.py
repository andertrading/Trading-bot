
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
    ETORO_EXECUTION_TYPES_CONFIRMED,
    ETORO_FEE_SCHEDULE_CONFIRMED,
    ETORO_ACCOUNT_CURRENCY_CONFIRMED,
    ETORO_EXECUTION_PRICE_BASIS_CONFIRMED,
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    TIMEZONE,
)

from data_engine import (
    download_data,
    get_data_timestamp,
    is_data_fresh,
    is_intraday_market_open,
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

    except requests.RequestException as exc:
        # No registrar la excepción: su URL puede incluir el token
        # del bot de Telegram en la ruta de la petición.
        logger.error(
            "TELEGRAM CONNECTION ERROR | %s",
            type(exc).__name__,
        )
        return False

    except ValueError as exc:
        logger.error(
            "TELEGRAM INVALID RESPONSE | %s",
            type(exc).__name__,
        )
        return False

    except Exception as exc:
        logger.error(
            "TELEGRAM UNEXPECTED ERROR | %s",
            type(exc).__name__,
        )
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

    except requests.RequestException as exc:
        logger.error(
            "TELEGRAM PHOTO CONNECTION ERROR | %s",
            type(exc).__name__,
        )
        return False

    except ValueError as exc:
        logger.error(
            "TELEGRAM PHOTO INVALID RESPONSE | %s",
            type(exc).__name__,
        )
        return False

    except Exception as exc:
        logger.error(
            "TELEGRAM PHOTO UNEXPECTED ERROR | %s",
            type(exc).__name__,
        )
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


def _percentage_between(start, end):
    """Gross percentage change between two positive price levels."""
    try:
        start_value = float(start)
        end_value = float(end)
        if (
            not math.isfinite(start_value)
            or not math.isfinite(end_value)
            or start_value <= 0
            or end_value <= 0
        ):
            return None
        return (end_value / start_value - 1.0) * 100.0
    except (TypeError, ValueError, OverflowError):
        return None


def build_telegram_messages(scan_result):
    """
    Construye un resumen y un informe por candidata técnica.

    Las cifras de rentabilidad/riesgo son movimientos brutos entre niveles:
    no incluyen spread, slippage, comisiones ni financiación.
    """

    timestamp = scan_result.get(
        "timestamp",
        datetime.now(timezone.utc).isoformat(),
    )
    analyses = scan_result.get("analyses_completed", 0)
    technical = scan_result.get("technical_candidates_found", 0)
    blocked = scan_result.get("candidates_blocked", 0)
    validated = scan_result.get("validated_candidates", 0)
    no_trade_count = scan_result.get("no_trade_count", 0)

    portfolio = scan_result.get("portfolio_status", {})
    portfolio_status = (
        portfolio.get("status", "UNKNOWN")
        if isinstance(portfolio, dict)
        else "UNKNOWN"
    )

    candidates = [
        item
        for item in scan_result.get("results", [])
        if (
            isinstance(item, dict)
            and item.get("action") == "BUY CANDIDATE"
            and item.get("validation_status") == "BLOCKED"
        )
    ]

    summary = (
        "📊 <b>TRADING BOT 3.0</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>ANÁLISIS COMPLETADO</b>\n\n"
        f"🕒 {escape_html(format_timestamp(timestamp))}\n\n"
        "📈 <b>RESULTADOS</b>\n"
        f"• Análisis realizados: <b>{analyses}</b>\n"
        f"• Candidatas técnicas: <b>{technical}</b>\n"
        f"• Candidatas pendientes de validación: <b>{blocked}</b>\n"
        f"• Candidatas validadas para operativa: <b>{validated}</b>\n"
        f"• Sin oportunidad técnica: <b>{no_trade_count}</b>\n\n"
        "🛡️ <b>MODO DE FUNCIONAMIENTO</b>\n"
        "Alertas informativas: <b>SÍ</b>\n"
        "Ejecución automática: <b>DESACTIVADA</b>\n"
        "Acceso a cuenta eToro: <b>NO REQUERIDO</b>\n"
        f"Estado de cartera: <b>{escape_html(portfolio_status)}</b> "
        "(no conectado)"
    )

    messages = [summary]

    if not candidates:
        messages.append(
            "🔎 <b>RESUMEN DE OPORTUNIDADES</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "No se han detectado candidatas técnicas en este análisis."
        )
        return messages

    for candidate in candidates:
        asset = escape_html(candidate.get("asset", "N/D"))
        timeframe = escape_html(candidate.get("timeframe", "N/D"))
        score = escape_html(candidate.get("score", "N/D"))

        entry_value = get_candidate_entry(candidate)
        stop_value = candidate.get("stop")
        target_value = candidate.get("target")

        entry = format_number(entry_value, 4)
        stop = format_number(stop_value, 4)
        target = format_number(target_value, 4)
        rr = format_number(candidate.get("risk_reward"), 2)

        potential_reward = _percentage_between(
            entry_value, target_value
        )
        potential_risk = _percentage_between(
            entry_value, stop_value
        )
        reward_text = (
            f"+{potential_reward:.2f}%"
            if potential_reward is not None and potential_reward >= 0
            else "N/D"
        )
        risk_text = (
            f"{abs(potential_risk):.2f}%"
            if potential_risk is not None and potential_risk <= 0
            else "N/D"
        )

        risk_fraction = candidate.get("provisional_risk_pct")
        risk = format_risk(risk_fraction)
        try:
            risk_amount = float(INITIAL_CAPITAL_USD) * float(risk_fraction)
            if not math.isfinite(risk_amount) or risk_amount < 0:
                raise ValueError("invalid risk amount")
            risk_amount_text = f"{risk_amount:.2f} USD"
        except (TypeError, ValueError, OverflowError):
            risk_amount_text = "N/D"
            risk_amount = None

        try:
            rr_numeric = float(candidate.get("risk_reward"))
            reward_amount = (
                risk_amount * rr_numeric
                if risk_amount is not None
                and math.isfinite(rr_numeric)
                and rr_numeric > 0
                else None
            )
            reward_amount_text = (
                f"{reward_amount:.2f} USD"
                if reward_amount is not None and math.isfinite(reward_amount)
                else "N/D"
            )
        except (TypeError, ValueError, OverflowError):
            reward_amount_text = "N/D"

        data_timestamp = candidate.get("data_timestamp")
        data_age = candidate.get("data_age_minutes")
        data_provider = escape_html(candidate.get("data_provider", "N/D"))
        adjustment_policy = escape_html(
            candidate.get("price_adjustment_policy", "N/D")
        )
        signal_expiry = candidate.get("expires_at")
        if data_timestamp:
            data_time_text = escape_html(format_timestamp(data_timestamp))
        else:
            data_time_text = "N/D"
        if signal_expiry:
            expiry_text = escape_html(format_timestamp(signal_expiry))
        else:
            expiry_text = "NO CONFIGURADA"
        try:
            age_value = float(data_age)
            age_text = f"{age_value:.1f} min" if math.isfinite(age_value) else "N/D"
        except (TypeError, ValueError, OverflowError):
            age_text = "N/D"

        technical_reasons = candidate.get("reasons", [])
        if not isinstance(technical_reasons, list):
            technical_reasons = [str(technical_reasons)]
        technical_text = "\n".join(
            f"• {escape_html(reason)}"
            for reason in technical_reasons
        ) or "• Sin motivos técnicos detallados."
        if len(technical_text) > 600:
            technical_text = technical_text[:600] + "..."

        blockers = candidate.get("validation_reasons", [])
        if not isinstance(blockers, list):
            blockers = [str(blockers)]
        blockers_display = []
        for reason in blockers:
            if "Cartera no confirmada" in str(reason):
                blockers_display.append(
                    "Riesgo agregado de cartera no verificado; el bot no consulta "
                    "posiciones. Comprueba manualmente el límite del 3%."
                )
            else:
                blockers_display.append(str(reason))
        blocker_text = "\n".join(
            f"• {escape_html(reason)}"
            for reason in blockers_display
        ) or "• Validación pendiente."
        if len(blocker_text) > 600:
            blocker_text = blocker_text[:600] + "..."

        strategy_validated = candidate.get("strategy_validated") is True
        if strategy_validated:
            report_title = "📡 <b>SEÑAL TÉCNICA · DECISIÓN MANUAL</b>"
            report_status = (
                "⚠️ <b>CONFIRMA COSTES Y CONDICIONES ANTES DE ENTRAR</b>"
            )
            report_closing = (
                "La estrategia ha superado la validación histórica definida. "
                "La decisión y ejecución siguen siendo manuales."
            )
        else:
            report_title = "🔎 <b>CANDIDATA TÉCNICA · SOLO INVESTIGACIÓN</b>"
            report_status = "🚫 <b>NO OPERAR — ESTRATEGIA NO VALIDADA</b>"
            report_closing = (
                "La estrategia aún no ha superado la validación histórica. "
                "No utilizar como instrucción de entrada."
            )

        message = (
            f"{report_title}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{report_status}\n\n"
            f"📌 <b>{asset}</b>  |  {timeframe}  |  Score <b>{score}/100</b>\n"
            "🟢 Dirección: <b>LONG</b>\n\n"
            "💰 <b>NIVELES Y MOVIMIENTO POTENCIAL</b>\n"
            f"Entrada de referencia: <code>{entry}</code>\n"
            f"Stop técnico: <code>{stop}</code>\n"
            f"Objetivo técnico: <code>{target}</code>\n"
            f"Potencial bruto hasta objetivo: <b>{reward_text}</b>\n"
            f"Movimiento adverso hasta stop: <b>{risk_text}</b>\n"
            f"Ratio riesgo/beneficio teórico: <b>{rr}</b>\n"
            "<i>Porcentajes sobre niveles OHLC; antes de costes y slippage.</i>\n\n"
            "🧮 <b>RIESGO DE REFERENCIA</b>\n"
            f"Tier provisional: <b>{risk}</b>\n"
            f"Pérdida teórica al SL sobre {INITIAL_CAPITAL_USD:.0f} USD: "
            f"<b>{risk_amount_text}</b>\n"
            f"Beneficio teórico al TP: <b>{reward_amount_text}</b>\n"
            "<i>Estimación suponiendo que el tamaño se ajusta al riesgo indicado; "
            "no es tamaño real ni incluye costes.</i>\n\n"
            "📊 <b>JUSTIFICACIÓN TÉCNICA</b>\n"
            f"{technical_text}\n\n"
            "🕒 <b>CALIDAD DEL DATO</b>\n"
            f"Última vela: {data_time_text}\n"
            f"Antigüedad al analizar: <b>{age_text}</b>\n"
            f"Proveedor: <b>{data_provider}</b>\n"
            f"Ajuste OHLC: <code>{adjustment_policy}</code>\n\n"
            "🔒 <b>VALIDACIONES PENDIENTES</b>\n"
            f"{blocker_text}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Probabilidad histórica: <b>NO VALIDADA</b>\n"
            f"Caducidad de la señal: <b>{expiry_text}</b>\n"
            "Noticias y costes: <b>NO INCORPORADOS AÚN</b>\n"
            f"{escape_html(report_closing)}\n"
            "Este informe no constituye asesoramiento financiero."
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
        "data_valid": False,
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

    if not ETORO_EXECUTION_PRICE_BASIS_CONFIRMED:
        blockers.append(
            "Correspondencia entre OHLC históricos y precios bid/ask ejecutables de eToro no verificada"
        )

    if not FX_CONVERSION_CONFIRMED:
        blockers.append(
            "Conversión de divisa pendiente de confirmar"
        )

    if not ETORO_EXECUTION_TYPES_CONFIRMED:
        blockers.append(
            "Tipo de ejecución REAL/CFD pendiente de verificar en cada instrumento de eToro"
        )

    if not ETORO_FEE_SCHEDULE_CONFIRMED:
        blockers.append(
            "Nivel Club y comisiones personales de eToro pendientes de confirmar"
        )

    if not ETORO_ACCOUNT_CURRENCY_CONFIRMED:
        blockers.append(
            "Divisa real de la cuenta eToro pendiente de confirmar"
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
        result["data_valid"] = True
        result["last_price"] = float(df["Close"].iloc[-1])
        result["data_provider"] = df.attrs.get("data_provider", "unknown")
        result["price_adjustment_policy"] = df.attrs.get(
            "price_adjustment_policy", "unknown"
        )
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


def run_scan(timeframes=None):
    """Analyze selected timeframes without requiring broker/account access."""
    results = []

    if timeframes is None:
        selected_timeframes = (INTRADAY_INTERVAL, SWING_INTERVAL)
    else:
        selected_timeframes = tuple(dict.fromkeys(timeframes))
        if not selected_timeframes or any(
            timeframe not in (INTRADAY_INTERVAL, SWING_INTERVAL)
            for timeframe in selected_timeframes
        ):
            raise ValueError("Temporalidades de escaneo no válidas")

    logger.info(
        "SCAN LOOP START | timeframes=%s",
        selected_timeframes,
    )

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

    enabled_assets = [
        asset for asset, settings in ASSETS.items()
        if settings.get("enabled", False)
    ]
    total_analyses = len(enabled_assets) * len(selected_timeframes)

    with state_lock:
        scan_state["total_analyses"] = total_analyses

    for asset in enabled_assets:
        for timeframe in selected_timeframes:
            if (
                timeframe == INTRADAY_INTERVAL
                and not is_intraday_market_open(asset)
            ):
                result = no_trade(
                    asset,
                    timeframe,
                    "Mercado cerrado o aún sin vela intradía completa",
                )
                result["market_closed"] = True
            else:
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
                total_analyses,
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
        "timeframes": list(selected_timeframes),
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
