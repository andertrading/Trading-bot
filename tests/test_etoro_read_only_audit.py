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
        self.account_snapshot_calls = 0
        self.missing_symbols = set(missing_symbols)
        self.rejected_fields = set(rejected_fields)
        self.search_calls = []
        self.quote_calls = []
        self.eligibility_calls = 0

    def get_account_snapshot(self, account_currency):
        self.account_snapshot_calls += 1
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

    def get_instrument_display_data(self, instrument_ids):
        return EtoroResponse({"instrumentDisplayDatas":[{
            "instrumentID":iid,"exchangeID":1,"symbolFull":"TSLA","priceSource":"Nasdaq"
        } for iid in instrument_ids]},200,{})

    def get_supported_exchanges(self, exchange_ids):
        return EtoroResponse({"exchangeInfo":[{
            "exchangeID":eid,"exchangeDescription":"NASDAQ"
        } for eid in exchange_ids]},200,{})

    def get_validated_instrument_eligibility(self, instrument_ids, currency="USD"):
        self.eligibility_calls += 1
        return {
            instrument_id: SimpleNamespace(supports_long=True)
            for instrument_id in instrument_ids
        }

    def get_realtime_quotes(
        self, instrument_ids, now_utc, max_age_seconds,
        asset_calendars=None, max_closed_age_seconds=604800,
    ):
        self.quote_calls.append((
            list(instrument_ids), now_utc, max_age_seconds,
            asset_calendars, max_closed_age_seconds,
        ))
        return {
            instrument_id: SimpleNamespace(
                quote_type="realtime",
                timestamp=now_utc,
            )
            for instrument_id in instrument_ids
        }


