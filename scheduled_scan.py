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
from paper_forward import (
    new_paper_state,
    paper_summary,
    paper_portfolio_snapshot,
    process_signal_bar,
)


logger = logging.getLogger("scheduled_scan")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def _send_signal_report(scan_result: dict, candidate: dict, signal: dict) -> bool:
    """Send production alerts only for individually admitted daily REAL candidates.

    The global strategy switch is not an override for per-candidate validation.
    M15 remains a research/lab timeframe and cannot emit operational alerts.
    """
    if (
        not Bot.is_operational_alert_candidate(candidate)
        or STRATEGY_VALIDATED is not True
    ):
        logger.warning(
            "OPERATIONAL ALERT BLOCKED | %s | %s | candidate admission incomplete",
            candidate.get("asset") if isinstance(candidate, dict) else "UNKNOWN",
            candidate.get("timeframe") if isinstance(candidate, dict) else "UNKNOWN",
        )
        return False

    report_candidate = dict(candidate)
    report_candidate["expires_at"] = signal["expires_at"]
    report_candidate["generated_at"] = signal.get("generated_at")
    report_candidate["signal_id"] = signal["signal_id"]
    report_candidate["strategy_validated"] = STRATEGY_VALIDATED

    report_scan = dict(scan_result)
    report_scan.update({
        "technical_candidates_found": 1,
        "candidates_blocked": 0,
        "validated_candidates": 1,
        "no_trade_count": 0,
        "results": [report_candidate],
    })

    messages = Bot.build_telegram_messages(report_scan)
    if len(messages) < 2:
        logger.error("No se pudo construir el informe Telegram para %s", signal["signal_id"])
        return False

    # Telegram photo captions are limited to 1024 characters. Send the full
    # report as text first so expiry, entry window and risk warnings cannot be
    # silently truncated by the photo-caption limit.
    if not Bot.send_telegram_message(messages[1]):
        logger.error(
            "No se pudo enviar el informe completo para %s",
            signal["signal_id"],
        )
        return False

    try:
        chart = Bot.generate_candidate_chart(report_candidate)
    except Exception:
        logger.exception(
            "No se pudo generar el gráfico para %s; el informe completo ya se envió",
            signal["signal_id"],
        )
        return True

    if chart is not None:
        caption = (
            "📈 <b>Gráfico técnico</b> · "
            f"<b>{Bot.escape_html(candidate.get('asset', 'N/D'))}</b> · "
            f"{Bot.escape_html(candidate.get('timeframe', 'N/D'))}\n"
            "El informe completo se ha enviado en el mensaje anterior."
        )
        try:
            if not Bot.send_telegram_photo(chart, caption):
                logger.warning(
                    "No se pudo enviar el gráfico para %s; el informe completo ya se envió",
                    signal["signal_id"],
                )
        except Exception:
            logger.exception(
                "Falló el envío del gráfico para %s; el informe completo ya se envió",
                signal["signal_id"],
            )

    return True


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
            "No abras una nueva operación con esta alerta. "
            "Esto no es una instrucción para cerrar una posición ya abierta; "
            "el bot no puede saber si ejecutaste manualmente."
        ),
        "SIGNAL_ENTRY_ZONE_INVALIDATED": (
            "⚠️ <b>PRECIO FUERA DE LA ZONA DE ENTRADA</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{asset}</b> | {timeframe}\n"
            f"{signal_id_line}"
            "El precio ya no conserva el riesgo/recompensa mínimo ni la "
            "distancia de seguridad prevista hasta el stop. No abrir una "
            "nueva entrada. Esto no ordena cerrar una posición existente."
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


def _pending_notification_events(signal: dict) -> list[str]:
    """Return queued lifecycle events, including the legacy single-event field."""
    queued = signal.get("pending_notification_events")
    events = list(queued) if isinstance(queued, list) else []
    legacy = signal.get("pending_notification_event")
    if legacy and legacy not in events:
        events.insert(0, legacy)
    return events


def _set_pending_notification_events(signal: dict, events: list[str]) -> dict:
    updated = dict(signal)
    # Keep the first item in the legacy field for old readers and pruning logic.
    updated["pending_notification_events"] = list(events)
    updated["pending_notification_event"] = events[0] if events else None
    return updated


def _notify_lifecycle_event(store, signal: dict, event: str, alerts_enabled: bool):
    if not alerts_enabled or not signal.get("alert_sent"):
        return signal

    message = _lifecycle_message(signal, event)
    if not message:
        return signal

    pending = _pending_notification_events(signal)
    if Bot.send_telegram_message(message):
        if event in pending:
            pending.remove(event)
        updated = _set_pending_notification_events(signal, pending)
        updated["last_notification_event"] = event
        updated["last_notification_at"] = datetime.now(timezone.utc).isoformat()
        store.update(updated)
        return updated

    if event not in pending:
        pending.append(event)
    updated = _set_pending_notification_events(signal, pending)
    store.update(updated)
    return updated


def _retry_pending_notifications(store, state: dict, timeframe: str, alerts_enabled: bool):
    """Retry pending Telegram events and refresh the in-memory snapshot.

    The scan loop below consumes this same state snapshot. Keeping it in sync
    with successful retries prevents an old pending event from being written
    back to storage when the signal is updated later in the same scan.
    """
    for stored_signal in list(state["signals"].values()):
        if stored_signal.get("timeframe") != timeframe:
            continue
        pending_events = _pending_notification_events(stored_signal)
        if pending_events and stored_signal.get("alert_sent") and alerts_enabled:
            updated = stored_signal
            # Preserve event order. If Telegram is unavailable, leave this and
            # all later events queued for the next scheduled scan.
            for pending_event in pending_events:
                previous_count = len(_pending_notification_events(updated))
                updated = _notify_lifecycle_event(
                    store, updated, pending_event, alerts_enabled
                )
                remaining = _pending_notification_events(updated)
                if pending_event in remaining or len(remaining) >= previous_count:
                    break
            state["signals"][stored_signal["signal_id"]] = updated
    return state


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
    store.prune(now=now, retention_days=180, max_terminal_records=10000)
    state = store.load()
    paper_state = state.get("paper_forward")
    verified_paper_state = state.get("paper_forward_verified")
    paper_assets = {
        str(signal.get("asset", "")).upper()
        for signal in state.get("signals", {}).values()
        if signal.get("timeframe") == timeframe
        and signal.get("monitor_status", "ACTIVE") == "ACTIVE"
    }
    for ledger in (paper_state, verified_paper_state):
        if isinstance(ledger, dict):
            paper_assets.update(
                str(trade.get("asset", "")).upper()
                for trade in ledger.get("trades", {}).values()
                if trade.get("status") == "OPEN" and trade.get("asset")
            )
    paper_assets.discard("")
    if paper_state is None:
        paper_state = new_paper_state()
    else:
        # Never silently reset a malformed persisted paper ledger.
        paper_portfolio_snapshot(
            paper_state,
            expected_initial_capital_usd=Bot.INITIAL_CAPITAL_USD,
        )
    if verified_paper_state is None:
        verified_paper_state = new_paper_state()
    else:
        paper_portfolio_snapshot(
            verified_paper_state,
            expected_initial_capital_usd=Bot.INITIAL_CAPITAL_USD,
        )

    scan_result = Bot.run_scan(
        timeframes=[timeframe],
        include_paper_bars=True,
        paper_assets=sorted(paper_assets),
        # Operational candidate risk/cash checks must use the verified ledger;
        # gross-only history remains a separate diagnostic stream.
        paper_portfolio_state=verified_paper_state,
    )
    results = scan_result.get("results", [])
    by_asset = {
        item.get("asset"): item
        for item in results
        if isinstance(item, dict) and item.get("timeframe") == timeframe
    }

    paper_bars_processed = 0
    verified_paper_bars_processed = 0

    # Process the latest completed OHLC bar for each existing signal before
    # lifecycle monitoring can invalidate it. The signal's own creation candle
    # is ignored by the paper engine; only later bars can produce a fill.
    for stored_signal in list(state["signals"].values()):
        if stored_signal.get("timeframe") != timeframe:
            continue
        current = by_asset.get(stored_signal.get("asset"))
        if not current or current.get("data_valid") is not True:
            continue
        required_bar_fields = (
            "data_timestamp", "last_open", "last_high", "last_low", "last_price"
        )
        if not all(current.get(field) is not None for field in required_bar_fields):
            continue
        paper_risk = stored_signal.get("provisional_risk_pct")
        try:
            paper_risk = float(paper_risk) if paper_risk is not None else 0.0
        except (TypeError, ValueError, OverflowError):
            paper_risk = 0.0

        bars = current.get("paper_bars")
        if not isinstance(bars, list) or not bars:
            # Compatibility for older scan adapters and unit-test fixtures.
            bars = [{
                "timestamp": current["data_timestamp"],
                "open": current["last_open"],
                "high": current["last_high"],
                "low": current["last_low"],
                "close": current["last_price"],
            }]

        for completed_bar in bars:
            previous_bar = paper_state.get("processed_bars", {}).get(
                stored_signal.get("signal_id")
            )
            process_signal_bar(
                paper_state,
                stored_signal,
                completed_bar,
                risk_fraction=paper_risk,
            )
            if paper_state.get("processed_bars", {}).get(
                stored_signal.get("signal_id")
            ) != previous_bar:
                paper_bars_processed += 1

            previous_verified_bar = verified_paper_state.get("processed_bars", {}).get(
                stored_signal.get("signal_id")
            )
            process_signal_bar(
                verified_paper_state,
                stored_signal,
                completed_bar,
                risk_fraction=paper_risk,
                require_verified_costs=True,
            )
            if verified_paper_state.get("processed_bars", {}).get(
                stored_signal.get("signal_id")
            ) != previous_verified_bar:
                verified_paper_bars_processed += 1

    store.update_paper_state(paper_state)
    store.update_paper_state(
        verified_paper_state,
        ledger_key="paper_forward_verified",
    )
    transitions = 0
    retried_alerts = 0

    # Retry pending events first and refresh the in-memory snapshot so a
    # successful retry cannot be overwritten by stale state later this scan.
    pending_lifecycle_before = sum(
        len(_pending_notification_events(signal))
        for signal in state["signals"].values()
        if signal.get("timeframe") == timeframe and signal.get("alert_sent")
    )
    state = _retry_pending_notifications(store, state, timeframe, alerts_enabled)
    pending_lifecycle_after = sum(
        len(_pending_notification_events(signal))
        for signal in state["signals"].values()
        if signal.get("timeframe") == timeframe and signal.get("alert_sent")
    )
    lifecycle_notifications_retried = max(
        0, pending_lifecycle_before - pending_lifecycle_after
    )

    # Continue monitoring price levels even after the entry window expires
    # or technical conditions invalidate the setup. The bot cannot know
    # whether the user entered manually.
    for stored_signal in list(state["signals"].values()):
        if (
            stored_signal.get("timeframe") != timeframe
            or stored_signal.get("monitor_status", "ACTIVE") != "ACTIVE"
            or stored_signal.get("status") not in {"PENDING", "EXPIRED", "INVALIDATED"}
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
                if data_is_valid and stored_signal.get("status") == "PENDING"
                else True
            ),
            latest_high=current.get("last_high") if data_is_valid else None,
            latest_low=current.get("last_low") if data_is_valid else None,
            latest_data_timestamp=(
                current.get("data_timestamp") if data_is_valid else None
            ),
        )

        if event:
            pending_events = _pending_notification_events(updated)
            if event not in pending_events:
                pending_events.append(event)
            updated = _set_pending_notification_events(updated, pending_events)
            store.update(updated)
            transitions += 1
            if updated.get("alert_sent"):
                # Do not send a newer event ahead of an older queued event.
                if alerts_enabled and pending_events and pending_events[0] == event:
                    updated = _notify_lifecycle_event(
                        store, updated, event, alerts_enabled
                    )
            else:
                updated = _set_pending_notification_events(updated, [])
                store.update(updated)
            continue

        if updated != stored_signal:
            store.update(updated)

        # Retry an unsent initial alert only while the entry window is open
        # and the same opportunity remains valid.
        if (
            updated.get("status") == "PENDING"
            and alerts_enabled
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
    stale_candidates_skipped = 0
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
        if expiry <= now:
            stale_candidates_skipped += 1
            logger.warning(
                "STALE CANDIDATE NOT SENT | %s | %s | expiry=%s",
                candidate.get("asset"),
                timeframe,
                expiry.isoformat(),
            )
            continue

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
        "lifecycle_notifications_retried": lifecycle_notifications_retried,
        "stale_candidates_skipped": stale_candidates_skipped,
        "paper_bars_processed": paper_bars_processed,
        "verified_paper_bars_processed": verified_paper_bars_processed,
        "paper_forward": paper_summary(paper_state),
        "paper_forward_verified": paper_summary(verified_paper_state),
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
