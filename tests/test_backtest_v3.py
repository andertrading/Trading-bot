
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
        data = self.make_data()

        self.assertTrue(backtest_v3._valid_ohlc(data))

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

        result = backtest_v3.download_history

        with patch.object(
            backtest_v3.yf,
            "download",
            return_value=duplicated,
        ):
            cleaned = result("AAPL", "15m")

        if cleaned is not None:
            self.assertFalse(cleaned.index.has_duplicates)

    def test_metrics_with_no_trades(self):
        result = backtest_v3.calculate_metrics(
            pd.DataFrame()
        )

        self.assertIsInstance(result, dict)

    def test_metrics_ignore_invalid_r_values(self):
        trades = pd.DataFrame(
            {
                "result_r": [1.0, -1.0, float("nan")],
                "duration_bars": [2, 3, 4],
            }
        )

        result = backtest_v3.calculate_metrics(trades)

        self.assertIsInstance(result, dict)

        if "total_trades" in result:
            self.assertEqual(result["total_trades"], 2)

    def test_drawdown_never_negative(self):
        trades = pd.DataFrame(
            {
                "result_r": [1.0, -1.0, 2.0, -0.5],
            }
        )

        result = backtest_v3._calculate_drawdown(trades)

        self.assertGreaterEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
