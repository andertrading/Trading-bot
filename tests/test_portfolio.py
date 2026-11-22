
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

    def test_single_trade_risk_cannot_exceed_one_percent(self):
        result = can_add_trade(
            proposed_risk_usd=10.01,
            open_positions=[],
            positions_confirmed=True,
            capital_usd=1000,
        )
        self.assertFalse(result["allowed"])
        self.assertIn("límite absoluto del 1 %", result["reason"])
        self.assertAlmostEqual(result["max_single_trade_risk_usd"], 10.0)

    def test_single_trade_limit_is_hard_capped_even_if_config_is_higher(self):
        with patch("portfolio.MAX_RISK_PER_TRADE", 0.05):
            result = can_add_trade(
                proposed_risk_usd=10.01,
                open_positions=[],
                positions_confirmed=True,
                capital_usd=1000,
            )
        self.assertFalse(result["allowed"])
        self.assertAlmostEqual(result["max_single_trade_risk_usd"], 10.0)

    def test_single_trade_at_one_percent_can_be_allowed_if_portfolio_has_room(self):
        result = can_add_trade(
            proposed_risk_usd=10.0,
            open_positions=[{"risk_usd": 15.0}],
            positions_confirmed=True,
            capital_usd=1000,
        )
        self.assertTrue(result["allowed"])

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


    def test_sized_trade_admission_uses_total_reserved_risk_including_fees(self):
        from portfolio import can_add_sized_trade

        sizing = {
            "status": "SIMULACION_NO_VALIDADA",
            "validated": False,
            "risk_amount_usd": 10.0,
            "position_risk_usd": 8.0,
            "fixed_costs_usd": 2.0,
            "position_size": 1.0,
            "entry_price_quote": 100.0,
            "quote_to_usd": 1.0,
            "position_notional_usd": 100.0,
            "available_capital_usd": 1000.0,
            "total_reserved_risk_usd": 10.0,
        }
        result = can_add_sized_trade(
            sizing,
            open_positions=[{"risk_usd": 20.0}],
            positions_confirmed=True,
            capital_usd=1000,
        )
        self.assertTrue(result["allowed"])
        self.assertAlmostEqual(result["proposed_risk_usd"], 10.0)
        self.assertFalse(result["live_execution_authorized"])

    def test_sized_trade_admission_blocks_when_fees_push_over_aggregate_limit(self):
        from portfolio import can_add_sized_trade

        sizing = {
            "status": "SIMULACION_NO_VALIDADA",
            "validated": False,
            "risk_amount_usd": 10.0,
            "position_risk_usd": 9.0,
            "fixed_costs_usd": 1.0,
            "position_size": 1.0,
            "entry_price_quote": 100.0,
            "quote_to_usd": 1.0,
            "position_notional_usd": 100.0,
            "available_capital_usd": 1000.0,
            "total_reserved_risk_usd": 10.0,
        }
        result = can_add_sized_trade(
            sizing,
            open_positions=[{"risk_usd": 21.0}],
            positions_confirmed=True,
            capital_usd=1000,
        )
        self.assertFalse(result["allowed"])

    def test_sized_trade_admission_blocks_unknown_open_positions(self):
        from portfolio import can_add_sized_trade

        sizing = {
            "status": "SIMULACION_NO_VALIDADA",
            "validated": False,
            "risk_amount_usd": 5.0,
            "position_risk_usd": 5.0,
            "fixed_costs_usd": 0.0,
            "position_size": 1.0,
            "entry_price_quote": 100.0,
            "quote_to_usd": 1.0,
            "position_notional_usd": 100.0,
            "available_capital_usd": 1000.0,
            "total_reserved_risk_usd": 5.0,
        }
        result = can_add_sized_trade(
            sizing,
            positions_confirmed=False,
            capital_usd=1000,
        )
        self.assertFalse(result["allowed"])

    def test_sized_trade_admission_blocks_open_position_without_known_stop_risk(self):
        from portfolio import can_add_sized_trade

        sizing = {
            "status": "SIMULACION_NO_VALIDADA",
            "validated": False,
            "risk_amount_usd": 5.0,
            "position_risk_usd": 5.0,
            "fixed_costs_usd": 0.0,
            "position_size": 1.0,
            "entry_price_quote": 100.0,
            "quote_to_usd": 1.0,
            "position_notional_usd": 100.0,
            "available_capital_usd": 1000.0,
            "total_reserved_risk_usd": 5.0,
        }
        result = can_add_sized_trade(
            sizing,
            open_positions=[{"position_id": 123}],
            positions_confirmed=True,
            capital_usd=1000,
        )
        self.assertFalse(result["allowed"])

    def test_sized_trade_admission_rejects_inconsistent_reserved_risk(self):
        from portfolio import can_add_sized_trade

        sizing = {
            "status": "SIMULACION_NO_VALIDADA",
            "validated": False,
            "risk_amount_usd": 5.0,
            "position_risk_usd": 5.0,
            "fixed_costs_usd": 2.0,
            "position_size": 1.0,
            "entry_price_quote": 100.0,
            "quote_to_usd": 1.0,
            "position_notional_usd": 100.0,
            "available_capital_usd": 1000.0,
            "total_reserved_risk_usd": 7.0,
        }
        result = can_add_sized_trade(
            sizing,
            open_positions=[],
            positions_confirmed=True,
            capital_usd=1000,
        )
        self.assertFalse(result["allowed"])

    def test_sized_trade_admission_rejects_blocked_sizing_result(self):
        from portfolio import can_add_sized_trade

        result = can_add_sized_trade(
            {"status": "BLOCKED", "total_reserved_risk_usd": 5.0},
            open_positions=[],
            positions_confirmed=True,
            capital_usd=1000,
        )
        self.assertFalse(result["allowed"])


if __name__ == "__main__":
    unittest.main()
