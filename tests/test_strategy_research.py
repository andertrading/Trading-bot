import unittest

import pandas as pd

from strategy_research import (
    _chronological_period_bounds,
    _out_of_sample_trades,
    _snapshot_coverage,
    _broker_cost_sensitivity_rows,
    _history_calendar_coverage,
    _synchronized_oos_trades,
    _portfolio_allocated_cost_trades,
    _net_portfolio_cost_scenarios,
    mtf_daily_price_confirmed_pullback,
    daily_swing_trend_pullback,
    _asset_class_universes,
)


class TestAssetClassUniverseIsolation(unittest.TestCase):

    def test_separates_stocks_and_crypto_without_mixing_etfs(self):
        groups = _asset_class_universes(
            ["AAPL", "BTC", "CSPX", "ETH", "MSFT"],
            assets_config={
                "AAPL": {"type": "STOCK"},
                "BTC": {"type": "CRYPTO"},
                "CSPX": {"type": "ETF"},
                "ETH": {"type": "CRYPTO"},
                "MSFT": {"type": "STOCK"},
            },
        )
        self.assertEqual(groups, {
            "US_STOCKS": ["AAPL", "MSFT"],
            "CRYPTO": ["BTC", "ETH"],
        })


class TestDailyPriceConfirmedMtfStrategy(unittest.TestCase):

    def test_daily_price_filter_blocks_when_completed_daily_close_is_below_ema200(self):
        data = pd.DataFrame([{
            "DAILY_TREND_UP": True,
            "DAILY_PRICE_ABOVE_EMA200": False,
        }])
        result = mtf_daily_price_confirmed_pullback(data, "AAPL", "15m")
        self.assertEqual(result["action"], "NO TRADE")

    def test_daily_price_filter_blocks_non_intraday_timeframe(self):
        data = pd.DataFrame([{
            "DAILY_TREND_UP": True,
            "DAILY_PRICE_ABOVE_EMA200": True,
        }])
        result = mtf_daily_price_confirmed_pullback(data, "AAPL", "1d")
        self.assertEqual(result["action"], "NO TRADE")


class TestDailySwingTrendPullback(unittest.TestCase):

    def test_daily_swing_accepts_completed_daily_pullback_reclaim(self):
        data = pd.DataFrame([
            {
                "Close": 99.0, "Low": 98.5, "EMA20": 100.0,
                "EMA50": 95.0, "EMA200": 90.0, "RSI": 48.0,
                "MACD": 0.2, "MACD_SIGNAL": 0.3,
                "ATR": 2.0, "ATR_PERCENT": 2.0,
            },
            {
                "Close": 102.0, "Low": 100.0, "EMA20": 101.0,
                "EMA50": 96.0, "EMA200": 90.5, "RSI": 54.0,
                "MACD": 0.5, "MACD_SIGNAL": 0.4,
                "ATR": 2.0, "ATR_PERCENT": 2.0,
            },
        ])
        result = daily_swing_trend_pullback(data, "AAPL", "1d", index=1)
        self.assertEqual(result["action"], "BUY CANDIDATE")
        self.assertEqual(result["timeframe"], "1d")
        self.assertFalse(result["tradable"])
        self.assertFalse(result["validated"])

    def test_daily_swing_rejects_intraday_and_weak_trend(self):
        data = pd.DataFrame([
            {
                "Close": 99.0, "Low": 98.5, "EMA20": 100.0,
                "EMA50": 95.0, "EMA200": 90.0, "RSI": 48.0,
                "MACD": 0.2, "MACD_SIGNAL": 0.3,
                "ATR": 2.0, "ATR_PERCENT": 2.0,
            },
            {
                "Close": 102.0, "Low": 100.0, "EMA20": 101.0,
                "EMA50": 89.0, "EMA200": 90.5, "RSI": 54.0,
                "MACD": 0.5, "MACD_SIGNAL": 0.4,
                "ATR": 2.0, "ATR_PERCENT": 2.0,
            },
        ])
        self.assertEqual(
            daily_swing_trend_pullback(data, "AAPL", "15m", index=1)["action"],
            "NO TRADE",
        )
        self.assertEqual(
            daily_swing_trend_pullback(data, "AAPL", "1d", index=1)["action"],
            "NO TRADE",
        )


