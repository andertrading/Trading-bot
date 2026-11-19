
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from data_engine import (
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


if __name__ == "__main__":
    unittest.main()
