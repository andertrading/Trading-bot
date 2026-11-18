
import unittest

import numpy as np
import pandas as pd

from indicators import (
    calculate_rsi,
    calculate_atr,
    add_indicators,
)


class TestIndicators(unittest.TestCase):

    def setUp(self):
        dates = pd.date_range(
            "2025-01-01",
            periods=300,
            freq="D"
        )

        close = pd.Series(
            np.linspace(100, 150, 300)
        )

        self.df = pd.DataFrame({
            "Open": close - 0.5,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1000,
        }, index=dates)

    def test_rsi_is_within_valid_range(self):
        rsi = calculate_rsi(self.df["Close"])

        valid = rsi.dropna()

        self.assertTrue(
            ((valid >= 0) & (valid <= 100)).all()
        )

    def test_atr_is_positive(self):
        atr = calculate_atr(self.df)

        self.assertTrue(
            (atr.dropna() > 0).all()
        )

    def test_indicators_are_created(self):
        result = add_indicators(self.df)

        expected = [
            "EMA20",
            "EMA50",
            "EMA200",
            "RSI",
            "MACD",
            "MACD_SIGNAL",
            "ATR",
            "HIGH20",
            "LOW20",
            "RELATIVE_VOLUME",
        ]

        for column in expected:
            self.assertIn(column, result.columns)

    def test_original_data_is_not_modified(self):
        original = self.df.copy()

        add_indicators(self.df)

        pd.testing.assert_frame_equal(
            self.df,
            original
        )

    def test_previous_high_does_not_include_current_candle(self):
        result = add_indicators(self.df)

        self.assertAlmostEqual(
            result["HIGH20"].iloc[-1],
            self.df["High"].iloc[-21:-1].max()
        )


if __name__ == "__main__":
    unittest.main()
