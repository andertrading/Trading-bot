
import unittest
from datetime import datetime, timezone, date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd

import data_engine as engine


# ---------------------------------------------------------------------
# RELOJES CONTROLADOS PARA PRUEBAS
# ---------------------------------------------------------------------

class FrozenDateTime(datetime):

    @classmethod
    def now(cls, tz=None):
        value = cls(
            2026, 11, 19, 13, 50,
            tzinfo=timezone.utc,
        )

        if tz is not None:
            return value.astimezone(tz)

        return value.replace(tzinfo=None)


class FrozenDateTimeBeforeOpen(datetime):

    @classmethod
    def now(cls, tz=None):
        value = cls(
            2026, 11, 19, 13, 0,
            tzinfo=timezone.utc,
        )

        if tz is not None:
            return value.astimezone(tz)

        return value.replace(tzinfo=None)


class FrozenDateTimeDuringSession(datetime):

    @classmethod
    def now(cls, tz=None):
        value = cls(
            2026, 11, 19, 15, 50,
            tzinfo=timezone.utc,
        )

        if tz is not None:
            return value.astimezone(tz)

        return value.replace(tzinfo=None)


class FrozenDateTimeAfterClose(datetime):

    @classmethod
    def now(cls, tz=None):
        value = cls(
            2026, 11, 19, 22, 0,
            tzinfo=timezone.utc,
        )

        if tz is not None:
            return value.astimezone(tz)

        return value.replace(tzinfo=None)


class FrozenDateTimeEarlyClose(datetime):

    @classmethod
    def now(cls, tz=None):
        value = cls(
            2026, 11, 27, 18, 30,
            tzinfo=timezone.utc,
        )

        if tz is not None:
            return value.astimezone(tz)

        return value.replace(tzinfo=None)


# ---------------------------------------------------------------------
# UTILIDADES DE PRUEBA
# ---------------------------------------------------------------------

def make_ohlcv(index):
    index = pd.DatetimeIndex(index)

    return pd.DataFrame(
        {
            "Open": np.full(len(index), 100.0),
            "High": np.full(len(index), 102.0),
            "Low": np.full(len(index), 99.0),
            "Close": np.full(len(index), 101.0),
            "Volume": np.full(len(index), 1000.0),
        },
        index=index,
    )


# ---------------------------------------------------------------------
# NORMALIZACION OHLCV
# ---------------------------------------------------------------------

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
        result = engine._normalise_dataframe(self.df)

        self.assertIsNotNone(result)

        self.assertEqual(
            list(result.columns),
            ["Open", "High", "Low", "Close", "Volume"],
        )

    def test_rejects_none(self):
        self.assertIsNone(
            engine._normalise_dataframe(None)
        )

    def test_rejects_non_dataframe(self):
        self.assertIsNone(
            engine._normalise_dataframe([1, 2, 3])
        )

    def test_rejects_empty_dataframe(self):
        self.assertIsNone(
            engine._normalise_dataframe(pd.DataFrame())
        )

    def test_rejects_infinite_prices(self):
        df = self.df.copy()
        df.loc[self.index[1], "close"] = np.inf

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_rejects_negative_infinite_prices(self):
        df = self.df.copy()
        df.loc[self.index[1], "close"] = -np.inf

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_rejects_missing_prices(self):
        df = self.df.copy()
        df.loc[self.index[1], "close"] = np.nan

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_rejects_non_numeric_prices(self):
        df = self.df.copy()
        df["close"] = [102, "invalid", 104]

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_rejects_zero_prices(self):
        df = self.df.copy()
        df.loc[self.index[1], "open"] = 0

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_rejects_negative_prices(self):
        df = self.df.copy()
        df.loc[self.index[1], "close"] = -10

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_rejects_inconsistent_ohlc_candles(self):
        df = self.df.copy()
        df.loc[self.index[1], "high"] = 99

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_rejects_low_above_open(self):
        df = self.df.copy()
        df.loc[self.index[1], "low"] = 102

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_replaces_missing_volume_with_zero(self):
        df = self.df.drop(columns=["volume"])

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertIn("Volume", result.columns)
        self.assertTrue((result["Volume"] == 0).all())

    def test_replaces_infinite_volume_with_zero(self):
        df = self.df.copy()
        df.loc[self.index[1], "volume"] = np.inf

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(result.iloc[1]["Volume"], 0)

    def test_replaces_negative_infinite_volume_with_zero(self):
        df = self.df.copy()
        df.loc[self.index[1], "volume"] = -np.inf

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(result.iloc[1]["Volume"], 0)

    def test_replaces_negative_volume_with_zero(self):
        df = self.df.copy()
        df.loc[self.index[1], "volume"] = -100

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(result.iloc[1]["Volume"], 0)

    def test_replaces_nan_volume_with_zero(self):
        df = self.df.copy()
        df.loc[self.index[1], "volume"] = np.nan

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(result.iloc[1]["Volume"], 0)

    def test_removes_identical_duplicate_timestamps(self):
        df = pd.concat(
            [
                self.df,
                self.df.iloc[[1]].copy(),
            ]
        )

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3)
        self.assertEqual(
            result.index.duplicated().sum(),
            0,
        )

    def test_rejects_conflicting_duplicate_timestamps(self):
        df = pd.concat(
            [
                self.df,
                self.df.iloc[[1]].assign(close=103.5),
            ]
        )

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_converts_timestamps_to_utc(self):
        df = self.df.copy()
        df.index = df.index.tz_convert("Europe/Madrid")

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(str(result.index.tz), "UTC")

    def test_localises_naive_timestamps_as_utc(self):
        df = self.df.copy()
        df.index = df.index.tz_localize(None)

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(str(result.index.tz), "UTC")

    def test_rejects_missing_ohlc_columns(self):
        df = self.df.drop(columns=["high"])

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_rejects_invalid_timestamp(self):
        df = self.df.copy()
        df.index = [
            "2026-11-18T13:00:00Z",
            "fecha_invalida",
            "2026-11-18T13:30:00Z",
        ]

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_sorts_timestamps_chronologically(self):
        df = self.df.iloc[::-1].copy()

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertTrue(result.index.is_monotonic_increasing)

    def test_rejects_duplicate_columns_after_normalisation(self):
        df = self.df.copy()
        df["OPEN"] = df["open"]

        result = engine._normalise_dataframe(df)

        self.assertIsNone(result)

    def test_normalises_multiindex_columns(self):
        columns = pd.MultiIndex.from_product(
            [
                ["Open", "High", "Low", "Close", "Volume"],
                ["TSLA"],
            ]
        )

        df = pd.DataFrame(
            [
                [100, 102, 99, 101, 1000],
                [101, 103, 100, 102, 1100],
                [102, 104, 101, 103, 1200],
            ],
            index=self.index,
            columns=columns,
        )

        result = engine._normalise_dataframe(df)

        self.assertIsNotNone(result)
        self.assertEqual(
            list(result.columns),
            ["Open", "High", "Low", "Close", "Volume"],
        )
        self.assertEqual(len(result), 3)


