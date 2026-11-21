import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import scheduled_scan


class MemoryStore:
    instance = None

    def __init__(self, url, token):
        self.state = {"schema_version": 1, "signals": {}}
        MemoryStore.instance = self

    def load(self):
        return copy.deepcopy(self.state)

    def upsert(self, signal):
        signal_id = signal["signal_id"]
        if signal_id in self.state["signals"]:
            return False
        self.state["signals"][signal_id] = copy.deepcopy(signal)
        return True

    def update(self, signal):
        self.state["signals"][signal["signal_id"]] = copy.deepcopy(signal)

    def prune(self, **kwargs):
        return 0


def make_candidate(now):
    return {
        "action": "BUY CANDIDATE",
        "asset": "BTC",
        "timeframe": "15m",
        "validation_status": "BLOCKED",
        "entry_reference": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "risk_reward": 2.0,
        "score": 85,
        "reasons": ["Trend and momentum aligned"],
        "validation_reasons": ["Execution validation pending"],
        "provisional_risk_pct": 0.0025,
        "data_timestamp": (now - timedelta(minutes=15)).isoformat(),
        "data_age_minutes": 3.0,
        "data_valid": True,
        "last_price": 100.0,
        "last_high": 100.5,
        "last_low": 99.5,
    }


class ScheduledScanTests(unittest.TestCase):
    def setUp(self):
        MemoryStore.instance = None
        self.now = datetime.now(timezone.utc)
        self.scan = {
            "timeframes": ["15m"],
            "analyses_completed": 1,
            "technical_candidates_found": 1,
            "candidates_blocked": 1,
            "validated_candidates": 0,
            "no_trade_count": 0,
            "results": [make_candidate(self.now)],
        }

    def test_new_candidate_is_persisted_without_sending_when_alerts_disabled(self):
        with patch.object(scheduled_scan, "UpstashSignalStateStore", MemoryStore), patch.object(
            scheduled_scan.Bot, "run_scan", return_value=self.scan
        ), patch.object(scheduled_scan, "SIGNAL_ALERTS_ENABLED", False), patch.object(
            scheduled_scan.Bot, "send_telegram_message"
        ) as send_message:
            result = scheduled_scan.run_scheduled_scan("15m")

        self.assertEqual(result["signals_created"], 1)
        self.assertEqual(result["alerts_sent"], 0)
        self.assertFalse(result["alerts_enabled"])
        self.assertEqual(len(MemoryStore.instance.state["signals"]), 1)
        signal = next(iter(MemoryStore.instance.state["signals"].values()))
        self.assertEqual(signal["status"], "PENDING")
        self.assertFalse(signal["alert_sent"])
        send_message.assert_not_called()

    def test_same_candle_does_not_create_duplicate_signal(self):
        store = MemoryStore("url", "token")
        with patch.object(
            scheduled_scan, "UpstashSignalStateStore", return_value=store
        ), patch.object(
            scheduled_scan.Bot, "run_scan", return_value=self.scan
        ), patch.object(scheduled_scan, "SIGNAL_ALERTS_ENABLED", False):
            first = scheduled_scan.run_scheduled_scan("15m")
            second = scheduled_scan.run_scheduled_scan("15m")

        self.assertEqual(first["signals_created"], 1)
        self.assertEqual(second["signals_created"], 0)
        self.assertEqual(len(store.state["signals"]), 1)

    def test_existing_signal_expires_even_when_market_data_is_stale(self):
        store = MemoryStore("url", "token")
        with patch.object(
            scheduled_scan, "UpstashSignalStateStore", return_value=store
        ):
            old = {
                "schema_version": 1,
                "signal_id": "expired-1",
                "asset": "BTC",
                "timeframe": "15m",
                "status": "PENDING",
                "generated_at": (self.now - timedelta(hours=1)).isoformat(),
                "expires_at": (self.now - timedelta(minutes=1)).isoformat(),
                "direction": "LONG",
                "stop": 99.0,
                "target": 102.0,
                "alert_sent": False,
                "position_status": "UNKNOWN",
            }
            store.upsert(old)
            stale_scan = {
                "timeframes": ["15m"],
                "analyses_completed": 1,
                "technical_candidates_found": 0,
                "candidates_blocked": 0,
                "validated_candidates": 0,
                "no_trade_count": 1,
                "results": [{
                    "action": "NO TRADE",
                    "asset": "BTC",
                    "timeframe": "15m",
                    "data_valid": False,
                }],
            }
            with patch.object(
                scheduled_scan.Bot, "run_scan", return_value=stale_scan
            ), patch.object(scheduled_scan, "SIGNAL_ALERTS_ENABLED", False):
                result = scheduled_scan.run_scheduled_scan("15m")

        updated = store.state["signals"]["expired-1"]
        self.assertEqual(updated["status"], "EXPIRED")
        self.assertEqual(result["lifecycle_transitions"], 1)


    def test_stale_candidate_is_not_created_or_sent(self):
        stale = copy.deepcopy(self.scan)
        stale["results"] = [make_candidate(self.now - timedelta(hours=2))]
        store = MemoryStore("url", "token")
        with patch.object(
            scheduled_scan, "UpstashSignalStateStore", return_value=store
        ), patch.object(
            scheduled_scan.Bot, "run_scan", return_value=stale
        ), patch.object(scheduled_scan, "SIGNAL_ALERTS_ENABLED", False):
            result = scheduled_scan.run_scheduled_scan("15m")

        self.assertEqual(result["signals_created"], 0)
        self.assertEqual(result["stale_candidates_skipped"], 1)
        self.assertEqual(store.state["signals"], {})

    def test_expired_signal_is_still_monitored_for_stop_touch(self):
        store = MemoryStore("url", "token")
        old = {
            "schema_version": 1,
            "signal_id": "expired-monitor-1",
            "asset": "BTC",
            "timeframe": "15m",
            "status": "EXPIRED",
            "monitor_status": "ACTIVE",
            "generated_at": (self.now - timedelta(hours=1)).isoformat(),
            "expires_at": (self.now - timedelta(minutes=30)).isoformat(),
            "monitoring_expires_at": (self.now + timedelta(hours=4)).isoformat(),
            "direction": "LONG",
            "entry_reference": 100.0,
            "stop": 99.0,
            "target": 102.0,
            "alert_sent": False,
            "position_status": "UNKNOWN",
        }
        store.upsert(old)
        current = {
            "action": "NO TRADE",
            "asset": "BTC",
            "timeframe": "15m",
            "data_valid": True,
            "last_price": 99.5,
            "last_high": 100.0,
            "last_low": 98.8,
            "data_timestamp": (self.now - timedelta(minutes=15)).isoformat(),
        }
        scan = {
            "timeframes": ["15m"],
            "analyses_completed": 1,
            "technical_candidates_found": 0,
            "candidates_blocked": 0,
            "validated_candidates": 0,
            "no_trade_count": 1,
            "results": [current],
        }
        with patch.object(
            scheduled_scan, "UpstashSignalStateStore", return_value=store
        ), patch.object(
            scheduled_scan.Bot, "run_scan", return_value=scan
        ), patch.object(scheduled_scan, "SIGNAL_ALERTS_ENABLED", False):
            result = scheduled_scan.run_scheduled_scan("15m")

        updated = store.state["signals"]["expired-monitor-1"]
        self.assertEqual(updated["status"], "EXPIRED")
        self.assertEqual(updated["monitor_status"], "STOP_TOUCHED")
        self.assertEqual(result["lifecycle_transitions"], 1)

    def test_alerts_remain_blocked_until_strategy_validation_passes(self):
        store = MemoryStore("url", "token")
        with patch.object(
            scheduled_scan, "UpstashSignalStateStore", return_value=store
        ), patch.object(
            scheduled_scan.Bot, "run_scan", return_value=self.scan
        ), patch.object(
            scheduled_scan, "SIGNAL_ALERTS_ENABLED", True
        ), patch.object(
            scheduled_scan, "STRATEGY_VALIDATED", False
        ), patch.object(
            scheduled_scan.Bot, "send_telegram_message"
        ) as send_message:
            result = scheduled_scan.run_scheduled_scan("15m")

        self.assertFalse(result["alerts_enabled"])
        send_message.assert_not_called()

    def test_alerts_send_only_when_strategy_and_telegram_are_enabled(self):
        store = MemoryStore("url", "token")
        with patch.object(
            scheduled_scan, "UpstashSignalStateStore", return_value=store
        ), patch.object(
            scheduled_scan.Bot, "run_scan", return_value=self.scan
        ), patch.object(
            scheduled_scan, "SIGNAL_ALERTS_ENABLED", True
        ), patch.object(
            scheduled_scan, "STRATEGY_VALIDATED", True
        ), patch.object(
            scheduled_scan.Bot, "TELEGRAM_ENABLED", True
        ), patch.object(
            scheduled_scan.Bot, "generate_candidate_chart", return_value=None
        ), patch.object(
            scheduled_scan.Bot, "send_telegram_message", return_value=True
        ) as send_message:
            result = scheduled_scan.run_scheduled_scan("15m")

        self.assertTrue(result["alerts_enabled"])
        self.assertEqual(result["alerts_sent"], 1)
        self.assertTrue(next(iter(store.state["signals"].values()))["alert_sent"])
        send_message.assert_called_once()


    def test_lifecycle_notifications_include_the_same_signal_id(self):
        signal = {
            "signal_id": "abcdef1234567890",
            "asset": "BTC",
            "timeframe": "15m",
            "expires_at": "2026-11-21T12:30:00+00:00",
        }
        message = scheduled_scan._lifecycle_message(signal, "SIGNAL_EXPIRED")
        self.assertIn("ID: <code>abcdef123456</code>", message)
        self.assertIn("No abras una nueva operación", message)
        self.assertIn("no es una instrucción para cerrar", message)

    @patch.object(scheduled_scan.Bot, "send_telegram_message", return_value=False)
    def test_failed_lifecycle_notification_is_persisted_for_retry(self, send):
        store = MemoryStore("url", "token")
        signal = {
            "signal_id": "retry-expiry",
            "asset": "BTC",
            "timeframe": "15m",
            "expires_at": "2026-11-21T12:30:00+00:00",
            "alert_sent": True,
            "pending_notification_event": None,
        }
        updated = scheduled_scan._notify_lifecycle_event(
            store, signal, "SIGNAL_EXPIRED", alerts_enabled=True
        )
        send.assert_called_once()
        self.assertEqual(updated["pending_notification_event"], "SIGNAL_EXPIRED")
        self.assertEqual(
            store.state["signals"]["retry-expiry"]["pending_notification_event"],
            "SIGNAL_EXPIRED",
        )

    @patch.object(scheduled_scan.Bot, "send_telegram_message", return_value=True)
    def test_successful_retry_refreshes_scan_snapshot(self, send):
        store = MemoryStore("url", "token")
        signal = {
            "signal_id": "snapshot-refresh",
            "asset": "BTC",
            "timeframe": "15m",
            "expires_at": "2026-11-21T12:30:00+00:00",
            "alert_sent": True,
            "pending_notification_event": "SIGNAL_EXPIRED",
        }
        store.upsert(signal)
        state = store.load()

        refreshed = scheduled_scan._retry_pending_notifications(
            store, state, "15m", alerts_enabled=True
        )

        send.assert_called_once()
        self.assertIsNone(
            refreshed["signals"]["snapshot-refresh"]["pending_notification_event"]
        )
        self.assertIsNone(
            store.state["signals"]["snapshot-refresh"]["pending_notification_event"]
        )

    @patch.object(scheduled_scan.Bot, "send_telegram_message", return_value=False)
    def test_failed_retry_keeps_pending_event_in_scan_snapshot(self, send):
        store = MemoryStore("url", "token")
        signal = {
            "signal_id": "snapshot-retry",
            "asset": "BTC",
            "timeframe": "15m",
            "expires_at": "2026-11-21T12:30:00+00:00",
            "alert_sent": True,
            "pending_notification_event": "SIGNAL_EXPIRED",
        }
        store.upsert(signal)
        state = store.load()

        refreshed = scheduled_scan._retry_pending_notifications(
            store, state, "15m", alerts_enabled=True
        )

        send.assert_called_once()
        self.assertEqual(
            refreshed["signals"]["snapshot-retry"]["pending_notification_event"],
            "SIGNAL_EXPIRED",
        )
        self.assertEqual(
            store.state["signals"]["snapshot-retry"]["pending_notification_event"],
            "SIGNAL_EXPIRED",
        )

    @patch.object(scheduled_scan.Bot, "send_telegram_message", return_value=True)
    def test_successful_lifecycle_retry_clears_pending_event(self, send):
        store = MemoryStore("url", "token")
        signal = {
            "signal_id": "retry-expiry",
            "asset": "BTC",
            "timeframe": "15m",
            "expires_at": "2026-11-21T12:30:00+00:00",
            "alert_sent": True,
            "pending_notification_event": "SIGNAL_EXPIRED",
        }
        updated = scheduled_scan._notify_lifecycle_event(
            store, signal, "SIGNAL_EXPIRED", alerts_enabled=True
        )
        send.assert_called_once()
        self.assertIsNone(updated["pending_notification_event"])
        self.assertEqual(updated["last_notification_event"], "SIGNAL_EXPIRED")
        self.assertTrue(updated.get("last_notification_at"))
        self.assertIsNone(
            store.state["signals"]["retry-expiry"]["pending_notification_event"]
        )

    @patch.object(scheduled_scan.Bot, "send_telegram_message")
    def test_lifecycle_notification_is_gated_by_initial_alert(self, send):
        store = MemoryStore("url", "token")
        signal = {
            "signal_id": "never-alerted",
            "asset": "BTC",
            "timeframe": "15m",
            "expires_at": "2026-11-21T12:30:00+00:00",
            "alert_sent": False,
            "pending_notification_event": None,
        }
        unchanged = scheduled_scan._notify_lifecycle_event(
            store, signal, "SIGNAL_EXPIRED", alerts_enabled=True
        )
        send.assert_not_called()
        self.assertEqual(unchanged, signal)
        self.assertEqual(store.state["signals"], {})

    @patch.object(scheduled_scan.Bot, "send_telegram_message", return_value=True)
    @patch.object(
        scheduled_scan.Bot, "generate_candidate_chart",
        side_effect=RuntimeError("chart renderer unavailable"),
    )
    @patch.object(
        scheduled_scan.Bot, "build_telegram_messages",
        return_value=["summary", "signal text"],
    )
    def test_chart_failure_falls_back_to_text_alert(
        self, build_messages, generate_chart, send_text
    ):
        result = scheduled_scan._send_signal_report(
            self.scan, self.scan["results"][0],
            {"expires_at": "2026-11-21T12:30:00+00:00", "signal_id": "chart-fallback"},
        )
        self.assertTrue(result)
        build_messages.assert_called_once()
        generate_chart.assert_called_once()
        send_text.assert_called_once_with("signal text")

    @patch.object(scheduled_scan.Bot, "send_telegram_message", return_value=True)
    @patch.object(scheduled_scan.Bot, "send_telegram_photo", return_value=False)
    @patch.object(scheduled_scan.Bot, "generate_candidate_chart", return_value=b"chart")
    @patch.object(
        scheduled_scan.Bot, "build_telegram_messages",
        return_value=["summary", "signal text"],
    )
    def test_photo_send_failure_falls_back_to_text(
        self, build_messages, generate_chart, send_photo, send_text
    ):
        result = scheduled_scan._send_signal_report(
            self.scan, self.scan["results"][0],
            {"expires_at": "2026-11-21T12:30:00+00:00", "signal_id": "photo-fallback"},
        )
        self.assertTrue(result)
        send_photo.assert_called_once()
        self.assertIn("Gráfico técnico", send_photo.call_args.args[1])
        self.assertIn("informe completo se ha enviado", send_photo.call_args.args[1])
        self.assertNotEqual(send_photo.call_args.args[1], "signal text")
        send_text.assert_called_once_with("signal text")

    @patch.object(scheduled_scan.Bot, "send_telegram_message", return_value=True)
    @patch.object(scheduled_scan.Bot, "send_telegram_photo", return_value=True)
    @patch.object(scheduled_scan.Bot, "generate_candidate_chart", return_value=b"chart")
    @patch.object(
        scheduled_scan.Bot, "build_telegram_messages",
        return_value=["summary", "X" * 1800],
    )
    def test_long_report_is_sent_in_full_and_photo_uses_short_caption(
        self, build_messages, generate_chart, send_photo, send_text
    ):
        """Ensure photo captions never replace or truncate the full text report."""
        result = scheduled_scan._send_signal_report(
            self.scan,
            self.scan["results"][0],
            {
                "generated_at": "2026-11-21T12:00:00+00:00",
                "expires_at": "2026-11-21T12:30:00+00:00",
                "signal_id": "long-report",
            },
        )

        self.assertTrue(result)
        send_text.assert_called_once_with("X" * 1800)
        send_photo.assert_called_once()
        caption = send_photo.call_args.args[1]
        self.assertLessEqual(len(caption), 1024)
        self.assertIn("informe completo se ha enviado", caption)
        self.assertNotEqual(caption, "X" * 1800)

    def test_invalid_timeframe_is_rejected_before_network_access(self):
        with self.assertRaises(ValueError):
            scheduled_scan.run_scheduled_scan("5m")


    @patch.object(scheduled_scan.Bot, "send_telegram_message", return_value=False)
    def test_new_lifecycle_event_does_not_overtake_an_older_failed_event(self, send):
        store = MemoryStore("url", "token")
        old = {
            "schema_version": 1,
            "signal_id": "ordered-signal",
            "asset": "BTC",
            "timeframe": "15m",
            "status": "PENDING",
            "monitor_status": "ACTIVE",
            "generated_at": (self.now - timedelta(minutes=30)).isoformat(),
            "expires_at": (self.now + timedelta(minutes=30)).isoformat(),
            "monitoring_expires_at": (self.now + timedelta(hours=4)).isoformat(),
            "direction": "LONG",
            "entry_reference": 100.0,
            "stop": 99.0,
            "target": 102.0,
            "alert_sent": True,
            "pending_notification_event": "SIGNAL_INVALIDATED",
            "pending_notification_events": ["SIGNAL_INVALIDATED"],
            "data_timestamp": (self.now - timedelta(minutes=30)).isoformat(),
        }
        store.upsert(old)
        current = {
            "action": "NO TRADE",
            "asset": "BTC",
            "timeframe": "15m",
            "data_valid": True,
            "last_price": 98.8,
            "last_high": 100.0,
            "last_low": 98.7,
            "data_timestamp": (self.now - timedelta(minutes=15)).isoformat(),
        }
        scan = {
            "timeframes": ["15m"],
            "analyses_completed": 1,
            "technical_candidates_found": 0,
            "candidates_blocked": 0,
            "validated_candidates": 0,
            "no_trade_count": 1,
            "results": [current],
        }

        with patch.object(
            scheduled_scan, "UpstashSignalStateStore", return_value=store
        ), patch.object(
            scheduled_scan.Bot, "run_scan", return_value=scan
        ), patch.object(
            scheduled_scan, "SIGNAL_ALERTS_ENABLED", True
        ), patch.object(
            scheduled_scan, "STRATEGY_VALIDATED", True
        ), patch.object(
            scheduled_scan.Bot, "TELEGRAM_ENABLED", True
        ):
            scheduled_scan.run_scheduled_scan("15m")

        updated = store.state["signals"]["ordered-signal"]
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            updated["pending_notification_events"],
            ["SIGNAL_INVALIDATED", "SIGNAL_INVALIDATED_STOP_LEVEL"],
        )
        self.assertEqual(
            updated["pending_notification_event"], "SIGNAL_INVALIDATED"
        )
        self.assertEqual(updated["monitor_status"], "STOP_TOUCHED")

    @patch.object(scheduled_scan.Bot, "send_telegram_message", side_effect=[False, False, True, True])
    def test_multiple_failed_lifecycle_events_are_queued_and_retried_in_order(self, send):
        """Previously failed lifecycle alerts must never be overwritten."""
        store = MemoryStore("url", "token")
        signal = {
            "signal_id": "queued-events",
            "asset": "BTC",
            "timeframe": "15m",
            "expires_at": "2026-11-21T12:30:00+00:00",
            "alert_sent": True,
            "pending_notification_event": None,
        }

        first = scheduled_scan._notify_lifecycle_event(
            store, signal, "SIGNAL_INVALIDATED", alerts_enabled=True
        )
        second = scheduled_scan._notify_lifecycle_event(
            store, first, "SIGNAL_INVALIDATED_STOP_LEVEL", alerts_enabled=True
        )

        self.assertEqual(
            second["pending_notification_events"],
            ["SIGNAL_INVALIDATED", "SIGNAL_INVALIDATED_STOP_LEVEL"],
        )
        self.assertEqual(
            second["pending_notification_event"], "SIGNAL_INVALIDATED"
        )

        state = store.load()
        refreshed = scheduled_scan._retry_pending_notifications(
            store, state, "15m", alerts_enabled=True
        )
        updated = refreshed["signals"]["queued-events"]

        self.assertEqual(send.call_count, 4)
        self.assertEqual(updated["pending_notification_events"], [])
        self.assertIsNone(updated["pending_notification_event"])
        self.assertEqual(
            updated["last_notification_event"], "SIGNAL_INVALIDATED_STOP_LEVEL"
        )

    @patch.object(scheduled_scan.Bot, "send_telegram_message", side_effect=[False, False, False])
    def test_retry_failure_keeps_entire_notification_queue_in_order(self, send):
        store = MemoryStore("url", "token")
        signal = {
            "signal_id": "queued-failure",
            "asset": "BTC",
            "timeframe": "15m",
            "expires_at": "2026-11-21T12:30:00+00:00",
            "alert_sent": True,
            "pending_notification_event": "SIGNAL_INVALIDATED",
            "pending_notification_events": [
                "SIGNAL_INVALIDATED",
                "SIGNAL_INVALIDATED_STOP_LEVEL",
            ],
        }
        store.upsert(signal)
        refreshed = scheduled_scan._retry_pending_notifications(
            store, store.load(), "15m", alerts_enabled=True
        )
        updated = refreshed["signals"]["queued-failure"]

        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            updated["pending_notification_events"],
            ["SIGNAL_INVALIDATED", "SIGNAL_INVALIDATED_STOP_LEVEL"],
        )
        self.assertEqual(
            store.state["signals"]["queued-failure"]["pending_notification_events"],
            ["SIGNAL_INVALIDATED", "SIGNAL_INVALIDATED_STOP_LEVEL"],
        )

    @patch.object(scheduled_scan.Bot, "send_telegram_message", return_value=True)
    def test_scan_summary_counts_successful_lifecycle_notification_retries(self, send):
        """Successful lifecycle retries must be visible in scan telemetry."""
        store = MemoryStore("url", "token")
        signal = {
            "signal_id": "retry-metric",
            "asset": "BTC",
            "timeframe": "15m",
            "status": "EXPIRED",
            "monitor_status": "ENDED",
            "expires_at": "2026-11-21T12:30:00+00:00",
            "alert_sent": True,
            "pending_notification_event": "SIGNAL_EXPIRED",
            "pending_notification_events": ["SIGNAL_EXPIRED"],
        }
        store.upsert(signal)
        scan = {
            "timeframes": ["15m"],
            "analyses_completed": 1,
            "technical_candidates_found": 0,
            "candidates_blocked": 0,
            "validated_candidates": 0,
            "no_trade_count": 1,
            "results": [{
                "action": "NO TRADE",
                "asset": "BTC",
                "timeframe": "15m",
                "data_valid": False,
            }],
        }

        with patch.object(
            scheduled_scan, "UpstashSignalStateStore", return_value=store
        ), patch.object(
            scheduled_scan.Bot, "run_scan", return_value=scan
        ), patch.object(
            scheduled_scan, "SIGNAL_ALERTS_ENABLED", True
        ), patch.object(
            scheduled_scan, "STRATEGY_VALIDATED", True
        ), patch.object(
            scheduled_scan.Bot, "TELEGRAM_ENABLED", True
        ):
            result = scheduled_scan.run_scheduled_scan("15m")

        self.assertEqual(result["lifecycle_notifications_retried"], 1)
        self.assertEqual(send.call_count, 1)
        self.assertIsNone(
            store.state["signals"]["retry-metric"]["pending_notification_event"]
        )


if __name__ == "__main__":
    unittest.main()
