import unittest

import numpy as np
import pandas as pd

from research.run_segmented_crypto_walkforward import run_oos_fold
from research.segmented_crypto_history import split_contiguous_segments
from strategies import evaluate_signal_at as evaluate_strategy_signal


class FakeBacktestModule:
    MIN_BARS = 220
    PERIODS = {"15m": {"max_bars": 5}}

    def __init__(self):
        self.DOWNLOAD_ERRORS = {}
        self.EXCLUDED_OHLC_CANDLES = []
        self.RR_REJECTIONS = []
        self.SIGNAL_DIAGNOSTICS = []
        self.download_history = lambda *args, **kwargs: None
        self.evaluate_signal_at = lambda *args, **kwargs: None
        self.received = None

    def run_backtest(self, asset, ticker, interval):
        self.received = self.download_history(ticker, interval, asset=asset)
        return [], None

    @staticmethod
    def calculate_metrics(trades):
        return {"trades": len(trades)}




class DiagnosticRecordingBacktestModule(FakeBacktestModule):
    def run_backtest(self, asset, ticker, interval):
        self.received = self.download_history(ticker, interval, asset=asset)
        self.SIGNAL_DIAGNOSTICS.append({
            "asset": asset,
            "signal_status": "SKIPPED_OVERLAPPING_TRADE",
            "score": 80,
        })
        self.RR_REJECTIONS.append({"asset": asset, "reason": "RR too low"})
        self.EXCLUDED_OHLC_CANDLES.append({"asset": asset, "reason": "invalid OHLC"})
        self.DOWNLOAD_ERRORS[(ticker, interval)] = "diagnostic fixture"
        return [], None


class StrategyCheckingBacktestModule(FakeBacktestModule):
    def __init__(self):
        super().__init__()
        self.signal_result = None
        # FakeBacktestModule installs an instance-level no-op evaluator;
        # replace it with the real strategy adapter for this integration test.
        self.evaluate_signal_at = self._evaluate

    @staticmethod
    def _evaluate(data, asset, timeframe, index):
        return evaluate_strategy_signal(asset, data, timeframe)

    def run_backtest(self, asset, ticker, interval):
        self.received = self.download_history(ticker, interval, asset=asset)
        self.signal_result = self.evaluate_signal_at(
            self.received,
            asset,
            interval,
            len(self.received) - 1,
        )
        return [], None


def make_segment():
    index = pd.date_range("2026-01-01", periods=500, freq="15min", tz="UTC")
    volume = np.full(500, 10.0)
    volume[250] = 0.0
    data = pd.DataFrame(
        {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.5,
            "Volume": volume,
            "EMA20": 105.0,
            "EMA50": 100.0,
            "EMA200": 95.0,
            "RSI": 55.0,
            "MACD": 2.0,
            "MACD_SIGNAL": 1.0,
            "ATR": 2.0,
            "ATR_PERCENT": 2.0,
            "HIGH20": 109.0,
            "relative_volume": 1.3,
        },
        index=index,
    )
    data.attrs.update({
        "volume_quality": "contains_zero_bars",
        "volume_column_present": True,
        "volume_zero_count_before_normalization": 1,
        "volume_positive_count_before_normalization": 499,
        "volume_invalid_count_before_normalization": 0,
    })
    segments, _ = split_contiguous_segments(data, "15m")
    return segments[0]


class TestSegmentedCryptoWalkforwardProvenance(unittest.TestCase):
    def test_fold_preserves_candidate_and_rejection_diagnostics(self):
        segment = make_segment()
        fake = DiagnosticRecordingBacktestModule()

        result = run_oos_fold(
            segment,
            "BTC",
            "BTC-USD",
            "15m",
            "validation",
            300,
            399,
            fake,
        )

        self.assertEqual(
            result["signal_diagnostics"],
            [{
                "asset": "BTC",
                "signal_status": "SKIPPED_OVERLAPPING_TRADE",
                "score": 80,
            }],
        )
        self.assertEqual(result["rr_rejections"][0]["reason"], "RR too low")
        self.assertEqual(result["excluded_ohlc_candles"][0]["reason"], "invalid OHLC")
        self.assertEqual(
            result["download_errors"],
            {("BTC-USD", "15m"): "diagnostic fixture"},
        )
        # run_oos_fold must still restore the module's original mutable state.
        self.assertEqual(fake.SIGNAL_DIAGNOSTICS, [])
        self.assertEqual(fake.RR_REJECTIONS, [])
        self.assertEqual(fake.EXCLUDED_OHLC_CANDLES, [])
        self.assertEqual(fake.DOWNLOAD_ERRORS, {})

    def test_validation_prefix_rebases_volume_counts_before_strategy(self):
        segment = make_segment()
        fake = FakeBacktestModule()

        result = run_oos_fold(
            segment,
            "BTC",
            "BTC-USD",
            "15m",
            "validation",
            300,
            399,
            fake,
        )

        self.assertEqual(len(fake.received), 400)
        self.assertEqual(fake.received.attrs["volume_quality"], "contains_zero_bars")
        self.assertEqual(fake.received.attrs["volume_zero_count_before_normalization"], 1)
        self.assertEqual(fake.received.attrs["volume_positive_count_before_normalization"], 399)
        self.assertEqual(fake.received.attrs["volume_invalid_count_before_normalization"], 0)
        self.assertEqual(
            fake.received.attrs["volume_zero_count_before_normalization"]
            + fake.received.attrs["volume_positive_count_before_normalization"],
            len(fake.received),
        )
        self.assertTrue(result["all_trades_inside_fold"])

    def test_validation_prefix_still_awards_volume_bonus_in_real_strategy(self):
        segment = make_segment()
        fake = StrategyCheckingBacktestModule()

        run_oos_fold(
            segment,
            "BTC",
            "BTC-USD",
            "15m",
            "validation",
            300,
            399,
            fake,
        )

        self.assertEqual(fake.signal_result["action"], "BUY CANDIDATE")
        self.assertIn(
            "Volumen superior a la media",
            fake.signal_result["reasons"],
        )
        self.assertGreater(fake.signal_result["score"], 0)

    def test_test_prefix_keeps_full_segment_volume_counts(self):
        segment = make_segment()
        fake = FakeBacktestModule()

        run_oos_fold(
            segment,
            "BTC",
            "BTC-USD",
            "15m",
            "test",
            400,
            499,
            fake,
        )

        self.assertEqual(len(fake.received), 500)
        self.assertEqual(fake.received.attrs["volume_zero_count_before_normalization"], 1)
        self.assertEqual(fake.received.attrs["volume_positive_count_before_normalization"], 499)

    def test_invalid_source_volume_provenance_is_not_upgraded_by_prefix(self):
        segment = make_segment()
        segment.attrs.update({
            "volume_quality": "invalid_or_missing_values",
            "volume_zero_count_before_normalization": 0,
            "volume_positive_count_before_normalization": 499,
            "volume_invalid_count_before_normalization": 1,
        })
        fake = FakeBacktestModule()

        run_oos_fold(
            segment,
            "BTC",
            "BTC-USD",
            "15m",
            "validation",
            300,
            399,
            fake,
        )

        self.assertEqual(
            fake.received.attrs["volume_quality"],
            "invalid_or_missing_values",
        )
        self.assertEqual(fake.received.attrs["volume_invalid_count_before_normalization"], 1)


if __name__ == "__main__":
    unittest.main()