# ---------------------------------------------------------------------
# CONFIGURACION DE ACTIVOS
# ---------------------------------------------------------------------

class TestAssetConfiguration(unittest.TestCase):

    def test_enabled_asset_is_accepted(self):
        assets = {
            "TSLA": {
                "enabled": True,
                "currency": "USD",
            }
        }

        with patch.object(engine, "ASSETS", assets):
            result = engine._asset_config("TSLA")

        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "USD")

    def test_disabled_asset_is_rejected(self):
        assets = {
            "TSLA": {
                "enabled": False,
            }
        }

        with patch.object(engine, "ASSETS", assets):
            result = engine._asset_config("TSLA")

        self.assertIsNone(result)

    def test_asset_without_enabled_flag_is_rejected(self):
        assets = {
            "TSLA": {
                "currency": "USD",
            }
        }

        with patch.object(engine, "ASSETS", assets):
            result = engine._asset_config("TSLA")

        self.assertIsNone(result)

    def test_unknown_asset_is_rejected(self):
        with patch.object(engine, "ASSETS", {}):
            result = engine._asset_config("UNKNOWN")

        self.assertIsNone(result)

    def test_invalid_asset_configuration_is_rejected(self):
        assets = {
            "TSLA": "invalid",
        }

        with patch.object(engine, "ASSETS", assets):
            result = engine._asset_config("TSLA")

        self.assertIsNone(result)


# ---------------------------------------------------------------------
# VELAS INCOMPLETAS
# ---------------------------------------------------------------------

class TestIncompleteCandles(unittest.TestCase):

    def setUp(self):
        self.daily_df = make_ohlcv(
            pd.to_datetime(
                [
                    "2026-11-17T00:00:00Z",
                    "2026-11-18T00:00:00Z",
                    "2026-11-19T00:00:00Z",
                ]
            )
        )

    def test_removes_current_incomplete_daily_candle(self):
        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine._remove_incomplete_candle(
                self.daily_df,
                "1d",
                "TSLA",
            )

        self.assertEqual(len(result), 2)

        self.assertEqual(
            result.index[-1].date().isoformat(),
            "2026-11-18",
        )

    def test_removes_current_daily_candle_before_market_close(self):
        with patch.object(
            engine,
            "datetime",
            FrozenDateTimeDuringSession,
        ):
            result = engine._remove_incomplete_candle(
                self.daily_df,
                "1d",
                "TSLA",
            )

        self.assertEqual(len(result), 2)

    def test_keeps_completed_daily_candle_after_market_close(self):
        with patch.object(
            engine,
            "datetime",
            FrozenDateTimeAfterClose,
        ):
            result = engine._remove_incomplete_candle(
                self.daily_df,
                "1d",
                "TSLA",
            )

        self.assertEqual(len(result), 3)

    def test_empty_dataframe_is_returned_unchanged(self):
        df = pd.DataFrame()

        result = engine._remove_incomplete_candle(
            df,
            "15m",
            "BTC",
        )

        self.assertTrue(result.empty)

    def test_none_is_returned_unchanged(self):
        self.assertIsNone(
            engine._remove_incomplete_candle(
                None,
                "15m",
                "BTC",
            )
        )

    def test_removes_current_incomplete_crypto_candle(self):
        now = datetime(
            2026, 11, 19, 13, 50,
            tzinfo=timezone.utc,
        )

        current_start = now.replace(
            minute=45,
            second=0,
            microsecond=0,
        )

        index = pd.DatetimeIndex(
            [
                current_start - timedelta(minutes=30),
                current_start - timedelta(minutes=15),
                current_start,
            ]
        )

        df = make_ohlcv(index)

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine._remove_incomplete_candle(
                df,
                "15m",
                "BTC",
            )

        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------
# FRESCURA DE DATOS DIARIOS
# ---------------------------------------------------------------------

