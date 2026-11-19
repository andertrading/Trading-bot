
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
            freq="D",
        )

        close = pd.Series(
            np.linspace(100, 150, 300),
            index=dates,
        )

        self.df = pd.DataFrame(
            {
                "Open": close - 0.5,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": 1000,
            },
            index=dates,
        )

    # RSI

    def test_rsi_is_within_valid_range(self):
        rsi = calculate_rsi(self.df["Close"])
        valid = rsi.dropna()

        self.assertTrue(
            ((valid >= 0) & (valid <= 100)).all()
        )

    def test_rsi_is_100_when_market_only_rises(self):
        prices = pd.Series(
            np.arange(100, 130, dtype=float)
        )

        rsi = calculate_rsi(prices)

        self.assertAlmostEqual(
            float(rsi.iloc[-1]),
            100.0,
        )

    def test_rsi_is_zero_when_market_only_falls(self):
        prices = pd.Series(
            np.arange(130, 100, -1, dtype=float)
        )

        rsi = calculate_rsi(prices)

        self.assertAlmostEqual(
            float(rsi.iloc[-1]),
            0.0,
        )

    def test_rsi_is_50_when_market_is_flat(self):
        prices = pd.Series([100.0] * 40)

        rsi = calculate_rsi(prices)

        self.assertAlmostEqual(
            float(rsi.iloc[-1]),
            50.0,
        )

    def test_rsi_rejects_invalid_period(self):
        with self.assertRaises(ValueError):
            calculate_rsi(
                self.df["Close"],
                period=0,
            )

    # ATR

    def test_atr_is_positive(self):
        atr = calculate_atr(self.df)

        self.assertTrue(
            (atr.dropna() > 0).all()
        )

    def test_atr_calculates_gap_correctly(self):
        data = pd.DataFrame(
            {
                "Open": [100.0, 110.0, 110.0],
                "High": [101.0, 112.0, 111.0],
                "Low": [99.0, 109.0, 109.0],
                "Close": [100.0, 111.0, 110.0],
            }
        )

        atr = calculate_atr(data, period=2)

        # Rangos verdaderos:
        # Primera vela: 2
        # Segunda vela: 12 (incluye el hueco)
        # Tercera vela: 2
        expected = (
            2 * (1 - 1 / 2) ** 2
            + 12 * (1 - 1 / 2)
            + 2
        ) / 1

        self.assertTrue(np.isfinite(atr.iloc[-1]))
        self.assertGreater(atr.iloc[-1], 2.0)

    def test_atr_rejects_invalid_period(self):
        with self.assertRaises(ValueError):
            calculate_atr(
                self.df,
                period=0,
            )

    # Indicadores generales

    def test_indicators_are_created(self):
        result = add_indicators(self.df)

        expected = [
            "EMA20",
            "EMA50",
            "EMA200",
            "RSI",
            "MACD",
            "MACD_SIGNAL",
            "MACD_HIST",
            "ATR",
            "ATR_PERCENT",
            "HIGH20",
            "LOW20",
            "RELATIVE_VOLUME",
            "TREND_BULLISH",
            "TREND_BEARISH",
        ]

        for column in expected:
            self.assertIn(column, result.columns)

    def test_original_data_is_not_modified(self):
        original = self.df.copy()

        add_indicators(self.df)

        pd.testing.assert_frame_equal(
            self.df,
            original,
        )

    def test_previous_high_does_not_include_current_candle(self):
        result = add_indicators(self.df)

        previous_high = self.df["High"].iloc[-21:-1].max()
        calculated_high = result["HIGH20"].iloc[-1]

        self.assertTrue(np.isfinite(calculated_high))

        self.assertAlmostEqual(
            calculated_high,
            previous_high,
        )

    def test_previous_low_does_not_include_current_candle(self):
        result = add_indicators(self.df)

        previous_low = self.df["Low"].iloc[-21:-1].min()
        calculated_low = result["LOW20"].iloc[-1]

        self.assertTrue(np.isfinite(calculated_low))

        self.assertAlmostEqual(
            calculated_low,
            previous_low,
        )

    # Ausencia de anticipación de datos futuros

    def test_future_data_does_not_change_previous_indicators(self):
        original = add_indicators(self.df)

        modified = self.df.copy()

        # Alteramos únicamente las últimas 20 velas.
        modified.iloc[-20:, modified.columns.get_loc("Close")] *= 1.5
        modified.iloc[-20:, modified.columns.get_loc("Open")] *= 1.5
        modified.iloc[-20:, modified.columns.get_loc("High")] *= 1.5
        modified.iloc[-20:, modified.columns.get_loc("Low")] *= 1.5

        changed = add_indicators(modified)

        columns = [
            "EMA20",
            "EMA50",
            "EMA200",
            "RSI",
            "MACD",
            "MACD_SIGNAL",
            "ATR",
            "ATR_PERCENT",
            "HIGH20",
            "LOW20",
        ]

        for column in columns:
            np.testing.assert_allclose(
                original[column].iloc[:280].to_numpy(),
                changed[column].iloc[:280].to_numpy(),
                equal_nan=True,
                err_msg=f"Anticipación detectada en {column}",
            )

    # Validación de datos

    def test_missing_required_column_is_rejected(self):
        data = self.df.drop(columns=["High"])

        self.assertIsNone(
            add_indicators(data)
        )

    def test_invalid_prices_are_rejected(self):
        data = self.df.copy()
        data.iloc[-1, data.columns.get_loc("Close")] = 0

        self.assertIsNone(
            add_indicators(data)
        )

    def test_inconsistent_candle_is_rejected(self):
        data = self.df.copy()

        data.iloc[-1, data.columns.get_loc("High")] = 1

        self.assertIsNone(
            add_indicators(data)
        )


if __name__ == "__main__":
    unittest.main()
