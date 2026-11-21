import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from etoro_read_only import EtoroAccountSnapshot, EtoroResponse
from etoro_read_only_audit import run_audit


class FakeAuditClient:
    def __init__(self, *, account_ok=True, missing_symbols=(), rejected_fields=()):
        self.account_ok = account_ok
        self.missing_symbols = set(missing_symbols)
        self.rejected_fields = set(rejected_fields)
        self.search_calls = []
        self.quote_calls = []

    def get_account_snapshot(self, account_currency):
        if not self.account_ok:
            raise RuntimeError("private account data must not be logged")
        return EtoroAccountSnapshot(
            account_currency=account_currency,
            account_currency_source="bot_configuration",
            client_id=None,
            available_cash=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            positions=(),
            has_unbounded_stop_risk=False,
        )

    def search_instruments(self, filters, *, page_size=100, page_number=1):
        self.search_calls.append(dict(filters))
        field, value = next(iter(filters.items()))
        if field in self.rejected_fields:
            raise RuntimeError("simulated unsupported search filter")
        symbol = value
        if symbol in self.missing_symbols:
            items = []
        elif field == "internalSymbolFull" and value == "TSLA":
            items = [{
                "instrumentId": 123,
                "displayname": "Tesla",
                "internalSymbolFull": "TSLA",
                "instrumentType": "Stock",
                "isCurrentlyTradable": True,
                "isActiveInPlatform": True,
                "isBuyEnabled": True,
                "isDelisted": False,
            }]
        elif field == "displayname" and value == "Tesla":
            items = [{
                "instrumentId": 123,
                "displayname": "Tesla",
                "internalSymbolFull": "TSLA",
                "instrumentType": "Stock",
                "isCurrentlyTradable": True,
                "isActiveInPlatform": True,
                "isBuyEnabled": True,
                "isDelisted": False,
            }]
        else:
            items = []
        return EtoroResponse({"items": items, "totalItems": len(items)}, 200, {})

    def get_validated_instrument_eligibility(self, instrument_ids, currency="USD"):
        return {
            instrument_id: SimpleNamespace(supports_long=True)
            for instrument_id in instrument_ids
        }

    def get_realtime_quotes(self, instrument_ids, now_utc, max_age_seconds):
        self.quote_calls.append((list(instrument_ids), now_utc, max_age_seconds))
        return {instrument_id: object() for instrument_id in instrument_ids}


class ReadOnlyAuditTests(unittest.TestCase):
    def setUp(self):
        self.assets = {
            "TSLA": {
                "name": "Tesla",
                "yahoo": "TSLA",
                "type": "STOCK",
                "enabled": True,
            }
        }
        self.now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)

    def test_success_report_contains_only_public_ids_and_status_flags(self):
        client = FakeAuditClient()
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertTrue(report["account_snapshot_schema_valid"])
        self.assertTrue(report["instrument_eligibility_validated"])
        self.assertTrue(report["complete_realtime_quotes_validated"])
        self.assertTrue(report["gross_position_stop_risk_diagnostic_validated"])
        self.assertTrue(report["complete_position_risk_validated"])
        self.assertFalse(report["portfolio_risk_admission_ready"])
        self.assertTrue(report["overall_pass"])
        self.assertEqual(report["instrument_resolution"][0]["instrument_id"], 123)
        self.assertNotIn("available_cash", str(report))
        self.assertNotIn("positions", str(report))
        self.assertEqual(client.quote_calls[0][0], [123])

    def test_unresolved_asset_fails_without_guessing_an_id(self):
        client = FakeAuditClient(missing_symbols={"TSLA", "Tesla"})
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertFalse(report["overall_pass"])
        self.assertFalse(report["instrument_resolution"][0]["candidate_found"])
        self.assertIsNone(report["instrument_resolution"][0]["instrument_id"])
        self.assertEqual(client.quote_calls, [])


    def test_search_continues_after_first_filter_error(self):
        client = FakeAuditClient(rejected_fields={"internalSymbolFull"})
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertTrue(report["instrument_resolution"][0]["candidate_found"])
        self.assertEqual(
            report["instrument_resolution"][0]["resolution_status"], "RESOLVED"
        )
        self.assertEqual(
            client.search_calls,
            [
                {"internalSymbolFull": "TSLA"},
                {"displayname": "Tesla"},
            ],
        )

    def test_account_snapshot_failure_is_redacted_and_blocks_overall_pass(self):
        client = FakeAuditClient(account_ok=False)
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertFalse(report["account_snapshot_schema_valid"])
        self.assertFalse(report["gross_position_stop_risk_diagnostic_validated"])
        self.assertFalse(report["overall_pass"])
        self.assertNotIn("private account data", str(report))

    def test_quote_validation_failure_blocks_overall_pass(self):
        client = FakeAuditClient()
        client.get_realtime_quotes = Mock(side_effect=RuntimeError("sensitive quote payload"))
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertFalse(report["complete_realtime_quotes_validated"])
        self.assertFalse(report["overall_pass"])
        self.assertNotIn("sensitive quote payload", str(report))

    def test_long_ineligible_instrument_blocks_overall_pass(self):
        client = FakeAuditClient()
        client.get_validated_instrument_eligibility = Mock(return_value={
            123: SimpleNamespace(supports_long=False)
        })
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertFalse(report["instrument_eligibility_validated"])
        self.assertFalse(report["overall_pass"])

    def test_eligibility_failure_blocks_overall_pass_without_leaking_details(self):
        client = FakeAuditClient()
        client.get_validated_instrument_eligibility = Mock(
            side_effect=RuntimeError("private eligibility detail")
        )
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertFalse(report["instrument_eligibility_validated"])
        self.assertFalse(report["overall_pass"])
        self.assertNotIn("private eligibility detail", str(report))


if __name__ == "__main__":
    unittest.main()
