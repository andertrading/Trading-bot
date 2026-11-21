import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

from etoro_read_only import EtoroAPIError, EtoroAccountSnapshot, EtoroQuote, EtoroReadOnlyClient, EtoroResponse
from etoro_position_risk import (
    calculate_account_gross_stop_risk_from_client,
    calculate_account_gross_stop_risk_usd,
    calculate_position_gross_stop_risk_usd,
    normalize_instrument_breakdown,
)


NOW = datetime(2026, 11, 21, 22, 30, tzinfo=timezone.utc)


def position(**overrides):
    data = {
        "positionId": 7001,
        "instrumentId": 101,
        "assetCurrency": "USD",
        "settlementType": "real",
        "direction": "long",
        "leverage": 1,
        "stopLossRate": 95,
        "stopLossType": "fixed",
        "units": 2,
        "currentRate": 100,
        "currentConversionRate": 1,
        "currentExposureAcctCcy": 200,
        "rateTimestamp": NOW.isoformat().replace("+00:00", "Z"),
    }
    data.update(overrides)
    return data


def breakdown(position_data=None, **overrides):
    item = position() if position_data is None else position_data
    data = {
        "accountCurrency": "USD",
        "timestamp": NOW.isoformat().replace("+00:00", "Z"),
        "instruments": [{
            "instrumentId": item["instrumentId"],
            "positions": [item],
        }],
    }
    data.update(overrides)
    return data


def quote(instrument_id=101, bid="99", ask="101"):
    return EtoroQuote(
        instrument_id=instrument_id,
        bid=Decimal(bid),
        ask=Decimal(ask),
        timestamp=NOW,
        quote_type="realtime",
    )


