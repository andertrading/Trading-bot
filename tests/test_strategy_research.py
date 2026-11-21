import unittest

import pandas as pd

from strategy_research import (
    _chronological_period_bounds,
    _out_of_sample_trades,
    _snapshot_coverage,
)


class TestCommonCalendarOutOfSampleSplit(unittest.TestCase):

    def test_snapshot_coverage_explicitly_reports_missing_universe_pair(self):
        assets = [(name, name) for name in ("BTC", "SOL")]
        rows, summary = _snapshot_coverage(
            assets,
            ("15m", "1d"),
            [
                {"asset": "BTC", "timeframe": "15m"},
                {"asset": "BTC", "timeframe": "1d"},
                {"asset": "SOL", "timeframe": "1d"},
            ],
        )
        self.assertEqual(summary["status"], "PARTIAL")
        self.assertEqual(summary["expected_combinations"], 4)
        self.assertEqual(summary["available_combinations"], 3)
        self.assertEqual(
            summary["missing_combinations"],
            [{"asset": "SOL", "timeframe": "15m"}],
        )
        missing = [row for row in rows if row["status"] == "MISSING"]
        self.assertEqual(
            missing,
            [{"asset": "SOL", "timeframe": "15m", "status": "MISSING"}],
        )

    def test_snapshot_coverage_rejects_duplicate_manifest_entries(self):
        with self.assertRaisesRegex(ValueError, "Duplicate snapshot"):
            _snapshot_coverage(
                [("BTC", "BTC")],
                ("15m",),
                [
                    {"asset": "BTC", "timeframe": "15m"},
                    {"asset": "BTC", "timeframe": "15m"},
                ],
            )

    def test_naive_history_bounds_are_interpreted_as_utc(self):
        bounds = _chronological_period_bounds(
            "2025-01-01 00:00:00",
            "2026-01-01 00:00:00",
        )
        self.assertIsNotNone(bounds)
        start, early_end, end, folds = bounds
        self.assertEqual(str(start.tz), "UTC")
        self.assertEqual(str(end.tz), "UTC")
        self.assertEqual(len(folds), 3)
        self.assertEqual(early_end, start + (end - start) * 0.5)

    def test_oos_cutoff_uses_full_history_not_first_or_last_trade(self):
        bounds = _chronological_period_bounds(
            "2025-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        )
        _, early_end, _, _ = bounds
        trades = [
            {"entry_time": "2025-01-02T00:00:00Z", "result_R": 1.0},
            {"entry_time": "2025-12-30T00:00:00Z", "result_R": -1.0},
        ]
        selected = _out_of_sample_trades(
            trades, "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["entry_time"], "2025-12-30T00:00:00Z")
        self.assertGreater(pd.Timestamp(selected[0]["entry_time"]), early_end)

    def test_invalid_or_missing_history_bounds_fail_closed(self):
        self.assertIsNone(_chronological_period_bounds(None, None))
        self.assertIsNone(_chronological_period_bounds(
            "2026-01-01T00:00:00Z",
            "2025-01-01T00:00:00Z",
        ))
        self.assertEqual(
            _out_of_sample_trades(
                [{"entry_time": "2025-06-01T00:00:00Z"}], None, None
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