class TestDailyDataFreshness(unittest.TestCase):

    def setUp(self):
        self.df = make_ohlcv(
            pd.to_datetime(
                [
                    "2026-11-17T00:00:00Z",
                    "2026-11-18T00:00:00Z",
                    "2026-11-19T00:00:00Z",
                ]
            )
        )

    def test_us_daily_candle_is_fresh_before_current_session(self):
        df = self.df.iloc[:2].copy()

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                df,
                asset="TSLA",
                interval="1d",
            )

        self.assertTrue(result)

    def test_london_daily_candle_is_fresh_before_current_session(self):
        df = self.df.iloc[:2].copy()

        df.index = pd.to_datetime(
            [
                "2026-11-17T08:00:00Z",
                "2026-11-18T08:00:00Z",
            ]
        )

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                df,
                asset="CSPX",
                interval="1d",
            )

        self.assertTrue(result)

    def test_daily_data_rejects_old_session(self):
        df = self.df.iloc[:1].copy()

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                df,
                asset="TSLA",
                interval="1d",
            )

        self.assertFalse(result)

    def test_daily_data_is_fresh_after_us_market_close(self):
        df = self.df.iloc[2:].copy()

        with patch.object(
            engine,
            "datetime",
            FrozenDateTimeAfterClose,
        ):
            result = engine.is_data_fresh(
                df,
                asset="TSLA",
                interval="1d",
            )

        self.assertTrue(result)

    def test_daily_data_rejects_future_timestamp(self):
        df = self.df.iloc[:1].copy()
        df.index = pd.to_datetime(
            ["2026-11-20T00:00:00Z"]
        )

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                df,
                asset="TSLA",
                interval="1d",
            )

        self.assertFalse(result)

    def test_daily_data_rejects_missing_asset(self):
        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                self.df,
                interval="1d",
            )

        self.assertFalse(result)

    def test_daily_data_rejects_unknown_interval(self):
        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                self.df,
                asset="TSLA",
                interval="5m",
            )

        self.assertFalse(result)


# ---------------------------------------------------------------------
# FRESCURA DE DATOS INTRADIA
# ---------------------------------------------------------------------

class TestIntradayDataFreshness(unittest.TestCase):

    def test_rejects_future_timestamp(self):
        df = make_ohlcv(
            pd.to_datetime(
                ["2026-11-19T14:00:00Z"]
            )
        )

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                df,
                asset="BTC",
                interval="15m",
            )

        self.assertFalse(result)

    def test_rejects_stale_crypto_data(self):
        df = make_ohlcv(
            pd.to_datetime(
                ["2026-11-19T12:55:00Z"]
            )
        )

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                df,
                asset="BTC",
                interval="15m",
            )

        self.assertFalse(result)

    def test_accepts_fresh_crypto_data(self):
        df = make_ohlcv(
            pd.to_datetime(
                ["2026-11-19T13:45:00Z"]
            )
        )

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                df,
                asset="BTC",
                interval="15m",
            )

        self.assertTrue(result)

    def test_rejects_missing_asset(self):
        df = make_ohlcv(
            pd.to_datetime(
                ["2026-11-19T13:45:00Z"]
            )
        )

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                df,
                interval="15m",
            )

        self.assertFalse(result)

    def test_rejects_unknown_asset(self):
        df = make_ohlcv(
            pd.to_datetime(
                ["2026-11-19T13:45:00Z"]
            )
        )

        with patch.object(
            engine,
            "ASSETS",
            {},
        ):
            result = engine.is_data_fresh(
                df,
                asset="UNKNOWN",
                interval="15m",
            )

        self.assertFalse(result)

    def test_rejects_unknown_interval(self):
        df = make_ohlcv(
            pd.to_datetime(
                ["2026-11-19T13:45:00Z"]
            )
        )

        with patch.object(engine, "datetime", FrozenDateTime):
            result = engine.is_data_fresh(
                df,
                asset="BTC",
                interval="5m",
            )

        self.assertFalse(result)

    def test_accepts_expected_us_intraday_timestamp(self):
        df = make_ohlcv(
            pd.to_datetime(
                ["2026-11-19T15:45:00Z"]
            )
        )

        with patch.object(
            engine,
            "datetime",
            FrozenDateTimeDuringSession,
        ):
            result = engine.is_data_fresh(
                df,
                asset="TSLA",
                interval="15m",
            )

        self.assertTrue(result)

    def test_rejects_old_us_intraday_timestamp(self):
        df = make_ohlcv(
            pd.to_datetime(
                ["2026-11-19T14:30:00Z"]
            )
        )

        with patch.object(
            engine,
            "datetime",
            FrozenDateTimeDuringSession,
        ):
            result = engine.is_data_fresh(
                df,
                asset="TSLA",
                interval="15m",
            )

        self.assertFalse(result)


# ---------------------------------------------------------------------
# HUECOS INTRADIA
# ---------------------------------------------------------------------

