import unittest

import pandas as pd

from strategy_research import (
    _chronological_period_bounds,
    _out_of_sample_trades,
    _snapshot_coverage,
    _broker_cost_sensitivity_rows,
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

    def test_broker_cost_sensitivity_models_published_fee_scenarios(self):
        assets = {
            "AAPL": {"type": "STOCK", "currency": "USD"},
            "BTC": {"type": "CRYPTO", "currency": "USD"},
        }
        stock = {
            "strategy": "mtf",
            "asset": "AAPL",
            "timeframe": "15m",
            "entry_time": "2026-01-01T10:00:00Z",
            "exit_time": "2026-01-01T11:00:00Z",
            "entry": 100.0,
            "exit": 110.0,
            "hypothetical_quantity": 1.0,
            "hypothetical_risk_budget_usd": 5.0,
            "hypothetical_gross_pnl_usd": 10.0,
        }
        crypto = {
            **stock,
            "asset": "BTC",
            "entry": 100.0,
            "exit": 110.0,
        }
        rows = _broker_cost_sensitivity_rows(
            [stock, crypto], assets_config=assets, spread_bps_values=(0,)
        )
        lookup = {(row["asset"], row["venue"], row["fee_mode"]): row for row in rows}
        self.assertEqual(lookup[("AAPL", "ETORO_REAL", "published_fee_scenario")]["commission_scenario_usd"], 4.0)
        self.assertEqual(lookup[("AAPL", "XTB_OMI", "zero_commission_conditional")]["commission_scenario_usd"], 0.0)
        self.assertEqual(lookup[("AAPL", "XTB_OMI", "zero_commission_conditional")]["broker_symbol"], "AAPL.US")
        self.assertEqual(lookup[("BTC", "BITVAVO_SPOT", "maker")]["broker_symbol"], "BTC/EUR")
        self.assertEqual(lookup[("BTC", "ETORO_REAL", "published_fee_scenario")]["commission_scenario_usd"], 2.1)
        self.assertAlmostEqual(lookup[("BTC", "BITVAVO_SPOT", "maker")]["commission_scenario_usd"], 0.315)
        self.assertAlmostEqual(lookup[("BTC", "BITVAVO_SPOT", "taker")]["commission_scenario_usd"], 0.525)

    def test_broker_cost_sensitivity_spread_is_separate_and_xtb_threshold_fails_closed(self):
        assets = {"AAPL": {"type": "STOCK", "currency": "USD"}}
        trade = {
            "strategy": "baseline",
            "asset": "AAPL",
            "timeframe": "15m",
            "entry_time": "2026-01-01T10:00:00Z",
            "exit_time": "2026-01-01T11:00:00Z",
            "entry": 100.0,
            "exit": 110.0,
            "hypothetical_quantity": 1.0,
            "hypothetical_risk_budget_usd": 5.0,
            "hypothetical_gross_pnl_usd": 10.0,
        }
        rows = _broker_cost_sensitivity_rows(
            [trade], assets_config=assets, spread_bps_values=(10,)
        )
        xtb = next(row for row in rows if row["venue"] == "XTB_OMI")
        self.assertAlmostEqual(xtb["spread_scenario_usd"], 0.105)
        self.assertFalse(xtb["historical_spread_verified"])

        large = dict(trade, hypothetical_quantity=1000.0)
        blocked = _broker_cost_sensitivity_rows(
            [large], assets_config=assets, spread_bps_values=(0,)
        )
        xtb_blocked = next(row for row in blocked if row["venue"] == "XTB_OMI")
        self.assertEqual(
            xtb_blocked["scenario_status"],
            "NOT_PRICED_XTB_ZERO_COMMISSION_CONDITION_NOT_MET",
        )
        self.assertIsNone(xtb_blocked["scenario_net_total_R"])

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
