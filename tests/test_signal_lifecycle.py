import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from signal_lifecycle import (
    SignalStateStore,
    advance_signal,
    build_signal_record,
    make_signal_id,
)


NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def candidate(**overrides):
    result = {
        "action": "BUY CANDIDATE",
        "asset": "NVDA",
        "timeframe": "15m",
        "data_timestamp": "2026-11-21T11:45:00+00:00",
        "entry_reference": 180.0,
        "stop": 178.2,
        "target": 183.6,
        "score": 85,
        "reasons": ["EMA20 favorable", "MACD bullish"],
    }
    result.update(overrides)
    return result


class SignalLifecycleTests(unittest.TestCase):
    def test_signal_id_is_stable_for_same_candle(self):
        first = make_signal_id("nvda", "15M", "2026-11-21T11:45:00Z")
        second = make_signal_id("NVDA", "15m", "2026-11-21T12:45:00+01:00")
        self.assertEqual(first, second)

    def test_signal_id_changes_for_new_candle(self):
        first = make_signal_id("NVDA", "15m", "2026-11-21T11:45:00Z")
        second = make_signal_id("NVDA", "15m", "2026-11-21T12:00:00Z")
        self.assertNotEqual(first, second)

    def test_build_signal_calculates_potential_and_expiry(self):
        signal = build_signal_record(candidate(), NOW, 30)
        self.assertEqual(signal["status"], "PENDING")
        self.assertEqual(signal["expires_at"], (NOW + timedelta(minutes=30)).isoformat())
        self.assertEqual(signal["potential_risk_pct"], 1.0)
        self.assertEqual(signal["potential_reward_pct"], 2.0)
        self.assertIsNone(signal["probability"])
        self.assertEqual(signal["position_status"], "UNKNOWN")

    def test_build_signal_rejects_invalid_validity(self):
        for validity in (0, -1, 1.5, True):
            with self.subTest(validity=validity):
                with self.assertRaises(ValueError):
                    build_signal_record(candidate(), NOW, validity)

    def test_build_signal_rejects_non_candidate(self):
        with self.assertRaises(ValueError):
            build_signal_record(candidate(action="NO TRADE"), NOW, 30)

    def test_build_signal_rejects_incoherent_long_levels(self):
        with self.assertRaises(ValueError):
            build_signal_record(candidate(stop=181.0), NOW, 30)

    def test_expiry_transition_happens_once(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(signal, NOW + timedelta(minutes=30))
        self.assertEqual(updated["status"], "EXPIRED")
        self.assertEqual(event, "SIGNAL_EXPIRED")
        again, second_event = advance_signal(updated, NOW + timedelta(minutes=31))
        self.assertEqual(again["status"], "EXPIRED")
        self.assertIsNone(second_event)

    def test_stop_level_invalidates_without_claiming_position_closed(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(signal, NOW + timedelta(minutes=5), 178.0)
        self.assertEqual(updated["status"], "INVALIDATED")
        self.assertEqual(event, "SIGNAL_INVALIDATED_STOP_LEVEL")
        self.assertEqual(updated["position_status"], "UNKNOWN")

    def test_target_before_entry_marks_missed_not_trade_won(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(signal, NOW + timedelta(minutes=5), 184.0)
        self.assertEqual(updated["status"], "MISSED_ENTRY")
        self.assertEqual(event, "TARGET_REACHED_BEFORE_ENTRY")
        self.assertEqual(updated["position_status"], "UNKNOWN")

    def test_invalid_conditions_invalidate_pending_signal(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(
            signal, NOW + timedelta(minutes=5), conditions_valid=False
        )
        self.assertEqual(updated["status"], "INVALIDATED")
        self.assertEqual(event, "SIGNAL_INVALIDATED")

    def test_store_is_atomic_persistent_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "signals.json"
            store = SignalStateStore(path)
            signal = build_signal_record(candidate(), NOW, 30)

            self.assertTrue(store.upsert(signal))
            self.assertFalse(store.upsert(signal))

            reloaded = SignalStateStore(path).load()
            self.assertEqual(
                reloaded["signals"][signal["signal_id"]]["status"], "PENDING"
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1
            )

    def test_store_update_requires_existing_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignalStateStore(Path(directory) / "signals.json")
            signal = build_signal_record(candidate(), NOW, 30)
            with self.assertRaises(KeyError):
                store.update(signal)


if __name__ == "__main__":
    unittest.main()
