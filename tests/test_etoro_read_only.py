import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

import requests

from etoro_read_only import (
    EtoroAPIError,
    EtoroConfigurationError,
    EtoroReadOnlyClient,
    calculate_available_cash,
    normalize_account_snapshot,
)


class FakeResponse:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self._data = {} if data is None else data
        self.headers = headers or {}

    def json(self):
        return self._data


class EtoroReadOnlyClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = EtoroReadOnlyClient(
            "app-secret-test",
            "user-secret-test",
            session=self.session,
        )

    def test_credentials_are_required_before_any_request(self):
        session = Mock()
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(EtoroConfigurationError):
                EtoroReadOnlyClient(session=session)
        session.request.assert_not_called()

    def test_account_endpoint_uses_read_only_auth_headers(self):
        self.session.request.return_value = FakeResponse(data={"credit": 100})
        result = self.client.get_account_pnl()
        self.assertEqual(result.data, {"credit": 100})
        args, kwargs = self.session.request.call_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(args[1].endswith("/api/v1/trading/info/real/pnl"))
        self.assertEqual(kwargs["headers"]["x-api-key"], "app-secret-test")
        self.assertEqual(kwargs["headers"]["x-user-key"], "user-secret-test")
        self.assertTrue(kwargs["headers"]["x-request-id"])
        self.assertEqual(kwargs["timeout"], 10)
        self.assertFalse(kwargs["allow_redirects"])

    def test_partial_rates_are_exposed_not_misrepresented_as_complete(self):
        self.session.request.return_value = FakeResponse(
            status_code=206,
            data={"rates": [{"instrumentId": 1}]},
        )
        result = self.client.get_rates([1, 2])
        self.assertEqual(result.status_code, 206)

    def test_partial_account_snapshot_fails_closed(self):
        self.session.request.return_value = FakeResponse(
            status_code=206,
            data={"credit": 100},
        )
        with self.assertRaises(EtoroAPIError):
            self.client.get_account_pnl()

    def test_invalid_instrument_ids_are_rejected_without_request(self):
        with self.assertRaises(ValueError):
            self.client.get_rates([1, 0])
        self.session.request.assert_not_called()

    def test_candle_request_is_bounded_and_uses_documented_path(self):
        self.session.request.return_value = FakeResponse(data={"candles": []})
        self.client.get_candles(123, "desc", "15m", 500)
        args, _ = self.session.request.call_args
        self.assertTrue(args[1].endswith(
            "/api/v1/market-data/instruments/123/history/candles/desc/15m/500"
        ))

    def test_candle_count_above_api_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client.get_candles(123, "desc", "15m", 1001)
        self.session.request.assert_not_called()

    def test_what_if_endpoint_only_accepts_open_action(self):
        self.session.request.return_value = FakeResponse(data={"costs": []})
        self.client.get_what_if_costs({"action": "open", "instrumentId": 1})
        args, kwargs = self.session.request.call_args
        self.assertEqual(args[0], "POST")
        self.assertTrue(args[1].endswith("/api/v2/trading/info/costs"))
        self.assertEqual(kwargs["json"]["action"], "open")
        with self.assertRaises(ValueError):
            self.client.get_what_if_costs({"action": "close"})
        self.assertEqual(self.session.request.call_count, 1)

    def test_rate_limit_retries_are_bounded_and_honor_retry_after(self):
        self.session.request.side_effect = [
            FakeResponse(status_code=429, headers={"Retry-After": "0"}),
            FakeResponse(data={"credit": 100}),
        ]
        with patch("etoro_read_only.time.sleep") as sleep:
            result = self.client.get_account_pnl()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.session.request.call_count, 2)
        sleep.assert_called_once_with(0.0)

    def test_server_errors_retry_but_auth_errors_do_not(self):
        self.session.request.side_effect = [
            FakeResponse(status_code=503),
            FakeResponse(data={"credit": 100}),
        ]
        with patch("etoro_read_only.time.sleep"):
            self.client.get_account_pnl()
        self.assertEqual(self.session.request.call_count, 2)

        self.session.reset_mock()
        self.session.request.side_effect = None
        self.session.request.return_value = FakeResponse(status_code=403)
        with self.assertRaises(EtoroAPIError):
            self.client.get_account_pnl()
        self.assertEqual(self.session.request.call_count, 1)

    def test_network_failures_are_retried_and_error_is_redacted(self):
        self.session.request.side_effect = [
            requests.ConnectionError("secret should not leak"),
            FakeResponse(data={"credit": 100}),
        ]
        with patch("etoro_read_only.time.sleep"):
            result = self.client.get_account_pnl()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.session.request.call_count, 2)

    def test_unsupported_endpoint_is_not_exposed(self):
        with self.assertRaises(EtoroAPIError):
            self.client._request("POST", "/api/v1/trading/orders")
        self.session.request.assert_not_called()

    def test_candle_path_traversal_is_rejected(self):
        with self.assertRaises(EtoroAPIError):
            self.client._request(
                "GET",
                "/api/v1/market-data/instruments/1/../../trading/orders/history/candles/desc/15m/10",
            )
        self.session.request.assert_not_called()

    def test_private_cost_request_cannot_bypass_open_only_guard(self):
        with self.assertRaises(EtoroAPIError):
            self.client._request(
                "POST",
                "/api/v2/trading/info/costs",
                json_body={"action": "close"},
            )
        self.session.request.assert_not_called()


