import os
import unittest
import tempfile
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

import pandas as pd

import backtest_v3


class TestBacktestV3(unittest.TestCase):

    def test_extended_daily_provider_uses_yahoo_for_london_assets_only(self):
        with patch.dict("os.environ", {"BACKTEST_EXTENDED_DAILY_HISTORY": "true"}):
            self.assertEqual(backtest_v3._provider_for_asset("CSPX", "1d"), "yahoo")
            self.assertEqual(backtest_v3._provider_for_asset("CNDX", "1d"), "yahoo")
            self.assertEqual(backtest_v3._provider_for_asset("AAPL", "1d"), "twelvedata")

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(backtest_v3._provider_for_asset("CSPX", "1d"), "eodhd")

    def test_daily_lookback_reads_environment_value(self):
        with patch.dict(os.environ, {"BACKTEST_DAILY_LOOKBACK_DAYS": "1825"}):
            self.assertEqual(backtest_v3._parse_daily_lookback_days(), 1825)

    def test_daily_lookback_rejects_invalid_environment_value(self):
        for value in ("abc", "100", "5000"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"BACKTEST_DAILY_LOOKBACK_DAYS": value}
            ):
                with self.assertRaises(ValueError):
                    backtest_v3._parse_daily_lookback_days()

    def test_daily_snapshot_period_label_tracks_configured_lookback(self):
        with patch.object(backtest_v3, "BACKTEST_DAILY_LOOKBACK_DAYS", 730):
            self.assertEqual(backtest_v3._daily_period_label(), "2y")
        with patch.object(backtest_v3, "BACKTEST_DAILY_LOOKBACK_DAYS", 1825):
            self.assertEqual(backtest_v3._daily_period_label(), "1825d")

    def test_asset_filter_selects_only_requested_enabled_assets(self):
        with patch.dict(
            backtest_v3.os.environ,
            {"BACKTEST_ASSETS": "AAPL,MSFT"},
            clear=False,
        ):
            selected = backtest_v3.get_assets()
        self.assertEqual([asset for asset, _ in selected], ["AAPL", "MSFT"])

    def test_asset_filter_rejects_unknown_assets(self):
        with patch.dict(
            backtest_v3.os.environ,
            {"BACKTEST_ASSETS": "NOT_A_REAL_ASSET"},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                backtest_v3.get_assets()

    def test_default_timeframe_is_daily_swing_only(self):
        with patch.dict(
            backtest_v3.os.environ,
            {},
            clear=True,
        ):
            self.assertEqual(backtest_v3.get_timeframes(), ("1d",))

    def test_timeframe_filter_selects_only_requested_intervals(self):
        with patch.dict(
            backtest_v3.os.environ,
            {"BACKTEST_TIMEFRAMES": "15m"},
            clear=False,
        ):
            self.assertEqual(backtest_v3.get_timeframes(), ("15m",))

    def test_timeframe_filter_rejects_unknown_intervals(self):
        with patch.dict(
            backtest_v3.os.environ,
            {"BACKTEST_TIMEFRAMES": "5m"},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                backtest_v3.get_timeframes()

    def make_data(self, rows=230):
        index = pd.date_range(
            "2025-01-01",
            periods=rows,
            freq="15min",
        )

        close = [100.0 + i * 0.1 for i in range(rows)]

        data = pd.DataFrame(
            {
                "Open": close,
                "High": [price + 1 for price in close],
                "Low": [price - 1 for price in close],
                "Close": close,
                "Volume": [1000] * rows,
            },
            index=index,
        )
        data.attrs.update({
            "data_provider": "twelvedata",
            "price_adjustment_policy": "raw_unadjusted_intraday",
            "price_adjustment_verified": True,
        })
        return data

    def make_execution_data(self, rows=14):
        index = pd.date_range(
            "2025-01-01",
            periods=rows,
            freq="15min",
        )

        data = pd.DataFrame(
            {
                "Open": [100.0] * rows,
                "High": [101.0] * rows,
                "Low": [99.0] * rows,
                "Close": [100.0] * rows,
                "Volume": [1000] * rows,
                "ATR": [2.0] * rows,
            },
            index=index,
        )

        data.iloc[5, data.columns.get_loc("Open")] = 101.0

        return data

    def run_execution_test(
        self,
        data,
        signal_indexes=(4,),
        target=109.0,
        max_bars=3,
    ):
        signals = set(signal_indexes)

        backtest_v3.RR_REJECTIONS.clear()

        def fake_signal(data, asset, timeframe, index):
            if index in signals:
                return {
                    "action": "BUY CANDIDATE",
                    "score": 80,
                    "entry_reference": 100.0,
                    "stop": 97.0,
                    "target": target,
                }

            return {
                "action": "NO TRADE",
                "score": 0,
            }

        with (
            patch.object(backtest_v3, "MIN_BARS", 5),
            patch.dict(
                backtest_v3.PERIODS,
                {
                    "15m": {
                        "period": "60d",
                        "max_bars": max_bars,
                    }
                },
            ),
            patch.object(
                backtest_v3,
                "download_history",
                return_value=data.copy(),
            ),
            patch.object(
                backtest_v3,
                "add_indicators",
                side_effect=lambda frame: frame,
            ),
            patch.object(
                backtest_v3,
                "evaluate_signal_at",
                side_effect=fake_signal,
            ),
        ):
            return backtest_v3.run_backtest(
                "AAPL",
                "AAPL",
                "15m",
            )

    # ---------------------------------------------------------
    # VALIDACIÓN OHLC
    # ---------------------------------------------------------

    def test_valid_ohlc_data(self):
        self.assertTrue(
            backtest_v3._valid_ohlc(self.make_data())
        )

    def test_invalid_ohlc_data_is_rejected(self):
        data = self.make_data()
        data.loc[data.index[10], "High"] = 1

        self.assertFalse(backtest_v3._valid_ohlc(data))

    def test_missing_ohlc_column_is_rejected(self):
        data = self.make_data().drop(columns=["Low"])

        self.assertFalse(backtest_v3._valid_ohlc(data))

    def test_backtest_uses_live_data_engine_provider(self):
        data = self.make_data()
        with (
            patch.object(backtest_v3, "BACKTEST_DATA_MODE", "download"),
            patch.object(backtest_v3, "download_data", return_value=data) as live_mock,
            patch.object(backtest_v3, "_save_snapshot", return_value=None),
        ):
            result = backtest_v3.download_history("AAPL", "15m", asset="AAPL")
        self.assertIsNotNone(result)
        live_mock.assert_called_once_with("AAPL", "15m")
        self.assertEqual(backtest_v3._provider_for_asset("AAPL", "15m"), "twelvedata")

    def test_daily_backtest_history_is_capped_to_two_years(self):
        index = pd.date_range(
            "2010-01-01",
            periods=5000,
            freq="1D",
            tz="UTC",
        )
        values = [100.0 + i * 0.001 for i in range(len(index))]
        data = pd.DataFrame(
            {
                "Open": values,
                "High": [value + 1 for value in values],
                "Low": [value - 1 for value in values],
                "Close": values,
                "Volume": [1000] * len(index),
            },
            index=index,
        )
        data.attrs.update({
            "data_provider": "twelvedata",
            "price_adjustment_policy": "split_adjusted_ohlc_no_dividend_adjustment",
            "price_adjustment_verified": True,
            "price_adjustment_verification_status": "INDEPENDENTLY_VERIFIED",
            "price_adjustment_verification_source": "https://example.org/split-adjustment-audit.json",
            "price_adjustment_verified_at": "2026-11-22T00:00:00Z",
        })
        with (
            patch.object(backtest_v3, "BACKTEST_DATA_MODE", "download"),
            patch.object(backtest_v3, "download_data", return_value=data),
            patch.object(backtest_v3, "_save_snapshot", return_value=None),
        ):
            result = backtest_v3.download_history(
                "AAPL", "1d", asset="AAPL"
            )
        self.assertIsNotNone(result)
        self.assertLessEqual(
            (result.index[-1] - result.index[0]).days,
            backtest_v3.BACKTEST_DAILY_LOOKBACK_DAYS,
        )
        self.assertGreaterEqual(len(result), backtest_v3.MIN_BARS)

    def test_backtest_rejects_unverified_price_adjustment_metadata(self):
        data = self.make_data()
        data.attrs["price_adjustment_verified"] = False
        with (
            patch.object(backtest_v3, "BACKTEST_DATA_MODE", "download"),
            patch.object(backtest_v3, "download_data", return_value=data),
        ):
            result = backtest_v3.download_history("AAPL", "15m", asset="AAPL")
        self.assertIsNone(result)
        self.assertIn("ajuste de precios no está verificado", backtest_v3.DOWNLOAD_ERRORS[("AAPL", "15m")])

    def test_daily_twelvedata_verified_status_without_evidence_is_rejected(self):
        data = self.make_data()
        data.attrs.update({
            "data_provider": "twelvedata",
            "price_adjustment_policy": "split_adjusted_ohlc_no_dividend_adjustment",
            "price_adjustment_verified": True,
            "price_adjustment_verification_status": "INDEPENDENTLY_VERIFIED",
            "price_adjustment_verification_source": None,
            "price_adjustment_verified_at": None,
        })
        with (
            patch.object(backtest_v3, "BACKTEST_DATA_MODE", "download"),
            patch.object(backtest_v3, "download_data", return_value=data),
        ):
            result = backtest_v3.download_history("AAPL", "1d", asset="AAPL")
        self.assertIsNone(result)
        self.assertIn(
            "no tiene evidencia independiente válida",
            backtest_v3.DOWNLOAD_ERRORS[("AAPL", "1d")],
        )

    def test_legacy_twelvedata_daily_snapshot_true_flag_is_not_trusted(self):
        index = pd.date_range(
            "2024-01-01T00:00:00Z",
            periods=220,
            freq="D",
        )
        close = pd.Series(100.0, index=index)
        data = pd.DataFrame({
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1000.0,
        }, index=index)
        data.attrs.update({
            "data_provider": "twelvedata",
            "price_adjustment_policy": "split_adjusted_ohlc_no_dividend_adjustment",
            "price_adjustment_verified": True,
            # Simulates a legacy manifest with no independent verification status.
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(backtest_v3, "SNAPSHOT_DIR", backtest_v3.Path(tmpdir)):
                backtest_v3._save_snapshot("AAPL", "AAPL", "1d", data)
                with self.assertRaisesRegex(
                    ValueError,
                    "no tiene evidencia independiente válida",
                ):
                    backtest_v3._load_snapshot("AAPL", "AAPL", "1d")

    def test_backtest_rejects_daily_twelvedata_without_independent_split_verification(self):
        data = self.make_data()
        data.attrs.update({
            "data_provider": "twelvedata",
            "price_adjustment_policy": "split_adjusted_ohlc_no_dividend_adjustment",
            "price_adjustment_verified": False,
            "price_adjustment_verification_status": (
                "REQUESTED_NOT_INDEPENDENTLY_VERIFIED"
            ),
        })
        with (
            patch.object(backtest_v3, "BACKTEST_DATA_MODE", "download"),
            patch.object(backtest_v3, "download_data", return_value=data),
        ):
            result = backtest_v3.download_history("AAPL", "1d", asset="AAPL")
        self.assertIsNone(result)
        self.assertIn(
            "ajuste de precios no está verificado",
            backtest_v3.DOWNLOAD_ERRORS[("AAPL", "1d")],
        )

    def test_snapshot_metadata_records_provider_and_adjustment_policy(self):
        data = self.make_data()
        metadata = backtest_v3._snapshot_metadata(
            "AAPL",
            "AAPL",
            "15m",
            data,
            backtest_v3.Path("snapshot.csv"),
            "sha256",
            "twelvedata",
        )
        self.assertEqual(metadata["provider"], "twelvedata")
        self.assertEqual(metadata["adjustment_policy"], "raw_unadjusted_intraday")
        self.assertTrue(metadata["price_adjustment_verified"])

        daily_data = self.make_data()
        daily_data.attrs.update({
            "data_provider": "twelvedata",
            "price_adjustment_policy": "split_adjusted_ohlc_no_dividend_adjustment",
            "price_adjustment_verified": True,
            "price_adjustment_verification_status": "INDEPENDENTLY_VERIFIED",
            "price_adjustment_verification_source": "https://example.org/split-adjustment-audit.json",
            "price_adjustment_verified_at": "2026-11-22T00:00:00Z",
        })
        daily_metadata = backtest_v3._snapshot_metadata(
            "AAPL",
            "AAPL",
            "1d",
            daily_data,
            backtest_v3.Path("daily_snapshot.csv"),
            "sha256",
            "twelvedata",
        )
        self.assertEqual(daily_metadata["period_requested"], "2y")
        self.assertEqual(
            daily_metadata["price_adjustment_verification_status"],
            "INDEPENDENTLY_VERIFIED",
        )
        self.assertEqual(
            daily_metadata["price_adjustment_verification_source"],
            "https://example.org/split-adjustment-audit.json",
        )

        daily_data.attrs.update({
            "price_source_detail": "LSE primary; SIX alternate OHLC repair",
            "ohlc_repair_count": 9,
            "ohlc_repair_source_ticker": "CSNDX.SW",
            "ohlc_repair_max_close_deviation_pct": 0.2293,
        })
        repaired_metadata = backtest_v3._snapshot_metadata(
            "CNDX",
            "CNDX.L",
            "1d",
            daily_data,
            backtest_v3.Path("repaired_daily_snapshot.csv"),
            "sha256",
            "yahoo",
        )
        self.assertEqual(repaired_metadata["ohlc_repair_count"], 9)
        self.assertEqual(
            repaired_metadata["ohlc_repair_source_ticker"], "CSNDX.SW"
        )
        self.assertAlmostEqual(
            repaired_metadata["ohlc_repair_max_close_deviation_pct"], 0.2293
        )

    def test_backtest_rejects_duplicate_timestamps_from_provider(self):
        data = self.make_data()
        duplicated = pd.concat([data, data.iloc[[-1]]])
        with (
            patch.object(backtest_v3, "BACKTEST_DATA_MODE", "download"),
            patch.object(backtest_v3, "download_data", return_value=duplicated),
        ):
            result = backtest_v3.download_history("AAPL", "15m", asset="AAPL")
        self.assertIsNone(result)
        self.assertIn("timestamps duplicados", backtest_v3.DOWNLOAD_ERRORS[("AAPL", "15m")])

    def test_backtest_does_not_drop_an_additional_closed_candle(self):
        data = self.make_data()
        with (
            patch.object(backtest_v3, "BACKTEST_DATA_MODE", "download"),
            patch.object(backtest_v3, "download_data", return_value=data),
            patch.object(backtest_v3, "_save_snapshot", return_value=None),
        ):
            result = backtest_v3.download_history("AAPL", "15m", asset="AAPL")
        self.assertEqual(len(result), len(data))
        self.assertEqual(result.index[-1], pd.Timestamp(data.index[-1]).tz_localize("UTC"))

    def test_backtest_rejects_incoherent_history_from_provider(self):
        data = self.make_data()
        data.loc[data.index[10], "High"] = 1.0
        backtest_v3.EXCLUDED_OHLC_CANDLES.clear()
        with (
            patch.object(backtest_v3, "BACKTEST_DATA_MODE", "download"),
            patch.object(backtest_v3, "download_data", return_value=data),
            redirect_stdout(StringIO()),
        ):
            result = backtest_v3.download_history("AAPL", "15m", asset="AAPL")
        self.assertIsNone(result)
        self.assertIn("OHLC incoherentes", backtest_v3.DOWNLOAD_ERRORS[("AAPL", "15m")])
        self.assertEqual(len(backtest_v3.EXCLUDED_OHLC_CANDLES), 1)

    def test_backtest_rejects_missing_ohlc_in_provider_history(self):
        data = self.make_data()
        data.iloc[10, data.columns.get_loc("Close")] = float("nan")
        with (
            patch.object(backtest_v3, "BACKTEST_DATA_MODE", "download"),
            patch.object(backtest_v3, "download_data", return_value=data),
        ):
            result = backtest_v3.download_history("AAPL", "15m", asset="AAPL")
        self.assertIsNone(result)
        self.assertIn("Close contiene precios ausentes", backtest_v3.DOWNLOAD_ERRORS[("AAPL", "15m")])

    def test_provider_mapping_matches_live_routing(self):
        self.assertEqual(backtest_v3._provider_for_asset("BTC", "15m"), "twelvedata")
        self.assertEqual(backtest_v3._provider_for_asset("CSPX", "15m"), "yahoo")
        self.assertEqual(backtest_v3._provider_for_asset("CSPX", "1d"), "eodhd")
        self.assertEqual(backtest_v3._provider_for_asset("CNDX", "1d"), "eodhd")
    def test_backtest_cost_model_applies_known_stock_fees_and_flags_unknown_spread(self):
        fields = backtest_v3._estimate_backtest_cost_fields(
            "AAPL",
            {
                "entry": 100.0,
                "exit": 110.0,
                "stop": 95.0,
                "score": 90,
                "entry_time": "2026-11-20T10:00:00",
                "exit_time": "2026-11-20T11:00:00",
            },
        )
        self.assertAlmostEqual(fields["commission_cost_usd"], 4.0)
        self.assertAlmostEqual(fields["hypothetical_risk_budget_usd"], 7.5)
        self.assertAlmostEqual(fields["result_R_after_known_fees"], 1.4667)
        self.assertFalse(fields["costs_complete"])
        self.assertIn("live_market_bid_ask_spread_missing", fields["scenario_unpriced_costs"])
        self.assertIn("round_trip_fee_evidence_missing", fields["unpriced_costs"])

    def test_registry_incomplete_overrides_legacy_complete_cost_flag(self):
        with (
            patch.object(
                backtest_v3,
                "estimate_etoro_trade_costs",
                return_value={
                    "complete": True,
                    "known_costs_total": 0.0,
                    "commission_cost": 0.0,
                    "market_spread_cost": 0.0,
                    "fx_conversion_cost": 0.0,
                    "overnight_cost": 0.0,
                    "unpriced_costs": [],
                    "assumptions": [],
                },
            ),
            patch.object(
                backtest_v3,
                "estimate_verified_registry_trade_costs",
                return_value={
                    "valid": True,
                    "complete": False,
                    "registry_status": "COSTS_UNVERIFIED",
                    "known_costs_total": None,
                    "unpriced_costs": ["round_trip_fee_evidence_missing"],
                },
            ),
        ):
            fields = backtest_v3._estimate_backtest_cost_fields(
                "AAPL",
                {
                    "entry": 100.0, "exit": 110.0, "stop": 95.0, "score": 90,
                    "entry_time": "2026-11-20T10:00:00",
                    "exit_time": "2026-11-20T11:00:00",
                },
            )
        self.assertFalse(fields["costs_complete"])
        self.assertEqual(fields["registry_costs_status"], "COSTS_UNVERIFIED")
        self.assertIsNone(fields["verified_costs_total_usd"])
        self.assertIn("round_trip_fee_evidence_missing", fields["unpriced_costs"])

    def test_verified_registry_costs_produce_separate_net_result(self):
        with (
            patch.object(
                backtest_v3,
                "estimate_etoro_trade_costs",
                return_value={
                    "complete": False,
                    "known_costs_total": 0.0,
                    "commission_cost": 0.0,
                    "market_spread_cost": None,
                    "fx_conversion_cost": 0.0,
                    "overnight_cost": None,
                    "unpriced_costs": ["live_market_bid_ask_spread_missing"],
                    "assumptions": [],
                },
            ),
            patch.object(
                backtest_v3,
                "estimate_verified_registry_trade_costs",
                return_value={
                    "valid": True,
                    "complete": True,
                    "registry_status": "ELIGIBLE",
                    "known_costs_total": 1.0,
                    "unpriced_costs": [],
                },
            ),
        ):
            fields = backtest_v3._estimate_backtest_cost_fields(
                "AAPL",
                {
                    "entry": 100.0, "exit": 110.0, "stop": 95.0, "score": 90,
                    "entry_time": "2026-11-20T10:00:00",
                    "exit_time": "2026-11-20T11:00:00",
                },
            )
        self.assertTrue(fields["costs_complete"])
        self.assertEqual(fields["unpriced_costs"], "")
        self.assertIn("live_market_bid_ask_spread_missing", fields["scenario_unpriced_costs"])
        self.assertEqual(fields["registry_costs_status"], "ELIGIBLE")
        self.assertAlmostEqual(fields["verified_costs_total_usd"], 1.0)
        self.assertAlmostEqual(fields["verified_net_pnl_usd"], 14.0)
        self.assertAlmostEqual(fields["verified_result_R"], 1.8667)

    def test_metrics_report_known_fee_adjusted_results_separately(self):
        metrics = backtest_v3.calculate_metrics([
            {
                "result_R": 2.0,
                "result_R_after_known_fees": 1.5,
                "duration_bars": 2,
                "costs_complete": False,
            },
            {
                "result_R": -1.0,
                "result_R_after_known_fees": -1.2,
                "duration_bars": 3,
                "costs_complete": False,
            },
        ])
        self.assertAlmostEqual(metrics["total_R"], 1.0)
        self.assertAlmostEqual(metrics["known_fee_adjusted_total_R"], 0.3)
        self.assertAlmostEqual(metrics["known_fee_adjusted_average_R"], 0.15)
        self.assertEqual(metrics["trades_with_complete_costs"], 0)
        self.assertEqual(metrics["trades_with_unpriced_costs"], 2)
        self.assertIsNone(metrics["verified_cost_adjusted_total_R"])
        self.assertEqual(metrics["trades_with_verified_costs"], 0)

    def test_run_backtest_rejects_invalid_history_before_indicators(self):
        data = self.make_execution_data()
        data.iloc[6, data.columns.get_loc("High")] = 1.0

        with (
            patch.object(backtest_v3, "MIN_BARS", 5),
            patch.object(
                backtest_v3,
                "download_history",
                return_value=data,
            ),
            patch.object(
                backtest_v3,
                "add_indicators",
            ) as indicators_mock,
        ):
            trades, error = backtest_v3.run_backtest(
                "AAPL",
                "AAPL",
                "15m",
            )

        self.assertEqual(trades, [])
        self.assertIsNotNone(error)
        indicators_mock.assert_not_called()

    # ---------------------------------------------------------
    # DETECCIÓN DE HUECOS TEMPORALES
    # ---------------------------------------------------------

    def test_crypto_15m_detects_missing_candle(self):
        previous = pd.Timestamp("2025-01-01 10:00")
        current = pd.Timestamp("2025-01-01 10:30")

        result = backtest_v3._has_unexpected_gap(
            previous,
            current,
            "15m",
            "CRYPTO",
        )

        self.assertTrue(result)

    def test_crypto_15m_accepts_consecutive_candles(self):
        previous = pd.Timestamp("2025-01-01 10:00")
        current = pd.Timestamp("2025-01-01 10:15")

        result = backtest_v3._has_unexpected_gap(
            previous,
            current,
            "15m",
            "CRYPTO",
        )

        self.assertFalse(result)

    def test_stock_15m_detects_missing_candle_same_day(self):
        previous = pd.Timestamp("2025-01-02 10:00")
        current = pd.Timestamp("2025-01-02 10:30")

        result = backtest_v3._has_unexpected_gap(
            previous,
            current,
            "15m",
            "STOCK",
        )

        self.assertTrue(result)

    def test_stock_15m_accepts_overnight_closure(self):
        previous = pd.Timestamp("2025-01-02 17:45")
        current = pd.Timestamp("2025-01-03 09:30")

        result = backtest_v3._has_unexpected_gap(
            previous,
            current,
            "15m",
            "STOCK",
        )

        self.assertFalse(result)

    def test_etf_15m_accepts_overnight_closure(self):
        previous = pd.Timestamp("2025-01-02 17:45")
        current = pd.Timestamp("2025-01-03 09:00")

        result = backtest_v3._has_unexpected_gap(
            previous,
            current,
            "15m",
            "ETF",
        )

        self.assertFalse(result)

    def test_stock_daily_does_not_reject_weekend_gap(self):
        previous = pd.Timestamp("2025-01-03")
        current = pd.Timestamp("2025-01-06")

        result = backtest_v3._has_unexpected_gap(
            previous,
            current,
            "1d",
            "STOCK",
        )

        self.assertFalse(result)

    def test_crypto_daily_detects_missing_day(self):
        previous = pd.Timestamp("2025-01-01")
        current = pd.Timestamp("2025-01-03")

        result = backtest_v3._has_unexpected_gap(
            previous,
            current,
            "1d",
            "CRYPTO",
        )

        self.assertTrue(result)

    def test_execution_gap_validator_accepts_contiguous_data(self):
        data = self.make_execution_data()

        result = backtest_v3._validate_execution_gaps(
            data,
            4,
            7,
            "15m",
            "STOCK",
        )

        self.assertIsNone(result)

    def test_execution_gap_validator_detects_missing_candle(self):
        data = self.make_execution_data()

        new_index = list(data.index)

        for index in range(6, len(new_index)):
            new_index[index] = (
                new_index[index] + pd.Timedelta(minutes=15)
            )

        data.index = pd.DatetimeIndex(new_index)

        result = backtest_v3._validate_execution_gaps(
            data,
            5,
            6,
            "15m",
            "STOCK",
        )

        self.assertIsNotNone(result)
        self.assertIn("Hueco temporal anómalo", result)

    def test_engine_stops_if_gap_appears_during_open_trade(self):
        data = self.make_execution_data()

        new_index = list(data.index)

        for index in range(6, len(new_index)):
            new_index[index] = (
                new_index[index] + pd.Timedelta(minutes=15)
            )

        data.index = pd.DatetimeIndex(new_index)

        trades, error = self.run_execution_test(data)

        self.assertEqual(trades, [])
        self.assertIsNotNone(error)
        self.assertIn("Hueco temporal anómalo", error)

    def test_engine_does_not_reject_overnight_gap_for_stock(self):
        data = self.make_execution_data()

        new_index = list(data.index)

        new_index[6] = pd.Timestamp("2025-01-02 09:30")

        for index in range(7, len(new_index)):
            new_index[index] = (
                pd.Timestamp("2025-01-02 09:30")
                + pd.Timedelta(minutes=15 * (index - 6))
            )

        data.index = pd.DatetimeIndex(new_index)

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)

    # ---------------------------------------------------------
    # MÉTRICAS
    # ---------------------------------------------------------

    def test_metrics_with_no_trades(self):
        result = backtest_v3.calculate_metrics(
            pd.DataFrame()
        )

        self.assertEqual(result, backtest_v3._empty_metrics())

    def test_metrics_calculate_valid_trades(self):
        trades = pd.DataFrame(
            {
                "result_R": [1.5, -1.0],
                "duration_bars": [2, 3],
            }
        )

        result = backtest_v3.calculate_metrics(trades)

        self.assertEqual(result["trades"], 2)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["losses"], 1)
        self.assertAlmostEqual(result["win_rate_pct"], 50.0)
        self.assertAlmostEqual(result["total_R"], 0.5)
        self.assertAlmostEqual(result["average_R"], 0.25)
        self.assertAlmostEqual(result["profit_factor_R"], 1.5)
        self.assertAlmostEqual(result["max_drawdown_R"], 1.0)
        self.assertAlmostEqual(result["average_duration_bars"], 2.5)

    def test_metrics_ignore_invalid_r_values(self):
        trades = pd.DataFrame(
            {
                "result_R": [1.0, -1.0, float("nan")],
                "duration_bars": [2, 3, 4],
            }
        )

        result = backtest_v3.calculate_metrics(trades)

        self.assertEqual(result["trades"], 2)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["losses"], 1)

    def test_metrics_handle_zero_result(self):
        trades = pd.DataFrame(
            {
                "result_R": [1.0, 0.0, -1.0],
                "duration_bars": [2, 2, 3],
            }
        )

        result = backtest_v3.calculate_metrics(trades)

        self.assertEqual(result["trades"], 3)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["losses"], 1)

        self.assertAlmostEqual(
            result["win_rate_pct"],
            33.33,
        )

    def test_profit_factor_without_losses(self):
        trades = pd.DataFrame(
            {
                "result_R": [1.0, 2.0],
                "duration_bars": [2, 3],
            }
        )

        result = backtest_v3.calculate_metrics(trades)

        self.assertIsNone(result["profit_factor_R"])

    def test_drawdown_never_negative(self):
        trades = pd.DataFrame(
            {
                "result_R": [1.0, -1.0, 2.0, -0.5],
            }
        )

        result = backtest_v3._calculate_drawdown(
            trades["result_R"].to_numpy()
        )

        self.assertGreaterEqual(result, 0)

    def test_drawdown_calculates_expected_value(self):
        results = [1.0, -1.0, 2.0, -0.5]

        result = backtest_v3._calculate_drawdown(results)

        self.assertAlmostEqual(result, 1.0)

    # ---------------------------------------------------------
    # RESUMEN DE OPERACIONES
    # ---------------------------------------------------------

    def test_operations_summary_with_valid_trades(self):
        trades = [
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-10",
                "exit_reason": "TARGET",
                "result_R": 1.5,
            },
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-11",
                "exit_reason": "STOP",
                "result_R": -1.0,
            },
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-12",
                "exit_reason": "MAX_HOLD",
                "result_R": 0.0,
            },
        ]

        result = backtest_v3.generate_operations_summary(trades)

        self.assertEqual(len(result), 2)

        total = result[
            result["tipo"] == "ACTIVO_TEMPORALIDAD"
        ].iloc[0]

        self.assertEqual(total["operaciones"], 3)
        self.assertEqual(total["ganadoras"], 1)
        self.assertEqual(total["perdedoras"], 1)
        self.assertAlmostEqual(total["resultado_R"], 0.5)
        self.assertAlmostEqual(total["acierto_pct"], 33.33)
        self.assertAlmostEqual(total["profit_factor"], 1.5)
        self.assertEqual(total["racha_perdedora_max"], 1)

    def test_operations_summary_rejects_missing_columns(self):
        trades = pd.DataFrame(
            {
                "asset": ["AAPL"],
                "result_R": [1.0],
            }
        )

        result = backtest_v3.generate_operations_summary(trades)

        self.assertTrue(result.empty)

    def test_operations_summary_calculates_losing_streak(self):
        trades = [
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-10",
                "exit_reason": "STOP",
                "result_R": -1.0,
            },
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-11",
                "exit_reason": "STOP",
                "result_R": -1.0,
            },
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-12",
                "exit_reason": "TARGET",
                "result_R": 2.0,
            },
        ]

        result = backtest_v3.generate_operations_summary(trades)

        total = result[
            result["tipo"] == "ACTIVO_TEMPORALIDAD"
        ].iloc[0]

        self.assertEqual(total["racha_perdedora_max"], 2)

    def test_operations_summary_separates_months(self):
        trades = [
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-10",
                "exit_reason": "TARGET",
                "result_R": 1.0,
            },
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-02-10",
                "exit_reason": "STOP",
                "result_R": -1.0,
            },
        ]

        result = backtest_v3.generate_operations_summary(trades)

        monthly = result[result["tipo"] == "MENSUAL"]

        self.assertEqual(len(monthly), 2)
        self.assertEqual(
            set(monthly["periodo"]),
            {"2025-01", "2025-02"},
        )

    def test_operations_summary_accepts_dataframe(self):
        trades = pd.DataFrame([
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-10",
                "exit_reason": "TARGET",
                "result_R": 1.5,
            },
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-11",
                "exit_reason": "STOP",
                "result_R": -1.0,
            },
        ])

        result = backtest_v3.generate_operations_summary(trades)

        total = result[
            result["tipo"] == "ACTIVO_TEMPORALIDAD"
        ].iloc[0]

        self.assertEqual(total["operaciones"], 2)
        self.assertAlmostEqual(total["resultado_R"], 0.5)

    def test_operations_summary_ignores_invalid_results(self):
        trades = [
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-10",
                "exit_reason": "TARGET",
                "result_R": 1.0,
            },
            {
                "asset": "AAPL",
                "timeframe": "1d",
                "exit_time": "2025-01-11",
                "exit_reason": "STOP",
                "result_R": float("nan"),
            },
        ]

        result = backtest_v3.generate_operations_summary(trades)

        total = result[
            result["tipo"] == "ACTIVO_TEMPORALIDAD"
        ].iloc[0]

        self.assertEqual(total["operaciones"], 1)
        self.assertAlmostEqual(total["resultado_R"], 1.0)

    # ---------------------------------------------------------
    # PRUEBAS DEL FILTRO DE RATIO RIESGO/BENEFICIO
    # ---------------------------------------------------------

    def test_execution_rr_is_exactly_two_to_one(self):
        data = self.make_execution_data()

        trades, error = self.run_execution_test(
            data,
            target=109.0,
        )

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)

        self.assertAlmostEqual(
            trades[0]["execution_rr"],
            2.0,
        )

        self.assertEqual(
            len(backtest_v3.RR_REJECTIONS),
            0,
        )

    def test_execution_rr_below_two_to_one_is_rejected(self):
        data = self.make_execution_data()

        trades, error = self.run_execution_test(
            data,
            target=106.0,
        )

        self.assertIsNone(error)
        self.assertEqual(trades, [])

        self.assertEqual(
            len(backtest_v3.RR_REJECTIONS),
            1,
        )

        rejection = backtest_v3.RR_REJECTIONS[0]

        self.assertAlmostEqual(
            rejection["actual_rr"],
            1.25,
        )

    def test_entry_gap_that_consumes_more_than_half_original_risk_is_rejected(self):
        data = self.make_execution_data()
        data.iloc[5, data.columns.get_loc("Open")] = 98.0
        data.iloc[5, data.columns.get_loc("High")] = 99.0
        data.iloc[5, data.columns.get_loc("Low")] = 97.5
        data.iloc[5, data.columns.get_loc("Close")] = 98.0

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(trades, [])
        self.assertEqual(
            backtest_v3.SIGNAL_DIAGNOSTICS[-1]["signal_status"],
            "ENTRY_GAP_CONSUMED_RISK",
        )

    def test_entry_gap_with_exactly_half_original_risk_remaining_is_accepted(self):
        data = self.make_execution_data()
        data.iloc[5, data.columns.get_loc("Open")] = 98.5
        data.iloc[5, data.columns.get_loc("High")] = 99.0
        data.iloc[5, data.columns.get_loc("Low")] = 98.0
        data.iloc[5, data.columns.get_loc("Close")] = 98.5

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)

    def test_execution_rr_above_two_to_one_is_accepted(self):
        data = self.make_execution_data()

        trades, error = self.run_execution_test(
            data,
            target=113.0,
        )

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)

        self.assertAlmostEqual(
            trades[0]["execution_rr"],
            3.0,
        )

    # ---------------------------------------------------------
    # PRUEBAS DE EJECUCIÓN DEL MOTOR
    # ---------------------------------------------------------

    def test_entry_uses_next_candle_open(self):
        data = self.make_execution_data()

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entry"], 101.0)

    def test_stop_and_target_preserve_original_signal_levels(self):
        data = self.make_execution_data()

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)

        trade = trades[0]

        self.assertAlmostEqual(trade["signal_reference"], 100.0)
        self.assertAlmostEqual(trade["entry"], 101.0)
        self.assertAlmostEqual(trade["stop"], 97.0)
        self.assertAlmostEqual(trade["target"], 109.0)
        self.assertAlmostEqual(trade["initial_risk_per_unit"], 4.0)

    def test_entry_is_rejected_if_open_is_at_or_below_original_stop(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("Open")] = 96.0
        data.iloc[5, data.columns.get_loc("High")] = 101.0
        data.iloc[5, data.columns.get_loc("Low")] = 95.0
        data.iloc[5, data.columns.get_loc("Close")] = 100.0

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(trades, [])

    def test_entry_is_rejected_if_open_is_at_or_above_original_target(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("Open")] = 109.0
        data.iloc[5, data.columns.get_loc("High")] = 110.0
        data.iloc[5, data.columns.get_loc("Low")] = 108.0
        data.iloc[5, data.columns.get_loc("Close")] = 109.0

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(trades, [])

    def test_stop_is_triggered(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("Low")] = 96.5

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "STOP")
        self.assertAlmostEqual(trades[0]["exit"], 97.0)

    def test_target_is_triggered(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("High")] = 109.5

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "TARGET")
        self.assertAlmostEqual(trades[0]["exit"], 109.0)

    def test_stop_has_priority_when_stop_and_target_are_hit(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("Low")] = 96.5
        data.iloc[5, data.columns.get_loc("High")] = 109.5

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "STOP")
        self.assertAlmostEqual(trades[0]["exit"], 97.0)

    def test_stop_gap_exits_at_open(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("High")] = 102.0
        data.iloc[5, data.columns.get_loc("Low")] = 100.0

        data.iloc[6, data.columns.get_loc("Open")] = 95.0
        data.iloc[6, data.columns.get_loc("High")] = 98.0
        data.iloc[6, data.columns.get_loc("Low")] = 94.0
        data.iloc[6, data.columns.get_loc("Close")] = 95.0

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["exit"], 95.0)
        self.assertTrue(backtest_v3._valid_ohlc(data))

    def test_target_gap_exits_at_target(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("High")] = 102.0
        data.iloc[5, data.columns.get_loc("Low")] = 100.0

        data.iloc[6, data.columns.get_loc("Open")] = 110.0
        data.iloc[6, data.columns.get_loc("High")] = 111.0
        data.iloc[6, data.columns.get_loc("Low")] = 109.5
        data.iloc[6, data.columns.get_loc("Close")] = 110.0

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["exit"], 109.0)
        self.assertTrue(backtest_v3._valid_ohlc(data))

    def test_trade_closes_at_max_hold(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("High")] = 102.0
        data.iloc[5, data.columns.get_loc("Low")] = 100.0

        data.iloc[7, data.columns.get_loc("Close")] = 100.5

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "MAX_HOLD")
        self.assertAlmostEqual(trades[0]["exit"], 100.5)
        self.assertTrue(backtest_v3._valid_ohlc(data))

    def test_engine_does_not_open_overlapping_trades(self):
        data = self.make_execution_data(rows=14)

        trades, error = self.run_execution_test(
            data,
            signal_indexes=(4, 5, 8),
        )

        self.assertIsNone(error)
        self.assertEqual(len(trades), 2)

        self.assertEqual(
            pd.Timestamp(trades[0]["signal_time"]),
            data.index[4],
        )

        self.assertEqual(
            pd.Timestamp(trades[1]["signal_time"]),
            data.index[8],
        )

    # ---------------------------------------------------------
    # PRUEBAS DEL CÁLCULO DE R
    # ---------------------------------------------------------

    def test_intraday_equity_trade_closes_at_session_end_before_overnight(self):
        first_session = pd.date_range(
            "2025-01-02T14:30:00Z",
            periods=26,
            freq="15min",
        )
        next_session = pd.date_range(
            "2025-01-03T14:30:00Z",
            periods=14,
            freq="15min",
        )
        index = first_session.append(next_session)
        data = pd.DataFrame(
            {
                "Open": [100.0] * len(index),
                "High": [101.0] * len(index),
                "Low": [99.0] * len(index),
                "Close": [100.0] * len(index),
                "Volume": [1000] * len(index),
                "ATR": [2.0] * len(index),
            },
            index=index,
        )
        data.iloc[5, data.columns.get_loc("Open")] = 101.0
        data.iloc[5, data.columns.get_loc("High")] = 102.0
        data.iloc[26, data.columns.get_loc("Open")] = 95.0
        data.iloc[26, data.columns.get_loc("High")] = 96.0
        data.iloc[26, data.columns.get_loc("Low")] = 94.0
        data.iloc[26, data.columns.get_loc("Close")] = 95.0

        trades, error = self.run_execution_test(
            data,
            max_bars=32,
        )

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "SESSION_CLOSE")
        self.assertEqual(trades[0]["exit_time"], str(first_session[-1]))
        self.assertAlmostEqual(trades[0]["exit"], 100.0)

    def test_stop_gap_result_r_can_be_below_minus_one(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("High")] = 102.0
        data.iloc[5, data.columns.get_loc("Low")] = 100.0

        data.iloc[6, data.columns.get_loc("Open")] = 95.0
        data.iloc[6, data.columns.get_loc("High")] = 96.0
        data.iloc[6, data.columns.get_loc("Low")] = 94.0
        data.iloc[6, data.columns.get_loc("Close")] = 95.0

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)

        trade = trades[0]

        self.assertEqual(trade["exit_reason"], "STOP_GAP")
        self.assertAlmostEqual(trade["exit"], 95.0)
        self.assertAlmostEqual(trade["result_R"], -1.5)

    def test_target_gap_result_r_uses_original_signal_levels(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("High")] = 102.0
        data.iloc[5, data.columns.get_loc("Low")] = 100.0

        data.iloc[6, data.columns.get_loc("Open")] = 110.0
        data.iloc[6, data.columns.get_loc("High")] = 111.0
        data.iloc[6, data.columns.get_loc("Low")] = 109.5
        data.iloc[6, data.columns.get_loc("Close")] = 110.0

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "TARGET_GAP")
        self.assertAlmostEqual(trades[0]["result_R"], 2.0)

    def test_intrabar_stop_result_is_minus_one_r(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("Low")] = 96.5

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "STOP")
        self.assertAlmostEqual(trades[0]["result_R"], -1.0)

    def test_intrabar_target_result_is_two_r(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("High")] = 109.5

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "TARGET")
        self.assertAlmostEqual(trades[0]["result_R"], 2.0)

    def test_stop_priority_result_is_minus_one_r(self):
        data = self.make_execution_data()

        data.iloc[5, data.columns.get_loc("Low")] = 96.5
        data.iloc[5, data.columns.get_loc("High")] = 109.5

        trades, error = self.run_execution_test(data)

        self.assertIsNone(error)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "STOP")
        self.assertAlmostEqual(trades[0]["result_R"], -1.0)


if __name__ == "__main__":
    unittest.main()
