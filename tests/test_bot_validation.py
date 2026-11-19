
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

        self.assertEqual(
            result["status"],
            "BLOCKED_VALIDATION",
        )
        self.assertEqual(
            result["validation_status"],
            "BLOCKED",
        )
        self.assertFalse(result["validated"])
        self.assertFalse(result["tradable"])
        self.assertTrue(result["validation_reasons"])

    def test_candidate_is_blocked_with_invalid_portfolio(self):
        result = Bot.apply_validation_gate(
            self.candidate,
            None,
        )

        self.assertEqual(
            result["validation_status"],
            "BLOCKED",
        )
        self.assertIn(
            "Estado de cartera no disponible",
            result["validation_reasons"],
        )
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

        self.assertEqual(
            result["validation_status"],
            "BLOCKED",
        )
        self.assertIn(
            "Validación integral y cálculo de posición pendientes",
            result["validation_reasons"],
        )
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

        self.assertEqual(
            result["validation_status"],
            "BLOCKED",
        )
        self.assertIn(
            "Nivel de riesgo provisional no válido",
            result["validation_reasons"],
        )
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

    def test_zero_risk_blocks_candidate(self):
        with patch.object(
            Bot,
            "risk_pct_for_score",
            return_value=0,
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

    def test_non_finite_risk_blocks_candidate(self):
        for value in (
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            with self.subTest(value=value):
                with patch.object(
                    Bot,
                    "risk_pct_for_score",
                    return_value=value,
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

    def test_non_usd_account_currency_blocks_candidate(self):
        with patch.object(
            Bot,
            "ACCOUNT_CURRENCY",
            "EUR",
        ):
            result = Bot.apply_validation_gate(
                self.candidate,
                {"status": "OK"},
            )

        self.assertIn(
            "La divisa de la cuenta no está configurada en USD",
            result["validation_reasons"],
        )
        self.assertFalse(result["tradable"])

    def test_position_sizing_disabled_blocks_candidate(self):
        with patch.object(
            Bot,
            "ALLOW_POSITION_SIZING",
            False,
        ):
            result = Bot.apply_validation_gate(
                self.candidate,
                {"status": "OK"},
            )

        self.assertIn(
            "Cálculo de tamaño de posición desactivado",
            result["validation_reasons"],
        )

    def test_unconfirmed_costs_block_candidate(self):
        with patch.object(
            Bot,
            "CLOSE_FEES_CONFIRMED",
            False,
        ), patch.object(
            Bot,
            "SPREADS_CONFIRMED",
            False,
        ):
            result = Bot.apply_validation_gate(
                self.candidate,
                {"status": "OK"},
            )

        self.assertIn(
            "Comisiones de cierre pendientes de confirmar",
            result["validation_reasons"],
        )
        self.assertIn(
            "Spreads pendientes de confirmar",
            result["validation_reasons"],
        )
        self.assertFalse(result["tradable"])

    def test_unconfirmed_fx_blocks_candidate(self):
        with patch.object(
            Bot,
            "FX_CONVERSION_CONFIRMED",
            False,
        ):
            result = Bot.apply_validation_gate(
                self.candidate,
                {"status": "OK"},
            )

        self.assertIn(
            "Conversión de divisa pendiente de confirmar",
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

        self.assertEqual(
            result["action"],
            "NO TRADE",
        )
        self.assertFalse(result["tradable"])
        self.assertFalse(result["validated"])

    def test_portfolio_error_does_not_abort_scan(self):
        with patch.object(
            Bot,
            "get_portfolio_status",
            side_effect=RuntimeError("Portfolio error"),
        ), patch.object(
            Bot,
            "ASSETS",
            {},
        ):
            result = Bot.run_scan()

        self.assertEqual(
            result["portfolio_status"]["status"],
            "UNKNOWN",
        )
        self.assertEqual(
            result["analyses_completed"],
            0,
        )
        self.assertFalse(result["automatic_execution"])
        self.assertFalse(result["real_trading_authorized"])


if __name__ == "__main__":
    unittest.main()
