import unittest

import numpy as np
import pandas as pd

import backtest_v3
import strategies
from research.resampled_crypto_history import (
    research_timeframe_enabled,
    resample_complete_coinbase_bars,
)


def make_15m(rows=32):
    index = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="15min")
    close = 100.0 + np.arange(rows, dtype=float)
    frame = pd.DataFrame({
        "Open": close - 0.25,
        "High": close + 0.5,
        "Low": close - 0.5,
        "Close": close,
        "Volume": np.full(rows, 10.0),
    }, index=index)
    frame.attrs["volume_quality"] = "available"
    frame.attrs["volume_column_present"] = True
    frame.attrs["volume_invalid_count_before_normalization"] = 0
    frame.attrs["volume_positive_count_before_normalization"] = rows
    frame.attrs["volume_zero_count_before_normalization"] = 0
    return frame


class TestResampledCryptoHistory(unittest.TestCase):
    def test_one_hour_aggregation_uses_complete_groups_and_correct_ohlcv(self):
        result, metadata = resample_complete_coinbase_bars(make_15m(16), "1h")
        self.assertEqual(len(result), 4)
        self.assertEqual(result.iloc[0]["Open"], 99.75)
        self.assertEqual(result.iloc[0]["High"], 103.5)
        self.assertEqual(result.iloc[0]["Low"], 99.5)
        self.assertEqual(result.iloc[0]["Close"], 103.0)
        self.assertEqual(result.iloc[0]["Volume"], 40.0)
        self.assertEqual(metadata["complete_target_bars"], 4)
        self.assertFalse(metadata["synthetic_bars"])
        self.assertEqual(result.attrs["volume_quality"], "available")

    def test_four_hour_aggregation_uses_sixteen_source_bars(self):
        result, metadata = resample_complete_coinbase_bars(make_15m(32), "4h")
        self.assertEqual(len(result), 2)
        self.assertEqual(result.iloc[0]["Volume"], 160.0)
        self.assertEqual(metadata["complete_target_bars"], 2)
        self.assertEqual(metadata["target_segment_count"], 1)

    def test_partial_source_buckets_are_discarded_not_filled(self):
        result, metadata = resample_complete_coinbase_bars(make_15m(6), "1h")
        self.assertEqual(len(result), 1)
        self.assertEqual(metadata["partial_target_buckets_discarded"], 1)
        self.assertFalse(metadata["synthetic_bars"])

    def test_missing_source_candle_prevents_cross_gap_aggregation(self):
        frame = make_15m(8).drop(pd.Timestamp("2026-01-01T00:15:00Z"))
        result, metadata = resample_complete_coinbase_bars(frame, "1h")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.index[0], pd.Timestamp("2026-01-01T01:00:00Z"))
        self.assertEqual(metadata["source_missing_15m_bars"], 1)
        self.assertGreaterEqual(metadata["partial_target_buckets_discarded"], 2)

    def test_research_timeframe_patch_is_temporary_and_gap_aware(self):
        original_periods = backtest_v3.PERIODS
        original_supported = strategies.SUPPORTED_TIMEFRAMES
        original_gap = backtest_v3._has_unexpected_gap
        with research_timeframe_enabled(backtest_v3, "4h"):
            self.assertIn("4h", backtest_v3.PERIODS)
            self.assertIn("4h", strategies.SUPPORTED_TIMEFRAMES)
            self.assertFalse(backtest_v3._has_unexpected_gap(
                pd.Timestamp("2026-01-01T00:00:00Z"),
                pd.Timestamp("2026-01-01T04:00:00Z"),
                "4h", "CRYPTO",
            ))
            self.assertTrue(backtest_v3._has_unexpected_gap(
                pd.Timestamp("2026-01-01T00:00:00Z"),
                pd.Timestamp("2026-01-01T08:00:00Z"),
                "4h", "CRYPTO",
            ))
        self.assertIs(backtest_v3.PERIODS, original_periods)
        self.assertIs(strategies.SUPPORTED_TIMEFRAMES, original_supported)
        self.assertIs(backtest_v3._has_unexpected_gap, original_gap)

    def test_unsupported_interval_fails_closed(self):
        with self.assertRaises(ValueError):
            resample_complete_coinbase_bars(make_15m(16), "2h")


if __name__ == "__main__":
    unittest.main()
