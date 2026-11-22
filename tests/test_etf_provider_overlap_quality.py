import unittest

import pandas as pd

from research.etf_provider_overlap_audit import (
    _price_error_metrics,
    assess_alternate_repairs,
    ohlc_integrity,
)


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

    def test_assess_alternate_repairs_accepts_same_date_close_within_limit(self):
        index = pd.Index([pd.Timestamp("2026-03-02").date()])
        primary = pd.DataFrame(
            {"Open": [100.0], "High": [103.0], "Low": [101.5], "Close": [102.0], "Volume": [10]},
            index=index,
        )
        alternate = pd.DataFrame(
            {"Open": [100.0], "High": [103.0], "Low": [99.0], "Close": [102.1], "Volume": [10]},
            index=index,
        )
        result = assess_alternate_repairs(primary, alternate)
        self.assertTrue(result["all_invalid_bars_repairable"])
        self.assertEqual(result["alternate_repair_issues"], [])
        self.assertLessEqual(result["max_close_deviation_pct"], 0.5)

    def test_assess_alternate_repairs_rejects_close_divergence(self):
        index = pd.Index([pd.Timestamp("2026-03-02").date()])
        primary = pd.DataFrame(
            {"Open": [100.0], "High": [103.0], "Low": [101.5], "Close": [102.0], "Volume": [10]},
            index=index,
        )
        alternate = pd.DataFrame(
            {"Open": [100.0], "High": [103.0], "Low": [99.0], "Close": [103.0], "Volume": [10]},
            index=index,
        )
        result = assess_alternate_repairs(primary, alternate)
        self.assertFalse(result["all_invalid_bars_repairable"])
        self.assertEqual(
            result["alternate_repair_issues"][0]["reason"],
            "close_deviation_over_0_5pct",
        )

    def test_assess_alternate_repairs_rejects_missing_alternate_date(self):
        index = pd.Index([pd.Timestamp("2026-03-02").date()])
        primary = pd.DataFrame(
            {"Open": [100.0], "High": [103.0], "Low": [101.5], "Close": [102.0], "Volume": [10]},
            index=index,
        )
        alternate = pd.DataFrame(
            {"Open": [100.0], "High": [103.0], "Low": [99.0], "Close": [102.0], "Volume": [10]},
            index=pd.Index([pd.Timestamp("2026-03-03").date()]),
        )
        result = assess_alternate_repairs(primary, alternate)
        self.assertFalse(result["all_invalid_bars_repairable"])
        self.assertEqual(
            result["alternate_repair_issues"][0]["reason"],
            "alternate_date_missing",
        )

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
        self.assertAlmostEqual(result["High"]["max_abs_pct"], 100.0 * abs(101.0 / 110.0 - 1.0))
        self.assertEqual(result["Open"]["max_abs_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
