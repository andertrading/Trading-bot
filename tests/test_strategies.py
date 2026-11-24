
import unittest
from unittest.mock import patch

import pandas as pd

from research.segmented_crypto_history import split_contiguous_segments
from strategies import (
    MIN_BARS,
    MIN_SCORE,
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
            "AAPL",
            make_valid_data(),
            "1d",
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
            msg=str(result),
        )

        self.assertFalse(result["validated"])
        self.assertFalse(result["tradable"])

        self.assertEqual(
            result["status"],
            "PENDING_VALIDATION",
        )

    def test_bearish_main_trend_is_rejected(self):
        data = make_valid_data()

        data.loc[data.index[-1], "EMA50"] = 90.0
        data.loc[data.index[-1], "EMA200"] = 95.0

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

        self.assertIn(
            "Tendencia principal no alcista",
            result["reason"],
        )

    def test_equal_ema50_and_ema200_is_rejected(self):
        data = make_valid_data()

        data.loc[data.index[-1], "EMA50"] = 95.0
        data.loc[data.index[-1], "EMA200"] = 95.0

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

        self.assertIn(
            "Tendencia principal no alcista",
            result["reason"],
        )

    def test_bullish_main_trend_is_required_for_candidate(self):
        data = make_valid_data()

        data.loc[data.index[-1], "EMA50"] = 100.0
        data.loc[data.index[-1], "EMA200"] = 95.0

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
        )

        self.assertIn(
            "Tendencia principal alcista",
            result["reasons"],
        )

    def test_valid_candidate_works_on_intraday_timeframe(self):
        result = evaluate_signal_at(
            "AAPL",
            make_valid_data(),
            "15m",
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
            msg=str(result),
        )

    def test_unsupported_timeframe_is_rejected(self):
        result = evaluate_signal_at(
            "AAPL",
            make_valid_data(),
            "5m",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

    def test_insufficient_data_is_rejected(self):
        result = evaluate_signal_at(
            "AAPL",
            make_valid_data(MIN_BARS - 1),
            "1d",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

    def test_missing_required_column_is_rejected(self):
        data = make_valid_data().drop(
            columns=["RSI"]
        )

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

        self.assertIn(
            "Falta indicador",
            result["reason"],
        )

    def test_invalid_price_is_rejected(self):
        data = make_valid_data()

        data.loc[data.index[-1], "Close"] = 0

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

    def test_negative_price_is_rejected(self):
        data = make_valid_data()

        data.loc[data.index[-1], "Close"] = -10

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

    def test_non_finite_atr_is_rejected(self):
        invalid_values = (
            float("nan"),
            float("inf"),
            float("-inf"),
        )

        for value in invalid_values:
            with self.subTest(value=value):
                data = make_valid_data()

                data.loc[data.index[-1], "ATR"] = value

                result = evaluate_signal_at(
                    "AAPL",
                    data,
                    "1d",
                )

                self.assertEqual(
                    result["action"],
                    "NO TRADE",
                )

    def test_boolean_numeric_value_is_rejected(self):
        data = make_valid_data()

        data["RSI"] = data["RSI"].astype(object)

        data.loc[data.index[-1], "RSI"] = True

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

    def test_invalid_rsi_is_rejected(self):
        invalid_values = (-1, 101, 105)

        for value in invalid_values:
            with self.subTest(value=value):
                data = make_valid_data()

                data.loc[data.index[-1], "RSI"] = value

                result = evaluate_signal_at(
                    "AAPL",
                    data,
                    "1d",
                )

                self.assertEqual(
                    result["action"],
                    "NO TRADE",
                )

    def test_excessive_volatility_is_rejected(self):
        data = make_valid_data()

        data.loc[
            data.index[-1],
            "ATR_PERCENT",
        ] = 5.1

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

    def test_negative_volatility_is_rejected(self):
        data = make_valid_data()

        data.loc[
            data.index[-1],
            "ATR_PERCENT",
        ] = -0.1

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

    def test_risk_reward_is_exactly_two(self):
        result = evaluate_signal_at(
            "AAPL",
            make_valid_data(),
            "1d",
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
        )

        self.assertAlmostEqual(
            result["risk_reward"],
            2.0,
        )

    def test_stop_and_target_are_correct(self):
        result = evaluate_signal_at(
            "AAPL",
            make_valid_data(),
            "1d",
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
        )

        self.assertAlmostEqual(
            result["entry_reference"],
            110.0,
        )

        self.assertAlmostEqual(
            result["stop"],
            107.0,
        )

        self.assertAlmostEqual(
            result["target"],
            116.0,
        )

    def test_volume_is_optional(self):
        data = make_valid_data().drop(
            columns=["relative_volume"]
        )

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
            msg=str(result),
        )

    def test_valid_volume_bonus_survives_gap_segmentation(self):
        data = make_valid_data(rows=MIN_BARS * 2)
        first = pd.date_range(
            "2026-01-01", periods=MIN_BARS, freq="15min", tz="UTC"
        )
        second = pd.date_range(
            first[-1] + pd.Timedelta(hours=1),
            periods=MIN_BARS,
            freq="15min",
        )
        data.index = first.append(second)
        data["Volume"] = 10.0
        data.iloc[MIN_BARS + 5, data.columns.get_loc("Volume")] = 0.0
        data.attrs.update({
            "volume_quality": "contains_zero_bars",
            "volume_column_present": True,
            "volume_zero_count_before_normalization": 1,
            "volume_positive_count_before_normalization": len(data) - 1,
            "volume_invalid_count_before_normalization": 0,
        })

        segments, _ = split_contiguous_segments(data, "15m")

        self.assertEqual(len(segments), 2)
        expected = [
            ("available", 0, MIN_BARS),
            ("contains_zero_bars", 1, MIN_BARS - 1),
        ]
        for segment, (quality, zero_count, positive_count) in zip(segments, expected):
            with self.subTest(quality=quality):
                self.assertEqual(segment.attrs["volume_quality"], quality)
                self.assertEqual(
                    segment.attrs["volume_zero_count_before_normalization"],
                    zero_count,
                )
                self.assertEqual(
                    segment.attrs["volume_positive_count_before_normalization"],
                    positive_count,
                )
                result = evaluate_signal_at("AAPL", segment, "15m")
                self.assertIn("Volumen superior a la media", result["reasons"])

    def test_volume_bonus_requires_explicit_valid_provenance(self):
        data = make_valid_data()
        data.attrs.update({
            "volume_quality": "available",
            "volume_column_present": True,
            "volume_invalid_count_before_normalization": 0,
            "volume_positive_count_before_normalization": MIN_BARS,
            "volume_zero_count_before_normalization": 0,
        })

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertIn("Volumen superior a la media", result["reasons"])

    def test_partial_invalid_volume_provenance_suppresses_bonus(self):
        data = make_valid_data()
        data.attrs.update({
            "volume_quality": "invalid_or_missing_values",
            "volume_column_present": True,
            "volume_invalid_count_before_normalization": 1,
            "volume_positive_count_before_normalization": MIN_BARS - 1,
            "volume_zero_count_before_normalization": 0,
        })

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertEqual(result["action"], "BUY CANDIDATE", msg=str(result))
        self.assertNotIn("Volumen superior a la media", result["reasons"])

    def test_missing_or_untrusted_volume_provenance_suppresses_bonus(self):
        metadata_cases = (
            {},
            {
                "volume_quality": "unknown",
                "volume_column_present": True,
                "volume_invalid_count_before_normalization": 0,
                "volume_positive_count_before_normalization": MIN_BARS,
                "volume_zero_count_before_normalization": 0,
            },
            {
                "volume_quality": "available",
                "volume_column_present": False,
                "volume_invalid_count_before_normalization": 0,
                "volume_positive_count_before_normalization": MIN_BARS,
                "volume_zero_count_before_normalization": 0,
            },
            {
                "volume_quality": "available",
                "volume_column_present": True,
                "volume_invalid_count_before_normalization": True,
                "volume_positive_count_before_normalization": MIN_BARS,
                "volume_zero_count_before_normalization": 0,
            },
        )

        for metadata in metadata_cases:
            with self.subTest(metadata=metadata):
                data = make_valid_data()
                data.attrs.update(metadata)
                result = evaluate_signal_at("AAPL", data, "1d")
                self.assertNotIn(
                    "Volumen superior a la media",
                    result.get("reasons", []),
                )

    def test_inconsistent_volume_provenance_counts_suppress_bonus(self):
        data = make_valid_data()
        data.attrs.update({
            "volume_quality": "available",
            "volume_column_present": True,
            "volume_invalid_count_before_normalization": 0,
            "volume_positive_count_before_normalization": MIN_BARS - 1,
            "volume_zero_count_before_normalization": 0,
        })

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertNotIn("Volumen superior a la media", result.get("reasons", []))

    def test_genuine_zero_volume_bars_can_still_qualify(self):
        data = make_valid_data()
        data.attrs.update({
            "volume_quality": "contains_zero_bars",
            "volume_column_present": True,
            "volume_invalid_count_before_normalization": 0,
            "volume_positive_count_before_normalization": MIN_BARS - 2,
            "volume_zero_count_before_normalization": 2,
        })

        result = evaluate_signal_at("AAPL", data, "1d")

        self.assertIn("Volumen superior a la media", result["reasons"])

    def test_all_zero_volume_does_not_receive_optional_volume_score(self):
        close = pd.Series([100.0 + index * 0.05 for index in range(MIN_BARS)])
        data = pd.DataFrame({
            "Open": close,
            "High": close + 0.01,
            "Low": close - 0.01,
            "Close": close,
            "Volume": 0.0,
        })

        result = evaluate_signal("AAPL", data, "1d")

        self.assertEqual(result["action"], "BUY CANDIDATE", msg=str(result))
        self.assertNotIn("Volumen superior a la media", result["reasons"])
        self.assertFalse(result["validated"])
        self.assertFalse(result["tradable"])

    def test_low_volume_does_not_add_volume_score(self):
        data = make_valid_data()

        data.loc[
            data.index[-1],
            "relative_volume",
        ] = 0.5

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
        )

        self.assertNotIn(
            "Volumen superior a la media",
            result["reasons"],
        )

    def test_candidate_score_meets_minimum(self):
        result = evaluate_signal_at(
            "AAPL",
            make_valid_data(),
            "1d",
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
        )

        self.assertGreaterEqual(
            result["score"],
            MIN_SCORE,
        )

    def test_invalid_index_is_rejected(self):
        invalid_indices = (
            True,
            1.5,
            "last",
            None,
        )

        for index in invalid_indices:
            with self.subTest(index=index):
                result = evaluate_signal_at(
                    "AAPL",
                    make_valid_data(),
                    "1d",
                    index=index,
                )

                self.assertEqual(
                    result["action"],
                    "NO TRADE",
                )

    def test_out_of_range_index_is_rejected(self):
        result = evaluate_signal_at(
            "AAPL",
            make_valid_data(),
            "1d",
            index=MIN_BARS,
        )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

    def test_negative_index_selects_previous_candle(self):
        data = make_valid_data()

        data.loc[
            data.index[-1],
            "EMA50",
        ] = 90.0

        result = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
            index=-2,
        )

        self.assertEqual(
            result["action"],
            "BUY CANDIDATE",
        )

    def test_asset_and_dataframe_argument_orders(self):
        data = make_valid_data()

        result_a = evaluate_signal_at(
            data,
            "AAPL",
            "1d",
        )

        result_b = evaluate_signal_at(
            "AAPL",
            data,
            "1d",
        )

        self.assertEqual(
            result_a["action"],
            result_b["action"],
        )

        self.assertEqual(
            result_a["score"],
            result_b["score"],
        )

    def test_indicator_exception_is_handled(self):
        with patch(
            "strategies.add_indicators",
            side_effect=ValueError(
                "Indicadores no disponibles"
            ),
        ):
            result = evaluate_signal(
                "AAPL",
                make_valid_data(),
                "1d",
            )

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )

        self.assertIn(
            "Error al calcular indicadores",
            result["reason"],
        )


if __name__ == "__main__":
    unittest.main()