class TestIntradayGaps(unittest.TestCase):

    def test_accepts_continuous_crypto_data(self):
        index = pd.date_range(
            "2026-11-19T12:00:00Z",
            periods=10,
            freq="15min",
        )

        df = make_ohlcv(index)

        self.assertFalse(
            engine._has_intraday_gaps(df, "BTC")
        )

    def test_rejects_missing_crypto_candle(self):
        index = pd.date_range(
            "2026-11-19T12:00:00Z",
            periods=10,
            freq="15min",
        ).delete(5)

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "BTC")
        )

    def test_rejects_empty_dataframe(self):
        self.assertTrue(
            engine._has_intraday_gaps(
                pd.DataFrame(),
                "BTC",
            )
        )

    def test_rejects_none_dataframe(self):
        self.assertTrue(
            engine._has_intraday_gaps(
                None,
                "BTC",
            )
        )

    def test_accepts_continuous_us_session(self):
        index = pd.date_range(
            "2026-11-18T14:30:00Z",
            periods=26,
            freq="15min",
        )

        df = make_ohlcv(index)

        self.assertFalse(
            engine._has_intraday_gaps(df, "TSLA")
        )

    def test_rejects_missing_us_session_candle(self):
        index = pd.date_range(
            "2026-11-18T14:30:00Z",
            periods=26,
            freq="15min",
        ).delete(5)

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "TSLA")
        )

    def test_rejects_missing_first_us_session_candle(self):
        index = pd.date_range(
            "2026-11-18T14:30:00Z",
            periods=26,
            freq="15min",
        ).delete(0)

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "TSLA")
        )

    def test_rejects_missing_last_us_session_candle(self):
        index = pd.date_range(
            "2026-11-18T14:30:00Z",
            periods=26,
            freq="15min",
        ).delete(-1)

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "TSLA")
        )

    def test_accepts_continuous_london_session(self):
        index = pd.date_range(
            "2026-11-18T08:00:00Z",
            periods=34,
            freq="15min",
        )

        df = make_ohlcv(index)

        self.assertFalse(
            engine._has_intraday_gaps(df, "CSPX")
        )

    def test_rejects_missing_london_session_candle(self):
        index = pd.date_range(
            "2026-11-18T08:00:00Z",
            periods=34,
            freq="15min",
        ).delete(10)

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "CSPX")
        )

    def test_rejects_us_candle_before_market_open(self):
        index = pd.date_range(
            "2026-11-18T14:15:00Z",
            periods=10,
            freq="15min",
        )

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "TSLA")
        )

    def test_rejects_us_candle_after_market_close(self):
        index = pd.date_range(
            "2026-11-18T21:00:00Z",
            periods=2,
            freq="15min",
        )

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "TSLA")
        )

    def test_rejects_crypto_gap_of_thirty_minutes(self):
        index = pd.to_datetime(
            [
                "2026-11-19T12:00:00Z",
                "2026-11-19T12:15:00Z",
                "2026-11-19T12:45:00Z",
            ]
        )

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "BTC")
        )

    def test_rejects_weekend_us_candles(self):
        index = pd.date_range(
            "2026-11-21T14:30:00Z",
            periods=4,
            freq="15min",
        )

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "TSLA")
        )

    # REGRESIONES NUEVAS

    def test_rejects_single_candle_us_session(self):
        index = pd.to_datetime(
            ["2026-11-18T14:30:00Z"]
        )

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "TSLA")
        )

    def test_rejects_single_candle_london_session(self):
        index = pd.to_datetime(
            ["2026-11-18T08:00:00Z"]
        )

        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "CSPX")
        )

    def test_rejects_missing_entire_us_session(self):
        first_session = pd.date_range(
            "2026-11-17T14:30:00Z",
            periods=26,
            freq="15min",
        )

        third_session = pd.date_range(
            "2026-11-19T14:30:00Z",
            periods=26,
            freq="15min",
        )

        index = first_session.append(third_session)
        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "TSLA")
        )

    def test_rejects_missing_entire_london_session(self):
        first_session = pd.date_range(
            "2026-11-17T08:00:00Z",
            periods=34,
            freq="15min",
        )

        third_session = pd.date_range(
            "2026-11-19T08:00:00Z",
            periods=34,
            freq="15min",
        )

        index = first_session.append(third_session)
        df = make_ohlcv(index)

        self.assertTrue(
            engine._has_intraday_gaps(df, "CSPX")
        )


