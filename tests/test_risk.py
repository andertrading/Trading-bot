
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

    def test_invalid_scores_have_no_risk(self):
        for score in (
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            with self.subTest(score=score):
                self.assertEqual(
                    risk_pct_for_score(score),
                    0.0,
                )

    def test_position_sizing_is_disabled(self):
        result = calculate_position_size(
            entry=100,
            stop=95,
            score=85,
            quote_to_usd=1,
            portfolio_risk_usd=0,
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
            )

        self.assertEqual(result["status"], "BLOCKED")

    def test_invalid_prices_block_position_size(self):
        invalid_values = (
            float("nan"),
            float("inf"),
            float("-inf"),
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
                    )

                    self.assertEqual(
                        result["status"],
                        "BLOCKED",
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
