import json
import tempfile
import unittest
from pathlib import Path
from datetime import timezone
from unittest.mock import Mock

from etoro_read_only import EtoroAPIError, EtoroResponse
from etoro_historical_market_probe import (
    EtoroHistoricalMarketDataClient,
    load_instrument_ids,
    normalize_candle_history,
    run_probe,
)


def candle(iid=123, stamp="2026-11-20T10:00:00Z", **overrides):
    value = {
        "instrumentID": iid, "fromDate": stamp,
        "open": 100, "high": 105, "low": 98, "close": 103, "volume": 0,
    }
    value.update(overrides)
    return value


class HistoricalMarketProbeTests(unittest.TestCase):
    def test_normalizes_official_grouped_ohlcv_response(self):
        response = EtoroResponse({
            "interval": "FifteenMinutes",
            "candles": [{"instrumentId": 123, "candles": [candle()]}],
        }, 200, {})
        result = normalize_candle_history(response, 123, "FifteenMinutes")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["close"], "103")
        self.assertEqual(result[0]["volume"], "0")

    def test_empty_history_is_reported_as_empty_not_malformed(self):
        response = EtoroResponse({"interval": "OneDay", "candles": []}, 200, {})
        self.assertEqual(normalize_candle_history(response, 123, "OneDay"), [])

    def test_rejects_wrong_instrument_or_interval(self):
        response = EtoroResponse({
            "interval": "OneDay",
            "candles": [{"instrumentId": 999, "candles": [candle(iid=999)]}],
        }, 200, {})
        with self.assertRaises(EtoroAPIError):
            normalize_candle_history(response, 123, "FifteenMinutes")

    def test_rejects_inconsistent_ohlc(self):
        response = EtoroResponse({
            "interval": "OneDay",
            "candles": [{"instrumentId": 123, "candles": [candle(high=99)]}],
        }, 200, {})
        with self.assertRaises(EtoroAPIError):
            normalize_candle_history(response, 123, "OneDay")

    def test_preserves_and_flags_duplicate_daily_timestamps(self):
        duplicate = EtoroResponse({
            "interval": "OneDay",
            "candles": [{"instrumentId": 123, "candles": [
                candle(stamp="2024-11-03T00:00:00Z", close=103),
                candle(stamp="2024-11-03T00:00:00Z", close=104),
            ]}],
        }, 200, {})
        result = normalize_candle_history(duplicate, 123, "OneDay")
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["timestamp_duplicate"])
        self.assertTrue(result[1]["timestamp_duplicate"])
        self.assertEqual([item["close"] for item in result], ["103", "104"])

    def test_run_probe_counts_all_rows_in_duplicate_daily_groups(self):
        class DuplicateDailyClient:
            def get_candle_history(self, instrument_id, interval, candles_count, direction):
                rows = [candle(iid=instrument_id)]
                if interval == "OneDay":
                    rows = [
                        candle(iid=instrument_id, stamp="2024-11-03T00:00:00Z", close=103),
                        candle(iid=instrument_id, stamp="2024-11-03T00:00:00Z", close=104),
                        candle(iid=instrument_id, stamp="2024-11-04T00:00:00Z", close=105),
                    ]
                return EtoroResponse({
                    "interval": interval,
                    "candles": [{"instrumentId": instrument_id, "candles": rows}],
                }, 200, {})

        result = run_probe(DuplicateDailyClient(), {"BTC": 123}, 10)
        daily = result["assets"]["BTC"]["intervals"]["OneDay"]
        self.assertEqual(daily["duplicate_timestamp_candles"], 2)
        self.assertEqual(daily["duplicate_timestamp_groups"], 1)
        self.assertEqual(daily["duplicate_timestamps"], ["2024-11-03T00:00:00+00:00"])
        self.assertTrue(daily["candles"][0]["timestamp_duplicate"])
        self.assertTrue(daily["candles"][1]["timestamp_duplicate"])
        self.assertFalse(daily["candles"][2]["timestamp_duplicate"])

    def test_rejects_duplicate_intraday_timestamps_and_nonchronological_timestamps(self):
        duplicate = EtoroResponse({
            "interval": "FifteenMinutes",
            "candles": [{"instrumentId": 123, "candles": [
                candle(), candle(stamp="2026-11-20T10:00:00Z")
            ]}],
        }, 200, {})
        with self.assertRaises(EtoroAPIError):
            normalize_candle_history(duplicate, 123, "FifteenMinutes")
        reverse = EtoroResponse({
            "interval": "OneDay",
            "candles": [{"instrumentId": 123, "candles": [
                candle(stamp="2026-11-21T10:00:00Z"), candle(stamp="2026-11-20T10:00:00Z")
            ]}],
        }, 200, {})
        with self.assertRaises(EtoroAPIError):
            normalize_candle_history(reverse, 123, "OneDay")

    def test_client_only_permits_market_candle_get(self):
        session = Mock()
        response = Mock()
        response.status_code = 200
        response.headers = {}
        response.json.return_value = {
            "interval": "OneDay",
            "candles": [{"instrumentId": 123, "candles": [candle()]}],
        }
        session.request.return_value = response
        client = EtoroHistoricalMarketDataClient("app", "user", session=session)
        result = client.get_candle_history(123, "OneDay", 100, "asc")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(session.request.call_count, 1)
        self.assertIn("/history/candles/asc/OneDay/100", session.request.call_args.args[1])
        with self.assertRaises(EtoroAPIError):
            client._request("GET", "/api/v1/trading/info/real/pnl")
        self.assertEqual(session.request.call_count, 1)

    def test_run_probe_preserves_successful_results_when_one_request_fails(self):
        class PartiallyFailingClient:
            def get_candle_history(self, instrument_id, interval, candles_count, direction):
                if instrument_id == 123 and interval == "FifteenMinutes":
                    raise EtoroAPIError("eToro devolvió HTTP 503")
                return EtoroResponse({
                    "interval": interval,
                    "candles": [{
                        "instrumentId": instrument_id,
                        "candles": [candle(iid=instrument_id)],
                    }],
                }, 200, {})

        result = run_probe(
            PartiallyFailingClient(),
            {"BTC": 123, "ETH": 124},
            100,
        )

        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["asset"], "BTC")
        self.assertEqual(
            result["assets"]["BTC"]["intervals"]["FifteenMinutes"]["status"],
            "ERROR",
        )
        self.assertEqual(
            result["assets"]["BTC"]["intervals"]["OneDay"]["status"],
            "DATA_RETURNED",
        )
        self.assertEqual(
            result["assets"]["ETH"]["intervals"]["FifteenMinutes"]["status"],
            "DATA_RETURNED",
        )
        self.assertEqual(
            result["assets"]["ETH"]["intervals"]["OneDay"]["status"],
            "DATA_RETURNED",
        )

    def test_loads_only_explicitly_confirmed_instrument_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps({
                "assets": {
                    "BTC": {"instrument": {"confirmed": True, "etoro_instrument_id": 123}},
                    "ETH": {"instrument": {"confirmed": True, "etoro_instrument_id": 456}},
                }
            }), encoding="utf-8")
            self.assertEqual(load_instrument_ids(path), {"BTC": 123, "ETH": 456})

    def test_rejects_unconfirmed_or_malformed_instrument_metadata(self):
        invalid_registries = [
            {"assets": {"BTC": {"instrument": {"confirmed": False, "etoro_instrument_id": 123}}}},
            {"assets": {"BTC": {"instrument": {"etoro_instrument_id": 123}}}},
            {"assets": {"BTC": {"instrument": None}}},
            {"assets": {"BTC": None}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            for payload in invalid_registries:
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_instrument_ids(path)

    def test_rejects_duplicate_confirmed_instrument_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps({
                "assets": {
                    "BTC": {"instrument": {"confirmed": True, "etoro_instrument_id": 123}},
                    "ETH": {"instrument": {"confirmed": True, "etoro_instrument_id": 123}},
                }
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicados"):
                load_instrument_ids(path)

    def test_rejects_invalid_count_and_interval_before_network(self):
        session = Mock()
        client = EtoroHistoricalMarketDataClient("app", "user", session=session)
        for count in (0, 1001, True):
            with self.assertRaises(ValueError):
                client.get_candle_history(123, "OneDay", count)
        with self.assertRaises(ValueError):
            client.get_candle_history(123, "OneHour", 10)
        session.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
