import copy
import unittest
from datetime import datetime, timezone

from asset_economic_registry import load_registry
from cost_aware_sizing import calculate_verified_cost_aware_quantity


def eligible_test_registry(*, fractional=True, minimum_notional=10.0):
    registry = copy.deepcopy(load_registry())
    now = datetime.now(timezone.utc).isoformat()
    item = registry["assets"]["AAPL"]
    item["fees"] = {
        "confirmed": True,
        "source": "synthetic unit-test evidence only",
        "observed_at": now,
        "entry": {"model": "FIXED", "value": 1.0, "unit": "USD"},
        "exit": {"model": "FIXED", "value": 1.0, "unit": "USD"},
    }
    item["spread"].update({
        "bid_ask_bps": 10.0,
        "treatment": "SEPARATE_FROM_PRICES",
        "evidence_scope": "HISTORICAL_CONSERVATIVE_MODEL",
        "confirmed": True,
        "source": "synthetic unit-test evidence only",
        "observed_at": now,
    })
    item["price_basis"] = {
        "historical_basis": "synthetic raw OHLC test basis",
        "execution_basis": "synthetic bid/ask test basis",
        "mapping_rule": "synthetic explicit test mapping",
        "confirmed": True,
        "source": "synthetic unit-test evidence only",
        "observed_at": now,
    }
    item["minimum_order"].update({
        "minimum_notional": minimum_notional,
        "fractional_allowed": fractional,
        "confirmed": True,
        "source": "synthetic unit-test evidence only",
        "observed_at": now,
    })
    return registry


class VerifiedCostAwareSizingTests(unittest.TestCase):
    def test_real_registry_with_missing_cost_evidence_fails_closed(self):
        result = calculate_verified_cost_aware_quantity(
            "AAPL", 100.0, 95.0, 10.0
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "COSTS_UNVERIFIED")
        self.assertIsNone(result["quantity"])
        self.assertIn("round_trip_fee_evidence_missing", result["blockers"])

    def test_sizes_gross_stop_loss_plus_round_trip_cost_inside_budget(self):
        registry = eligible_test_registry()
        result = calculate_verified_cost_aware_quantity(
            "AAPL", 100.0, 95.0, 10.0, registry=registry
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["status"], "SIZED_WITH_VERIFIED_ROUND_TRIP_COSTS")
        self.assertAlmostEqual(result["round_trip_cost_at_stop_usd"], 2.0 + result["quantity"] * 0.0975)
        self.assertAlmostEqual(result["total_risk_reserved_usd"], 10.0)
        self.assertLessEqual(result["total_risk_reserved_usd"], result["risk_budget_usd"] + 1e-8)
        self.assertGreater(result["notional_usd"], 10.0)

    def test_non_fractional_asset_rounds_down_and_preserves_risk_cap(self):
        registry = eligible_test_registry(fractional=False)
        result = calculate_verified_cost_aware_quantity(
            "AAPL", 100.0, 95.0, 10.0, registry=registry
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["quantity"], 1.0)
        self.assertLessEqual(result["total_risk_reserved_usd"], 10.0)

    def test_fixed_costs_cannot_consume_more_than_risk_budget(self):
        result = calculate_verified_cost_aware_quantity(
            "AAPL", 100.0, 95.0, 1.5,
            registry=eligible_test_registry(),
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "RISK_BUDGET_BELOW_FIXED_COSTS")

    def test_verified_minimum_order_is_enforced(self):
        result = calculate_verified_cost_aware_quantity(
            "AAPL", 100.0, 95.0, 10.0,
            registry=eligible_test_registry(minimum_notional=1000.0),
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "BELOW_VERIFIED_MINIMUM_ORDER")

    def test_invalid_long_levels_are_rejected(self):
        result = calculate_verified_cost_aware_quantity(
            "AAPL", 100.0, 100.0, 10.0,
            registry=eligible_test_registry(),
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
