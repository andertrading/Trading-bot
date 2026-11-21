import unittest
from unittest.mock import patch

import transaction_costs as costs


class TestEtoroTransactionCosts(unittest.TestCase):
    def confirmed_model(self):
        return (
            patch.object(costs, "ETORO_EXECUTION_TYPES_CONFIRMED", True),
            patch.object(costs, "ETORO_FEE_SCHEDULE_CONFIRMED", True),
            patch.object(costs, "ETORO_ACCOUNT_CURRENCY_CONFIRMED", True),
            patch.object(costs, "ETORO_EXECUTION_PRICE_BASIS_CONFIRMED", True),
        )

    def test_real_stock_uses_conservative_two_dollar_fee_each_side(self):
        result = costs.estimate_etoro_trade_costs(
            "AAPL", 100.0, 110.0, 1.0
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
                "TEST_EUR", 100.0, 110.0, 1.0,
                execution_type="REAL", market_spread_bps=0
            )
            priced = costs.estimate_etoro_trade_costs(
                "TEST_EUR",
                100.0,
                110.0,
                1.0,
                execution_type="REAL",
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
            leverage=2.0,
        )
        self.assertAlmostEqual(result["commission_cost"], 0.315)
        self.assertIn("cfd_benchmark_rate_missing", result["unpriced_costs"])

    def test_leveraged_stock_cfd_overnight_uses_annual_fee_plus_benchmark(self):
        with patch.object(
            costs, "ETORO_CFD_STOCK_ETF_BENCHMARK_RATE_PCT", 0.036
        ):
            result = costs.estimate_etoro_trade_costs(
                "AAPL",
                100.0,
                110.0,
                1.0,
                execution_type="CFD",
                market_spread_bps=0,
                overnight_charge_units=2,
                leverage=2.0,
            )
        self.assertAlmostEqual(result["overnight_cost"], 100.0 * 0.10 * 2 / 365)

    def test_short_stock_cfd_requires_instrument_borrow_fee(self):
        result = costs.estimate_etoro_trade_costs(
            "AAPL",
            100.0,
            90.0,
            1.0,
            execution_type="CFD",
            market_spread_bps=0,
            overnight_charge_units=1,
            leverage=1.0,
            side="SELL",
        )
        self.assertIn("cfd_short_borrow_rate_missing", result["unpriced_costs"])

    def test_short_stock_cfd_uses_annual_borrow_rate_on_opening_notional(self):
        with patch.dict(
            costs.ETORO_CFD_STOCK_SHORT_BORROW_ANNUAL_RATE_PCT,
            {"AAPL": 0.11},
        ):
            result = costs.estimate_etoro_trade_costs(
                "AAPL",
                100.0,
                90.0,
                1.0,
                execution_type="CFD",
                market_spread_bps=0,
                overnight_charge_units=1,
                leverage=1.0,
                side="SELL",
            )
        self.assertAlmostEqual(result["overnight_cost"], 100.0 * 0.11 / 365)

    def test_unleveraged_long_stock_cfd_has_no_overnight_fee(self):
        result = costs.estimate_etoro_trade_costs(
            "AAPL",
            100.0,
            110.0,
            1.0,
            execution_type="CFD",
            market_spread_bps=0,
            overnight_charge_units=None,
            leverage=1.0,
            side="BUY",
        )
        self.assertEqual(result["overnight_cost"], 0.0)
        self.assertNotIn("cfd_overnight_fee_missing", result["unpriced_costs"])

    def test_unleveraged_long_crypto_cfd_in_spain_requires_overnight_fee(self):
        result = costs.estimate_etoro_trade_costs(
            "BTC",
            100.0,
            110.0,
            1.0,
            execution_type="CFD",
            market_spread_bps=0,
            overnight_charge_units=None,
            leverage=1.0,
            side="BUY",
            country_code="ES",
        )
        self.assertIn("cfd_overnight_charge_units_missing", result["unpriced_costs"])

    def test_unverified_execution_price_basis_prevents_complete_costs(self):
        with patch.object(costs, "ETORO_EXECUTION_TYPES_CONFIRMED", True), \
             patch.object(costs, "ETORO_FEE_SCHEDULE_CONFIRMED", True), \
             patch.object(costs, "ETORO_ACCOUNT_CURRENCY_CONFIRMED", True), \
             patch.object(costs, "ETORO_EXECUTION_PRICE_BASIS_CONFIRMED", False):
            result = costs.estimate_etoro_trade_costs(
                "AAPL", 100.0, 110.0, 1.0, market_spread_bps=10
            )
        self.assertFalse(result["complete"])
        self.assertIn(
            "historical_ohlc_to_etoro_execution_price_basis_unverified",
            result["unpriced_costs"],
        )

    def test_funding_conversion_is_separate_from_trade_fx(self):
        same_currency = costs.estimate_funding_conversion_cost(
            1000.0, "USD", "USD"
        )
        missing = costs.estimate_funding_conversion_cost(
            1000.0, "EUR", "USD"
        )
        priced = costs.estimate_funding_conversion_cost(
            1000.0,
            "EUR",
            "USD",
            fx_rate=1.1,
            conversion_fee_pct=0.005,
        )
        self.assertTrue(same_currency["complete"])
        self.assertEqual(same_currency["conversion_cost_target_currency"], 0.0)
        self.assertIn("funding_fx_rate_or_fee_missing", missing["unpriced_costs"])
        self.assertAlmostEqual(priced["conversion_cost_target_currency"], 5.5)

    def test_all_cost_components_can_be_complete_when_confirmed(self):
        patches = self.confirmed_model()
        with patches[0], patches[1], patches[2], patches[3]:
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
