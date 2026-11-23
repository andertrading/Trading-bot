import unittest
from unittest.mock import Mock

import pandas as pd

from etoro_yahoo_reconciliation import build_report, compare_asset_history


class EtoroYahooReconciliationTests(unittest.TestCase):
    def test_compares_only_overlapping_usable_daily_bars(self):
        etoro = {
            "history_usable": True,
            "candles": [
                {
                    "timestamp": "2026-11-20T00:00:00+00:00",
                    "open": "100", "high": "103", "low": "99", "close": "102",
                },
                {
                    "timestamp": "2026-11-21T00:00:00+00:00",
                    "open": "102", "high": "104", "low": "101", "close": "103",
                },
            ],
        }
        frame = pd.DataFrame(
            {
                "Open": [100.0, 102.0],
                "High": [103.0, 104.0],
                "Low": [99.0, 101.0],
                "Close": [101.0, 103.0],
            },
            index=pd.to_datetime(["2026-11-20", "2026-11-21"]),
        )
        ticker = Mock()
        ticker.history.return_value = frame
        result = compare_asset_history(
            "AAPL", etoro, ticker_symbol="AAPL",
            ticker_factory=lambda symbol: ticker,
        )
        self.assertEqual(result["overlap_bars"], 2)
        self.assertEqual(result["status"], "COMPARISON_ONLY_NOT_VALIDATION")
        self.assertAlmostEqual(
            result["difference_metrics_pct"]["close"]["median_absolute_pct"],
            (1 / 101 + 0) / 2 * 100,
        )
        worst = result["difference_metrics_pct"]["close"][
            "largest_absolute_differences"
        ][0]
        self.assertEqual(worst["date"], "2026-11-20")
        self.assertIn("yahoo_ohlc", result["largest_daily_outlier_rows"][0])
        self.assertIn("etoro_range_pct", result["largest_daily_outlier_rows"][0])
        ticker.history.assert_called_once()
        self.assertFalse(ticker.history.call_args.kwargs["auto_adjust"])

    def test_excludes_incomplete_current_day_and_rejects_incoherent_yahoo_ohlc(self):
        etoro = {
            "history_usable": True,
            "candles": [
                {
                    "timestamp": "2026-11-22T00:00:00+00:00",
                    "open": "100", "high": "103", "low": "99", "close": "102",
                },
                {
                    "timestamp": "2026-11-23T00:00:00+00:00",
                    "open": "102", "high": "104", "low": "101", "close": "103",
                },
            ],
        }
        frame = pd.DataFrame(
            {
                "Open": [100.0, 102.0],
                "High": [103.0, 104.0],
                "Low": [99.0, 130.0],
                "Close": [101.0, 103.0],
            },
            index=pd.to_datetime(["2026-11-22", "2026-11-23"]),
        )
        ticker = Mock()
        ticker.history.return_value = frame
        result = compare_asset_history(
            "AAPL", etoro, ticker_symbol="AAPL",
            ticker_factory=lambda symbol: ticker,
            captured_at="2026-11-23T15:00:00+00:00",
        )
        self.assertEqual(result["excluded_incomplete_etoro_bars"], 1)
        self.assertEqual(result["overlap_bars"], 1)
        self.assertEqual(result["invalid_yahoo_ohlc_bars"], 0)

    def test_incoherent_completed_yahoo_ohlc_is_excluded(self):
        etoro = {
            "history_usable": True,
            "candles": [
                {
                    "timestamp": "2026-11-21T00:00:00+00:00",
                    "open": "100", "high": "103", "low": "99", "close": "102",
                },
                {
                    "timestamp": "2026-11-22T00:00:00+00:00",
                    "open": "102", "high": "104", "low": "101", "close": "103",
                },
            ],
        }
        frame = pd.DataFrame(
            {
                "Open": [100.0, 102.0],
                "High": [103.0, 104.0],
                "Low": [99.0, 130.0],
                "Close": [101.0, 103.0],
            },
            index=pd.to_datetime(["2026-11-21", "2026-11-22"]),
        )
        ticker = Mock()
        ticker.history.return_value = frame
        result = compare_asset_history(
            "AAPL", etoro, ticker_symbol="AAPL",
            ticker_factory=lambda symbol: ticker,
        )
        self.assertEqual(result["overlap_bars"], 1)
        self.assertEqual(result["invalid_yahoo_ohlc_bars"], 1)
        self.assertEqual(
            result["invalid_yahoo_ohlc_examples"][0]["date"], "2026-11-22"
        )

    def test_unusable_etoro_history_is_not_compared(self):
        result = compare_asset_history(
            "BTC",
            {"history_usable": False, "candles": []},
            ticker_symbol="BTC-USD",
            ticker_factory=Mock(),
        )
        self.assertEqual(result["status"], "SKIPPED_ETORO_HISTORY_UNUSABLE")

    def test_report_never_claims_price_basis_or_execution_validation(self):
        source = {
            "captured_at": "2026-11-23T15:00:00+00:00",
            "assets": {
                "BTC": {
                    "historical_candles": {
                        "history_usable": False,
                        "candles": [],
                    }
                }
            },
        }
        report = build_report(
            source,
            assets={"BTC": {"yahoo": "BTC-USD"}},
            ticker_factory=Mock(),
        )
        self.assertEqual(report["price_basis_validation"], "NOT_VALIDATED")
        self.assertEqual(report["executable_price_parity"], "NOT_VALIDATED")
        self.assertEqual(report["summary"]["assets_with_overlap"], 0)


if __name__ == "__main__":
    unittest.main()
