import unittest
from unittest.mock import patch

import asset_economic_eligibility as eligibility


class TestAssetEconomicEligibility(unittest.TestCase):
    def complete_inputs(self):
        return {
            "execution_type": "REAL",
            "execution_type_confirmed": True,
            "fee_schedule_confirmed": True,
            "close_fees_confirmed": True,
            "execution_price_basis_confirmed": True,
            "market_spread_bps": 4.0,
            "spreads_confirmed": True,
            "account_currency": "USD",
            "account_currency_confirmed": True,
            "minimum_order_constraints_confirmed": True,
        }

    def test_unknown_asset_is_ineligible(self):
        result = eligibility.classify_asset("NOT_REAL")
        self.assertEqual(result["status"], "INELIGIBLE")
        self.assertIn("asset_not_configured", result["blockers"])

    def test_disabled_asset_is_ineligible(self):
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "STOCK", "currency": "USD", "enabled": False},
        )
        self.assertEqual(result["status"], "INELIGIBLE")
        self.assertIn("asset_disabled_in_configuration", result["blockers"])

    def test_disabled_configured_asset_is_ineligible_before_registry_lookup(self):
        with patch.dict(
            eligibility.ASSETS,
            {"DISABLED": {
                "name": "Disabled asset",
                "type": "STOCK",
                "currency": "USD",
                "enabled": False,
            }},
        ):
            result = eligibility.classify_asset("DISABLED")
        self.assertEqual(result["status"], "INELIGIBLE")
        self.assertIn("asset_disabled_in_configuration", result["blockers"])

    def test_confirmed_cfd_is_excluded_from_real_only_mvp(self):
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "STOCK", "currency": "USD", "enabled": True},
            execution_type="CFD",
            execution_type_confirmed=True,
        )
        self.assertEqual(result["status"], "INELIGIBLE")
        self.assertIn("mvp_requires_confirmed_REAL_execution", result["blockers"])

    def test_complete_verified_real_instrument_is_eligible(self):
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "ETF", "currency": "USD", "enabled": True},
            **self.complete_inputs(),
        )
        self.assertEqual(result["status"], "ELIGIBLE")
        self.assertEqual(result["blockers"], [])

    def test_missing_spread_is_never_assumed_to_be_zero(self):
        values = self.complete_inputs()
        values["market_spread_bps"] = None
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "ETF", "currency": "USD", "enabled": True},
            **values,
        )
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertIn("observed_bid_ask_spread_missing_or_invalid", result["blockers"])

    def test_numeric_spread_without_explicit_measurement_confirmation_blocks(self):
        values = self.complete_inputs()
        values["spreads_confirmed"] = False
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "ETF", "currency": "USD", "enabled": True},
            **values,
        )
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertIn("bid_ask_spread_measurement_not_confirmed", result["blockers"])

    def test_unconfirmed_round_trip_fees_block_eligibility(self):
        values = self.complete_inputs()
        values["close_fees_confirmed"] = False
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "ETF", "currency": "USD", "enabled": True},
            **values,
        )
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertIn("round_trip_close_fee_not_confirmed", result["blockers"])

    def test_missing_minimum_order_rules_blocks_eligibility(self):
        values = self.complete_inputs()
        values["minimum_order_constraints_confirmed"] = False
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "ETF", "currency": "USD", "enabled": True},
            **values,
        )
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertIn(
            "minimum_order_and_fractional_quantity_rules_not_confirmed",
            result["blockers"],
        )

    def test_non_usd_quote_requires_fx_evidence(self):
        values = self.complete_inputs()
        values["account_currency"] = "USD"
        values["fx_conversion_fee_pct"] = None
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "STOCK", "currency": "EUR", "enabled": True},
            **values,
        )
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertIn("trade_FX_conversion_fee_or_rate_not_verified", result["blockers"])

    def test_non_usd_quote_requires_both_fx_fee_and_conversion_rate(self):
        values = self.complete_inputs()
        values["fx_conversion_fee_pct"] = 0.001
        values["quote_to_account_rate"] = None
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "STOCK", "currency": "EUR", "enabled": True},
            **values,
        )
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertIn("trade_FX_conversion_fee_or_rate_not_verified", result["blockers"])

    def test_confirmed_but_missing_execution_type_fails_closed(self):
        result = eligibility.classify_asset(
            "TEST",
            asset_config={"name": "Test", "type": "STOCK", "currency": "USD", "enabled": True},
            execution_type=None,
            execution_type_confirmed=True,
        )
        self.assertEqual(result["status"], "INELIGIBLE")
        self.assertIn("mvp_requires_confirmed_REAL_execution", result["blockers"])

    def test_current_config_fails_closed_for_all_12_assets(self):
        report = eligibility.build_eligibility_report()
        self.assertEqual(len(report["assets"]), 12)
        self.assertEqual(report["summary"]["ELIGIBLE"], 0)
        self.assertEqual(report["summary"]["COSTS_UNVERIFIED"], 12)
        self.assertFalse(report["account_portfolio_data_accessed"])
        self.assertFalse(report["orders_or_alerts_enabled"])
        self.assertFalse(report["sizing_enabled"])

    def test_configured_asset_uses_per_instrument_registry_as_source_of_truth(self):
        result = eligibility.classify_asset("CSPX")
        self.assertTrue(result["registry_source_of_truth"])
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertNotIn("instrument_identity_not_confirmed_in_instrument_ticket", result["blockers"])
        self.assertIn("round_trip_close_fee_not_confirmed", result["blockers"])

    def test_etoro_eligibility_confirms_real_unleveraged_long_capability(self):
        result = eligibility.classify_asset("CSPX")
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertNotIn(
            "execution_type_not_confirmed_in_instrument_ticket",
            result["blockers"],
        )
        self.assertEqual(result["configured_execution_type"], "REAL")
        self.assertTrue(result["execution_type_confirmed"])


if __name__ == "__main__":
    unittest.main()
