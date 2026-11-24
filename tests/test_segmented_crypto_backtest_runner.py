import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.run_segmented_crypto_backtest import (
    load_coinbase_snapshot,
    run_segmented_backtest,
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


class FakeBacktestModule:
    PERIODS = {"15m": {"max_bars": 5}}
    MIN_BARS = 220

    def __init__(self):
        self.DOWNLOAD_ERRORS = {}
        self.EXCLUDED_OHLC_CANDLES = []
        self.RR_REJECTIONS = []
        self.SIGNAL_DIAGNOSTICS = []
        self.segment_lengths = []
        self.original_download = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("original download function must never be called")
        )
        self.download_history = self.original_download
        self.fail = False

    def run_backtest(self, asset, ticker, interval):
        data = self.download_history(ticker, interval, asset=asset)
        self.segment_lengths.append(len(data))
        differences = data.index.to_series().diff().dropna()
        if not (differences == pd.Timedelta(minutes=15)).all():
            raise AssertionError("runner passed a segment containing a gap")
        if self.fail:
            return [], "synthetic test error"
        return [
            {
                "result_R": 1.0,
                "duration_bars": 2,
                "entry_time": data.index[220].isoformat(),
                "exit_time": data.index[221].isoformat(),
            }
        ], None

    @staticmethod
    def calculate_metrics(trades):
        return {"trades": len(trades)}