class TestCommonCalendarOutOfSampleSplit(unittest.TestCase):



    def test_synchronized_oos_trades_excludes_trade_exit_after_window(self):
        trades = [
            {"timeframe": "15m", "entry_time": "2025-01-20T00:00:00Z",
             "exit_time": "2025-01-25T00:00:00Z"},
            {"timeframe": "15m", "entry_time": "2025-01-20T00:00:00Z",
             "exit_time": "2025-02-02T00:00:00Z"},
        ]
        selected = _synchronized_oos_trades(
            trades, "15m", "2025-01-01T00:00:00Z", "2025-01-31T00:00:00Z"
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["exit_time"], "2025-01-25T00:00:00Z")

    def test_portfolio_allocated_cost_trades_use_actual_scaled_quantity(self):
        source = [{
            "asset": "BTC", "timeframe": "15m",
            "entry_time": "2025-01-20T00:00:00Z",
            "exit_time": "2025-01-21T00:00:00Z",
            "entry": 100000.0, "exit": 101000.0,
            "hypothetical_quantity": 0.01,
            "hypothetical_risk_budget_usd": 10.0,
            "hypothetical_gross_pnl_usd": 10.0,
        }]
        accepted = [{
            "asset": "BTC", "timeframe": "15m",
            "entry_time": "2025-01-20T00:00:00+00:00",
            "exit_time": "2025-01-21T00:00:00+00:00",
            "quantity": 0.002, "allocated_risk_usd": 2.0,
            "realized_pnl_usd": 2.0, "notional_usd": 200.0,
            "allocation_factor": 0.2,
        }]
        scaled = _portfolio_allocated_cost_trades(source, accepted)
        self.assertEqual(len(scaled), 1)
        self.assertAlmostEqual(scaled[0]["hypothetical_quantity"], 0.002)
        self.assertAlmostEqual(scaled[0]["hypothetical_risk_budget_usd"], 2.0)
        self.assertAlmostEqual(scaled[0]["hypothetical_gross_pnl_usd"], 2.0)
        self.assertAlmostEqual(scaled[0]["portfolio_allocated_notional_usd"], 200.0)
        cost_rows = _broker_cost_sensitivity_rows(
            scaled,
            assets_config={"BTC": {"type": "CRYPTO", "currency": "USD"}},
            spread_bps_values=(0,),
        )
        etoro = next(
            row for row in cost_rows
            if row["venue"] == "ETORO_REAL"
            and row["fee_mode"] == "published_fee_scenario"
        )
        self.assertAlmostEqual(etoro["commission_scenario_usd"], 4.02)

    def test_synchronized_oos_trades_uses_shared_timeframe_midpoint_and_bounds(self):
        trades = [
            {"timeframe": "15m", "entry_time": "2025-01-05T00:00:00Z",
             "exit_time": "2025-01-06T00:00:00Z", "result_R": 1.0},
            {"timeframe": "15m", "entry_time": "2025-01-20T00:00:00Z",
             "exit_time": "2025-01-21T00:00:00Z", "result_R": 2.0},
            {"timeframe": "1d", "entry_time": "2025-01-20T00:00:00Z",
             "exit_time": "2025-01-22T00:00:00Z", "result_R": 3.0},
            {"timeframe": "15m", "entry_time": "2025-02-01T00:00:00Z",
             "exit_time": "2025-02-02T00:00:00Z", "result_R": 4.0},
        ]
        selected = _synchronized_oos_trades(
            trades, "15m", "2025-01-01T00:00:00Z", "2025-01-31T00:00:00Z"
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["result_R"], 2.0)

    def test_history_calendar_coverage_detects_non_overlapping_series(self):
        assets = [("BTC", "BTC"), ("CSPX", "CSPX")]
        _, summary = _history_calendar_coverage(
            {
                ("BTC", "15m"): ("2025-01-01T00:00:00Z", "2025-12-31T00:00:00Z"),
                ("CSPX", "15m"): ("2026-01-01T00:00:00Z", "2026-10-01T00:00:00Z"),
                ("BTC", "1d"): ("2025-01-01T00:00:00Z", "2025-12-31T00:00:00Z"),
                ("CSPX", "1d"): ("2025-01-01T00:00:00Z", "2025-12-31T00:00:00Z"),
            },
            assets, ("15m", "1d"),
        )
        self.assertFalse(summary["by_timeframe"]["15m"]["common_window_exists"])
        self.assertTrue(summary["by_timeframe"]["1d"]["common_window_exists"])
        self.assertEqual(summary["all_timeframes"]["status"], "NO_COMMON_CALENDAR_WINDOW")

    def test_history_calendar_coverage_reports_shared_window_and_missing_pair(self):
        rows, summary = _history_calendar_coverage(
            {
                ("BTC", "15m"): ("2025-01-01T00:00:00Z", "2025-12-31T00:00:00Z"),
                ("CSPX", "15m"): ("2025-03-01T00:00:00Z", "2025-10-01T00:00:00Z"),
            },
            [("BTC", "BTC"), ("CSPX", "CSPX"), ("SOL", "SOL")],
            ("15m",),
        )
        result = summary["all_timeframes"]
        self.assertEqual(result["status"], "PARTIAL_UNIVERSE")
        self.assertEqual(result["common_start_utc"], "2025-03-01T00:00:00+00:00")
        self.assertEqual(result["common_end_utc"], "2025-10-01T00:00:00+00:00")
        self.assertEqual(result["missing_history_bounds"], [
            {"asset": "SOL", "timeframe": "15m"}
        ])
        self.assertEqual(len(rows), 3)

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

    def test_net_portfolio_hybrid_uses_verified_bitvavo_pair_fee_tiers(self):
        scenarios = {
            name: (model, note)
            for name, model, note in _net_portfolio_cost_scenarios()
        }

        self.assertEqual(
            set(scenarios),
            {
                "ETORO_REAL_STOCK_1USD_CRYPTO_1PCT",
                "ETORO_REAL_STOCK_2USD_CRYPTO_1PCT",
                "ETORO_STOCKS_BITVAVO_USDC_MAKER_XLM_EUR_MAKER",
                "ETORO_STOCKS_BITVAVO_USDC_MAKER_XLM_EUR_MAKER_STOCK_2USD",
                "ETORO_STOCKS_BITVAVO_USDC_TAKER_XLM_EUR_TAKER",
                "ETORO_STOCKS_BITVAVO_USDC_TAKER_XLM_EUR_TAKER_STOCK_2USD",
            },
        )
        low_stock, low_note = scenarios[
            "ETORO_REAL_STOCK_1USD_CRYPTO_1PCT"
        ]
        high_stock, high_note = scenarios[
            "ETORO_REAL_STOCK_2USD_CRYPTO_1PCT"
        ]
        maker, maker_note = scenarios[
            "ETORO_STOCKS_BITVAVO_USDC_MAKER_XLM_EUR_MAKER"
        ]
        maker_high, maker_high_note = scenarios[
            "ETORO_STOCKS_BITVAVO_USDC_MAKER_XLM_EUR_MAKER_STOCK_2USD"
        ]
        taker, taker_note = scenarios[
            "ETORO_STOCKS_BITVAVO_USDC_TAKER_XLM_EUR_TAKER"
        ]
        taker_high, taker_high_note = scenarios[
            "ETORO_STOCKS_BITVAVO_USDC_TAKER_XLM_EUR_TAKER_STOCK_2USD"
        ]
        self.assertEqual(low_stock["stock_fee_per_side_usd"], 1.0)
        self.assertEqual(high_stock["stock_fee_per_side_usd"], 2.0)
        self.assertIn("lower-fee", low_note)
        self.assertIn("upper-fee", high_note)
        self.assertEqual(maker_high["stock_fee_per_side_usd"], 2.0)
        self.assertEqual(taker_high["stock_fee_per_side_usd"], 2.0)

        self.assertEqual(maker["crypto_rate_by_asset"]["BTC"], 0.0005)
        self.assertEqual(maker_high["crypto_rate_by_asset"]["BTC"], 0.0005)
        self.assertEqual(taker_high["crypto_rate_by_asset"]["BTC"], 0.0005)
        self.assertEqual(maker["crypto_rate_by_asset"]["ETH"], 0.0005)
        self.assertEqual(maker["crypto_rate_by_asset"]["SOL"], 0.0005)
        self.assertEqual(maker["crypto_rate_by_asset"]["XRP"], 0.0005)
        self.assertEqual(maker["crypto_rate_by_asset"]["XLM"], 0.0015)
        self.assertEqual(taker["crypto_rate_by_asset"]["XLM"], 0.0025)
        self.assertIn("FX", maker_note)
        self.assertIn("FX", maker_high_note)
        self.assertIn("FX", taker_note)
        self.assertIn("FX", taker_high_note)

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
        self.assertEqual(lookup[("AAPL", "ETORO_REAL", "published_fee_scenario")]["commission_scenario_usd"], 2.0)
        self.assertEqual(lookup[("AAPL", "ETORO_REAL", "published_fee_scenario_upper_bound")]["commission_scenario_usd"], 4.0)
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
