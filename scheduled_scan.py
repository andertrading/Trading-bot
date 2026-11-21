"""Scheduled research-signal scanner.

This entry point is intended for a single scheduled GitHub Actions job.
It never places orders and does not access the user's broker account.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

import Bot
from config import SIGNAL_ALERTS_ENABLED, STRATEGY_VALIDATED
from data_engine import signal_expiry_at
from signal_lifecycle import (
    UpstashSignalStateStore,
    advance_signal,
    build_signal_record,
)


logger = logging.getLogger("scheduled_scan")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def _send_signal_report(scan_result: dict, candidate: dict, signal: dict) -> bool:
    report_candidate = dict(candidate)
    report_candidate["expires_at"] = signal["expires_at"]
    report_candidate["signal_id"] = signal["signal_id"]
    report_candidate["strategy_validated"] = STRATEGY_VALIDATED

    report_scan = dict(scan_result)
    report_scan.update({
        "technical_candidates_found": 1,
        "candidates_blocked": 1,
        "validated_candidates": 0,
        "no_trade_count": 0,
        "results": [report_candidate],
    })

    messages = Bot.build_telegram_messages(report_scan)
    if len(messages) < 2:
        logger.error("No se pudo construir el informe Telegram para %s", signal["signal_id"])
        return False

    chart = Bot.generate_candidate_chart(report_candidate)
    if chart is not None:
        return bool(Bot.send_telegram_photo(chart, messages[1]))
    return bool(Bot.send_telegram_message(messages[1]))


def _lifecycle_message(signal: dict, event: str) -> str:
    asset = signal.get("asset", "N/D")
    timeframe = signal.get("timeframe", "N/D")
    expiry = signal.get("expires_at", "N/D")
    signal_id = str(signal.get("signal_id", "N/D"))[:12]
    signal_id_line = f"ID: <code>{signal_id}</code>\n"

    messages = {
        "SIGNAL_EXPIRED": (
            "⌛ <b>SEÑAL CADUCADA — NO OPERAR</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{asset}</b> | {timeframe}\n"
            f"{signal_id_line}"
            f"Caducidad: <code>{expiry}</code>\n"
            "Ha terminado la ventana de entrada de esta señal. "
            "No reutilices la alerta anterior."
        ),
        "SIGNAL_INVALIDATED": (
            "⚠️ <b>SEÑAL INVALIDADA</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{asset}</b> | {timeframe}\n"
            f"{signal_id_line}"
            "Las condiciones técnicas que originaron la señal ya no se cumplen. "
            "No abrir una nueva operación basándose en esta alerta."
        ),
        "SIGNAL_INVALIDATED_STOP_LEVEL": (
            "🛑 <b>NIVEL DE INVALIDACIÓN ALCANZADO</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{asset}</b> | {timeframe}\n"
            f"{signal_id_line}"
            "El precio ha alcanzado el stop técnico de referencia. "
            "La hipótesis queda invalidada. Esto no confirma si abriste "
            "una operación ni si tu stop se ejecutó."
        ),
        "TARGET_LEVEL_REACHED": (
            "🎯 <b>OBJETIVO TÉCNICO ALCANZADO</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{asset}</b> | {timeframe}\n"
            f"{signal_id_line}"
            "El mercado ha alcanzado el objetivo de referencia. "
            "La señal deja de ser una nueva entrada. No podemos saber "
            "si entraste ni calcular el resultado de una posición."
        ),
    }
    return messages.get(event, "")


def _notify_lifecycle_event(store, signal: dict, event: str, alerts_enabled: bool):
    if not alerts_enabled or not signal.get("alert_sent"):
        return signal

    message = _lifecycle_message(signal, event)
    if not message:
        return signal

    if Bot.send_telegram_message(message):
        updated = dict(signal)
        updated["pending_notification_event"] = None
        updated["last_notification_event"] = event
        updated["last_notification_at"] = datetime.now(timezone.utc).isoformat()
        store.update(updated)
        return updated

    updated = dict(signal)
    updated["pending_notification_event"] = event
    store.update(updated)
    return updated


def run_scheduled_scan(timeframe: str) -> dict:
    if timeframe not in {"15m", "1d"}:
        raise ValueError("Temporalidad no admitida: use 15m o 1d")

    alerts_enabled = bool(SIGNAL_ALERTS_ENABLED and STRATEGY_VALIDATED)
    if SIGNAL_ALERTS_ENABLED and not STRATEGY_VALIDATED:
        logger.warning(
            "TELEGRAM ALERTS HELD | strategy has not passed the validation gate"
        )
    if alerts_enabled and not Bot.TELEGRAM_ENABLED:
        raise RuntimeError(
            "SIGNAL_ALERTS_ENABLED está activo, pero faltan TELEGRAM_TOKEN/TELEGRAM_CHAT_ID"
        )

    state_url = os.getenv("UPSTASH_REDIS_REST_URL", "")
    state_token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
    store = UpstashSignalStateStore(state_url, state_token)

    now = datetime.now(timezone.utc)
    store.prune(now=now, retention_days=90, max_terminal_records=10000)
    scan_result = Bot.run_scan(timeframes=[timeframe])
    results = scan_result.get("results", [])
    by_asset = {
        item.get("asset"): item
        for item in results
        if isinstance(item, dict) and item.get("timeframe") == timeframe
    }

    state = store.load()
    transitions = 0
    retried_alerts = 0

    # Retry pending terminal-event notifications first. A failed Telegram
    # request must not make an already-detected lifecycle event disappear.
    for stored_signal in list(state["signals"].values()):
        if stored_signal.get("timeframe") != timeframe:
            continue
        pending_event = stored_signal.get("pending_notification_event")
        if pending_event and stored_signal.get("alert_sent") and alerts_enabled:
            _notify_lifecycle_event(
                store, stored_signal, pending_event, alerts_enabled
            )

    # Advance pending signals. Missing/stale market data never invalidates an
    # opportunity, but clock-based expiry is still evaluated on every scan.
    for stored_signal in list(state["signals"].values()):
        if (
            stored_signal.get("timeframe") != timeframe
            or stored_signal.get("status") != "PENDING"
        ):
            continue

        current = by_asset.get(stored_signal.get("asset"))
        data_is_valid = bool(current and current.get("data_valid") is True)

        updated, event = advance_signal(
            stored_signal,
            now,
            latest_price=current.get("last_price") if data_is_valid else None,
            conditions_valid=(
                current.get("action") == "BUY CANDIDATE"
                if data_is_valid
                else True
            ),
        )

        if event:
            updated["pending_notification_event"] = event
            store.update(updated)
            transitions += 1
            if updated.get("alert_sent"):
                _notify_lifecycle_event(store, updated, event, alerts_enabled)
            else:
                updated["pending_notification_event"] = None
                store.update(updated)
            continue

        # Retry an unsent initial alert only while the same opportunity remains
        # valid. A later scan never invents whether the user entered.
        if (
            alerts_enabled
            and data_is_valid
            and not updated.get("alert_sent")
            and current.get("action") == "BUY CANDIDATE"
        ):
            if _send_signal_report(scan_result, current, updated):
                updated["alert_sent"] = True
                updated["alert_sent_at"] = now.isoformat()
                store.update(updated)
                retried_alerts += 1

    # Create one stable signal per asset/timeframe/candle. Repeated scans of
    # the same completed candle cannot send duplicate initial alerts.
    created = 0
    sent = 0
    for candidate in results:
        if (
            candidate.get("action") != "BUY CANDIDATE"
            or candidate.get("data_valid") is not True
        ):
            continue

        expiry = signal_expiry_at(
            candidate["asset"],
            timeframe,
            generated_at=now,
            data_timestamp=candidate.get("data_timestamp"),
            validity_bars=2,
        )
        signal = build_signal_record(
            candidate,
            now,
            expires_at=expiry,
        )

        if not store.upsert(signal):
            continue

        created += 1
        if alerts_enabled and _send_signal_report(scan_result, candidate, signal):
            signal["alert_sent"] = True
            signal["alert_sent_at"] = now.isoformat()
            store.update(signal)
            sent += 1

    summary = {
        "timeframe": timeframe,
        "analyses_completed": scan_result.get("analyses_completed", 0),
        "technical_candidates_found": scan_result.get("technical_candidates_found", 0),
        "signals_created": created,
        "lifecycle_transitions": transitions,
        "alerts_sent": sent,
        "alerts_retried": retried_alerts,
        "alerts_enabled": alerts_enabled,
        "automatic_execution": False,
        "real_trading_authorized": False,
    }
    logger.info("SCHEDULED SCAN SUMMARY | %s", summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe", choices=("15m", "1d"), required=True)
    args = parser.parse_args()
    run_scheduled_scan(args.timeframe)


if __name__ == "__main__":
    main()