class TestSegmentedCryptoBacktestRunner(unittest.TestCase):
    def test_runner_calls_backtest_separately_for_each_segment_without_download(self):
        first = pd.date_range(
            "2026-01-01", periods=225, freq="15min", tz="UTC"
        )
        second = pd.date_range(
            first[-1] + pd.Timedelta(hours=1),
            periods=226,
            freq="15min",
        )
        data = make_ohlc(first.append(second))
        fake = FakeBacktestModule()
        original_download = fake.download_history

        result = run_segmented_backtest(
            data,
            "BTC",
            "BTC-USD",
            "15m",
            backtest_module=fake,
        )

        self.assertEqual(fake.segment_lengths, [225, 226])
        self.assertIs(fake.download_history, original_download)
        self.assertEqual(result["coverage"]["segment_count"], 2)
        self.assertEqual(result["coverage"]["missing_bar_count"], 3)
        self.assertEqual(result["metrics"]["trades"], 2)
        self.assertEqual([trade["segment_id"] for trade in result["trades"]], [0, 1])
        self.assertTrue(
            all(
                trade["data_source"] == "coinbase_exchange_only"
                and trade["source_venue"] == "Coinbase Exchange"
                and trade["etoro_price_equivalence"] == "not_established"
                for trade in result["trades"]
            )
        )
        self.assertEqual(result["network_requests"], 0)
        self.assertIn("not eToro executable bid/ask", result["execution_price_basis"])
        self.assertTrue(result["trade_segment_integrity"])
        self.assertTrue(result["all_segments_completed"])

    def test_gap_free_runner_preserves_direct_engine_trade_output(self):
        index = pd.date_range(
            "2026-01-01", periods=225, freq="15min", tz="UTC"
        )
        data = make_ohlc(index)

        direct = FakeBacktestModule()
        direct.download_history = (
            lambda _ticker, _interval, asset=None: data.copy()
        )
        direct_trades, error = direct.run_backtest(
            "BTC", "BTC-USD", "15m"
        )
        self.assertIsNone(error)
        direct_metrics = direct.calculate_metrics(direct_trades)

        segmented = FakeBacktestModule()
        result = run_segmented_backtest(
            data,
            "BTC",
            "BTC-USD",
            "15m",
            backtest_module=segmented,
        )

        provenance_fields = {
            "data_source",
            "source_venue",
            "price_adjustment_policy",
            "research_price_basis",
            "etoro_price_equivalence",
            "segment_id",
            "segment_start",
            "segment_end",
        }
        normalized_segmented_trades = [
            {
                key: value
                for key, value in trade.items()
                if key not in provenance_fields
            }
            for trade in result["trades"]
        ]

        self.assertEqual(result["coverage"]["segment_count"], 1)
        self.assertEqual(normalized_segmented_trades, direct_trades)
        self.assertEqual(result["metrics"], direct_metrics)
        self.assertEqual(result["network_requests"], 0)

    def test_short_segment_is_skipped_before_backtest(self):
        index = pd.date_range(
            "2026-01-01", periods=219, freq="15min", tz="UTC"
        )
        fake = FakeBacktestModule()

        result = run_segmented_backtest(
            make_ohlc(index),
            "BTC",
            "BTC-USD",
            "15m",
            backtest_module=fake,
        )

        self.assertEqual(fake.segment_lengths, [])
        self.assertEqual(
            result["segments"][0]["status"],
            "skipped_insufficient_history",
        )
        self.assertEqual(result["metrics"]["trades"], 0)

    def test_backtest_error_fails_closed_and_restores_download_function(self):
        index = pd.date_range(
            "2026-01-01", periods=225, freq="15min", tz="UTC"
        )
        fake = FakeBacktestModule()
        fake.fail = True
        original_download = fake.download_history

        with self.assertRaisesRegex(RuntimeError, "synthetic test error"):
            run_segmented_backtest(
                make_ohlc(index),
                "BTC",
                "BTC-USD",
                "15m",
                backtest_module=fake,
            )

        self.assertIs(fake.download_history, original_download)

    def test_loader_validates_coinbase_manifest_and_exact_timestamp_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = pd.DatetimeIndex(
                [
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:15:00Z",
                    "2026-01-01T00:45:00Z",
                ]
            )
            data = make_ohlc(index)
            csv_path = root / "BTC_15m.csv"
            export = data.copy()
            export.insert(0, "timestamp", index.astype(str))
            export.insert(1, "timestamp_epoch", index.astype("int64") // 1_000_000_000)
            export = export.rename(
                columns={
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            )
            export.to_csv(csv_path, index=False)

            missing_path = root / "BTC_15m_missing_timestamps.csv"
            pd.DataFrame(
                {
                    "timestamp": ["2026-01-01T00:30:00+00:00"],
                    "timestamp_epoch": [
                        int(pd.Timestamp("2026-01-01T00:30:00Z").timestamp())
                    ],
                }
            ).to_csv(missing_path, index=False)

            manifest = {
                "created_at_utc": "2026-01-02T00:00:00+00:00",
                "github_run_id": "test-run",
                "source_policy": {
                    "coinbase_only": True,
                    "twelvedata_used": False,
                    "eodhd_used": False,
                    "cross_provider_gap_repair": False,
                    "synthetic_candles": False,
                    "missing_candles_filled": False,
                },
                "series": [
                    {
                        "asset": "BTC",
                        "timeframe": "15m",
                        "provider": "coinbase_exchange_only",
                        "gap_repair": False,
                        "synthetic_bars": False,
                        "start_utc": "2026-01-01T00:00:00Z",
                        "end_utc": "2026-01-01T00:45:00Z",
                        "expected_bars": 4,
                        "received_bars": 3,
                        "missing_bars": 1,
                        "missing_timestamps_csv": "artifacts/coinbase_only/BTC_15m_missing_timestamps.csv",
                    }
                ],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            loaded, loaded_manifest = load_coinbase_snapshot(
                csv_path,
                manifest_path,
                "BTC",
                "15m",
            )

            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded.index.tz, pd.Timestamp.now(tz="UTC").tz)
            self.assertEqual(loaded_manifest["github_run_id"], "test-run")
            self.assertEqual(loaded.attrs["coinbase_manifest_series"]["missing_bars"], 1)
            self.assertEqual(loaded.attrs["volume_quality"], "available")
            self.assertIs(loaded.attrs["volume_column_present"], True)
            self.assertEqual(loaded.attrs["volume_zero_count_before_normalization"], 0)
            self.assertEqual(loaded.attrs["volume_positive_count_before_normalization"], 3)
            self.assertEqual(loaded.attrs["volume_invalid_count_before_normalization"], 0)

    def test_loader_rejects_non_coinbase_source_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "source_policy": {
                    "coinbase_only": False,
                    "twelvedata_used": True,
                    "eodhd_used": False,
                    "cross_provider_gap_repair": True,
                    "synthetic_candles": False,
                    "missing_candles_filled": False,
                },
                "series": [],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "coinbase_only"):
                load_coinbase_snapshot(
                    root / "BTC_15m.csv",
                    manifest_path,
                    "BTC",
                    "15m",
                )


if __name__ == "__main__":
    unittest.main()