class ReadOnlyAuditTests(unittest.TestCase):
    def setUp(self):
        self.assets = {
            "TSLA": {
                "name": "Tesla",
                "yahoo": "TSLA",
                "type": "STOCK",
                "market_calendar": "XNYS",
                "enabled": True,
            }
        }
        self.now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)

    def test_success_report_contains_only_public_ids_and_status_flags(self):
        client = FakeAuditClient()
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertFalse(report["account_portfolio_data_accessed"])
        self.assertEqual(client.account_snapshot_calls, 0)
        self.assertFalse(report["instrument_eligibility_validated"])
        self.assertEqual(report["instrument_eligibility_status"], "NOT_IN_SCOPE")
        self.assertEqual(client.eligibility_calls, 0)
        self.assertTrue(report["complete_realtime_quotes_validated"])
        self.assertFalse(report["portfolio_risk_admission_ready"])
        self.assertTrue(report["overall_pass"])
        self.assertEqual(report["instrument_resolution"][0]["instrument_id"], 123)
        self.assertTrue(report["market_metadata_validated"])
        self.assertTrue(report["exchange_metadata_validated"])
        self.assertEqual(report["instrument_resolution"][0]["exchange_description"], "NASDAQ")
        self.assertEqual(report["instrument_resolution"][0]["market_calendar"], "XNYS")
        self.assertNotIn("available_cash", str(report))
        self.assertNotIn("positions", str(report))
        self.assertEqual(client.quote_calls[0][0], [123])

    def test_unknown_exchange_blocks_quotes_and_overall_pass(self):
        class UnknownExchangeClient(FakeAuditClient):
            def get_supported_exchanges(self, exchange_ids):
                return EtoroResponse({"exchangeInfo":[{
                    "exchangeID":eid,"exchangeDescription":"Unmapped Venue"
                } for eid in exchange_ids]},200,{})
        client=UnknownExchangeClient()
        report=run_audit(client,self.assets,"USD",self.now)
        self.assertFalse(report["overall_pass"])
        self.assertTrue(report["market_metadata_validated"])
        self.assertEqual(report["instrument_resolution"][0]["market_metadata_status"],
                         "UNSUPPORTED_OR_AMBIGUOUS_EXCHANGE")
        self.assertEqual(client.quote_calls,[])

    def test_missing_instrument_metadata_blocks_quotes(self):
        class MissingMetadataClient(FakeAuditClient):
            def get_instrument_display_data(self, instrument_ids):
                return EtoroResponse({"instrumentDisplayDatas":[]},200,{})
        client=MissingMetadataClient()
        report=run_audit(client,self.assets,"USD",self.now)
        self.assertFalse(report["overall_pass"])
        self.assertFalse(report["market_metadata_validated"])
        self.assertEqual(client.quote_calls,[])

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


    def test_audit_never_reads_real_account_portfolio(self):
        client = FakeAuditClient(account_ok=False)
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertFalse(report["account_portfolio_data_accessed"])
        self.assertEqual(client.account_snapshot_calls, 0)
        self.assertTrue(report["overall_pass"])

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

    def test_audit_does_not_query_account_specific_eligibility(self):
        client = FakeAuditClient()
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertFalse(report["instrument_eligibility_validated"])
        self.assertEqual(report["instrument_eligibility_status"], "NOT_IN_SCOPE")
        self.assertEqual(client.eligibility_calls, 0)
        self.assertTrue(report["overall_pass"])

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


    def test_quote_failure_is_redacted_and_blocks_overall_pass(self):
        client = FakeAuditClient()
        client.get_realtime_quotes = Mock(
            side_effect=EtoroAPIError("eToro respondió HTTP 429: private quote payload")
        )
        report = run_audit(client, self.assets, "USD", self.now)
        self.assertEqual(report["realtime_quotes_status"], "HTTP_429")
        self.assertFalse(report["complete_realtime_quotes_validated"])
        self.assertFalse(report["overall_pass"])
        self.assertNotIn("private", str(report))

    def test_success_report_includes_validated_diagnostic_statuses(self):
        report = run_audit(FakeAuditClient(), self.assets, "USD", self.now)
        self.assertEqual(report["instrument_eligibility_status"], "NOT_IN_SCOPE")
        self.assertEqual(report["realtime_quotes_status"], "VALIDATED")


    def test_validation_errors_have_specific_safe_diagnostic_codes(self):
        cases = (
            (EtoroAPIError("Falta la lista de resultados de cotizaciones"), "QUOTES_RESULTS_MISSING"),
            (EtoroAPIError("La respuesta de cotizaciones no es un objeto JSON"), "QUOTES_RESPONSE_SCHEMA_INVALID"),
            (EtoroAPIError("instrumentId inválido en cotización 0"), "QUOTES_INSTRUMENT_ID_INVALID"),
            (EtoroAPIError("Fecha ausente en cotización 123"), "QUOTES_TIMESTAMP_MISSING"),
            (EtoroAPIError("La fecha de cotización 123 no está en UTC"), "QUOTES_TIMESTAMP_NOT_UTC"),
            (EtoroAPIError("Error de conexión con eToro (ConnectionError)"), "ETORO_CONNECTION_ERROR"),
            (EtoroAPIError("eToro devolvió una respuesta JSON inválida"), "ETORO_INVALID_JSON_RESPONSE"),
            (EtoroAPIError("La respuesta de elegibilidad no incluye el campo currency"), "ELIGIBILITY_CURRENCY_MISSING"),
            (EtoroAPIError("La respuesta de elegibilidad no está en USD"), "ELIGIBILITY_CURRENCY_MISMATCH"),
            (EtoroAPIError("La divisa de la respuesta de elegibilidad no es válida"), "ELIGIBILITY_CURRENCY_INVALID"),
            (EtoroAPIError("La fecha de cotización 123 no incluye zona horaria"), "QUOTES_TIMESTAMP_TIMEZONE_MISSING"),
            (EtoroAPIError("El instrumento tiene cotización retrasada"), "QUOTES_NOT_REALTIME"),
            (EtoroAPIError("La respuesta de elegibilidad está incompleta o contiene activos no encontrados"), "ELIGIBILITY_RESULTS_INCOMPLETE"),
            (EtoroAPIError("El desglose no confirma la divisa de cuenta USD"), "RISK_CURRENCY_UNCONFIRMED"),
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