class TestTwelveDataAdjustmentMode(unittest.TestCase):

    def test_daily_requests_explicit_split_adjustment_and_intraday_raw(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        for interval, expected_adjust in (("1d", "splits"), ("15m", "none")):
            with self.subTest(interval=interval):
                if interval == "1d":
                    index = pd.date_range(
                        "2026-01-01T00:00:00Z",
                        periods=220,
                        freq="D",
                    )
                else:
                    index = pd.date_range(
                        "2026-11-18T00:00:00Z",
                        periods=220,
                        freq="15min",
                    )

                values = [
                    {
                        "datetime": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "open": "100",
                        "high": "102",
                        "low": "99",
                        "close": "101",
                        "volume": "1000",
                    }
                    for timestamp in index
                ]
                payload = {
                    "meta": {
                        "symbol": "BTC/USD",
                        "currency_quote": "USD",
                    },
                    "values": values,
                }

                with (
                    patch.dict("os.environ", {"TWELVE_DATA_API_KEY": "test"}),
                    patch.object(
                        engine,
                        "_twelvedata_request",
                        return_value=Response(payload),
                    ) as request,
                    patch.object(
                        engine,
                        "_remove_incomplete_candle",
                        side_effect=lambda frame, timeframe, asset: frame,
                    ),
                    patch.object(engine, "_has_daily_gaps", return_value=False),
                    patch.object(engine, "_has_intraday_gaps", return_value=False),
                ):
                    result = engine._download_twelvedata(
                        "BTC",
                        engine.ASSETS["BTC"],
                        interval,
                    )

                self.assertIsNotNone(result)
                self.assertEqual(
                    request.call_args.args[0]["adjust"],
                    expected_adjust,
                )


# ---------------------------------------------------------------------
# HUECOS EN HISTÓRICOS DIARIOS
# ---------------------------------------------------------------------

class TestDailyGaps(unittest.TestCase):

    def make_daily(self, dates):
        return make_ohlcv(
            pd.to_datetime([f"{day}T00:00:00Z" for day in dates])
        )

    def test_accepts_consecutive_crypto_daily_candles(self):
        df = self.make_daily([
            "2026-11-16",
            "2026-11-17",
            "2026-11-18",
        ])
        self.assertFalse(engine._has_daily_gaps(df, "BTC"))

    def test_rejects_missing_crypto_daily_candle(self):
        df = self.make_daily([
            "2026-11-16",
            "2026-11-18",
        ])
        with self.assertLogs("data_engine", level="ERROR") as captured:
            self.assertTrue(engine._has_daily_gaps(df, "BTC"))
        message = "\\n".join(captured.output)
        self.assertIn("anterior=2026-11-16T00:00:00+00:00", message)
        self.assertIn("siguiente=2026-11-18T00:00:00+00:00", message)
        self.assertIn("días_transcurridos=2.0", message)

    def test_ignores_crypto_daily_gap_outside_two_year_lookback(self):
        df = self.make_daily([
            "2020-11-12",
            "2020-11-15",
            "2026-11-19",
            "2026-11-20",
        ])
        self.assertFalse(engine._has_daily_gaps(df, "ETH"))


    def test_accepts_us_weekend_gap(self):
        df = self.make_daily([
            "2026-11-20",
            "2026-11-23",
        ])
        self.assertFalse(engine._has_daily_gaps(df, "TSLA"))

    def test_rejects_missing_us_trading_session(self):
        df = self.make_daily([
            "2026-11-18",
            "2026-11-20",
        ])
        self.assertTrue(engine._has_daily_gaps(df, "TSLA"))

    def test_accepts_us_carter_state_funeral_closure(self):
        df = self.make_daily([
            "2025-01-08",
            "2025-01-10",
        ])
        self.assertFalse(engine._has_daily_gaps(df, "TSLA"))


    def test_accepts_london_weekend_gap(self):
        df = self.make_daily([
            "2026-11-20",
            "2026-11-23",
        ])
        self.assertFalse(engine._has_daily_gaps(df, "CSPX"))

    def test_rejects_missing_london_trading_session(self):
        df = self.make_daily([
            "2026-11-18",
            "2026-11-20",
        ])
        self.assertTrue(engine._has_daily_gaps(df, "CSPX"))

    def test_accepts_uk_christmas_substitute_holidays(self):
        df = self.make_daily([
            "2026-12-24",
            "2026-12-29",
        ])
        self.assertFalse(engine._has_daily_gaps(df, "CNDX"))

    def test_rejects_duplicate_daily_timestamps(self):
        df = self.make_daily([
            "2026-11-18",
            "2026-11-19",
        ])
        df = pd.concat([df, df.iloc[[-1]]])
        self.assertTrue(engine._has_daily_gaps(df, "CSPX"))

    def test_rejects_empty_daily_history(self):
        self.assertTrue(
            engine._has_daily_gaps(pd.DataFrame(), "CSPX")
        )


# ---------------------------------------------------------------------
# CALENDARIOS DE MERCADO
# ---------------------------------------------------------------------

class TestMarketCalendars(unittest.TestCase):

    def test_us_thanksgiving_is_not_trading_day(self):
        self.assertFalse(
            engine._is_trading_day(
                date(2026, 11, 26),
                "TSLA",
            )
        )

    def test_us_friday_after_thanksgiving_is_trading_day(self):
        self.assertTrue(
            engine._is_trading_day(
                date(2026, 11, 27),
                "TSLA",
            )
        )

    def test_previous_us_trading_day_before_friday_after_thanksgiving(self):
        result = engine._previous_trading_day(
            date(2026, 11, 27),
            "TSLA",
        )

        self.assertEqual(
            result,
            date(2026, 11, 25),
        )

    def test_uk_christmas_substitute_holiday(self):
        result = engine._previous_trading_day(
            date(2026, 12, 29),
            "CSPX",
        )

        self.assertEqual(
            result,
            date(2026, 12, 24),
        )

    def test_uk_boxing_day_substitute_is_holiday(self):
        self.assertFalse(
            engine._is_trading_day(
                date(2026, 12, 28),
                "CSPX",
            )
        )

    def test_crypto_does_not_use_weekend_calendar(self):
        result = engine._previous_trading_day(
            date(2026, 11, 22),
            "BTC",
        )

        self.assertEqual(
            result,
            date(2026, 11, 21),
        )

    def test_crypto_trades_on_sunday(self):
        self.assertTrue(
            engine._is_trading_day(
                date(2026, 11, 22),
                "BTC",
            )
        )

    def test_us_good_friday_is_holiday(self):
        self.assertFalse(
            engine._is_trading_day(
                date(2026, 4, 3),
                "TSLA",
            )
        )

    def test_uk_good_friday_is_holiday(self):
        self.assertFalse(
            engine._is_trading_day(
                date(2026, 4, 3),
                "CSPX",
            )
        )

    def test_uk_easter_monday_is_holiday(self):
        self.assertFalse(
            engine._is_trading_day(
                date(2026, 4, 6),
                "CSPX",
            )
        )


# ---------------------------------------------------------------------
# ZONAS HORARIAS Y CAMBIOS DST
# ---------------------------------------------------------------------

class TestMarketTimezones(unittest.TestCase):

    def test_us_market_uses_new_york_timezone(self):
        tz, opening, closing = engine._session_for_asset("TSLA")

        self.assertEqual(
            tz.key,
            "America/New_York",
        )

        self.assertEqual(
            opening.isoformat(),
            "09:30:00",
        )

        self.assertEqual(
            closing.isoformat(),
            "16:00:00",
        )

    def test_london_market_uses_london_timezone(self):
        tz, opening, closing = engine._session_for_asset("CSPX")

        self.assertEqual(
            tz.key,
            "Europe/London",
        )

        self.assertEqual(
            opening.isoformat(),
            "08:00:00",
        )

        self.assertEqual(
            closing.isoformat(),
            "16:30:00",
        )

    def test_us_open_after_spring_dst_change(self):
        tz, opening, _ = engine._session_for_asset("TSLA")

        local_open = datetime.combine(
            date(2026, 3, 9),
            opening,
            tzinfo=tz,
        )

        self.assertEqual(
            local_open.astimezone(timezone.utc),
            datetime(
                2026, 3, 9, 13, 30,
                tzinfo=timezone.utc,
            ),
        )

    def test_us_open_after_autumn_dst_change(self):
        tz, opening, _ = engine._session_for_asset("TSLA")

        local_open = datetime.combine(
            date(2026, 11, 2),
            opening,
            tzinfo=tz,
        )

        self.assertEqual(
            local_open.astimezone(timezone.utc),
            datetime(
                2026, 11, 2, 14, 30,
                tzinfo=timezone.utc,
            ),
        )

    def test_london_open_after_spring_dst_change(self):
        tz, opening, _ = engine._session_for_asset("CSPX")

        local_open = datetime.combine(
            date(2026, 3, 30),
            opening,
            tzinfo=tz,
        )

        self.assertEqual(
            local_open.astimezone(timezone.utc),
            datetime(
                2026, 3, 30, 7, 0,
                tzinfo=timezone.utc,
            ),
        )

    def test_london_open_after_autumn_dst_change(self):
        tz, opening, _ = engine._session_for_asset("CSPX")

        local_open = datetime.combine(
            date(2026, 10, 26),
            opening,
            tzinfo=tz,
        )

        self.assertEqual(
            local_open.astimezone(timezone.utc),
            datetime(
                2026, 10, 26, 8, 0,
                tzinfo=timezone.utc,
            ),
        )


# ---------------------------------------------------------------------
# SESIONES REDUCIDAS
# ---------------------------------------------------------------------

class TestEarlyMarketClose(unittest.TestCase):

    def test_friday_after_thanksgiving_has_early_close(self):
        with patch.object(
            engine,
            "datetime",
            FrozenDateTimeEarlyClose,
        ):
            result = engine._last_expected_intraday_timestamp(
                "TSLA",
                datetime(
                    2026, 11, 27, 18, 30,
                    tzinfo=timezone.utc,
                ),
            )

        expected = datetime(
            2026, 11, 27, 17, 45,
            tzinfo=timezone.utc,
        )

        self.assertEqual(
            result.astimezone(timezone.utc),
            expected,
        )


# ---------------------------------------------------------------------
# CACHÉ
# ---------------------------------------------------------------------

class TestDataCache(unittest.TestCase):

    def setUp(self):
        self.index = pd.date_range(
            "2026-11-19T12:00:00Z",
            periods=3,
            freq="15min",
        )

        self.df = make_ohlcv(self.index)

    def test_save_and_retrieve_cached_data(self):
        with patch.object(
            engine,
            "_data_cache",
            {},
        ):
            engine._save_cached_data(
                "BTC",
                "15m",
                self.df,
            )

            result = engine._get_cached_data(
                "BTC",
                "15m",
            )

        self.assertIsNotNone(result)
        pd.testing.assert_frame_equal(result, self.df)

    def test_cached_data_is_returned_as_copy(self):
        with patch.object(
            engine,
            "_data_cache",
            {},
        ):
            engine._save_cached_data(
                "BTC",
                "15m",
                self.df,
            )

            result = engine._get_cached_data(
                "BTC",
                "15m",
            )

            result.iloc[0, 0] = 9999

            cached_again = engine._get_cached_data(
                "BTC",
                "15m",
            )

        self.assertNotEqual(
            cached_again.iloc[0, 0],
            9999,
        )

    def test_expired_cache_is_rejected(self):
        key = ("BTC", "15m")

        expired_at = (
            engine.time.monotonic()
            - engine.CACHE_TTL["15m"]
            - 1
        )

        with patch.object(
            engine,
            "_data_cache",
            {
                key: (self.df.copy(), expired_at),
            },
        ):
            result = engine._get_cached_data(
                "BTC",
                "15m",
            )

        self.assertIsNone(result)

    def test_missing_cache_returns_none(self):
        with patch.object(
            engine,
            "_data_cache",
            {},
        ):
            result = engine._get_cached_data(
                "BTC",
                "15m",
            )

        self.assertIsNone(result)

    def test_cache_is_separated_by_asset_and_interval(self):
        with patch.object(
            engine,
            "_data_cache",
            {},
        ):
            engine._save_cached_data(
                "BTC",
                "15m",
                self.df,
            )

            result = engine._get_cached_data(
                "BTC",
                "1d",
            )

        self.assertIsNone(result)

    def test_download_uses_cached_data_when_valid(self):
        with patch.object(
            engine,
            "_get_cached_data",
            return_value=self.df.copy(),
        ), patch.object(
            engine,
            "_download_twelvedata",
        ) as downloader, patch.object(
            engine,
            "PROVIDER_MAP",
            {"BTC": "twelvedata"},
        ), patch.object(
            engine,
            "ASSETS",
            {
                "BTC": {
                    "enabled": True,
                    "currency": "USD",
                }
            },
        ):

            result = engine.download_data(
                "BTC",
                "15m",
            )

        downloader.assert_not_called()
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------
# DESCARGA YAHOO: VELAS INCOMPLETAS
# ---------------------------------------------------------------------

class TestYahooDownload(unittest.TestCase):

    def test_retries_intraday_http_422_with_shorter_window(self):
        index = pd.date_range(
            "2026-10-01T13:30:00Z",
            periods=220,
            freq="15min",
        )
        payload = {
            "chart": {
                "error": None,
                "result": [{
                    "meta": {
                        "currency": "USD",
                        "fullExchangeName": "London Stock Exchange",
                        "exchangeTimezoneName": "Europe/London",
                    },
                    "timestamp": [int(value.timestamp()) for value in index],
                    "indicators": {
                        "quote": [{
                            "open": [100.0] * len(index),
                            "high": [102.0] * len(index),
                            "low": [99.0] * len(index),
                            "close": [101.0] * len(index),
                            "volume": [1000] * len(index),
                        }],
                    },
                }],
            },
        }

        class Response422:
            status_code = 422

        class ResponseOK:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return payload

        with (
            patch.object(engine, "datetime", FrozenDateTime),
            patch.object(
                engine.requests,
                "get",
                side_effect=[Response422(), ResponseOK()],
            ) as request,
            patch.object(
                engine,
                "_remove_incomplete_candle",
                side_effect=lambda frame, interval, asset: frame,
            ),
            patch.object(engine, "_has_intraday_gaps", return_value=False),
        ):
            result = engine._download_yahoo(
                "CNDX",
                {"yahoo": "CNDX.L", "currency": "USD"},
                "15m",
            )

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 220)
        self.assertEqual(request.call_count, 2)
        first_period = request.call_args_list[0].kwargs["params"]["period1"]
        second_period = request.call_args_list[1].kwargs["params"]["period1"]
        self.assertGreater(second_period, first_period)

    def test_discards_incomplete_daily_candle_before_ohlc_validation(self):
        index = pd.bdate_range(
            end="2026-11-19",
            periods=221,
            tz="UTC",
        )
        values = {
            "open": [100.0] * len(index),
            "high": [102.0] * len(index),
            "low": [99.0] * len(index),
            "close": [101.0] * len(index),
            "volume": [1000] * len(index),
        }

        # La última vela corresponde al día actual y aún no está cerrada.
        # Yahoo puede devolver OHLC nulo en esa fila.
        for field in ("open", "high", "low", "close"):
            values[field][-1] = None

        payload = {
            "chart": {
                "error": None,
                "result": [{
                    "meta": {
                        "currency": "USD",
                        "fullExchangeName": "London Stock Exchange",
                        "exchangeTimezoneName": "Europe/London",
                    },
                    "timestamp": [int(value.timestamp()) for value in index],
                    "indicators": {
                        "quote": [values],
                    },
                }],
            },
        }

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return payload

        with (
            patch.object(engine, "datetime", FrozenDateTime),
            patch.object(engine.requests, "get", return_value=FakeResponse()),
        ):
            result = engine._download_yahoo(
                "CSPX",
                {"yahoo": "CSPX.L", "currency": "USD"},
                "1d",
            )

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 220)
        self.assertTrue(np.isfinite(result[["Open", "High", "Low", "Close"]]).all().all())
        self.assertNotIn(index[-1], result.index)


