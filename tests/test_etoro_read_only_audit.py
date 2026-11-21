import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import ASSETS
from etoro_read_only import EtoroAPIError, EtoroAccountSnapshot, EtoroResponse
from etoro_read_only_audit import _resolution_error_status, run_audit


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

    def search_instrument_by_symbol(self, symbol, page_size=100):
        self.search_calls.append(symbol)
        if symbol in self.missing_symbols:
            results = []
        else:
            results = [{
                "instrumentId": 123,
                "displayName": "Tesla",
                "symbol": "TSLA",
                "type": "Stocks",
            }] if symbol == "TSLA" else []
        return EtoroResponse({
            "results": results,
            "pagination": {"hasNext": False, "nextPageToken": None, "pageSize": page_size},
        }, 200, {})

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


    def test_audit_uses_exact_v2_symbol_search(self):
        client = FakeAuditClient()
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertTrue(report["instrument_resolution"][0]["candidate_found"])
        self.assertEqual(report["instrument_resolution"][0]["resolution_status"], "RESOLVED")
        self.assertEqual(client.search_calls, ["TSLA"])

    def test_audit_reports_type_mismatch_without_accepting_instrument(self):
        class WrongTypeClient(FakeAuditClient):
            def search_instrument_by_symbol(self, symbol, page_size=100):
                self.search_calls.append(symbol)
                return EtoroResponse({
                    "results": [{
                        "instrumentId": 123,
                        "displayName": "Tesla",
                        "symbol": "TSLA",
                        "type": "ETF",
                    }],
                    "pagination": {"hasNext": False, "nextPageToken": None, "pageSize": page_size},
                }, 200, {})
        report = run_audit(WrongTypeClient(), self.assets, "USD", self.now)
        self.assertFalse(report["instrument_resolution"][0]["candidate_found"])
        self.assertEqual(report["instrument_resolution"][0]["resolution_status"], "ASSET_TYPE_MISMATCH")


    def test_account_snapshot_failure_is_redacted_and_blocks_overall_pass(self):
        client = FakeAuditClient(account_ok=False)
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertFalse(report["account_snapshot_schema_valid"])
        self.assertEqual(report["account_snapshot_status"], "RUNTIMEERROR")
        self.assertFalse(report["gross_position_stop_risk_diagnostic_validated"])
        self.assertFalse(report["overall_pass"])
        self.assertNotIn("private account data", str(report))

    def test_account_schema_failure_exposes_only_a_safe_diagnostic_code(self):
        class SchemaFailingClient(FakeAuditClient):
            def get_account_snapshot(self, account_currency):
                raise EtoroAPIError("Falta clientPortfolio; private payload omitted")

        report = run_audit(SchemaFailingClient(), self.assets, "USD", self.now)
        self.assertEqual(report["account_snapshot_status"], "MISSING_CLIENT_PORTFOLIO")
        self.assertNotIn("private payload", str(report))

    def test_account_diagnostic_codes_classify_http_and_position_schema_failures(self):
        class FailingClient(FakeAuditClient):
            def __init__(self, error):
                super().__init__()
                self.error = error

            def get_account_snapshot(self, account_currency):
                raise self.error

        http_report = run_audit(
            FailingClient(EtoroAPIError("eToro API respondió HTTP 403")),
            self.assets, "USD", self.now,
        )
        position_report = run_audit(
            FailingClient(EtoroAPIError("Posición 0 incompleta: faltan cid")),
            self.assets, "USD", self.now,
        )
        self.assertEqual(http_report["account_snapshot_status"], "ETORO_HTTP_403")
        self.assertEqual(position_report["account_snapshot_status"], "POSITION_MISSING_FIELDS")
        self.assertEqual(position_report["account_snapshot_missing_fields"], ["cid"])

    def test_expected_asset_types_are_read_from_each_asset_not_last_loop_value(self):
        client = FakeAuditClient()
        report = run_audit(client, ASSETS, "USD", self.now)
        expected = {symbol: asset["type"] for symbol, asset in ASSETS.items() if asset["enabled"]}
        actual = {
            item["symbol"]: item["expected_asset_type"]
            for item in report["instrument_resolution"]
        }
        self.assertEqual(actual, expected)
        self.assertEqual(actual["CSPX"], "ETF")
        self.assertEqual(actual["TSLA"], "STOCK")
        self.assertEqual(actual["BTC"], "CRYPTO")

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

    def test_etf_resolution_retries_with_configured_exchange_suffix(self):
        class SuffixedEtfClient(FakeAuditClient):
            def search_instrument_by_symbol(self, symbol, page_size=100):
                self.search_calls.append(symbol)
                if symbol == "CSPX":
                    raise EtoroAPIError("eToro devolvió HTTP 404")
                if symbol == "CSPX.L":
                    return EtoroResponse({
                        "results": [{
                            "instrumentId": 8020,
                            "displayName": "iShares Core S&P 500 UCITS ETF",
                            "symbol": "CSPX.L",
                            "type": "ETF",
                        }],
                        "pagination": {
                            "hasNext": False,
                            "nextPageToken": None,
                            "pageSize": page_size,
                        },
                    }, 200, {})
                return EtoroResponse({
                    "results": [],
                    "pagination": {"hasNext": False, "nextPageToken": None, "pageSize": page_size},
                }, 200, {})

        assets = {
            "CSPX": {
                "name": "iShares Core S&P 500",
                "yahoo": "CSPX.L",
                "type": "ETF",
                "enabled": True,
            }
        }
        client = SuffixedEtfClient()
        report = run_audit(client, assets, "USD", self.now)
        resolved = report["instrument_resolution"][0]
        self.assertTrue(resolved["candidate_found"])
        self.assertEqual(resolved["instrument_id"], 8020)
        self.assertEqual(resolved["resolution_status"], "RESOLVED")
        self.assertEqual(client.search_calls, ["CSPX", "CSPX.L"])


    def test_audit_reports_redacted_diagnostic_codes_for_post_resolution_failures(self):
        client = FakeAuditClient()
        client.get_validated_instrument_eligibility = Mock(
            side_effect=EtoroAPIError("eToro respondió HTTP 403: private eligibility payload")
        )
        client.get_realtime_quotes = Mock(
            side_effect=EtoroAPIError("eToro respondió HTTP 429: private quote payload")
        )
        with patch(
            "etoro_read_only_audit.calculate_account_stop_risk_from_client",
            side_effect=EtoroAPIError("eToro respondió HTTP 503: private position payload"),
        ):
            report = run_audit(client, self.assets, "USD", self.now)

        self.assertEqual(report["position_risk_diagnostic_status"], "HTTP_503")
        self.assertEqual(report["instrument_eligibility_status"], "HTTP_403")
        self.assertEqual(report["realtime_quotes_status"], "HTTP_429")
        self.assertFalse(report["gross_position_stop_risk_diagnostic_validated"])
        self.assertFalse(report["instrument_eligibility_validated"])
        self.assertFalse(report["complete_realtime_quotes_validated"])
        self.assertNotIn("private", str(report))

    def test_success_report_includes_validated_diagnostic_statuses(self):
        report = run_audit(FakeAuditClient(), self.assets, "USD", self.now)
        self.assertEqual(report["position_risk_diagnostic_status"], "COMPLETE_NO_OPEN_POSITIONS")
        self.assertEqual(report["instrument_eligibility_status"], "VALIDATED")
        self.assertEqual(report["realtime_quotes_status"], "VALIDATED")


    def test_validation_errors_have_specific_safe_diagnostic_codes(self):
        cases = (
            (EtoroAPIError("Falta la lista de resultados de cotizaciones"), "QUOTES_RESULTS_MISSING"),
            (EtoroAPIError("El instrumento tiene cotización retrasada"), "QUOTES_NOT_REALTIME"),
            (EtoroAPIError("La respuesta de elegibilidad está incompleta o contiene activos no encontrados"), "ELIGIBILITY_RESULTS_INCOMPLETE"),
            (EtoroAPIError("El desglose no confirma la divisa de cuenta USD"), "RISK_CURRENCY_UNCONFIRMED"),
            (EtoroAPIError("Al menos una posición del snapshot no tiene stop-loss confirmado"), "RISK_UNBOUNDED_STOP"),
            (EtoroAPIError("eToro devolvió HTTP 403: private payload"), "HTTP_403"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(_resolution_error_status(error), expected)
                self.assertNotIn("private", _resolution_error_status(error))

    def test_unknown_etoro_validation_error_is_not_mislabeled_as_search(self):
        code = _resolution_error_status(EtoroAPIError("private response detail"))
        self.assertEqual(code, "ETORO_API_VALIDATION_ERROR")
        self.assertNotIn("private", code)


if __name__ == "__main__":
    unittest.main()
