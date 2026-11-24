import json
import unittest
from pathlib import Path


class EtoroPublishedFeeEvidenceTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "research" / "ETORO_PUBLISHED_FEE_EVIDENCE_2026-11-23.json"
        self.evidence = json.loads(path.read_text(encoding="utf-8"))

    def test_public_fee_evidence_is_dated_and_scoped(self):
        self.assertEqual(self.evidence["mode"], "DATED_PUBLIC_FEE_SCHEDULE_EVIDENCE_ONLY")
        self.assertEqual(self.evidence["observed_date"], "2026-11-23")
        self.assertEqual(len(self.evidence["sources"]), 3)
        self.assertTrue(all(item["url"].startswith("https://") for item in self.evidence["sources"]))

    def test_conditional_fees_are_not_promoted_to_asset_specific_facts(self):
        fees = self.evidence["fee_classes"]
        self.assertIsNone(fees["stocks"]["amount_usd"])
        self.assertFalse(fees["stocks"]["instrument_specific_confirmed"])
        self.assertEqual(fees["etfs"]["commission_usd"], 0)
        self.assertFalse(fees["etfs"]["instrument_specific_confirmed"])
        self.assertIsNone(fees["manual_crypto"]["percent_notional"])
        self.assertIsNone(fees["manual_crypto"]["club_tier"])
        self.assertFalse(fees["manual_crypto"]["instrument_specific_confirmed"])

    def test_open_only_endpoint_does_not_confirm_round_trip_costs(self):
        status = self.evidence["cost_scope_status"]
        self.assertIn("OPEN", status["hypothetical_open_quote"])
        self.assertIn("UNAVAILABLE", status["hypothetical_close_quote"])
        self.assertEqual(status["verified_round_trip_cost"], "UNAVAILABLE")
        self.assertFalse(self.evidence["registry_policy"]["update_per_asset_fees_confirmed"])
        self.assertFalse(self.evidence["registry_policy"]["update_per_asset_round_trip_cost_confirmed"])

    def test_no_private_account_data_or_orders_were_accessed(self):
        self.assertFalse(self.evidence["account_or_portfolio_data_accessed"])
        self.assertFalse(self.evidence["order_or_close_request_submitted"])
        self.assertTrue(any("No real portfolio" in item for item in self.evidence["limitations"]))


if __name__ == "__main__":
    unittest.main()