# ---------------------------------------------------------------------
# DESCARGA PRINCIPAL Y VALIDACIONES BASICAS
# ---------------------------------------------------------------------

class TestDownloadData(unittest.TestCase):

    def test_rejects_unsupported_interval(self):
        result = engine.download_data(
            "BTC",
            "5m",
        )

        self.assertIsNone(result)

    def test_rejects_unknown_asset(self):
        with patch.object(
            engine,
            "ASSETS",
            {},
        ):
            result = engine.download_data(
                "UNKNOWN",
                "15m",
            )

        self.assertIsNone(result)

    def test_rejects_disabled_asset(self):
        with patch.object(
            engine,
            "ASSETS",
            {
                "BTC": {
                    "enabled": False,
                }
            },
        ):
            result = engine.download_data(
                "BTC",
                "15m",
            )

        self.assertIsNone(result)

    def test_rejects_asset_without_provider(self):
        with patch.object(
            engine,
            "ASSETS",
            {
                "UNKNOWN": {
                    "enabled": True,
                    "currency": "USD",
                }
            },
        ), patch.object(
            engine,
            "PROVIDER_MAP",
            {},
        ), patch.object(
            engine,
            "_get_cached_data",
            return_value=None,
        ):

            result = engine.download_data(
                "UNKNOWN",
                "15m",
            )

        self.assertIsNone(result)

    def test_dispatches_crypto_to_twelvedata(self):
        expected = make_ohlcv(
            pd.date_range(
                "2026-11-19T12:00:00Z",
                periods=3,
                freq="15min",
            )
        )

        with patch.object(
            engine,
            "ASSETS",
            {
                "BTC": {
                    "enabled": True,
                    "currency": "USD",
                }
            },
        ), patch.object(
            engine,
            "_get_cached_data",
            return_value=None,
        ), patch.object(
            engine,
            "_download_twelvedata",
            return_value=expected,
        ) as downloader, patch.object(
            engine,
            "_save_cached_data",
        ):

            result = engine.download_data(
                "BTC",
                "15m",
            )

        downloader.assert_called_once()
        self.assertIsNotNone(result)

    def test_dispatches_etf_to_yahoo(self):
        expected = make_ohlcv(
            pd.date_range(
                "2026-11-18T14:30:00Z",
                periods=3,
                freq="15min",
            )
        )

        with patch.object(
            engine,
            "ASSETS",
            {
                "CSPX": {
                    "enabled": True,
                    "currency": "USD",
                    "yahoo": "CSPX.L",
                }
            },
        ), patch.object(
            engine,
            "_get_cached_data",
            return_value=None,
        ), patch.object(
            engine,
            "_download_yahoo",
            return_value=expected,
        ) as downloader, patch.object(
            engine,
            "_save_cached_data",
        ):

            result = engine.download_data(
                "CSPX",
                "15m",
            )

        downloader.assert_called_once()
        self.assertIsNotNone(result)

    def test_does_not_cache_failed_download(self):
        with patch.object(
            engine,
            "ASSETS",
            {
                "BTC": {
                    "enabled": True,
                    "currency": "USD",
                }
            },
        ), patch.object(
            engine,
            "_get_cached_data",
            return_value=None,
        ), patch.object(
            engine,
            "_download_twelvedata",
            return_value=None,
        ), patch.object(
            engine,
            "_save_cached_data",
        ) as save_cache:

            result = engine.download_data(
                "BTC",
                "15m",
            )

        self.assertIsNone(result)
        save_cache.assert_not_called()