class AvailableCashTests(unittest.TestCase):
    def test_documented_available_cash_formula(self):
        payload = {
            "credit": 1000,
            "ordersForOpen": [
                {"amount": 100, "mirrorId": 0},
                {"amount": 200, "mirrorId": 42},
            ],
            "orders": [{"amount": 50}],
        }
        self.assertEqual(calculate_available_cash(payload), 850)

    def test_guide_casing_variant_is_supported_only_when_unambiguous(self):
        payload = {
            "credits": "500.00",
            "ordersForOpen": [{"amount": "25", "mirrorID": "0"}],
            "orders": [],
        }
        self.assertEqual(calculate_available_cash(payload), 475)

    def test_ambiguous_credit_casing_fails_closed(self):
        payload = {
            "credit": 500,
            "credits": 500,
            "ordersForOpen": [],
            "orders": [],
        }
        with self.assertRaises(EtoroAPIError):
            calculate_available_cash(payload)

    def test_missing_orders_or_invalid_amount_fails_closed(self):
        with self.assertRaises(EtoroAPIError):
            calculate_available_cash({"credit": 500, "ordersForOpen": []})
        with self.assertRaises(EtoroAPIError):
            calculate_available_cash({
                "credit": 500,
                "ordersForOpen": [],
                "orders": [{"amount": "NaN"}],
            })

    def test_negative_available_cash_fails_closed(self):
        with self.assertRaises(EtoroAPIError):
            calculate_available_cash({
                "credit": 10,
                "ordersForOpen": [{"amount": 8, "mirrorId": 0}],
                "orders": [{"amount": 5}],
            })

    def test_negative_order_amount_cannot_inflate_available_cash(self):
        with self.assertRaises(EtoroAPIError):
            calculate_available_cash({
                "credit": 100,
                "ordersForOpen": [],
                "orders": [{"amount": -50}],
            })


class AccountSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "accountCurrencyId": 1,
            "unrealizedPnL": "12.50",
            "credit": "1000",
            "ordersForOpen": [{"amount": "100", "mirrorId": 0}],
            "orders": [],
            "clientPortfolio": {
                "positions": [{
                    "positionID": 10,
                    "instrumentID": 20,
                    "isBuy": True,
                    "openRate": "100",
                    "stopLossRate": "95",
                    "amount": "200",
                    "leverage": "1",
                    "units": "2",
                    "settlementTypeID": 1,
                    "isNoStopLoss": False,
                }]
            },
        }

    def test_valid_snapshot_confirms_usd_and_cash_without_inventing_risk(self):
        snapshot = normalize_account_snapshot(self.payload)
        self.assertEqual(snapshot.account_currency, "USD")
        self.assertEqual(snapshot.available_cash, 900)
        self.assertEqual(snapshot.unrealized_pnl, Decimal("12.50"))
        self.assertEqual(len(snapshot.positions), 1)
        self.assertFalse(snapshot.has_unbounded_stop_risk)

    def test_explicit_empty_positions_is_not_confused_with_missing_positions(self):
        self.payload["clientPortfolio"]["positions"] = []
        snapshot = normalize_account_snapshot(self.payload)
        self.assertEqual(snapshot.positions, ())
        self.assertFalse(snapshot.has_unbounded_stop_risk)

    def test_missing_positions_does_not_become_an_empty_portfolio(self):
        del self.payload["clientPortfolio"]["positions"]
        with self.assertRaises(EtoroAPIError):
            normalize_account_snapshot(self.payload)

    def test_non_usd_or_ambiguous_currency_fails_closed(self):
        self.payload["accountCurrencyId"] = 2
        with self.assertRaises(EtoroAPIError):
            normalize_account_snapshot(self.payload)
        self.payload["accountCurrencyId"] = "1"
        with self.assertRaises(EtoroAPIError):
            normalize_account_snapshot(self.payload)

    def test_no_stop_position_is_preserved_and_flagged_as_unbounded(self):
        self.payload["clientPortfolio"]["positions"][0]["isNoStopLoss"] = True
        self.payload["clientPortfolio"]["positions"][0]["stopLossRate"] = None
        snapshot = normalize_account_snapshot(self.payload)
        self.assertTrue(snapshot.has_unbounded_stop_risk)
        self.assertIsNone(snapshot.positions[0]["stopLossRate"])

    def test_missing_stop_for_position_claiming_to_have_one_is_rejected(self):
        self.payload["clientPortfolio"]["positions"][0]["stopLossRate"] = None
        with self.assertRaises(EtoroAPIError):
            normalize_account_snapshot(self.payload)

    def test_unsupported_settlement_type_is_rejected(self):
        self.payload["clientPortfolio"]["positions"][0]["settlementTypeID"] = 99
        with self.assertRaises(EtoroAPIError):
            normalize_account_snapshot(self.payload)

    def test_invalid_position_direction_is_rejected(self):
        self.payload["clientPortfolio"]["positions"][0]["isBuy"] = 1
        with self.assertRaises(EtoroAPIError):
            normalize_account_snapshot(self.payload)


if __name__ == "__main__":
    unittest.main()
