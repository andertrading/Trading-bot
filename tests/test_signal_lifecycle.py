import json
from datetime import datetime, timedelta, timezone

import pytest

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


def test_signal_id_is_stable_for_same_candle():
    first = make_signal_id("nvda", "15M", "2026-11-21T11:45:00Z")
    second = make_signal_id("NVDA", "15m", "2026-11-21T12:45:00+01:00")
    assert first == second


def test_signal_id_changes_for_new_candle():
    first = make_signal_id("NVDA", "15m", "2026-11-21T11:45:00Z")
    second = make_signal_id("NVDA", "15m", "2026-11-21T12:00:00Z")
    assert first != second


def test_build_signal_calculates_potential_and_expiry():
    signal = build_signal_record(candidate(), NOW, 30)
    assert signal["status"] == "PENDING"
    assert signal["expires_at"] == (NOW + timedelta(minutes=30)).isoformat()
    assert signal["potential_risk_pct"] == 1.0
    assert signal["potential_reward_pct"] == 2.0
    assert signal["probability"] is None
    assert signal["position_status"] == "UNKNOWN"


@pytest.mark.parametrize("validity", [0, -1, 1.5, True])
def test_build_signal_rejects_invalid_validity(validity):
    with pytest.raises(ValueError):
        build_signal_record(candidate(), NOW, validity)


def test_build_signal_rejects_non_candidate():
    with pytest.raises(ValueError):
        build_signal_record(candidate(action="NO TRADE"), NOW, 30)


def test_build_signal_rejects_incoherent_long_levels():
    with pytest.raises(ValueError):
        build_signal_record(candidate(stop=181.0), NOW, 30)


def test_expiry_transition_happens_once():
    signal = build_signal_record(candidate(), NOW, 30)
    updated, event = advance_signal(signal, NOW + timedelta(minutes=30))
    assert updated["status"] == "EXPIRED"
    assert event == "SIGNAL_EXPIRED"
    again, second_event = advance_signal(updated, NOW + timedelta(minutes=31))
    assert again["status"] == "EXPIRED"
    assert second_event is None


def test_stop_level_invalidates_without_claiming_position_closed():
    signal = build_signal_record(candidate(), NOW, 30)
    updated, event = advance_signal(signal, NOW + timedelta(minutes=5), 178.0)
    assert updated["status"] == "INVALIDATED"
    assert event == "SIGNAL_INVALIDATED_STOP_LEVEL"
    assert updated["position_status"] == "UNKNOWN"


def test_target_before_entry_marks_missed_not_trade_won():
    signal = build_signal_record(candidate(), NOW, 30)
    updated, event = advance_signal(signal, NOW + timedelta(minutes=5), 184.0)
    assert updated["status"] == "MISSED_ENTRY"
    assert event == "TARGET_REACHED_BEFORE_ENTRY"
    assert updated["position_status"] == "UNKNOWN"


def test_invalid_conditions_invalidate_pending_signal():
    signal = build_signal_record(candidate(), NOW, 30)
    updated, event = advance_signal(signal, NOW + timedelta(minutes=5), conditions_valid=False)
    assert updated["status"] == "INVALIDATED"
    assert event == "SIGNAL_INVALIDATED"


def test_store_is_atomic_persistent_and_deduplicates(tmp_path):
    path = tmp_path / "state" / "signals.json"
    store = SignalStateStore(path)
    signal = build_signal_record(candidate(), NOW, 30)

    assert store.upsert(signal) is True
    assert store.upsert(signal) is False

    reloaded = SignalStateStore(path).load()
    assert reloaded["signals"][signal["signal_id"]]["status"] == "PENDING"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_store_update_requires_existing_signal(tmp_path):
    store = SignalStateStore(tmp_path / "signals.json")
    signal = build_signal_record(candidate(), NOW, 30)
    with pytest.raises(KeyError):
        store.update(signal)
