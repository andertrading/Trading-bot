
import unittest
from unittest.mock import patch

from risk_manager import (
    calculate_position_size,
    risk_pct_for_score,
)

from portfolio import (
    get_portfolio_status,
    can_add_trade,
)


class TestRiskManager(unittest.TestCase):

    def test_risk_never_exceeds_one_percent(self):
        for score in range(101):
            self.assertLessEqual(
                risk_pct_for_score(score),
                0.01,
            )

    def test_low_score_has_no_risk(self):
        self.assertEqual(
            risk_pct_for_score(50),
            0.0,
        )

    def test_higher_quality_signals_receive_more_risk(self):
        self.assertGreater(
            risk_pct_for_score(95),
            risk_pct_for_score(85),
        )
        self.assertGreater(
            risk_pct_for_score(85),
            risk_pct_for_score(75),
        )

    def test_invalid_scores_have_no_risk(self):
        for score in (
            float("nan"),
            float("inf"),
            float("-inf"),
            -1,
            101,
            True,
        ):
            with self.subTest(score=score):
                self.assertEqual(
                    risk_pct_for_score(score),
                    0.0,
                )

    def test_string_score_is_supported(self):
        self.assertEqual(
            risk_pct_for_score("85"),
            0.005,
        )

    def test_risk_tier_cannot_exceed_hard_limit(self):
        with patch(
            "risk_manager.RISK_TIERS",
            {"A": 0.05},
        ):
            self.assertEqual(
                risk_pct_for_score(95),
                0.01,
            )

    def test_position_sizing_is_disabled(self):
        result = calculate_position_size(
            entry=100,
            stop=95,
            score=85,
            quote_to_usd=1,
            portfolio_risk_usd=0,
            costs_per_unit_quote=0,
                fixed_costs_quote=0,
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["validated"])

    def test_missing_portfolio_risk_blocks_size(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            result = calculate_position_size(
                entry=100,
                stop=95,
                score=85,
                quote_to_usd=1,
                costs_per_unit_quote=0,
                fixed_costs_quote=0,
            )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["validated"])

    def test_missing_currency_conversion_blocks_size(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            result = calculate_position_size(
                entry=100,
                stop=95,
                score=85,
                quote_to_usd=None,
                portfolio_risk_usd=0,
                costs_per_unit_quote=0,
                fixed_costs_quote=0,
            )

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_costs_blocks_size(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            result = calculate_position_size(
                entry=100,
                stop=95,
                score=85,
                quote_to_usd=1,
                portfolio_risk_usd=0,
            )

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_fixed_costs_blocks_position_size(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            result = calculate_position_size(
                entry=100,
                stop=95,
                score=85,
                quote_to_usd=1,
                portfolio_risk_usd=0,
                costs_per_unit_quote=0,
            )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("costes fijos", result["reason"])

    def test_invalid_fixed_costs_block_position_size(self):
        invalid_values = (float("nan"), float("inf"), float("-inf"), -1)
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            for value in invalid_values:
                with self.subTest(value=value):
                    result = calculate_position_size(
                        entry=100,
                        stop=95,
                        score=85,
                        quote_to_usd=1,
                        portfolio_risk_usd=0,
                        costs_per_unit_quote=0,
                        fixed_costs_quote=value,
                    )
                    self.assertEqual(result["status"], "BLOCKED")

    def test_fixed_costs_reduce_position_without_exceeding_risk_budget(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            result = calculate_position_size(
                entry=100,
                stop=95,
                score=85,
                capital_usd=1000,
                quote_to_usd=1,
                portfolio_risk_usd=0,
                costs_per_unit_quote=1,
                fixed_costs_quote=2,
            )

        self.assertAlmostEqual(result["fixed_costs_usd"], 2)
        self.assertAlmostEqual(result["position_size"], 0.5)
        self.assertAlmostEqual(result["position_risk_usd"], 3)
        self.assertAlmostEqual(result["total_reserved_risk_usd"], 5)
        self.assertLessEqual(
            result["total_reserved_risk_usd"],
            result["risk_amount_usd"],
        )

    def test_fixed_costs_consuming_budget_block_position_size(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            result = calculate_position_size(
                entry=100,
                stop=95,
                score=85,
                capital_usd=1000,
                quote_to_usd=1,
                portfolio_risk_usd=0,
                costs_per_unit_quote=0,
                fixed_costs_quote=5,
            )

        self.assertEqual(result["status"], "BLOCKED")

    def test_invalid_prices_block_position_size(self):
        invalid_values = (
            float("nan"),
            float("inf"),
            float("-inf"),
            0,
            -1,
        )

        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            for value in invalid_values:
                with self.subTest(value=value):
                    result = calculate_position_size(
                        entry=value,
                        stop=95,
                        score=85,
                        quote_to_usd=1,
                        portfolio_risk_usd=0,
                        costs_per_unit_quote=0,
                fixed_costs_quote=0,
                    )

                    self.assertEqual(
                        result["status"],
                        "BLOCKED",
                    )

    def test_invalid_costs_block_position_size(self):
        invalid_values = (
            float("nan"),
            float("inf"),
            float("-inf"),
            -1,
        )

        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            for value in invalid_values:
                with self.subTest(value=value):
                    result = calculate_position_size(
                        entry=100,
                        stop=95,
                        score=85,
                        quote_to_usd=1,
                        portfolio_risk_usd=0,
                        costs_per_unit_quote=value,
                fixed_costs_quote=0,
                    )

                    self.assertEqual(
                        result["status"],
                        "BLOCKED",
                    )

    def test_theoretical_position_size_in_usd(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            result = calculate_position_size(
                entry=100,
                stop=95,
                score=85,
                capital_usd=1000,
                quote_to_usd=1,
                portfolio_risk_usd=0,
                costs_per_unit_quote=0,
                fixed_costs_quote=0,
            )

        self.assertEqual(
            result["status"],
            "SIMULACION_NO_VALIDADA",
        )
        self.assertFalse(result["validated"])
        self.assertEqual(result["currency"], "USD")
        self.assertAlmostEqual(result["risk_pct"], 0.005)
        self.assertAlmostEqual(result["risk_amount_usd"], 5)
        self.assertAlmostEqual(result["position_size"], 1)

    def test_position_size_includes_costs(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            result = calculate_position_size(
                entry=100,
                stop=95,
                score=85,
                capital_usd=1000,
                quote_to_usd=1,
                portfolio_risk_usd=0,
                costs_per_unit_quote=1,
                fixed_costs_quote=0,
            )

        self.assertAlmostEqual(
            result["risk_per_unit_usd"],
            6,
        )
        self.assertAlmostEqual(
            result["position_size"],
            5 / 6,
        )

    def test_position_size_respects_remaining_portfolio_risk(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            result = calculate_position_size(
                entry=100,
                stop=95,
                score=85,
                capital_usd=1000,
                quote_to_usd=1,
                portfolio_risk_usd=28,
                costs_per_unit_quote=0,
                fixed_costs_quote=0,
            )

        self.assertAlmostEqual(
            result["max_portfolio_risk_usd"],
            30,
        )
        self.assertAlmostEqual(
            result["remaining_portfolio_risk_usd"],
            2,
        )
        self.assertAlmostEqual(
            result["risk_amount_usd"],
            2,
        )
        self.assertAlmostEqual(
            result["position_size"],
            0.4,
        )

    def test_portfolio_limit_cannot_exceed_three_percent(self):
        with patch("risk_manager.ALLOW_POSITION_SIZING", True):
            with patch("risk_manager.MAX_PORTFOLIO_RISK", 0.10):
                result = calculate_position_size(
                    entry=100,
                    stop=95,
                    score=85,
                    capital_usd=1000,
                    quote_to_usd=1,
                    portfolio_risk_usd=0,
                    costs_per_unit_quote=0,
                    fixed_costs_quote=0,
                )

        self.assertAlmostEqual(
            result["max_portfolio_risk_usd"],
            30,
        )

    def test_unknown_portfolio_blocks_trading(self):
        result = get_portfolio_status(
            positions_confirmed=False,
        )

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(
            result["current_portfolio_risk_usd"]
        )

    def test_portfolio_limit(self):
        result = get_portfolio_status(
            open_positions=[
                {"risk_usd": 20},
            ],
            positions_confirmed=True,
            capital_usd=1000,
        )

        self.assertEqual(result["status"], "OK")
        self.assertEqual(
            result["current_portfolio_risk_usd"],
            20,
        )
        self.assertEqual(
            result["max_portfolio_risk_usd"],
            30,
        )
        self.assertEqual(
            result["remaining_risk_usd"],
            10,
        )

    def test_invalid_portfolio_risk_is_rejected(self):
        for value in (
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            with self.subTest(value=value):
                result = get_portfolio_status(
                    open_positions=[
                        {"risk_usd": value},
                    ],
                    positions_confirmed=True,
                    capital_usd=1000,
                )

                self.assertEqual(
                    result["status"],
                    "BLOCKED",
                )

    def test_trade_cannot_exceed_remaining_risk(self):
        self.assertFalse(
            can_add_trade(
                proposed_risk_usd=10,
                open_positions=[
                    {"risk_usd": 25},
                ],
                positions_confirmed=True,
                capital_usd=1000,
            )["allowed"]
        )

        self.assertTrue(
            can_add_trade(
                proposed_risk_usd=5,
                open_positions=[
                    {"risk_usd": 25},
                ],
                positions_confirmed=True,
                capital_usd=1000,
            )["allowed"]
        )

    def test_invalid_new_trade_risk_is_rejected(self):
        for value in (
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            with self.subTest(value=value):
                result = can_add_trade(
                    proposed_risk_usd=value,
                    open_positions=[],
                    positions_confirmed=True,
                    capital_usd=1000,
                )

                self.assertFalse(result["allowed"])


if __name__ == "__main__":
    unittest.main()
