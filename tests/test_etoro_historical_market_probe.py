import unittest
from datetime import timezone
from unittest.mock import Mock

from etoro_read_only import EtoroAPIError, EtoroResponse
from etoro_historical_market_probe import (
    EtoroHistoricalMarketDataClient,
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

    def test_rejects_duplicate_and_nonchronological_timestamps(self):
        duplicate = EtoroResponse({
            "interval": "OneDay",
            "candles": [{"instrumentId": 123, "candles": [
                candle(), candle(stamp="2026-11-20T10:00:00Z")
            ]}],
        }, 200, {})
        with self.assertRaises(EtoroAPIError):
            normalize_candle_history(duplicate, 123, "OneDay")
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