class InstrumentBreakdownClientTests(unittest.TestCase):
    def test_instrument_breakdown_uses_read_only_endpoint_and_cid_header(self):
        session = Mock()
        session.request.return_value = Mock(
            status_code=200,
            headers={},
            json=lambda: {"accountCurrency": "USD", "timestamp": "2026-11-21T22:30:00Z", "instruments": []},
        )
        client = EtoroReadOnlyClient("app-key", "user-key", session=session)
        response = client.get_instrument_breakdown(12345)
        self.assertEqual(response.status_code, 200)
        args, kwargs = session.request.call_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(args[1].endswith("/api/v2/trading/info/instrument-breakdown"))
        self.assertEqual(kwargs["headers"]["CID"], "12345")
        self.assertEqual(kwargs["headers"]["x-api-key"], "app-key")
        self.assertIsNone(kwargs["json"])

    def test_invalid_cid_is_rejected_without_request(self):
        session = Mock()
        client = EtoroReadOnlyClient("app-key", "user-key", session=session)
        for value in (0, -1, True, "123"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.get_instrument_breakdown(value)
        session.request.assert_not_called()


class InstrumentBreakdownNormalizationTests(unittest.TestCase):
    def test_normalizes_exact_fresh_position_set(self):
        result = normalize_instrument_breakdown(
            EtoroResponse(breakdown(), 200, {}),
            expected_client_id=123,
            expected_position_ids=[7001],
            now_utc=NOW,
        )
        self.assertEqual(result[7001]["instrumentId"], 101)

    def test_missing_duplicate_or_unexpected_position_fails_closed(self):
        cases = [
            (breakdown(position_data=position(positionId=7002)), [7001]),
            (breakdown(position_data=position()), [7001, 7002]),
            ({"accountCurrency": "USD", "timestamp": NOW.isoformat(), "instruments": [
                {"instrumentId": 101, "positions": [position(), position()]}
            ]}, [7001]),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload), self.assertRaises(EtoroAPIError):
                normalize_instrument_breakdown(
                    EtoroResponse(payload, 200, {}), 123, expected, NOW
                )

    def test_non_usd_or_stale_snapshot_is_rejected(self):
        payload = breakdown(accountCurrency="EUR")
        with self.assertRaises(EtoroAPIError):
            normalize_instrument_breakdown(EtoroResponse(payload, 200, {}), 123, [7001], NOW)
        payload = breakdown(timestamp=(NOW - timedelta(seconds=90)).isoformat())
        with self.assertRaises(EtoroAPIError):
            normalize_instrument_breakdown(EtoroResponse(payload, 200, {}), 123, [7001], NOW)


class PositionGrossStopRiskTests(unittest.TestCase):
    def test_long_uses_executable_bid_and_units(self):
        result = calculate_position_gross_stop_risk_usd(position(), quote(bid="99", ask="100"), "STOCK")
        self.assertEqual(result["gross_stop_risk_usd"], Decimal("8"))
        self.assertEqual(result["risk_usd"], Decimal("10"))
        self.assertEqual(result["estimated_close_fee_usd"], Decimal("2"))
        self.assertTrue(result["complete"])

    def test_short_uses_executable_ask_and_units(self):
        short = position(direction="short", stopLossRate=110)
        result = calculate_position_gross_stop_risk_usd(short, quote(bid="99", ask="101"), "STOCK")
        self.assertEqual(result["gross_stop_risk_usd"], Decimal("18"))
        self.assertEqual(result["risk_usd"], Decimal("20"))

    def test_profit_protecting_stop_is_not_rejected_by_entry_side_assumption(self):
        # The stop is above a hypothetical long entry; only current executable
        # price versus the current stop is relevant to this gross giveback risk.
        long = position(stopLossRate=105)
        result = calculate_position_gross_stop_risk_usd(long, quote(bid="110", ask="111"), "STOCK")
        self.assertEqual(result["gross_stop_risk_usd"], Decimal("10"))

    def test_unsupported_settlement_currency_leverage_and_trailing_stop_block(self):
        invalid_positions = [
            position(settlementType="cfd"),
            position(settlementType="realFutures"),
            position(assetCurrency="EUR"),
            position(leverage=2),
            position(stopLossType="trailing"),
        ]
        for item in invalid_positions:
            with self.subTest(item=item), self.assertRaises(EtoroAPIError):
                calculate_position_gross_stop_risk_usd(item, quote(), "STOCK")

    def test_inconsistent_units_vs_etoro_exposure_blocks(self):
        with self.assertRaises(EtoroAPIError):
            calculate_position_gross_stop_risk_usd(
                position(currentExposureAcctCcy=250), quote(), "STOCK"
            )

    def test_stop_already_touched_or_quote_mismatch_blocks(self):
        with self.assertRaises(EtoroAPIError):
            calculate_position_gross_stop_risk_usd(
                position(stopLossRate=100), quote(bid="99", ask="100"), "STOCK"
            )
        with self.assertRaises(EtoroAPIError):
            calculate_position_gross_stop_risk_usd(position(), quote(instrument_id=999), "STOCK")

    def test_account_aggregate_remains_unvalidated_until_close_costs_are_supported(self):
        positions = {7001: position(), 7002: position(positionId=7002, instrumentId=102)}
        quotes = {101: quote(), 102: quote(instrument_id=102, bid="98", ask="99")}
        result = calculate_account_gross_stop_risk_usd(
            positions, quotes, {101: "STOCK", 102: "ETF"}
        )
        self.assertEqual(result["gross_stop_risk_usd"], Decimal("8") + Decimal("6"))
        self.assertEqual(result["estimated_close_fees_usd"], Decimal("2"))
        self.assertEqual(result["risk_usd"], Decimal("16"))
        self.assertTrue(result["complete"])

    def test_etf_and_crypto_use_published_maximum_close_fees(self):
        etf = calculate_position_gross_stop_risk_usd(position(), quote(), "ETF")
        self.assertEqual(etf["estimated_close_fee_usd"], Decimal("0"))
        self.assertEqual(etf["risk_usd"], Decimal("8"))

        crypto_position = position(stopLossRate=95)
        crypto = calculate_position_gross_stop_risk_usd(
            crypto_position, quote(), "CRYPTO"
        )
        self.assertEqual(crypto["estimated_close_fee_usd"], Decimal("1.90"))
        self.assertEqual(crypto["risk_usd"], Decimal("9.90"))

    def test_unknown_fee_class_blocks_risk(self):
        with self.assertRaises(EtoroAPIError):
            calculate_position_gross_stop_risk_usd(position(), quote(), "UNKNOWN")

    def test_snapshot_with_unbounded_stop_blocks_end_to_end_risk(self):
        snapshot = EtoroAccountSnapshot(
            account_currency="USD",
            account_currency_source="bot_configuration",
            client_id=123,
            available_cash=Decimal("500"),
            unrealized_pnl=Decimal("0"),
            positions=({"positionId": 7001, "instrumentId": 101, "cid": 123},),
            has_unbounded_stop_risk=True,
        )
        with self.assertRaises(EtoroAPIError):
            calculate_account_gross_stop_risk_from_client(
                Mock(), snapshot, NOW, {101: "STOCK"}
            )

    def test_empty_portfolio_has_zero_open_position_risk(self):
        result = calculate_account_gross_stop_risk_usd({}, {}, {})
        self.assertEqual(result["risk_usd"], Decimal("0"))
        self.assertTrue(result["complete"])

    def test_position_mapping_key_must_match_position_id(self):
        with self.assertRaises(EtoroAPIError):
            calculate_account_gross_stop_risk_usd({9999: position()}, {101: quote()}, {101: "STOCK"})

    def test_end_to_end_read_only_path_fetches_breakdown_and_quotes(self):
        pnl_position = {
            "positionId": 7001,
            "instrumentId": 101,
            "cid": 123,
        }
        snapshot = EtoroAccountSnapshot(
            account_currency="USD",
            account_currency_source="bot_configuration",
            client_id=123,
            available_cash=Decimal("500"),
            unrealized_pnl=Decimal("0"),
            positions=(pnl_position,),
            has_unbounded_stop_risk=False,
        )
        client = Mock()
        client.get_instrument_breakdown.return_value = EtoroResponse(breakdown(), 200, {})
        client.get_realtime_quotes.return_value = {101: quote()}
        result = calculate_account_gross_stop_risk_from_client(client, snapshot, NOW, {101: "STOCK"})
        self.assertEqual(result["gross_stop_risk_usd"], Decimal("8"))
        self.assertEqual(result["risk_usd"], Decimal("10"))
        client.get_instrument_breakdown.assert_called_once_with(123)
        client.get_realtime_quotes.assert_called_once_with([101], NOW)

    def test_end_to_end_path_rejects_instrument_mismatch(self):
        pnl_position = {"positionId": 7001, "instrumentId": 999, "cid": 123}
        snapshot = EtoroAccountSnapshot(
            account_currency="USD",
            account_currency_source="bot_configuration",
            client_id=123,
            available_cash=Decimal("500"),
            unrealized_pnl=Decimal("0"),
            positions=(pnl_position,),
            has_unbounded_stop_risk=False,
        )
        client = Mock()
        client.get_instrument_breakdown.return_value = EtoroResponse(breakdown(), 200, {})
        with self.assertRaises(EtoroAPIError):
            calculate_account_gross_stop_risk_from_client(client, snapshot, NOW, {999: "STOCK"})
        client.get_realtime_quotes.assert_not_called()

    def test_missing_quote_blocks_aggregate(self):
        with self.assertRaises(EtoroAPIError):
            calculate_account_gross_stop_risk_usd({7001: position()}, {}, {101: "STOCK"})


if __name__ == "__main__":
    unittest.main()
