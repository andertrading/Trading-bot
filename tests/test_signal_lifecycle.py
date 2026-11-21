import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from signal_lifecycle import (
    SignalStateStore,
    UpstashSignalStateStore,
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


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


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
        self.assertEqual(signal["monitor_status"], "ACTIVE")
        self.assertEqual(
            signal["monitoring_expires_at"],
            (NOW + timedelta(minutes=30) + timedelta(hours=8)).isoformat(),
        )
        self.assertEqual(signal["minimum_risk_reward"], 2.0)
        self.assertEqual(signal["minimum_remaining_risk_fraction"], 0.5)
        self.assertEqual(signal["expires_at"], (NOW + timedelta(minutes=30)).isoformat())
        self.assertEqual(signal["potential_risk_pct"], 1.0)
        self.assertEqual(signal["potential_reward_pct"], 2.0)
        self.assertIsNone(signal["probability"])
        self.assertEqual(signal["position_status"], "UNKNOWN")

    def test_build_signal_accepts_calendar_aware_expiry(self):
        expiry = NOW + timedelta(days=4, hours=2)
        signal = build_signal_record(candidate(), NOW, expires_at=expiry)
        self.assertEqual(signal["expires_at"], expiry.isoformat())
        self.assertEqual(signal["validity_minutes"], 5880)

    def test_build_signal_rejects_invalid_validity(self):
        for validity in (0, -1, 1.5, True):
            with self.subTest(validity=validity):
                with self.assertRaises(ValueError):
                    build_signal_record(candidate(), NOW, validity)

    def test_build_signal_requires_exactly_one_expiry_policy(self):
        with self.assertRaises(ValueError):
            build_signal_record(candidate(), NOW)
        with self.assertRaises(ValueError):
            build_signal_record(candidate(), NOW, 30, NOW + timedelta(hours=1))

    def test_build_signal_rejects_invalid_entry_zone_policy(self):
        with self.assertRaises(ValueError):
            build_signal_record(candidate(minimum_risk_reward=0), NOW, 30)
        with self.assertRaises(ValueError):
            build_signal_record(
                candidate(minimum_remaining_risk_fraction=1.5), NOW, 30
            )

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

    def test_expired_signal_continues_monitoring_until_a_price_level_is_touched(self):
        signal = build_signal_record(candidate(), NOW, 30)
        expired, event = advance_signal(signal, NOW + timedelta(minutes=30))
        self.assertEqual(event, "SIGNAL_EXPIRED")
        self.assertEqual(expired["monitor_status"], "ACTIVE")

        updated, event = advance_signal(
            expired,
            NOW + timedelta(minutes=35),
            latest_price=179.0,
            latest_high=179.8,
            latest_low=178.0,
        )
        self.assertEqual(event, "SIGNAL_INVALIDATED_STOP_LEVEL")
        self.assertEqual(updated["status"], "EXPIRED")
        self.assertEqual(updated["monitor_status"], "STOP_TOUCHED")

    def test_signal_candle_does_not_trigger_stop_or_target_before_alert(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(
            signal,
            NOW + timedelta(minutes=5),
            latest_price=180.0,
            latest_high=184.0,
            latest_low=178.0,
            latest_data_timestamp=signal["data_timestamp"],
        )
        self.assertIsNone(event)
        self.assertEqual(updated["status"], "PENDING")

    def test_later_completed_candle_can_trigger_intrabar_stop(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(
            signal,
            NOW + timedelta(minutes=20),
            latest_price=179.5,
            latest_high=179.8,
            latest_low=178.0,
            latest_data_timestamp=NOW + timedelta(minutes=15),
        )
        self.assertEqual(event, "SIGNAL_INVALIDATED_STOP_LEVEL")
        self.assertEqual(updated["monitor_status"], "STOP_TOUCHED")

    def test_out_of_order_candle_cannot_invalidate_signal(self):
        signal = build_signal_record(candidate(), NOW, 30)
        newer, event = advance_signal(
            signal,
            NOW + timedelta(minutes=20),
            latest_price=179.5,
            latest_high=179.8,
            latest_low=179.2,
            latest_data_timestamp=NOW + timedelta(minutes=15),
        )
        self.assertIsNone(event)
        self.assertEqual(
            newer["last_market_data_timestamp"],
            (NOW + timedelta(minutes=15)).isoformat(),
        )

        stale, stale_event = advance_signal(
            newer,
            NOW + timedelta(minutes=25),
            latest_price=178.0,
            latest_high=179.0,
            latest_low=177.5,
            latest_data_timestamp=NOW + timedelta(minutes=10),
        )
        self.assertIsNone(stale_event)
        self.assertEqual(stale["status"], "PENDING")
        self.assertEqual(stale["monitor_status"], "ACTIVE")
        self.assertEqual(
            stale["last_market_data_timestamp"],
            (NOW + timedelta(minutes=15)).isoformat(),
        )

    def test_duplicate_candle_cannot_trigger_a_second_market_event(self):
        signal = build_signal_record(candidate(), NOW, 30)
        newer, event = advance_signal(
            signal,
            NOW + timedelta(minutes=20),
            latest_price=179.5,
            latest_high=179.8,
            latest_low=179.2,
            latest_data_timestamp=NOW + timedelta(minutes=15),
        )
        self.assertIsNone(event)

        duplicate, duplicate_event = advance_signal(
            newer,
            NOW + timedelta(minutes=21),
            latest_price=178.0,
            latest_high=179.0,
            latest_low=177.5,
            latest_data_timestamp=NOW + timedelta(minutes=15),
        )
        self.assertIsNone(duplicate_event)
        self.assertEqual(duplicate["status"], "PENDING")
        self.assertEqual(duplicate["monitor_status"], "ACTIVE")

    def test_intrabar_stop_is_detected_even_when_close_is_above_stop(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(
            signal,
            NOW + timedelta(minutes=5),
            latest_price=179.5,
            latest_high=179.8,
            latest_low=178.0,
        )
        self.assertEqual(event, "SIGNAL_INVALIDATED_STOP_LEVEL")
        self.assertEqual(updated["monitor_status"], "STOP_TOUCHED")

    def test_intrabar_target_is_detected_even_when_close_is_below_target(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(
            signal,
            NOW + timedelta(minutes=5),
            latest_price=182.0,
            latest_high=184.0,
            latest_low=181.5,
        )
        self.assertEqual(event, "TARGET_LEVEL_REACHED")
        self.assertEqual(updated["monitor_status"], "TARGET_TOUCHED")

    def test_stop_wins_if_stop_and_target_are_touched_in_same_candle(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(
            signal,
            NOW + timedelta(minutes=5),
            latest_price=180.0,
            latest_high=184.0,
            latest_low=178.0,
        )
        self.assertEqual(event, "SIGNAL_INVALIDATED_STOP_LEVEL")
        self.assertEqual(updated["monitor_status"], "STOP_TOUCHED")

    def test_entry_zone_invalidates_if_remaining_risk_is_too_small(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(
            signal,
            NOW + timedelta(minutes=5),
            latest_price=179.0,
        )
        self.assertEqual(event, "SIGNAL_ENTRY_ZONE_INVALIDATED")
        self.assertEqual(updated["status"], "INVALIDATED")

    def test_entry_zone_invalidates_if_current_rr_falls_below_two(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(
            signal,
            NOW + timedelta(minutes=5),
            latest_price=180.5,
        )
        self.assertEqual(event, "SIGNAL_ENTRY_ZONE_INVALIDATED")
        self.assertEqual(updated["status"], "INVALIDATED")

    def test_entry_zone_remains_valid_inside_price_band(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(
            signal,
            NOW + timedelta(minutes=5),
            latest_price=179.5,
        )
        self.assertIsNone(event)
        self.assertEqual(updated["status"], "PENDING")

    def test_monitoring_ends_after_monitoring_expiry(self):
        signal = build_signal_record(candidate(), NOW, 30)
        end = datetime.fromisoformat(signal["monitoring_expires_at"])
        updated, event = advance_signal(signal, end)
        self.assertEqual(event, "SIGNAL_EXPIRED")
        self.assertEqual(updated["status"], "EXPIRED")
        self.assertEqual(updated["monitor_status"], "ENDED")

    def test_stop_level_invalidates_without_claiming_position_closed(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(signal, NOW + timedelta(minutes=5), 178.0)
        self.assertEqual(updated["status"], "INVALIDATED")
        self.assertEqual(event, "SIGNAL_INVALIDATED_STOP_LEVEL")
        self.assertEqual(updated["position_status"], "UNKNOWN")

    def test_target_level_event_does_not_infer_whether_user_entered(self):
        signal = build_signal_record(candidate(), NOW, 30)
        updated, event = advance_signal(signal, NOW + timedelta(minutes=5), 184.0)
        self.assertEqual(updated["status"], "TARGET_REACHED")
        self.assertEqual(event, "TARGET_LEVEL_REACHED")
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

    def test_upstash_store_persists_and_deduplicates(self):
        remote = {}

        def fake_get(url, **kwargs):
            return FakeResponse({"result": remote.get("state")})

        def fake_post(url, data=None, **kwargs):
            remote["state"] = data.decode("utf-8")
            return FakeResponse({"result": "OK"})

        with patch("signal_lifecycle.requests.get", side_effect=fake_get), patch(
            "signal_lifecycle.requests.post", side_effect=fake_post
        ):
            store = UpstashSignalStateStore(
                "https://example.upstash.io", "test-token"
            )
            signal = build_signal_record(candidate(), NOW, 30)
            self.assertTrue(store.upsert(signal))
            self.assertFalse(store.upsert(signal))
            loaded = store.load()
            self.assertEqual(
                loaded["signals"][signal["signal_id"]]["status"], "PENDING"
            )


    def test_upstash_prune_removes_old_terminal_but_keeps_pending(self):
        remote = {
            "state": json.dumps({
                "schema_version": 1,
                "signals": {
                    "old": {
                        "signal_id": "old",
                        "status": "EXPIRED",
                        "generated_at": "2026-01-01T00:00:00Z",
                        "last_checked_at": "2026-01-02T00:00:00Z",
                    },
                    "recent": {
                        "signal_id": "recent",
                        "status": "INVALIDATED",
                        "generated_at": "2026-11-20T00:00:00Z",
                        "last_checked_at": "2026-11-20T00:00:00Z",
                    },
                    "pending": {
                        "signal_id": "pending",
                        "status": "PENDING",
                        "generated_at": "2026-01-01T00:00:00Z",
                    },
                },
            })
        }

        def fake_get(url, **kwargs):
            return FakeResponse({"result": remote["state"]})

        def fake_post(url, data=None, **kwargs):
            remote["state"] = data.decode("utf-8")
            return FakeResponse({"result": "OK"})

        with patch("signal_lifecycle.requests.get", side_effect=fake_get), patch(
            "signal_lifecycle.requests.post", side_effect=fake_post
        ):
            store = UpstashSignalStateStore(
                "https://example.upstash.io", "test-token"
            )
            removed = store.prune(now=NOW, retention_days=90)
            loaded = store.load()

        self.assertEqual(removed, 1)
        self.assertNotIn("old", loaded["signals"])
        self.assertIn("recent", loaded["signals"])
        self.assertIn("pending", loaded["signals"])

    def test_upstash_store_requires_credentials(self):
        with self.assertRaises(ValueError):
            UpstashSignalStateStore("", "")


if __name__ == "__main__":
    unittest.main()
