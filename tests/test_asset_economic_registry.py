import copy
import unittest

from asset_economic_registry import (
    build_registry_report,
    classify_registry_asset,
    load_registry,
    validate_registry,
)


class AssetEconomicRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def test_registry_covers_exactly_the_configured_12_assets(self):
        self.assertEqual(len(self.registry["assets"]), 12)
        report = build_registry_report(self.registry)
        self.assertEqual(report["summary"]["ELIGIBLE"], 0)
        self.assertEqual(report["summary"]["INELIGIBLE"], 0)
        self.assertEqual(report["summary"]["COSTS_UNVERIFIED"], 12)
        self.assertFalse(report["account_portfolio_data_accessed"])

    def test_unknown_asset_is_ineligible(self):
        result = classify_registry_asset("NOT_CONFIGURED", self.registry)
        self.assertEqual(result["status"], "INELIGIBLE")

    def test_missing_evidence_fails_closed(self):
        result = classify_registry_asset("CSPX", self.registry)
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertIn("execution_type_evidence_missing", result["blockers"])
        self.assertIn("round_trip_fee_evidence_missing", result["blockers"])
        self.assertIn("observed_bid_ask_spread_evidence_missing", result["blockers"])
        self.assertIn("minimum_order_rules_evidence_missing", result["blockers"])

    def test_confirmed_cfd_is_excluded_from_real_only_mvp(self):
        item = self.registry["assets"]["CSPX"]
        item["execution"] = {
            "type": "CFD", "confirmed": True,
            "source": "instrument ticket", "observed_at": "2026-11-22T00:00:00Z"
        }
        result = classify_registry_asset("CSPX", self.registry)
        self.assertEqual(result["status"], "INELIGIBLE")
        self.assertIn("mvp_requires_REAL_execution", result["blockers"])

    def test_flat_percentage_and_zero_fee_models_are_supported(self):
        item = self.registry["assets"]["CSPX"]
        item["fees"] = {
            "entry": {"model": "ZERO", "value": 0, "unit": "USD"},
            "exit": {"model": "FIXED", "value": 1.0, "unit": "USD"},
            "confirmed": True, "source": "instrument ticket",
            "observed_at": "2026-11-22T00:00:00Z"
        }
        from asset_economic_registry import valid_fee_side
        self.assertTrue(valid_fee_side(item["fees"]["entry"]))
        self.assertTrue(valid_fee_side(item["fees"]["exit"]))
        self.assertTrue(valid_fee_side({"model":"PERCENT_NOTIONAL","value":0.01,"unit":"PERCENT"}))
        self.assertFalse(valid_fee_side({"model":"FIXED","value":-1,"unit":"USD"}))

    def test_registry_rejects_missing_or_extra_assets(self):
        altered = copy.deepcopy(self.registry)
        altered["assets"].pop("CSPX")
        with self.assertRaises(ValueError):
            validate_registry(altered)
        altered = copy.deepcopy(self.registry)
        altered["assets"]["FAKE"] = altered["assets"]["CSPX"]
        with self.assertRaises(ValueError):
            validate_registry(altered)

    def test_registry_rejects_currency_or_asset_type_drift(self):
        altered = copy.deepcopy(self.registry)
        altered["assets"]["CSPX"]["instrument"]["currency"] = "EUR"
        with self.assertRaises(ValueError):
            validate_registry(altered)
        altered = copy.deepcopy(self.registry)
        altered["assets"]["TSLA"]["instrument"]["type"] = "ETF"
        with self.assertRaises(ValueError):
            validate_registry(altered)


if __name__ == "__main__":
    unittest.main()
