import unittest
from unittest.mock import patch

import transaction_costs as costs


class TestEtoroTransactionCosts(unittest.TestCase):
    def confirmed_model(self):
        return (
            patch.object(costs, "ETORO_EXECUTION_TYPES_CONFIRMED", True),
            patch.object(costs, "ETORO_FEE_SCHEDULE_CONFIRMED", True),
            patch.object(costs, "ETORO_ACCOUNT_CURRENCY_CONFIRMED", True),
        )

    def test_real_stock_uses_conservative_two_dollar_fee_each_side(self):
        result = costs.estimate_etoro_trade_costs(
            "AAPL", 100.0, 110.0, 1.0, market_spread_bps=0
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["commission_cost"], 4.0)
        self.assertFalse(result["complete"])
        self.assertIn("live_market_bid_ask_spread_missing", result["unpriced_costs"])

    def test_real_etf_has_zero_platform_commission(self):
        result = costs.estimate_etoro_trade_costs(
            "CSPX", 100.0, 110.0, 1.0, market_spread_bps=0
        )
        self.assertEqual(result["commission_cost"], 0.0)

    def test_real_crypto_charges_one_percent_on_each_side(self):
        result = costs.estimate_etoro_trade_costs(
            "BTC", 100.0, 110.0, 1.0, market_spread_bps=0
        )
        self.assertAlmostEqual(result["commission_cost"], 2.10)

    def test_market_spread_is_charged_half_on_each_side(self):
        result = costs.estimate_etoro_trade_costs(
            "AAPL",
            100.0,
            110.0,
            2.0,
            market_spread_bps=10,
        )
        self.assertAlmostEqual(result["market_spread_cost"], 0.21)

    def test_matching_quote_and_account_currency_has_no_trade_fx_cost(self):
        result = costs.estimate_etoro_trade_costs(
            "AAPL", 100.0, 110.0, 1.0, market_spread_bps=0
        )
        self.assertEqual(result["fx_conversion_cost"], 0.0)

    def test_different_currency_requires_rate_and_conversion_fee(self):
        modified_assets = dict(costs.ASSETS)
        modified_assets["TEST_EUR"] = {
            "type": "STOCK",
            "currency": "EUR",
        }
        with patch.object(costs, "ASSETS", modified_assets):
            missing = costs.estimate_etoro_trade_costs(
                "TEST_EUR", 100.0, 110.0, 1.0, market_spread_bps=0
            )
            priced = costs.estimate_etoro_trade_costs(
                "TEST_EUR",
                100.0,
                110.0,
                1.0,
                market_spread_bps=0,
                quote_to_account=1.1,
                fx_conversion_fee_pct=0.002,
            )
        self.assertIn("fx_conversion_rate_or_fee_missing", missing["unpriced_costs"])
        self.assertAlmostEqual(priced["fx_conversion_cost"], 0.462)

    def test_cfd_costs_include_published_spread_and_flag_missing_financing(self):
        result = costs.estimate_etoro_trade_costs(
            "AAPL",
            100.0,
            110.0,
            1.0,
            execution_type="CFD",
            market_spread_bps=0,
            overnight_charge_units=2,
        )
        self.assertAlmostEqual(result["commission_cost"], 0.315)
        self.assertIn("cfd_overnight_fee_missing", result["unpriced_costs"])

    def test_all_cost_components_can_be_complete_when_confirmed(self):
        patches = self.confirmed_model()
        with patches[0], patches[1], patches[2]:
            result = costs.estimate_etoro_trade_costs(
                "AAPL",
                100.0,
                110.0,
                1.0,
                market_spread_bps=10,
            )
        self.assertTrue(result["complete"])
        self.assertEqual(result["unpriced_costs"], [])
        self.assertAlmostEqual(result["known_costs_total"], 4.105)


if __name__ == "__main__":
    unittest.main()
