import unittest
from unittest.mock import patch

import pandas as pd
import requests

import eodhd_provider as provider
import data_engine as engine


def valid_payload(rows=225):
    dates = pd.bdate_range(
        end=pd.Timestamp.now(tz="UTC").date() - pd.Timedelta(days=1),
        periods=rows,
    )
    return [
        {
            "date": day.strftime("%Y-%m-%d"),
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "adjusted_close": 100.5,
            "volume": 1000,
        }
        for day in dates
    ]


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class TestEODHDProvider(unittest.TestCase):
    def setUp(self):
        self.cache_patch = patch.object(provider, "_cache", {})
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.split_cache_patch = patch.object(provider, "_split_cache", {})
        self.split_cache_patch.start()
        self.addCleanup(self.split_cache_patch.stop)

    def download(self, asset="CSPX", payload=None, **kwargs):
        response = FakeResponse(valid_payload() if payload is None else payload)
        with patch.dict("os.environ", {"EODHD_API_KEY": "test-secret"}), patch.object(
            provider.requests,
            "get",
            side_effect=[response, FakeResponse(kwargs.pop("splits", []))],
            **kwargs,
        ) as request:
            result = provider.download_eodhd_daily(
                asset,
                "USD",
                lambda frame, interval, symbol: frame,
                lambda frame, symbol: False,
            )
        return result, request

    def test_downloads_verified_cspx_listing_and_normalizes_columns(self):
        result, request = self.download("CSPX")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 225)
        self.assertEqual(
            list(result.columns),
            ["Open", "High", "Low", "Close", "Volume"],
        )
        self.assertEqual(
            request.call_args_list[0].args[0],
            "https://eodhd.com/api/eod/CSPX.LSE",
        )
        self.assertEqual(request.call_args_list[0].kwargs["params"]["period"], "d")
        self.assertEqual(
            request.call_args_list[0].kwargs["params"]["api_token"],
            "test-secret",
        )
        self.assertEqual(
            request.call_args_list[1].args[0],
            "https://eodhd.com/api/splits/CSPX.LSE",
        )

    def test_downloads_verified_cndx_listing(self):
        result, request = self.download("CNDX")
        self.assertIsNotNone(result)
        self.assertEqual(
            request.call_args_list[0].args[0],
            "https://eodhd.com/api/eod/CNDX.LSE",
        )

    def test_missing_api_key_fails_closed(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(
            provider.requests, "get"
        ) as request:
            result = provider.download_eodhd_daily(
                "CSPX", "USD", lambda f, i, a: f, lambda f, a: False
            )
        self.assertIsNone(result)
        request.assert_not_called()

    def test_unknown_asset_is_rejected(self):
        with patch.object(provider.requests, "get") as request:
            result = provider.download_eodhd_daily(
                "TSLA", "USD", lambda f, i, a: f, lambda f, a: False
            )
        self.assertIsNone(result)
        request.assert_not_called()

    def test_split_adjustment_rebases_ohlc_to_current_share_units(self):
        payload = valid_payload()
        dates = pd.bdate_range(
            end=pd.Timestamp.now(tz="UTC").date() - pd.Timedelta(days=1),
            periods=len(payload),
        )
        split_date = dates[100].strftime("%Y-%m-%d")
        result, _ = self.download(
            payload=payload,
            splits=[{"date": split_date, "split": "2.000000/1.000000"}],
        )
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.iloc[0]["Open"], 50.0)
        self.assertAlmostEqual(result.loc[pd.Timestamp(split_date, tz="UTC"), "Open"], 100.0)
        self.assertEqual(
            result.attrs["price_adjustment_policy"],
            "split_adjusted_ohlc_no_dividend_adjustment",
        )
        self.assertEqual(result.attrs["split_events_applied"], 1)

    def test_invalid_split_ratio_fails_closed(self):
        result, _ = self.download(
            splits=[{"date": "2026-01-01", "split": "not-a-ratio"}],
        )
        self.assertIsNone(result)

    def test_rejects_incoherent_ohlc(self):
        payload = valid_payload()
        payload[10]["open"] = 105.0
        result, _ = self.download(payload=payload)
        self.assertIsNone(result)

    def test_rejects_non_finite_prices(self):
        payload = valid_payload()
        payload[10]["close"] = None
        result, _ = self.download(payload=payload)
        self.assertIsNone(result)

    def test_rejects_short_history(self):
        result, _ = self.download(payload=valid_payload(219))
        self.assertIsNone(result)

    def test_daily_gap_callback_rejects_history(self):
        response = FakeResponse(valid_payload())
        with patch.dict("os.environ", {"EODHD_API_KEY": "test-secret"}), patch.object(
            provider.requests, "get", side_effect=[response, FakeResponse([])]
        ):
            result = provider.download_eodhd_daily(
                "CSPX",
                "USD",
                lambda frame, interval, asset: frame,
                lambda frame, asset: True,
            )
        self.assertIsNone(result)

    def test_cache_prevents_repeated_api_calls(self):
        response = FakeResponse(valid_payload())
        with patch.dict("os.environ", {"EODHD_API_KEY": "test-secret"}), patch.object(
            provider.requests, "get", side_effect=[response, FakeResponse([])]
        ) as request:
            first = provider.download_eodhd_daily(
                "CSPX", "USD", lambda f, i, a: f, lambda f, a: False
            )
            second = provider.download_eodhd_daily(
                "CSPX", "USD", lambda f, i, a: f, lambda f, a: False
            )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(request.call_count, 2)
        pd.testing.assert_frame_equal(first, second)

    def test_http_error_does_not_expose_exception_or_token(self):
        with patch.dict("os.environ", {"EODHD_API_KEY": "secret-must-not-leak"}), patch.object(
            provider.requests,
            "get",
            side_effect=requests.RequestException("secret-must-not-leak"),
        ), self.assertLogs("eodhd_provider", level="ERROR") as captured:
            result = provider.download_eodhd_daily(
                "CSPX", "USD", lambda f, i, a: f, lambda f, a: False
            )
        self.assertIsNone(result)
        self.assertNotIn("secret-must-not-leak", "\n".join(captured.output))


