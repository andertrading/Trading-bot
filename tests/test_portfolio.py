
import unittest
from unittest.mock import patch

from portfolio import (
    get_portfolio_status,
    can_add_trade,
)


class TestPortfolio(unittest.TestCase):

    def test_unknown_positions_block_new_trade(self):
        result = get_portfolio_status(
            positions_confirmed=False,
        )

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(
            result["current_portfolio_risk_usd"]
        )

        trade = can_add_trade(
            proposed_risk_usd=5,
            positions_confirmed=False,
        )

        self.assertFalse(trade["allowed"])

    def test_portfolio_limit_is_thirty_usd(self):
        result = get_portfolio_status(
            open_positions=[
                {"risk_usd": 20},
            ],
            positions_confirmed=True,
            capital_usd=1000,
        )

        self.assertEqual(result["status"], "OK")
        self.assertAlmostEqual(
            result["max_portfolio_risk_usd"],
            30,
        )
        self.assertAlmostEqual(
            result["remaining_risk_usd"],
            10,
        )

    def test_portfolio_risk_cannot_exceed_three_percent(self):
        with patch("portfolio.MAX_PORTFOLIO_RISK", 0.10):
            result = get_portfolio_status(
                open_positions=[],
                positions_confirmed=True,
                capital_usd=1000,
            )

        self.assertAlmostEqual(
            result["max_portfolio_risk_usd"],
            30,
        )

    def test_reaching_portfolio_limit_blocks_trading(self):
        result = get_portfolio_status(
            open_positions=[
                {"risk_usd": 30},
            ],
            positions_confirmed=True,
            capital_usd=1000,
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_exceeding_portfolio_limit_blocks_trading(self):
        result = get_portfolio_status(
            open_positions=[
                {"risk_usd": 35},
            ],
            positions_confirmed=True,
            capital_usd=1000,
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["remaining_risk_usd"], 0)

    def test_new_trade_must_fit_remaining_risk(self):
        result = can_add_trade(
            proposed_risk_usd=5,
            open_positions=[
                {"risk_usd": 25},
            ],
            positions_confirmed=True,
            capital_usd=1000,
        )

        self.assertTrue(result["allowed"])

        result = can_add_trade(
            proposed_risk_usd=6,
            open_positions=[
                {"risk_usd": 25},
            ],
            positions_confirmed=True,
            capital_usd=1000,
        )

        self.assertFalse(result["allowed"])

    def test_invalid_position_risk_is_rejected(self):
        invalid_values = (
            float("nan"),
            float("inf"),
            float("-inf"),
            -1,
            True,
            None,
        )

        for value in invalid_values:
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

    def test_invalid_capital_is_rejected(self):
        for value in (
            float("nan"),
            float("inf"),
            float("-inf"),
            0,
            -100,
            True,
        ):
            with self.subTest(value=value):
                result = get_portfolio_status(
                    open_positions=[],
                    positions_confirmed=True,
                    capital_usd=value,
                )

                self.assertEqual(
                    result["status"],
                    "BLOCKED",
                )

    def test_invalid_proposed_risk_is_rejected(self):
        invalid_values = (
            float("nan"),
            float("inf"),
            float("-inf"),
            0,
            -1,
            True,
        )

        for value in invalid_values:
            with self.subTest(value=value):
                result = can_add_trade(
                    proposed_risk_usd=value,
                    open_positions=[],
                    positions_confirmed=True,
                    capital_usd=1000,
                )

                self.assertFalse(result["allowed"])

    def test_invalid_portfolio_configuration_is_rejected(self):
        with patch("portfolio.MAX_PORTFOLIO_RISK", float("nan")):
            result = get_portfolio_status(
                open_positions=[],
                positions_confirmed=True,
                capital_usd=1000,
            )

        self.assertEqual(result["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
