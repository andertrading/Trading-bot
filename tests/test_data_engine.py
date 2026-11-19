
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd

from data_engine import (
    _asset_config,
    _normalise_dataframe,
    _remove_incomplete_candle,
    is_data_fresh,
)


class FrozenDateTime(datetime):

    @classmethod
    def now(cls, tz=None):
        return cls(
            2026, 11, 19, 13, 50,
            tzinfo=timezone.utc,
        )


class TestDailyDataFreshness(unittest.TestCase):

    def setUp(self):
        self.df = pd.DataFrame(
            {
                "Open": [100, 101, 102],
                "High": [102, 103, 104],
                "Low": [99, 100, 101],
                "Close": [101, 102, 103],
                "Volume": [1000, 1100, 1200],
            },
            index=pd.to_datetime(
                [
                    "2026-11-17T00:00:00Z",
                    "2026-11-18T00:00:00Z",
                    "2026-11-19T00:00:00Z",
                ]
            ),
        )

    def test_removes_current_incomplete_daily_candle(self):
        with patch("data_engine.datetime", FrozenDateTime):
            result = _remove_incomplete_candle(
                self.df,
                "1d",
                "TSLA",
            )

        self.assertEqual(len(result), 2)
        self.assertEqual(
            result.index[-1].date().isoformat(),
            "2026-11-18",
        )

    def test_us_daily_candle_is_fresh_in_utc(self):
        df = self.df.iloc[:2].copy()

        with patch("data_engine.datetime", FrozenDateTime):
            result = is_data_fresh(
                df,
                asset="TSLA",
                interval="1d",
            )

        self.assertTrue(result)

    def test_london_daily_candle_is_fresh_in_utc(self):
        df = self.df.iloc[:2].copy()
        df.index = pd.to_datetime(
            [
                "2026-11-17T08:00:00Z",
                "2026-11-18T08:00:00Z",
            ]
        )

        with patch("data_engine.datetime", FrozenDateTime):
            result = is_data_fresh(
                df,
                asset="CSPX",
                interval="1d",
            )

        self.assertTrue(result)


class TestDataNormalisation(unittest.TestCase):

    def setUp(self):
        self.index = pd.to_datetime(
            [
                "2026-11-18T13:00:00Z",
                "2026-11-18T13:15:00Z",
                "2026-11-18T13:30:00Z",
            ]
        )

        self.df = pd.DataFrame(
            {
                "open": [100, 101, 102],
                "high": [103, 104, 105],
                "low": [99, 100, 101],
                "close": [102, 103, 104],
                "volume": [1000, 1100, 1200],
            },
            index=self.index,
        )

    def test_normalises_column_names(self):
        result = _normalise_dataframe(self.df)

        self.assertIsNotNone(result)
        self.assertEqual(
            list(result.columns),
            ["Open", "High", "Low", "Close", "Volume"],
        )

    def test_rejects_infinite_prices(self):
        df = self.df.copy()
        df.loc[self.index[1], "close"] = np.inf

        result = _normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertTrue(np.isfinite(result["Close"]).all())

    def test_rejects_non_positive_prices(self):
        df = self.df.copy()
        df.loc[self.index[1], "open"] = 0

        result = _normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertTrue((result["Open"] > 0).all())

    def test_rejects_inconsistent_ohlc_candles(self):
        df = self.df.copy()
        df.loc[self.index[1], "high"] = 99

        result = _normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    def test_replaces_missing_volume_with_zero(self):
        df = self.df.drop(columns=["volume"])

        result = _normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertIn("Volume", result.columns)
        self.assertTrue((result["Volume"] == 0).all())

    def test_replaces_infinite_volume_with_zero(self):
        df = self.df.copy()
        df.loc[self.index[1], "volume"] = np.inf

        result = _normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(result.iloc[1]["Volume"], 0)

    def test_replaces_negative_volume_with_zero(self):
        df = self.df.copy()
        df.loc[self.index[1], "volume"] = -100

        result = _normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(result.iloc[1]["Volume"], 0)

    def test_removes_duplicate_timestamps(self):
        df = pd.concat(
            [
                self.df,
                self.df.iloc[[1]].assign(close=103.5),
            ]
        )

        result = _normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(result.index.duplicated().sum(), 0)
        self.assertEqual(result.loc[self.index[1], "Close"], 103.5)

    def test_converts_timestamps_to_utc(self):
        df = self.df.copy()
        df.index = df.index.tz_convert("Europe/Madrid")

        result = _normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(str(result.index.tz), "UTC")

    def test_rejects_missing_ohlc_columns(self):
        df = self.df.drop(columns=["high"])

        result = _normalise_dataframe(df)

        self.assertIsNone(result)


class TestAssetConfiguration(unittest.TestCase):

    def test_enabled_asset_is_accepted(self):
        assets = {
            "TSLA": {
                "enabled": True,
                "currency": "USD",
            }
        }

        with patch("data_engine.ASSETS", assets):
            result = _asset_config("TSLA")

        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "USD")

    def test_disabled_asset_is_rejected(self):
        assets = {
            "TSLA": {
                "enabled": False,
            }
        }

        with patch("data_engine.ASSETS", assets):
            result = _asset_config("TSLA")

        self.assertIsNone(result)

    def test_asset_without_enabled_flag_is_rejected(self):
        assets = {
            "TSLA": {
                "currency": "USD",
            }
        }

        with patch("data_engine.ASSETS", assets):
            result = _asset_config("TSLA")

        self.assertIsNone(result)

    def test_unknown_asset_is_rejected(self):
        with patch("data_engine.ASSETS", {}):
            result = _asset_config("UNKNOWN")

        self.assertIsNone(result)


class TestIntradayDataFreshness(unittest.TestCase):

    def test_rejects_future_timestamp(self):
        df = pd.DataFrame(
            {"Close": [100]},
            index=pd.to_datetime(
                ["2026-11-19T14:00:00Z"]
            ),
        )

        with patch("data_engine.datetime", FrozenDateTime):
            result = is_data_fresh(
                df,
                asset="BTC",
                interval="15m",
            )

        self.assertFalse(result)

    def test_rejects_stale_crypto_data(self):
        df = pd.DataFrame(
            {"Close": [100]},
            index=pd.to_datetime(
                ["2026-11-19T12:55:00Z"]
            ),
        )

        with patch("data_engine.datetime", FrozenDateTime):
            result = is_data_fresh(
                df,
                asset="BTC",
                interval="15m",
            )

        self.assertFalse(result)

    def test_rejects_missing_asset(self):
        df = pd.DataFrame(
            {"Close": [100]},
            index=pd.to_datetime(
                ["2026-11-19T13:45:00Z"]
            ),
        )

        with patch("data_engine.datetime", FrozenDateTime):
            result = is_data_fresh(
                df,
                interval="15m",
            )

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
