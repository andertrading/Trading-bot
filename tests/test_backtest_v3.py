
import unittest
from unittest.mock import patch

import pandas as pd

import backtest_v3


class TestBacktestV3(unittest.TestCase):

    def make_data(self, rows=230):
        index = pd.date_range(
            "2025-01-01",
            periods=rows,
            freq="15min",
        )

        close = [100.0 + i * 0.1 for i in range(rows)]

        return pd.DataFrame(
            {
                "Open": close,
                "High": [price + 1 for price in close],
                "Low": [price - 1 for price in close],
                "Close": close,
                "Volume": [1000] * rows,
            },
            index=index,
        )

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

    def test_duplicate_timestamps_are_removed(self):
        data = self.make_data()
        duplicated = pd.concat([data, data.iloc[[-1]]])

        with patch.object(
            backtest_v3.yf,
            "download",
            return_value=duplicated,
        ):
            cleaned = backtest_v3.download_history(
                "AAPL",
                "15m",
            )

        if cleaned is not None:
            self.assertFalse(cleaned.index.has_duplicates)

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


if __name__ == "__main__":
    unittest.main()
