import unittest

import pandas as pd

from research.etf_provider_overlap_audit import _price_error_metrics, ohlc_integrity


class TestEtfProviderOverlapQuality(unittest.TestCase):
    def setUp(self):
        self.index = pd.Index([pd.Timestamp("2026-03-02").date()], name="date")

    def test_ohlc_integrity_accepts_coherent_bar(self):
        frame = pd.DataFrame(
            {"Open": [100.0], "High": [103.0], "Low": [99.0], "Close": [102.0]},
            index=self.index,
        )
        result = ohlc_integrity(frame)
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["invalid_rows"], 0)
        self.assertEqual(result["invalid_dates"], [])

    def test_ohlc_integrity_flags_low_above_open(self):
        frame = pd.DataFrame(
            {"Open": [100.0], "High": [103.0], "Low": [101.0], "Close": [102.0]},
            index=self.index,
        )
        result = ohlc_integrity(frame)
        self.assertEqual(result["invalid_rows"], 1)
        self.assertEqual(result["invalid_dates"], ["2026-03-02"])

    def test_price_error_metrics_reports_tail_discrepancies(self):
        left = pd.DataFrame(
            {"Open": [100.0, 100.0], "High": [101.0, 101.0],
             "Low": [99.0, 99.0], "Close": [100.0, 100.0]},
            index=pd.Index([1, 2]),
        )
        right = pd.DataFrame(
            {"Open": [100.0, 100.0], "High": [101.0, 110.0],
             "Low": [99.0, 99.0], "Close": [100.0, 100.0]},
            index=pd.Index([1, 2]),
        )
        result = _price_error_metrics(left, right, "_left", "_right")
        self.assertEqual(result["High"]["observations"], 2)
        self.assertAlmostEqual(result["High"]["max_abs_pct"], 100.0 * (110.0 / 101.0 - 1.0))
        self.assertEqual(result["Open"]["max_abs_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
