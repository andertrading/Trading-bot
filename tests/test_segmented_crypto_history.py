import unittest

import numpy as np
import pandas as pd

from indicators import add_indicators
from research.segmented_crypto_history import (
    add_segmented_indicators,
    is_trade_within_segment,
    split_contiguous_segments,
)


def make_ohlc(index):
    close = 100.0 + np.arange(len(index), dtype=float) * 0.1
    return pd.DataFrame(
        {
            "Open": close - 0.02,
            "High": close + 0.10,
            "Low": close - 0.10,
            "Close": close,
            "Volume": np.full(len(index), 10.0),
        },
        index=index,
    )


class TestSegmentedCryptoHistory(unittest.TestCase):
    def test_split_contiguous_segments_keeps_complete_history_as_one_segment(self):
        index = pd.date_range(
            "2026-01-01", periods=8, freq="15min", tz="UTC"
        )
        data = make_ohlc(index)

        segments, metadata = split_contiguous_segments(data, "15m")

        self.assertEqual(len(segments), 1)
        pd.testing.assert_frame_equal(segments[0], data)
        self.assertEqual(metadata["segment_count"], 1)
        self.assertEqual(metadata["missing_bar_count"], 0)

    def test_split_contiguous_segments_splits_without_filling_gap(self):
        index = pd.DatetimeIndex(
            [
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:15:00Z",
                "2026-01-01T01:00:00Z",
                "2026-01-01T01:15:00Z",
            ]
        )
        data = make_ohlc(index)

        segments, metadata = split_contiguous_segments(data, "15m")

        self.assertEqual([len(segment) for segment in segments], [2, 2])
        self.assertEqual(metadata["segment_count"], 2)
        self.assertEqual(metadata["missing_bar_count"], 2)
        self.assertEqual(sum(len(segment) for segment in segments), len(data))
        self.assertTrue(
            all(
                timestamp in data.index
                for segment in segments
                for timestamp in segment.index
            )
        )
        self.assertNotIn(
            pd.Timestamp("2026-01-01T00:30:00Z"),
            pd.concat(segments).index,
        )

    def test_daily_crypto_segments_use_one_utc_day_cadence(self):
        index = pd.DatetimeIndex(
            [
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
                "2026-01-04T00:00:00Z",
                "2026-01-05T00:00:00Z",
            ]
        )
        segments, metadata = split_contiguous_segments(make_ohlc(index), "1d")

        self.assertEqual([len(segment) for segment in segments], [2, 2])
        self.assertEqual(metadata["missing_bar_count"], 1)

    def test_segmented_indicators_reset_state_and_apply_warmup_per_segment(self):
        first_index = pd.date_range(
            "2026-01-01", periods=250, freq="15min", tz="UTC"
        )
        second_index = pd.date_range(
            first_index[-1] + pd.Timedelta(hours=2),
            periods=250,
            freq="15min",
        )
        index = first_index.append(second_index)
        data = make_ohlc(index)

        prepared = add_segmented_indicators(
            data,
            "15m",
            add_indicators,
            warmup_bars=220,
        )

        self.assertEqual(prepared["segment_id"].iloc[0], 0)
        self.assertEqual(prepared["segment_id"].iloc[249], 0)
        self.assertEqual(prepared["segment_id"].iloc[250], 1)
        self.assertFalse(prepared["research_signal_eligible"].iloc[218])
        self.assertTrue(prepared["research_signal_eligible"].iloc[219])
        self.assertFalse(prepared["research_signal_eligible"].iloc[250])
        self.assertFalse(prepared["research_signal_eligible"].iloc[468])
        self.assertTrue(prepared["research_signal_eligible"].iloc[469])

        # EMA state starts from the first close of the new segment.
        self.assertAlmostEqual(
            prepared["EMA20"].iloc[250],
            prepared["Close"].iloc[250],
        )
        self.assertAlmostEqual(
            prepared["EMA50"].iloc[250],
            prepared["Close"].iloc[250],
        )
        self.assertAlmostEqual(
            prepared["EMA200"].iloc[250],
            prepared["Close"].iloc[250],
        )

        metadata = prepared.attrs["gap_aware_research"]
        self.assertEqual(metadata["segment_count"], 2)
        self.assertEqual(metadata["missing_bar_count"], 7)
        self.assertEqual(metadata["warmup_bars"], 220)
        self.assertEqual(metadata["warmup_rows_excluded"], 438)
        self.assertTrue(metadata["segment_indicator_reset"])

    def test_gap_free_segmented_indicators_match_normal_indicator_output(self):
        index = pd.date_range(
            "2026-01-01", periods=260, freq="15min", tz="UTC"
        )
        data = make_ohlc(index)

        expected = add_indicators(data)
        actual = add_segmented_indicators(data, "15m", add_indicators)

        for column in expected.columns:
            pd.testing.assert_series_equal(
                actual[column],
                expected[column],
                check_names=True,
            )

        self.assertEqual(actual["segment_id"].nunique(), 1)
        self.assertEqual(int(actual["research_signal_eligible"].sum()), 41)

    def test_trade_must_start_after_warmup_and_remain_inside_one_segment(self):
        first_index = pd.date_range(
            "2026-01-01", periods=250, freq="15min", tz="UTC"
        )
        second_index = pd.date_range(
            first_index[-1] + pd.Timedelta(hours=1),
            periods=250,
            freq="15min",
        )
        prepared = add_segmented_indicators(
            make_ohlc(first_index.append(second_index)),
            "15m",
            add_indicators,
            warmup_bars=220,
        )

        self.assertTrue(is_trade_within_segment(prepared, 219, 240))
        self.assertFalse(is_trade_within_segment(prepared, 219, 250))
        self.assertFalse(is_trade_within_segment(prepared, 250, 260))
        self.assertTrue(is_trade_within_segment(prepared, 469, 480))
        self.assertFalse(is_trade_within_segment(prepared, 469, 501))
        self.assertFalse(is_trade_within_segment(prepared, 501, 500))

    def test_invalid_time_indexes_fail_closed(self):
        indexes = [
            pd.date_range("2026-01-01", periods=3, freq="15min"),
            pd.DatetimeIndex(
                [
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:20:00Z",
                ]
            ),
            pd.DatetimeIndex(
                [
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:15:00Z",
                    "2026-01-01T00:15:00Z",
                ]
            ),
            pd.DatetimeIndex(
                [
                    "2026-01-01T00:15:00Z",
                    "2026-01-01T00:00:00Z",
                ]
            ),
        ]
        for index in indexes:
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    split_contiguous_segments(make_ohlc(index), "15m")

    def test_invalid_interval_and_indicator_result_fail_closed(self):
        index = pd.date_range(
            "2026-01-01", periods=4, freq="15min", tz="UTC"
        )
        data = make_ohlc(index)

        with self.assertRaises(ValueError):
            split_contiguous_segments(data, "5m")
        with self.assertRaises(ValueError):
            add_segmented_indicators(data, "15m", lambda _: None)
        with self.assertRaises(ValueError):
            add_segmented_indicators(
                data,
                "15m",
                add_indicators,
                warmup_bars=0,
            )


if __name__ == "__main__":
    unittest.main()
