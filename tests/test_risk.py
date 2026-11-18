
import unittest

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
        for score in range(0, 101):
            self.assertLessEqual(
                risk_pct_for_score(score),
                0.01
            )

    def test_low_score_has_no_risk(self):
        self.assertEqual(
            risk_pct_for_score(50),
            0.0
        )

    def test_missing_currency_conversion_blocks_size(self):
        result = calculate_position_size(
            entry=100,
            stop=95,
            score=85,
            quote_to_eur=None,
            portfolio_risk_eur=0,
        )

        self.assertIsNone(result)

    def test_unknown_portfolio_blocks_trading(self):
        result = get_portfolio_status(
            positions_confirmed=False
        )

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["can_open_trade"])

    def test_portfolio_limit(self):
        result = get_portfolio_status(
            open_positions=[
                {"risk_eur": 20}
            ],
            positions_confirmed=True,
            capital_eur=1000,
        )

        self.assertEqual(result["risk_eur"], 20)
        self.assertEqual(result["maximum_risk_eur"], 30)
        self.assertEqual(result["remaining_risk_eur"], 10)

    def test_trade_cannot_exceed_remaining_risk(self):
        portfolio = get_portfolio_status(
            open_positions=[
                {"risk_eur": 25}
            ],
            positions_confirmed=True,
            capital_eur=1000,
        )

        self.assertFalse(
            can_add_trade(portfolio, 10)
        )

        self.assertTrue(
            can_add_trade(portfolio, 5)
        )


if __name__ == "__main__":
    unittest.main()
