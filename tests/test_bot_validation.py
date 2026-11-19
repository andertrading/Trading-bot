
import unittest
from unittest.mock import patch

import Bot


class TestBotValidation(unittest.TestCase):

    def setUp(self):
        self.candidate = {
            "action": "BUY CANDIDATE",
            "asset": "AAPL",
            "timeframe": "1d",
            "score": 85,
            "status": "PENDING_VALIDATION",
        }

    def test_candidate_is_blocked_with_unknown_portfolio(self):
        result = Bot.apply_validation_gate(
            self.candidate,
            {"status": "UNKNOWN"},
        )

        self.assertEqual(result["status"], "BLOCKED_VALIDATION")
        self.assertEqual(result["validation_status"], "BLOCKED")
        self.assertFalse(result["validated"])
        self.assertFalse(result["tradable"])

    def test_candidate_never_approved_without_full_validation(self):
        with patch.object(Bot, "ALLOW_POSITION_SIZING", True), \
             patch.object(Bot, "CLOSE_FEES_CONFIRMED", True), \
             patch.object(Bot, "SPREADS_CONFIRMED", True), \
             patch.object(Bot, "FX_CONVERSION_CONFIRMED", True):

            result = Bot.apply_validation_gate(
                self.candidate,
                {"status": "OK"},
            )

        self.assertEqual(result["validation_status"], "BLOCKED")
        self.assertFalse(result["validated"])
        self.assertFalse(result["tradable"])

    def test_risk_manager_error_blocks_candidate(self):
        with patch.object(
            Bot,
            "risk_pct_for_score",
            side_effect=RuntimeError("Risk error"),
        ):
            result = Bot.apply_validation_gate(
                self.candidate,
                {"status": "OK"},
            )

        self.assertEqual(result["validation_status"], "BLOCKED")
        self.assertFalse(result["tradable"])

    def test_excessive_risk_blocks_candidate(self):
        with patch.object(
            Bot,
            "risk_pct_for_score",
            return_value=0.02,
        ):
            result = Bot.apply_validation_gate(
                self.candidate,
                {"status": "OK"},
            )

        self.assertIn(
            "Nivel de riesgo provisional no válido",
            result["validation_reasons"],
        )
        self.assertFalse(result["tradable"])

    def test_no_trade_remains_non_tradable(self):
        result = Bot.apply_validation_gate(
            {
                "action": "NO TRADE",
                "asset": "AAPL",
                "timeframe": "1d",
            },
            {"status": "OK"},
        )

        self.assertEqual(result["action"], "NO TRADE")
        self.assertFalse(result["tradable"])


if __name__ == "__main__":
    unittest.main()
