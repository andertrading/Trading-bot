
import unittest
from unittest.mock import patch

import pandas as pd

from strategies import (
    MIN_BARS,
    evaluate_signal,
    evaluate_signal_at,
)


def make_valid_data(rows=MIN_BARS):
    return pd.DataFrame({
        "Close": [110.0] * rows,
        "EMA20": [105.0] * rows,
        "EMA50": [100.0] * rows,
        "EMA200": [95.0] * rows,
        "RSI": [55.0] * rows,
        "MACD": [2.0] * rows,
        "MACD_SIGNAL": [1.0] * rows,
        "ATR": [2.0] * rows,
        "ATR_PERCENT": [2.0] * rows,
        "HIGH20": [109.0] * rows,
        "relative_volume": [1.3] * rows,
    })


class TestStrategies(unittest.TestCase):

    def test_valid_data_generates_candidate(self):
        result = evaluate_signal_at(
            "AAPL", make_valid_data(), "1d"
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
            msg=str(result),
        )
        self.assertFalse(result.get("validated", False))

    def test_unsupported_timeframe_is_rejected(self):
        result = evaluate_signal_at(
            "AAPL", make_valid_data(), "5m"
        )

        self.assertEqual(result["action"], "NO TRADE")

    def test_insufficient_data_is_rejected(self):
        result = evaluate_signal_at(
            "AAPL",
            make_valid_data(MIN_BARS - 1),
            "1d",
        )

        self.assertEqual(result["action"], "NO TRADE")

    def test_missing_required_column_is_rejected(self):
        data = make_valid_data().drop(columns=["RSI"])

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertEqual(result["action"], "NO TRADE")

    def test_invalid_price_is_rejected(self):
        data = make_valid_data()
        data.loc[data.index[-1], "Close"] = 0

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertEqual(result["action"], "NO TRADE")

    def test_non_finite_atr_is_rejected(self):
        data = make_valid_data()
        data.loc[data.index[-1], "ATR"] = float("inf")

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertEqual(result["action"], "NO TRADE")

    def test_boolean_numeric_value_is_rejected(self):
        data = make_valid_data()
        data["RSI"] = data["RSI"].astype(object)
        data.loc[data.index[-1], "RSI"] = True

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertEqual(result["action"], "NO TRADE")

    def test_invalid_rsi_is_rejected(self):
        data = make_valid_data()
        data.loc[data.index[-1], "RSI"] = 105

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertEqual(result["action"], "NO TRADE")

    def test_excessive_volatility_is_rejected(self):
        data = make_valid_data()
        data.loc[data.index[-1], "ATR_PERCENT"] = 5.1

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertEqual(result["action"], "NO TRADE")

    def test_risk_reward_is_at_least_two(self):
        result = evaluate_signal_at(
            "AAPL", make_valid_data(), "1d"
        )

        if result["action"] == "BUY CANDIDATE":
            self.assertGreaterEqual(result["risk_reward"], 2.0)

    def test_indicator_exception_is_handled(self):
        with patch(
            "strategies.add_indicators",
            side_effect=ValueError("Indicadores no disponibles"),
        ):
            result = evaluate_signal(
                "AAPL",
                make_valid_data(),
                "1d",
            )

        self.assertEqual(result["action"], "NO TRADE")


if __name__ == "__main__":
    unittest.main()
