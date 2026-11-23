import copy
import unittest
from unittest.mock import patch

import asset_economic_registry as registry_module
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


    def test_etoro_symbol_mapping_does_not_overwrite_data_provider_listing(self):
        item = self.registry["assets"]["CSPX"]["instrument"]
        self.assertEqual(item["listing"], "CSPX.LSE")
        self.assertEqual(item["etoro_symbol"], "CSPX.L")
        self.assertEqual(item["etoro_instrument_id"], 3434)

    def test_unknown_asset_is_ineligible(self):
        result = classify_registry_asset("NOT_CONFIGURED", self.registry)
        self.assertEqual(result["status"], "INELIGIBLE")

    def test_missing_evidence_fails_closed(self):
        result = classify_registry_asset("CSPX", self.registry)
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertNotIn("instrument_identity_evidence_missing", result["blockers"])
        self.assertNotIn("execution_type_evidence_missing", result["blockers"])
        self.assertIn("round_trip_fee_evidence_missing", result["blockers"])
        self.assertNotIn("observed_bid_ask_spread_evidence_missing", result["blockers"])
        self.assertIn("spread_treatment_unverified", result["blockers"])
        self.assertIn("historical_spread_model_not_validated", result["blockers"])
        self.assertNotIn("minimum_order_rules_evidence_missing", result["blockers"])
        self.assertEqual(
            self.registry["assets"]["CSPX"]["opening_cost_snapshot"]["scope"],
            "HYPOTHETICAL_OPEN_ONLY",
        )

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
        self.assertFalse(valid_fee_side({"model":"ZERO","value":False,"unit":"USD"}))

    def test_instrument_identity_requires_timestamped_evidence(self):
        item = self.registry["assets"]["CSPX"]
        item["instrument"].update({
            "confirmed": True, "source": "instrument ticket",
            "observed_at": "2026-11-22T00:00:00"
        })
        result = classify_registry_asset("CSPX", self.registry)
        self.assertIn("instrument_identity_evidence_missing", result["blockers"])
        item["instrument"]["observed_at"] = "2026-11-22T00:00:00Z"
        result = classify_registry_asset("CSPX", self.registry)
        self.assertNotIn("instrument_identity_evidence_missing", result["blockers"])

    def test_future_dated_evidence_is_rejected(self):
        item = self.registry["assets"]["CSPX"]
        item["instrument"].update({
            "confirmed": True,
            "source": "instrument ticket",
            "observed_at": "2099-01-01T00:00:00Z",
        })
        result = classify_registry_asset("CSPX", self.registry)
        self.assertIn("instrument_identity_evidence_missing", result["blockers"])

    def test_price_basis_requires_historical_execution_and_mapping_definitions(self):
        item = self.registry["assets"]["CSPX"]
        item["price_basis"].update({
            "confirmed": True,
            "source": "broker instrument ticket and historical feed spec",
            "observed_at": "2026-11-22T00:00:00Z",
        })
        result = classify_registry_asset("CSPX", self.registry)
        self.assertIn("execution_price_basis_evidence_missing", result["blockers"])

        item["price_basis"].update({
            "historical_basis": "split-adjusted, not dividend-adjusted",
            "execution_basis": "broker executable bid/ask quote",
            "mapping_rule": "signal at completed close; entry on next available ask",
        })
        result = classify_registry_asset("CSPX", self.registry)
        self.assertNotIn("execution_price_basis_evidence_missing", result["blockers"])

    def test_spread_treatment_and_historical_scope_are_explicit(self):
        item = self.registry["assets"]["CSPX"]
        item["spread"].update({
            "bid_ask_bps": 10.0,
            "treatment": "SEPARATE_FROM_PRICES",
            "evidence_scope": "CURRENT_SNAPSHOT_ONLY",
            "confirmed": True,
            "source": "timestamped eToro quote",
            "observed_at": "2026-11-22T00:00:00Z",
        })
        result = classify_registry_asset("CSPX", self.registry)
        self.assertNotIn("spread_treatment_unverified", result["blockers"])
        self.assertIn("historical_spread_model_not_validated", result["blockers"])

        item["spread"]["evidence_scope"] = "HISTORICAL_CONSERVATIVE_MODEL"
        result = classify_registry_asset("CSPX", self.registry)
        self.assertNotIn("historical_spread_model_not_validated", result["blockers"])

    def test_unknown_spread_treatment_is_rejected_by_schema(self):
        altered = copy.deepcopy(self.registry)
        altered["assets"]["CSPX"]["spread"]["treatment"] = "GUESS"
        with self.assertRaises(ValueError):
            validate_registry(altered)

    def test_registry_rejects_missing_or_extra_assets(self):
        altered = copy.deepcopy(self.registry)
        altered["assets"].pop("CSPX")
        with self.assertRaises(ValueError):
            validate_registry(altered)
        altered = copy.deepcopy(self.registry)
        altered["assets"]["FAKE"] = altered["assets"]["CSPX"]
        with self.assertRaises(ValueError):
            validate_registry(altered)

    def test_fx_requirement_must_match_quote_and_account_currency(self):
        altered = copy.deepcopy(self.registry)
        altered["assets"]["CSPX"]["fx"]["required"] = True
        with self.assertRaises(ValueError):
            validate_registry(altered)

        altered = copy.deepcopy(self.registry)
        altered["assets"]["CSPX"]["instrument"]["currency"] = "EUR"
        modified_config = dict(registry_module.ASSETS["CSPX"])
        modified_config["currency"] = "EUR"
        with patch.dict(registry_module.ASSETS, {"CSPX": modified_config}):
            with self.assertRaises(ValueError):
                validate_registry(altered)

    def test_fx_evidence_is_not_required_when_quote_matches_account_currency(self):
        item = self.registry["assets"]["CSPX"]
        item["fx"]["required"] = False
        item["fx"]["confirmed"] = False

        result = classify_registry_asset("CSPX", self.registry)

        self.assertNotIn("fx_conversion_evidence_missing", result["blockers"])

    def test_fx_evidence_is_required_when_quote_differs_from_account_currency(self):
        item = self.registry["assets"]["CSPX"]
        item["instrument"]["currency"] = "EUR"
        item["fx"]["required"] = True
        item["fx"]["confirmed"] = False
        modified_config = dict(registry_module.ASSETS["CSPX"])
        modified_config["currency"] = "EUR"

        with patch.dict(registry_module.ASSETS, {"CSPX": modified_config}):
            result = classify_registry_asset("CSPX", self.registry)

        self.assertIn("fx_conversion_evidence_missing", result["blockers"])

    def test_fx_conversion_fee_unit_must_be_percentage_points(self):
        altered = copy.deepcopy(self.registry)
        altered["assets"]["CSPX"]["fx"]["conversion_fee_unit"] = "DECIMAL"
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
