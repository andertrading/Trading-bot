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

    def test_invalid_timeframe_is_rejected_before_network_access(self):
        with self.assertRaises(ValueError):
            scheduled_scan.run_scheduled_scan("5m")


if __name__ == "__main__":
    unittest.main()