# ---------------------------------------------------------------------
# TIMESTAMP DE DATOS
# ---------------------------------------------------------------------

class TestDataTimestamp(unittest.TestCase):

    def test_returns_none_for_none(self):
        self.assertIsNone(
            engine.get_data_timestamp(None)
        )

    def test_returns_none_for_empty_dataframe(self):
        self.assertIsNone(
            engine.get_data_timestamp(pd.DataFrame())
        )

    def test_returns_last_timestamp_in_utc(self):
        index = pd.date_range(
            "2026-11-19T12:00:00Z",
            periods=3,
            freq="15min",
        )

        df = make_ohlcv(index)

        result = engine.get_data_timestamp(df)

        self.assertEqual(
            result,
            index[-1],
        )

        self.assertEqual(
            str(result.tz),
            "UTC",
        )

    def test_converts_local_timestamp_to_utc(self):
        index = pd.date_range(
            "2026-11-19T13:00:00",
            periods=3,
            freq="15min",
        ).tz_localize("Europe/Madrid")

        df = make_ohlcv(index)

        result = engine.get_data_timestamp(df)

        self.assertEqual(
            str(result.tz),
            "UTC",
        )


class TestAutomatedScanSchedulingHelpers(unittest.TestCase):

    def test_intraday_scan_skips_before_us_open(self):
        with patch.object(engine, "datetime", FrozenDateTimeBeforeOpen):
            self.assertFalse(engine.is_intraday_market_open("AAPL"))

    def test_intraday_scan_runs_during_us_session(self):
        with patch.object(engine, "datetime", FrozenDateTimeDuringSession):
            self.assertTrue(engine.is_intraday_market_open("AAPL"))


    def test_intraday_scan_allows_brief_post_close_data_publication_window(self):
        now = datetime(2026, 11, 19, 21, 3, tzinfo=timezone.utc)
        self.assertTrue(engine.is_intraday_market_open("AAPL", now=now))

    def test_intraday_scan_skips_after_us_close(self):
        with patch.object(engine, "datetime", FrozenDateTimeAfterClose):
            self.assertFalse(engine.is_intraday_market_open("AAPL"))

    def test_intraday_scan_skips_after_early_us_close(self):
        with patch.object(engine, "datetime", FrozenDateTimeEarlyClose):
            self.assertFalse(engine.is_intraday_market_open("AAPL"))

    def test_intraday_scan_uses_london_session(self):
        now = datetime(2026, 11, 19, 15, 50, tzinfo=timezone.utc)
        self.assertTrue(engine.is_intraday_market_open("CSPX", now=now))

    def test_crypto_intraday_scan_runs_on_sunday(self):
        sunday = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(engine.is_intraday_market_open("BTC", now=sunday))

    def test_intraday_expiry_uses_completed_candle_and_two_future_bars(self):
        expiry = engine.signal_expiry_at(
            "BTC",
            "15m",
            generated_at="2026-11-21T12:03:00Z",
            data_timestamp="2026-11-21T11:45:00Z",
            validity_bars=2,
        )
        self.assertEqual(expiry, datetime(2026, 11, 21, 12, 30, tzinfo=timezone.utc))

    def test_crypto_daily_expiry_uses_utc_day_boundaries(self):
        expiry = engine.signal_expiry_at(
            "BTC",
            "1d",
            generated_at=datetime(2026, 11, 21, 0, 30, tzinfo=timezone.utc),
            validity_bars=2,
        )
        self.assertEqual(expiry, datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc))

    def test_us_daily_expiry_uses_next_two_sessions(self):
        generated = datetime(2026, 11, 19, 15, 50, tzinfo=timezone.utc)
        expiry = engine.signal_expiry_at(
            "AAPL", "1d", generated_at=generated, validity_bars=2
        )
        self.assertEqual(expiry, datetime(2026, 11, 20, 21, 30, tzinfo=timezone.utc))

    def test_us_daily_expiry_skips_weekend(self):
        generated = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        expiry = engine.signal_expiry_at(
            "AAPL", "1d", generated_at=generated, validity_bars=2
        )
        self.assertEqual(expiry, datetime(2026, 11, 24, 21, 30, tzinfo=timezone.utc))

    def test_expiry_rejects_unknown_timeframe(self):
        with self.assertRaises(ValueError):
            engine.signal_expiry_at("AAPL", "5m")


