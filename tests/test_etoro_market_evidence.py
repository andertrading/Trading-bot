import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

from etoro_market_evidence import EtoroMarketEvidenceClient, collect_market_evidence
from etoro_read_only import EtoroAPIError, EtoroCostEstimate, EtoroInstrumentEligibility, EtoroQuote, EtoroResponse


class FakeMarketEvidenceClient:
    def __init__(self):
        self.cost_payloads = []
        self.quote_requests = 0

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
        self.quote_requests += 1
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
            "AAPL": {"market_calendar": "XNYS", "type": "STOCK"}, "BTC": {"market_calendar": "24/7", "type": "CRYPTO"}
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

    def test_closed_market_quote_skips_stale_hypothetical_cost_request(self):
        class ClosedMarketClient(FakeMarketEvidenceClient):
            def get_realtime_quotes(self, ids, now, asset_calendars=None):
                self.quote_requests += 1
                iid = ids[0]
                return {iid: EtoroQuote(
                    iid, Decimal("99"), Decimal("101"), now - timedelta(hours=48),
                    "last_session",
                )}

        client = ClosedMarketClient()
        report = collect_market_evidence(
            client,
            symbols={"AAPL": {"market_calendar": "XNYS", "type": "STOCK"}},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
        )

        asset = report["assets"]["AAPL"]
        self.assertEqual(asset["quote"]["quote_type"], "last_session")
        self.assertEqual(
            asset["quote"]["spread_evidence_scope"],
            "LAST_COMPLETED_SESSION_ONLY",
        )
        self.assertEqual(
            asset["hypothetical_open_cost"]["status"],
            "SKIPPED_MARKET_CLOSED",
        )
        self.assertEqual(client.cost_payloads, [])
        self.assertEqual(report["summary"]["assets_with_last_session_quotes"], 1)
        self.assertEqual(report["summary"]["assets_with_market_closed_cost_skips"], 1)
        self.assertEqual(report["summary"]["assets_with_validated_open_costs"], 0)
        self.assertEqual(report["summary"]["status"], "PARTIAL")

    def test_quote_failure_skips_hypothetical_cost_request(self):
        class QuoteFailureClient(FakeMarketEvidenceClient):
            def get_realtime_quotes(self, ids, now, asset_calendars=None):
                self.quote_requests += 1
                raise EtoroAPIError("Cotización no disponible")

        client = QuoteFailureClient()
        report = collect_market_evidence(
            client,
            symbols={"AAPL": {"market_calendar": "XNYS", "type": "STOCK"}},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
        )

        asset = report["assets"]["AAPL"]
        self.assertIsNone(asset["quote"])
        self.assertEqual(
            asset["hypothetical_open_cost"]["status"],
            "SKIPPED_NO_REALTIME_QUOTE",
        )
        self.assertEqual(client.cost_payloads, [])
        self.assertEqual(report["summary"]["assets_with_validated_open_costs"], 0)
        self.assertEqual(report["summary"]["status"], "PARTIAL")

    def test_configured_etoro_symbol_is_used_for_exact_instrument_resolution(self):
        class ExactEtoroSymbolClient(FakeMarketEvidenceClient):
            def __init__(self):
                super().__init__()
                self.searched_symbols = []

            def search_instrument_by_symbol(self, symbol):
                self.searched_symbols.append(symbol)
                return EtoroResponse({"results": [{
                    "instrumentId": 101,
                    "symbol": "CSPX.L",
                    "displayName": "iShares Core S&P 500 UCITS ETF",
                    "type": "ETF",
                }], "pagination": {"hasNext": False}}, 200, {})

        client = ExactEtoroSymbolClient()
        report = collect_market_evidence(
            client,
            symbols={"CSPX": {
                "etoro_symbol": "CSPX.L",
                "market_calendar": "XLON",
                "type": "ETF",
            }},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(client.searched_symbols, ["CSPX.L"])
        self.assertEqual(report["summary"]["resolved_instruments"], 1)
        self.assertEqual(report["assets"]["CSPX"]["instrument"]["symbol"], "CSPX.L")
        self.assertFalse(any(
            error.startswith("instrument_resolution:")
            for error in report["assets"]["CSPX"]["errors"]
        ))

    def test_live_capture_uses_fresh_clock_for_each_sequential_request(self):
        class AdvancingDateTime:
            calls = 0
            base = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                cls.calls += 1
                value = cls.base + timedelta(seconds=8 * (cls.calls - 1))
                return value if tz is None else value.astimezone(tz)

        client = FakeMarketEvidenceClient()
        with patch("etoro_market_evidence.datetime", AdvancingDateTime):
            report = collect_market_evidence(client, symbols={
                "AAPL": {"market_calendar": "XNYS", "type": "STOCK"},
                "BTC": {"market_calendar": "24/7", "type": "CRYPTO"},
            })

        self.assertEqual(report["summary"]["status"], "COMPLETE", report)
        self.assertEqual(report["assets"]["AAPL"]["quote"]["age_seconds"], 8.0)
        self.assertEqual(report["assets"]["BTC"]["quote"]["age_seconds"], 8.0)
        self.assertEqual(len(client.cost_payloads), 1)
        self.assertEqual(
            report["assets"]["AAPL"]["hypothetical_open_cost"]["last_updated"],
            "2026-11-22T12:00:24+00:00",
        )

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
        collect_market_evidence(client, symbols={"AAPL": {"market_calendar": "XNYS", "type": "STOCK"}},
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
        report = collect_market_evidence(client, symbols={"AAPL": {"market_calendar": "XNYS", "type": "STOCK"}},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(len(client.cost_payloads), 0)
        self.assertEqual(report["assets"]["AAPL"]["hypothetical_open_cost"]["status"], "SKIPPED")


    def test_v1_exact_symbol_search_fallback_resolves_etf_without_fuzzy_matching(self):
        class V1FallbackClient(FakeMarketEvidenceClient):
            def __init__(self):
                super().__init__()
                self.fallback_filters = []

            def search_instrument_by_symbol(self, symbol):
                raise EtoroAPIError("eToro devolvió HTTP 404")

            def search_instruments(self, filters):
                self.fallback_filters.append(dict(filters))
                return EtoroResponse({
                    "items": [{
                        "instrumentId": 101,
                        "internalSymbolFull": "CSPX",
                        "displayname": "iShares Core S&P 500 UCITS ETF",
                        "instrumentType": "ETFs",
                        "isCurrentlyTradable": True,
                        "isActiveInPlatform": True,
                        "isBuyEnabled": True,
                        "isDelisted": False,
                    }],
                    "totalItems": 1,
                }, 200, {})

        client = V1FallbackClient()
        report = collect_market_evidence(
            client,
            symbols={"CSPX": {"market_calendar": "XLON", "type": "ETF"}},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(client.fallback_filters, [{"internalSymbolFull": "CSPX"}])
        self.assertEqual(report["summary"]["resolved_instruments"], 1)
        self.assertEqual(
            report["assets"]["CSPX"]["instrument"]["symbol"], "CSPX"
        )
        self.assertFalse(any(
            "instrument_resolution" in error
            for error in report["assets"]["CSPX"]["errors"]
        ))

    def test_instrument_type_mismatch_blocks_quote_and_cost_capture(self):
        class WrongTypeClient(FakeMarketEvidenceClient):
            def search_instrument_by_symbol(self, symbol):
                response = super().search_instrument_by_symbol(symbol)
                if symbol == "BTC":
                    response.data["results"][0]["type"] = "ETF"
                return response

        client = WrongTypeClient()
        report = collect_market_evidence(
            client,
            symbols={"AAPL": {"market_calendar": "XNYS", "type": "STOCK"},
                     "BTC": {"market_calendar": "24/7", "type": "CRYPTO"}},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(report["summary"]["status"], "PARTIAL")
        self.assertEqual(report["summary"]["resolved_instruments"], 1)
        self.assertIsNotNone(report["assets"]["AAPL"]["quote"])
        self.assertIsNone(report["assets"]["BTC"]["quote"])
        self.assertTrue(any(
            "instrument type does not match" in error
            for error in report["assets"]["BTC"]["errors"]
        ))
        self.assertEqual(client.quote_requests, 1)
        self.assertEqual(len(client.cost_payloads), 1)

    def test_duplicate_instrument_ids_block_both_configured_assets(self):
        class DuplicateInstrumentClient(FakeMarketEvidenceClient):
            def search_instrument_by_symbol(self, symbol):
                response = super().search_instrument_by_symbol(symbol)
                response.data["results"][0]["instrumentId"] = 101
                return response

        client = DuplicateInstrumentClient()
        report = collect_market_evidence(
            client,
            symbols={"AAPL": {"market_calendar": "XNYS", "type": "STOCK"},
                     "BTC": {"market_calendar": "24/7", "type": "CRYPTO"}},
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(report["summary"]["status"], "PARTIAL")
        self.assertEqual(report["summary"]["resolved_instruments"], 0)
        for asset in ("AAPL", "BTC"):
            self.assertTrue(any(
                "duplicate eToro instrument ID 101" in error
                for error in report["assets"][asset]["errors"]
            ))
            self.assertIsNone(report["assets"][asset]["eligibility"])
            self.assertIsNone(report["assets"][asset]["quote"])
        self.assertEqual(client.quote_requests, 0)
        self.assertEqual(client.cost_payloads, [])

    def test_eligibility_failure_isolated_to_affected_instrument(self):
        class OneInstrumentFailureClient(FakeMarketEvidenceClient):
            def __init__(self):
                super().__init__()
                self.eligibility_requests = []

            def get_validated_instrument_eligibility(self, ids):
                self.eligibility_requests.append(list(ids))
                if ids == [101]:
                    raise EtoroAPIError("direction inválida en leverage config: 'BUY_SIDE'")
                return super().get_validated_instrument_eligibility(ids)

        client = OneInstrumentFailureClient()
        report = collect_market_evidence(
            client,
            symbols={
                "AAPL": {"market_calendar": "XNYS", "type": "STOCK"},
                "BTC": {"market_calendar": "24/7", "type": "CRYPTO"},
            },
            now_utc=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(client.eligibility_requests, [[101], [102]])
        self.assertEqual(report["summary"]["status"], "PARTIAL")
        self.assertIsNone(report["assets"]["AAPL"]["eligibility"])
        self.assertIn(
            "direction inválida",
            " ".join(report["assets"]["AAPL"]["errors"]),
        )
        self.assertIsNotNone(report["assets"]["BTC"]["eligibility"])
        self.assertIsNotNone(report["assets"]["BTC"]["hypothetical_open_cost"])

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
            symbols={"AAPL": {"market_calendar": "XNYS", "type": "STOCK"}, "BTC": {"market_calendar": "24/7", "type": "CRYPTO"}},
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
