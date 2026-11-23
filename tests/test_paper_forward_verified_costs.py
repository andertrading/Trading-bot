import unittest

import transaction_costs as costs
from paper_forward import calculate_verified_cost_aware_quantity


def complete_registry():
    registry = costs.load_registry()
    item = registry["assets"]["CSPX"]
    evidence = {
        "confirmed": True,
        "source": "test-only verified execution evidence",
        "observed_at": "2026-11-22T00:00:00Z",
    }
    item["instrument"].update(evidence)
    item["execution"].update({"type": "REAL", **evidence})
    item["fees"] = {
        "entry": {"model": "FIXED", "value": 1.0, "unit": "USD"},
        "exit": {"model": "PERCENT_NOTIONAL", "value": 0.1, "unit": "PERCENT"},
        **evidence,
    }
    item["spread"].update({
        "bid_ask_bps": 10.0,
        "treatment": "SEPARATE_FROM_PRICES",
        "evidence_scope": "HISTORICAL_CONSERVATIVE_MODEL",
        **evidence,
    })
    item["price_basis"].update({
        "historical_basis": "split-adjusted historical close",
        "execution_basis": "broker executable bid/ask quote",
        "mapping_rule": "entry ask and stop-side exit bid",
        **evidence,
    })
    item["minimum_order"].update({
        "minimum_notional": 1.0,
        "fractional_allowed": True,
        **evidence,
    })
    return registry


class VerifiedPaperSizingTests(unittest.TestCase):
    def test_sizes_for_gross_stop_loss_plus_round_trip_costs(self):
        result = calculate_verified_cost_aware_quantity(
            "CSPX", 100.0, 95.0, 2.5, registry=complete_registry()
        )
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(
            result["gross_stop_risk_usd"] + result["estimated_round_trip_costs_usd"],
            2.5,
        )
        self.assertAlmostEqual(result["reserved_stop_risk_usd"], 2.5)
        self.assertLess(result["quantity"], 0.5)

    def test_unverified_asset_is_never_treated_as_zero_cost(self):
        result = calculate_verified_cost_aware_quantity("BTC", 100.0, 95.0, 2.5)
        self.assertFalse(result["valid"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["quantity"])
        self.assertTrue(result["unpriced_costs"])

    def test_fixed_costs_above_budget_reject_trade(self):
        result = calculate_verified_cost_aware_quantity(
            "CSPX", 100.0, 95.0, 0.5, registry=complete_registry()
        )
        self.assertFalse(result["valid"])
        self.assertIn("fixed_costs_exceed_risk_budget", result["unpriced_costs"])

    def test_invalid_stop_and_nonpositive_budget_reject(self):
        registry = complete_registry()
        bad_stop = calculate_verified_cost_aware_quantity(
            "CSPX", 100.0, 100.0, 2.5, registry=registry
        )
        bad_budget = calculate_verified_cost_aware_quantity(
            "CSPX", 100.0, 95.0, 0.0, registry=registry
        )
        self.assertFalse(bad_stop["valid"])
        self.assertFalse(bad_budget["valid"])

    def test_non_fractional_instrument_rounds_down_without_exceeding_budget(self):
        registry = complete_registry()
        registry["assets"]["CSPX"]["minimum_order"]["fractional_allowed"] = False
        result = calculate_verified_cost_aware_quantity(
            "CSPX", 100.0, 95.0, 10.0, registry=registry
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["quantity"], 1)
        self.assertLessEqual(result["reserved_stop_risk_usd"], 10.0)


if __name__ == "__main__":
    unittest.main()
