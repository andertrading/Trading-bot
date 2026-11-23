import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from etoro_historical_candles import (
    EtoroHistoricalCandleClient,
    collect_historical_candles,
    normalize_candles,
)
from etoro_read_only import EtoroAPIError, EtoroResponse


NOW = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)


def candle(ts, opening="100", high="102", low="99", close="101", volume="10"):
    return {
        "instrumentID": 1001,
        "fromDate": ts,
        "open": opening,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def history_response(instrument_id=1001):
    return EtoroResponse(
        data={
            "interval": "OneDay",
            "candles": [{
                "instrumentId": instrument_id,
                "candles": [
                    candle("2026-11-22T00:00:00Z"),
                    candle("2026-11-21T00:00:00Z", "98", "101", "97", "100"),
                ],
            }],
        },
        status_code=200,
        headers={},
    )


class FakeClient:
    def __init__(self, instrument_type="Stocks", instrument_id=1001, response=None):
        self.instrument_type = instrument_type
        self.instrument_id = instrument_id
        self.response = response or history_response(instrument_id)
        self.history_calls = []

    def search_instrument_by_symbol(self, symbol):
        return EtoroResponse(
            data={
                "results": [{
                    "instrumentId": self.instrument_id,
                    "symbol": symbol,
                    "displayName": "Apple",
                    "type": self.instrument_type,
                }],
                "pagination": {"hasNext": False},
            },
            status_code=200,
            headers={},
        )

    def get_candles(self, instrument_id, direction, interval, count):
        self.history_calls.append((instrument_id, direction, interval, count))
        return self.response


class HistoricalCandleTests(unittest.TestCase):
    def test_normalizes_and_sorts_historical_candles(self):
        data = history_response().data
        normalized = normalize_candles(data, 1001, count=10, now_utc=NOW)
        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["timestamp"], "2026-11-21T00:00:00+00:00")
        self.assertEqual(normalized[1]["close"], "101")
        self.assertEqual(normalized[0]["volume"], "10")

    def test_rejects_incoherent_ohlc(self):
        data = history_response().data
        data["candles"][0]["candles"][0] = candle(
            "2026-11-22T00:00:00Z", opening="100", high="99", low="98", close="101"
        )
        with self.assertRaises(EtoroAPIError):
            normalize_candles(data, 1001, count=10, now_utc=NOW)

    def test_rejects_ungrouped_candles_without_instrument_identity(self):
        data = {"candles": [{
            "fromDate": "2026-11-22T00:00:00Z",
            "open": "100", "high": "102", "low": "99", "close": "101",
        }]}
        with self.assertRaises(EtoroAPIError):
            normalize_candles(data, 1001, count=10, now_utc=NOW)

    def test_removes_only_exact_duplicate_candle_rows(self):
        duplicate = candle("2026-11-22T00:00:00Z")
        data = {"candles": [{
            "instrumentId": 1001,
            "candles": [duplicate, dict(duplicate)],
        }]}
        diagnostics = {}
        normalized = normalize_candles(
            data, 1001, count=10, now_utc=NOW, diagnostics=diagnostics
        )
        self.assertEqual(len(normalized), 1)
        self.assertEqual(diagnostics["identical_duplicate_rows_removed"], 1)

    def test_rejects_duplicate_timestamps(self):
        data = history_response().data
        data["candles"][0]["candles"][1]["fromDate"] = "2026-11-22T00:00:00Z"
        with self.assertRaises(EtoroAPIError):
            normalize_candles(data, 1001, count=10, now_utc=NOW)

    def test_rejects_candle_for_different_instrument(self):
        data = history_response().data
        data["candles"][0]["candles"][0]["instrumentID"] = 9999
        with self.assertRaises(EtoroAPIError):
            normalize_candles(data, 1001, count=10, now_utc=NOW)

    def test_rejects_naive_and_future_timestamps(self):
        for timestamp in ("2026-11-22T00:00:00", "2026-11-24T00:00:00Z"):
            data = history_response().data
            data["candles"][0]["candles"][0]["fromDate"] = timestamp
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(EtoroAPIError):
                    normalize_candles(data, 1001, count=10, now_utc=NOW)

    def test_collects_history_and_labels_execution_basis_unconfirmed(self):
        client = FakeClient()
        report = collect_historical_candles(
            client,
            symbols={"AAPL": {"etoro_symbol": "AAPL", "type": "STOCK"}},
            now_utc=NOW,
            count=10,
            min_candles=2,
        )
        asset = report["assets"]["AAPL"]
        self.assertEqual(report["summary"]["status"], "COMPLETE")
        self.assertEqual(report["summary"]["assets_with_sufficient_history"], 1)
        self.assertEqual(client.history_calls, [(1001, "desc", "1d", 10)])
        self.assertFalse(report["account_data_accessed"])
        self.assertFalse(report["order_actions_performed"])
        self.assertFalse(asset["historical_candles"]["bid_ask_history_available"])
        self.assertFalse(asset["historical_candles"]["execution_price_basis_confirmed"])
        self.assertEqual(asset["historical_candles"]["price_adjustment_status"], "UNSPECIFIED")

    def test_insufficient_history_is_partial_not_zero_cost_or_complete(self):
        client = FakeClient(response=EtoroResponse(
            data={"candles": [{"instrumentId": 1001, "candles": [candle("2026-11-22T00:00:00Z")]}]},
            status_code=200,
            headers={},
        ))
        report = collect_historical_candles(
            client, symbols={"AAPL": {"etoro_symbol": "AAPL", "type": "STOCK"}},
            now_utc=NOW, count=10, min_candles=2,
        )
        self.assertEqual(report["summary"]["status"], "PARTIAL")
        self.assertFalse(report["assets"]["AAPL"]["historical_candles"]["sufficient_history"])

    def test_instrument_type_mismatch_blocks_history_request(self):
        client = FakeClient(instrument_type="Crypto")
        report = collect_historical_candles(
            client, symbols={"AAPL": {"etoro_symbol": "AAPL", "type": "STOCK"}},
            now_utc=NOW, count=10, min_candles=2,
        )
        self.assertEqual(report["summary"]["status"], "PARTIAL")
        self.assertEqual(client.history_calls, [])
        self.assertIsNone(report["assets"]["AAPL"]["historical_candles"])

    def test_history_client_allows_market_candles_but_blocks_account_endpoints(self):
        client = object.__new__(EtoroHistoricalCandleClient)
        response = EtoroResponse(data={"candles": []}, status_code=200, headers={})
        with patch("etoro_read_only.EtoroReadOnlyClient._request", return_value=response) as parent:
            allowed = client._request(
                "GET", "/api/v1/market-data/instruments/1001/history/candles/desc/OneDay/1000"
            )
            self.assertIs(allowed, response)
            parent.assert_called_once()
        with self.assertRaises(EtoroAPIError):
            client._request("GET", "/api/v1/trading/positions")
        with self.assertRaises(EtoroAPIError):
            client._request("GET", "/api/v1/trading/info/real/pnl")
        with self.assertRaises(EtoroAPIError):
            client._request(
                "GET", "/api/v1/market-data/instruments/1001/history/candles/desc/OneDay/1001"
            )

    def test_count_limits_are_enforced_before_network(self):
        client = FakeClient()
        for count in (0, 1001, True):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    collect_historical_candles(
                        client, symbols={"AAPL": {"etoro_symbol": "AAPL", "type": "STOCK"}},
                        now_utc=NOW, count=count,
                    )
        self.assertEqual(client.history_calls, [])


if __name__ == "__main__":
    unittest.main()