if __name__ == "__main__":
    unittest.main()




class TestExtendedHistoryIsBacktestOnly(unittest.TestCase):

    def setUp(self):
        self.frame = make_ohlcv(
            pd.date_range(
                "2026-11-18T00:00:00Z",
                periods=220,
                freq="15min",
            )
        )

    def test_extended_backfill_requires_explicit_backtest_flag(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(engine, "_get_cached_data", return_value=None),
            patch.object(engine, "_download_twelvedata", return_value=self.frame) as normal,
            patch.object(engine, "_download_twelvedata_extended") as extended,
            patch.object(engine, "_save_cached_data"),
        ):
            result = engine.download_data("BTC", "15m")

        self.assertIsNotNone(result)
        normal.assert_called_once()
        extended.assert_not_called()

    def test_extended_backfill_is_used_only_when_flag_is_true(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "BACKTEST_EXTENDED_INTRADAY_HISTORY": "true",
                },
            ),
            patch.object(engine, "_get_cached_data", return_value=None),
            patch.object(engine, "_download_twelvedata") as normal,
            patch.object(
                engine, "_download_twelvedata_extended", return_value=self.frame
            ) as extended,
            patch.object(engine, "_save_cached_data"),
        ):
            result = engine.download_data("BTC", "15m")

        self.assertIsNotNone(result)
        extended.assert_called_once()
        normal.assert_not_called()

class TestTwelveDataHistoricalWindows(unittest.TestCase):

    def test_crypto_history_is_chunked_below_five_thousand_bars(self):
        end = datetime(2026, 11, 21, 3, 23, tzinfo=timezone.utc)
        windows = engine.twelvedata_history_windows(
            "BTC", end=end, lookback_days=700
        )
        self.assertEqual(len(windows), 14)
        self.assertEqual(windows[-1][1], datetime(2026, 11, 21, 3, 15, tzinfo=timezone.utc))
        for start, finish in windows:
            self.assertEqual(finish - start, timedelta(days=50))

    def test_equity_history_uses_larger_contiguous_windows(self):
        end = datetime(2026, 11, 21, 3, 23, tzinfo=timezone.utc)
        windows = engine.twelvedata_history_windows(
            "AAPL", end=end, lookback_days=700
        )
        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[0][0], windows[0][1] - timedelta(days=180))
        self.assertTrue(all(
            timedelta(0) < finish - start <= timedelta(days=180)
            for start, finish in windows
        ))
        for previous, current in zip(windows, windows[1:]):
            self.assertEqual(previous[1], current[0])

    def test_historical_windows_reject_invalid_lookback(self):
        for days in (0, -1, True, 1.5):
            with self.subTest(days=days):
                with self.assertRaises(ValueError):
                    engine.twelvedata_history_windows(
                        "BTC",
                        end=datetime(2026, 11, 21, tzinfo=timezone.utc),
                        lookback_days=days,
                    )

    def test_historical_windows_require_timezone_aware_end(self):
        with self.assertRaises(ValueError):
            engine.twelvedata_history_windows(
                "BTC",
                end=datetime(2026, 11, 21),
                lookback_days=50,
            )