class TestEODHDDispatch(unittest.TestCase):
    def test_live_engine_declares_consistent_split_adjustment_policy(self):
        sample = pd.DataFrame(
            {"Open": [100.0], "High": [102.0], "Low": [99.0], "Close": [101.0], "Volume": [10.0]},
            index=pd.DatetimeIndex(["2026-11-20"], tz="UTC"),
        )
        intraday = engine._attach_market_data_metadata(sample, "AAPL", "15m")
        daily_stock = engine._attach_market_data_metadata(sample, "AAPL", "1d")
        daily_etf = engine._attach_market_data_metadata(sample, "CSPX", "1d")

        self.assertEqual(intraday.attrs["price_adjustment_policy"], "raw_unadjusted_intraday")
        self.assertEqual(daily_stock.attrs["price_adjustment_policy"], "split_adjusted_ohlc_no_dividend_adjustment")
        self.assertEqual(daily_etf.attrs["price_adjustment_policy"], "split_adjusted_ohlc_no_dividend_adjustment")
        self.assertEqual(daily_etf.attrs["data_provider"], "eodhd")
        self.assertTrue(intraday.attrs["price_adjustment_verified"])
        self.assertTrue(daily_stock.attrs["price_adjustment_verified"])
        self.assertTrue(daily_etf.attrs["price_adjustment_verified"])

    def test_daily_london_etf_uses_eodhd(self):
        expected = pd.DataFrame(
            {"Open": [100.0], "High": [102.0], "Low": [99.0], "Close": [101.0], "Volume": [10.0]},
            index=pd.DatetimeIndex(["2026-11-20"], tz="UTC"),
        )
        with patch.object(engine, "ASSETS", {
            "CSPX": {"enabled": True, "currency": "USD", "yahoo": "CSPX.L"}
        }), patch.object(engine, "_get_cached_data", return_value=None), patch.object(
            engine, "download_eodhd_daily", return_value=expected
        ) as eodhd, patch.object(engine, "_download_yahoo") as yahoo, patch.object(
            engine, "_save_cached_data"
        ):
            result = engine.download_data("CSPX", "1d")
        self.assertIsNotNone(result)
        eodhd.assert_called_once()
        yahoo.assert_not_called()

    def test_failed_eodhd_daily_does_not_fall_back_to_yahoo(self):
        with patch.object(engine, "ASSETS", {
            "CNDX": {"enabled": True, "currency": "USD", "yahoo": "CNDX.L"}
        }), patch.object(engine, "_get_cached_data", return_value=None), patch.object(
            engine, "download_eodhd_daily", return_value=None
        ) as eodhd, patch.object(engine, "_download_yahoo") as yahoo:
            result = engine.download_data("CNDX", "1d")
        self.assertIsNone(result)
        eodhd.assert_called_once()
        yahoo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
