import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

from etoro_market_evidence import EtoroMarketEvidenceClient, collect_market_evidence
from etoro_read_only import EtoroAPIError, EtoroCostEstimate, EtoroInstrumentEligibility, EtoroQuote, EtoroResponse


class FakeMarketEvidenceClient:
    def __init__(self):
        self.cost_payloads = []

    def search_instrument_by_symbol(self, symbol):
        iid = {"AAPL": 101, "BTC": 102}[symbol]
        return EtoroResponse({"results": [{
            "instrumentId": iid, "symbol": symbol, "displayName": symbol + " name",
            "type": "STOCK" if symbol == "AAPL" else "CRYPTO",
        }], "pagination": {"hasNext": False}}, 200, {})

    def get_validated_instrument_eligibility(self, ids):
        def item(iid, symbol, settlement):
            return EtoroInstrumentEligibility(
                instrument_id=iid, symbol=symbol, allow_open_position=True,
                supports_long=True, allow_mit_orders=True, allow_entry_orders=True,
                allow_exit_orders=True, allow_trailing_stop_loss=True,
                min_position_exposure=Decimal("50"), max_units_per_order=Decimal("1000"),
                units_quantity_type="FractionalUnits", order_fill_behavior_type="BestEffort",
                allowed_order_quantity_type="Both", trade_unit_type="Units",
                leverage_configs=({"settlementType": settlement, "direction": "LONG",
                    "leverageValues": [1], "isPotential": False, "minPositionAmount": 50},),
            )
        return {101: item(101, "AAPL", "REAL"), 102: item(102, "BTC", "CFD")}

    def get_realtime_quotes(self, ids, now, asset_calendars=None):
        iid = ids[0]
        return {iid: EtoroQuote(iid, Decimal("99"), Decimal("101"), now, "realtime")}

    def get_validated_what_if_costs(self, payload, now):
        self.cost_payloads.append(payload)
        return EtoroCostEstimate(payload["instrumentId"], "AAPL",
            {"marketSpread": Decimal("0.20"), "transactionFee": Decimal("1")}, now)


class MarketEvidenceTests(unittest.TestCase):
    def test_market_only_client_blocks_account_endpoints_before_network(self):
        session = Mock()
        client = EtoroMarketEvidenceClient("app", "user", session=session)
        with self.assertRaises(EtoroAPIError):
            client.get_account_pnl()
        with self.assertRaises(EtoroAPIError):
            client.get_instrument_breakdown(123)
        session.request.assert_not_called()

    def test_collects_quote_eligibility_and_open_cost_without_cfd_fallback(self):
        client = FakeMarketEvidenceClient()
        now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
        report = collect_market_evidence(client, symbols={
            "AAPL": {"market_calendar": "XNYS"}, "BTC": {"market_calendar": "24/7"}
        }, now_utc=now)
        self.assertEqual(report["summary"]["status"], "COMPLETE", report)
        self.assertEqual(report["assets"]["AAPL"]["quote"]["spread_absolute"], "2")
        self.assertEqual(report["assets"]["AAPL"]["quote"]["midpoint"], "100")
        self.assertEqual(report["assets"]["AAPL"]["quote"]["spread_bps"], "200")
        self.assertEqual(
            report["assets"]["AAPL"]["quote"]["spread_evidence_scope"],
            "CURRENT_SNAPSHOT_ONLY",
        )
        self.assertEqual(report["assets"]["AAPL"]["quote"]["age_seconds"], 0.0)
        self.assertTrue(report["assets"]["AAPL"]["eligibility"]["supports_real_unleveraged_long"])
        self.assertEqual(report["assets"]["AAPL"]["hypothetical_open_cost"]["scope"], "HYPOTHETICAL_OPEN_ONLY")
        self.assertFalse(report["assets"]["AAPL"]["hypothetical_open_cost"]["round_trip_cost_confirmed"])
        self.assertEqual(report["assets"]["BTC"]["hypothetical_open_cost"]["status"], "SKIPPED")
        self.assertEqual(len(client.cost_payloads), 1)
        self.assertEqual(client.cost_payloads[0]["settlementType"], "real")
        self.assertEqual(client.cost_payloads[0]["leverage"], 1)

    def test_cost_query_amount_is_at_least_account_minimum(self):
        client = FakeMarketEvidenceClient()
        original = client.get_validated_instrument_eligibility
        def higher_minimum(ids):
            result = original(ids)
            item = result[101]
            result[101] = EtoroInstrumentEligibility(
                **{**item.__dict__, "min_position_exposure": Decimal("250"),
                   "leverage_configs": ({"settlementType": "REAL", "direction": "LONG",
                     "leverageValues": [1], "isPotential": False, "minPositionAmount": 300},)}
            )
            return result
        client.get_validated_instrument_eligibility = higher_minimum
        collect_market_evidence(client, symbols={"AAPL": {"market_calendar": "XNYS"}},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(client.cost_payloads[0]["amount"], "300")

    def test_potential_real_configuration_does_not_trigger_cost_query(self):
        client = FakeMarketEvidenceClient()
        original = client.get_validated_instrument_eligibility
        def potential(ids):
            result = original(ids)
            item = result[101]
            result[101] = EtoroInstrumentEligibility(
                **{**item.__dict__, "leverage_configs": ({"settlementType": "REAL",
                  "direction": "LONG", "leverageValues": [1], "isPotential": True,
                  "minPositionAmount": 50},)}
            )
            return result
        client.get_validated_instrument_eligibility = potential
        report = collect_market_evidence(client, symbols={"AAPL": {"market_calendar": "XNYS"}},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(len(client.cost_payloads), 0)
        self.assertEqual(report["assets"]["AAPL"]["hypothetical_open_cost"]["status"], "SKIPPED")


    def test_missing_eligibility_record_marks_capture_partial(self):
        client = FakeMarketEvidenceClient()
        original = client.get_validated_instrument_eligibility

        def incomplete(ids):
            result = original(ids)
            result.pop(102, None)
            return result

        client.get_validated_instrument_eligibility = incomplete
        report = collect_market_evidence(
            client,
            symbols={"AAPL": {"market_calendar": "XNYS"}, "BTC": {"market_calendar": "24/7"}},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(report["summary"]["status"], "PARTIAL")
        self.assertEqual(report["summary"]["assets_with_errors"], 1)
        self.assertTrue(any(
            error.startswith("eligibility:")
            for error in report["assets"]["BTC"]["errors"]
        ))
        self.assertIsNone(report["assets"]["BTC"]["eligibility"])
        self.assertIsNone(report["assets"]["BTC"]["hypothetical_open_cost"])


if __name__ == "__main__":
    unittest.main()
