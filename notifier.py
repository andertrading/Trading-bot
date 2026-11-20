
import logging
import requests

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_telegram(message):
    """Envía una alerta a Telegram."""

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram no está configurado.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            logger.error("Telegram rechazó el mensaje.")
            return False

        return True

    except requests.RequestException as exc:
        # La URL de Telegram contiene el token en su ruta.
        # Registrar solo el tipo de error evita filtrarlo en los logs.
        logger.error(
            "Error enviando alerta a Telegram: %s",
            type(exc).__name__,
        )
        return False


def format_signal(signal):
    """Da formato legible a una señal."""

    if not isinstance(signal, dict):
        return "Trading Bot 3.0: señal no válida."

    action = signal.get("action", "NO TRADE")
    asset = signal.get("asset", "Desconocido")
    timeframe = signal.get("timeframe", "N/D")

    if action != "BUY CANDIDATE":
        return (
            "📊 <b>TRADING BOT 3.0</b>\n\n"
            f"Activo: {asset}\n"
            f"Temporalidad: {timeframe}\n"
            "Estado: <b>NO TRADE</b>\n"
            f"Motivo: {signal.get('reason', 'Sin oportunidad válida')}"
        )

    return (
        "📊 <b>TRADING BOT 3.0</b>\n\n"
        "⚠️ <b>CANDIDATO DE COMPRA — NO EJECUTAR</b>\n\n"
        f"Activo: {asset}\n"
        f"Temporalidad: {timeframe}\n"
        f"Precio de referencia: {signal.get('entry_reference')}\n"
        f"Stop orientativo: {signal.get('stop')}\n"
        f"Objetivo orientativo: {signal.get('target')}\n"
        f"Relación riesgo/beneficio: {signal.get('risk_reward')}\n"
        f"Puntuación: {signal.get('score')}/100\n\n"
        "Motivos:\n"
        + "\n".join(
            f"• {reason}"
            for reason in signal.get("reasons", [])
        )
        + "\n\n"
        "Estado: pendiente de validación, costes y riesgo de cartera."
    )
