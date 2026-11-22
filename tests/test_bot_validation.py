
import unittest
from datetime import datetime, timezone
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
        self.assertIn(
            "No existe un resultado completo de cálculo de tamaño para esta candidata",
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

    def test_unverified_etoro_execution_price_basis_blocks_candidate(self):
        with patch.object(
            Bot, "ETORO_EXECUTION_PRICE_BASIS_CONFIRMED", False
        ):
            result = Bot.apply_validation_gate(
                self.candidate,
                {"status": "OK"},
            )
        self.assertIn(
            "Correspondencia entre OHLC históricos y precios bid/ask ejecutables de eToro no verificada",
            result["validation_reasons"],
        )
        self.assertFalse(result["tradable"])

    def test_unconfirmed_etoro_execution_and_fee_details_block_candidate(self):
        with patch.object(
            Bot, "ETORO_EXECUTION_TYPES_CONFIRMED", False
        ), patch.object(
            Bot, "ETORO_FEE_SCHEDULE_CONFIRMED", False
        ), patch.object(
            Bot, "ETORO_ACCOUNT_CURRENCY_CONFIRMED", False
        ):
            result = Bot.apply_validation_gate(
                self.candidate,
                {"status": "OK"},
            )
        self.assertIn(
            "Tipo de ejecución REAL/CFD pendiente de verificar en cada instrumento de eToro",
            result["validation_reasons"],
        )
        self.assertIn(
            "Nivel Club y comisiones personales de eToro pendientes de confirmar",
            result["validation_reasons"],
        )
        self.assertIn(
            "Divisa real de la cuenta eToro pendiente de confirmar",
            result["validation_reasons"],
        )
        self.assertFalse(result["tradable"])

    def test_unconfirmed_fx_does_not_block_same_currency_asset(self):
        with patch.object(Bot, "FX_CONVERSION_CONFIRMED", False):
            blockers = Bot.get_validation_blockers(
                {"status": "OK"},
                asset="AAPL",
            )

        self.assertNotIn(
            "Conversión de divisa pendiente de confirmar",
            blockers,
        )

    def test_unconfirmed_fx_blocks_different_currency_asset(self):
        eur_config = dict(Bot.ASSETS["AAPL"])
        eur_config["currency"] = "EUR"
        with patch.object(Bot, "FX_CONVERSION_CONFIRMED", False):
            with patch.dict(Bot.ASSETS, {"AAPL": eur_config}):
                blockers = Bot.get_validation_blockers(
                    {"status": "OK"},
                    asset="AAPL",
                )

        self.assertIn(
            "Conversión de divisa pendiente de confirmar",
            blockers,
        )

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



    def test_scan_can_select_only_one_supported_timeframe(self):
        with patch.object(Bot, "ASSETS", {
            "AAPL": {"enabled": True},
        }), patch.object(
            Bot, "get_portfolio_status", return_value={"status": "UNKNOWN"}
        ), patch.object(
            Bot, "is_intraday_market_open", return_value=False
        ):
            result = Bot.run_scan(timeframes=["15m"])

        self.assertEqual(result["timeframes"], ["15m"])
        self.assertEqual(result["analyses_completed"], 1)
        self.assertTrue(result["results"][0]["market_closed"])
        self.assertFalse(result["results"][0]["data_valid"])

    def test_scan_rejects_unsupported_timeframe(self):
        with self.assertRaises(ValueError):
            Bot.run_scan(timeframes=["5m"])

    def test_daily_only_scan_does_not_call_intraday_market_check(self):
        with patch.object(Bot, "ASSETS", {
            "AAPL": {"enabled": True},
        }), patch.object(
            Bot, "get_portfolio_status", return_value={"status": "UNKNOWN"}
        ), patch.object(
            Bot, "analyze_asset", return_value={
                "action": "NO TRADE",
                "asset": "AAPL",
                "timeframe": "1d",
                "data_valid": True,
            }
        ) as analyze, patch.object(
            Bot, "is_intraday_market_open"
        ) as market_open:
            result = Bot.run_scan(timeframes=["1d"])

        analyze.assert_called_once_with("AAPL", "1d")
        market_open.assert_not_called()
        self.assertEqual(result["timeframes"], ["1d"])

    def test_cost_readiness_reports_missing_execution_inputs(self):
        candidate = {
            "asset": "AAPL",
            "entry": 100.0,
            "target": 110.0,
        }
        with patch.object(
            Bot,
            "estimate_verified_registry_trade_costs",
            return_value={
                "valid": True,
                "complete": False,
                "unpriced_costs": [
                    "execution_type_evidence_missing",
                    "observed_bid_ask_spread_evidence_missing",
                ],
            },
        ) as estimate:
            lines = Bot._format_cost_readiness(candidate)

        estimate.assert_called_once_with("AAPL", 100.0, 110.0, 1.0)
        report = "\n".join(lines)
        self.assertIn("INCOMPLETO", report)
        self.assertIn("Tipo de ejecución REAL/CFD pendiente", report)
        self.assertIn("Spread bid/ask observado y fechado no disponible", report)
        self.assertIn("rentabilidad neta no calculable", report)

    def test_cost_readiness_does_not_estimate_without_valid_levels(self):
        candidate = {
            "asset": "AAPL",
            "entry": 0,
            "target": 110.0,
        }
        with patch.object(Bot, "estimate_verified_registry_trade_costs") as estimate:
            lines = Bot._format_cost_readiness(candidate)

        estimate.assert_not_called()
        self.assertIn("INCOMPLETO", "\n".join(lines))
        self.assertIn("niveles de entrada u objetivo válidos", "\n".join(lines))

    def test_cost_readiness_never_claims_net_return_from_unit_estimate(self):
        candidate = {
            "asset": "AAPL",
            "entry": 100.0,
            "target": 110.0,
        }
        with patch.object(
            Bot,
            "estimate_verified_registry_trade_costs",
            return_value={
                "valid": True,
                "complete": True,
                "registry_status": "ELIGIBLE",
                "unpriced_costs": [],
            },
        ):
            lines = Bot._format_cost_readiness(candidate)

        report = "\n".join(lines)
        self.assertIn("unidad de referencia", report)
        self.assertIn("No se calcula beneficio neto", report)



    def test_sized_admission_gate_is_called_with_confirmed_open_positions(self):
        sizing_result = {
            "status": "SIMULACION_NO_VALIDADA",
            "validated": False,
            "risk_amount_usd": 5.0,
            "position_risk_usd": 4.0,
            "fixed_costs_usd": 1.0,
            "total_reserved_risk_usd": 5.0,
        }
        portfolio = {
            "status": "OK",
            "open_positions": [],
            "positions_confirmed": True,
        }
        candidate = {
            **self.candidate,
            "sizing_result": sizing_result,
            "data_valid": True,
            "data_timestamp": datetime.now(timezone.utc).isoformat(),
            "data_age_minutes": 1.0,
        }

        with patch.object(Bot, "ALLOW_POSITION_SIZING", True), \
             patch.object(Bot, "CLOSE_FEES_CONFIRMED", True), \
             patch.object(Bot, "SPREADS_CONFIRMED", True), \
             patch.object(Bot, "FX_CONVERSION_CONFIRMED", True), \
             patch.object(Bot, "ETORO_EXECUTION_TYPES_CONFIRMED", True), \
             patch.object(Bot, "ETORO_FEE_SCHEDULE_CONFIRMED", True), \
             patch.object(Bot, "ETORO_ACCOUNT_CURRENCY_CONFIRMED", True), \
             patch.object(Bot, "ETORO_EXECUTION_PRICE_BASIS_CONFIRMED", True), \
             patch.object(Bot, "STRATEGY_VALIDATED", True), \
             patch.object(Bot, "classify_asset_economic", return_value={"status": "ELIGIBLE", "blockers": []}):
            result = Bot.apply_validation_gate(candidate, portfolio)

        self.assertEqual(result["validation_status"], "APPROVED")
        self.assertTrue(result["validated"])
        self.assertTrue(result["tradable"])
        self.assertTrue(result["portfolio_admission"]["allowed"])
        self.assertFalse(result["portfolio_admission"]["live_execution_authorized"])

    def test_unverified_twelvedata_daily_split_adjustment_blocks_candidate(self):
        candidate = {
            **self.candidate,
            "data_valid": True,
            "data_timestamp": datetime.now(timezone.utc).isoformat(),
            "data_age_minutes": 1.0,
            "data_provider": "twelvedata",
            "price_adjustment_verified": False,
            "price_adjustment_verification_status": (
                "REQUESTED_NOT_INDEPENDENTLY_VERIFIED"
            ),
        }
        result = Bot.apply_validation_gate(candidate, {"status": "UNKNOWN"})
        self.assertIn(
            "Ajuste diario por splits de Twelve Data solicitado, pero no verificado independientemente",
            result["validation_reasons"],
        )
        self.assertFalse(result["tradable"])

    def test_twelvedata_daily_claim_without_config_evidence_is_blocked(self):
        candidate = {
            **self.candidate,
            "data_valid": True,
            "data_timestamp": datetime.now(timezone.utc).isoformat(),
            "data_age_minutes": 1.0,
            "data_provider": "twelvedata",
            "price_adjustment_verified": True,
            "price_adjustment_verification_status": "INDEPENDENTLY_VERIFIED",
            "price_adjustment_verification_source": None,
            "price_adjustment_verified_at": None,
        }
        result = Bot.apply_validation_gate(candidate, {"status": "UNKNOWN"})
        self.assertIn(
            "Ajuste diario por splits de Twelve Data solicitado, pero no verificado independientemente",
            result["validation_reasons"],
        )
        self.assertFalse(result["tradable"])

    def test_twelvedata_daily_cannot_pass_with_legacy_true_flag_only(self):
        candidate = {
            **self.candidate,
            "data_valid": True,
            "data_timestamp": datetime.now(timezone.utc).isoformat(),
            "data_age_minutes": 1.0,
            "data_provider": "twelvedata",
            "price_adjustment_verified": True,
            "price_adjustment_verification_status": (
                "REQUESTED_NOT_INDEPENDENTLY_VERIFIED"
            ),
        }
        result = Bot.apply_validation_gate(candidate, {"status": "UNKNOWN"})
        self.assertIn(
            "Ajuste diario por splits de Twelve Data solicitado, pero no verificado independientemente",
            result["validation_reasons"],
        )
        self.assertFalse(result["tradable"])

    def test_global_flags_cannot_bypass_unverified_instrument_registry(self):
        sizing_result = {
            "status": "SIMULACION_NO_VALIDADA",
            "validated": False,
            "risk_amount_usd": 5.0,
            "position_risk_usd": 4.0,
            "fixed_costs_usd": 1.0,
            "total_reserved_risk_usd": 5.0,
        }
        portfolio = {
            "status": "OK",
            "open_positions": [],
            "positions_confirmed": True,
        }
        candidate = {**self.candidate, "sizing_result": sizing_result}
        with patch.object(Bot, "ALLOW_POSITION_SIZING", True), \
             patch.object(Bot, "CLOSE_FEES_CONFIRMED", True), \
             patch.object(Bot, "SPREADS_CONFIRMED", True), \
             patch.object(Bot, "FX_CONVERSION_CONFIRMED", True), \
             patch.object(Bot, "ETORO_EXECUTION_TYPES_CONFIRMED", True), \
             patch.object(Bot, "ETORO_FEE_SCHEDULE_CONFIRMED", True), \
             patch.object(Bot, "ETORO_ACCOUNT_CURRENCY_CONFIRMED", True), \
             patch.object(Bot, "ETORO_EXECUTION_PRICE_BASIS_CONFIRMED", True), \
             patch.object(Bot, "STRATEGY_VALIDATED", True), \
             patch.object(Bot, "classify_asset_economic", return_value={
                 "status": "COSTS_UNVERIFIED",
                 "blockers": ["round_trip_fee_evidence_missing"],
             }):
            result = Bot.apply_validation_gate(candidate, portfolio)
        self.assertEqual(result["validation_status"], "BLOCKED")
        self.assertFalse(result["tradable"])
        self.assertIn(
            "Elegibilidad económica del instrumento: COSTS_UNVERIFIED.",
            result["validation_reasons"],
        )
        self.assertTrue(any(
            "round_trip_fee_evidence_missing" in reason
            for reason in result["validation_reasons"]
        ))

    def test_global_flags_and_registry_cannot_bypass_missing_market_data_validation(self):
        sizing_result = {
            "status": "SIMULACION_NO_VALIDADA",
            "validated": False,
            "risk_amount_usd": 5.0,
            "position_risk_usd": 4.0,
            "fixed_costs_usd": 1.0,
            "total_reserved_risk_usd": 5.0,
        }
        portfolio = {
            "status": "OK",
            "open_positions": [],
            "positions_confirmed": True,
        }
        candidate = {**self.candidate, "sizing_result": sizing_result}
        with patch.object(Bot, "ALLOW_POSITION_SIZING", True), \
             patch.object(Bot, "CLOSE_FEES_CONFIRMED", True), \
             patch.object(Bot, "SPREADS_CONFIRMED", True), \
             patch.object(Bot, "FX_CONVERSION_CONFIRMED", True), \
             patch.object(Bot, "ETORO_EXECUTION_TYPES_CONFIRMED", True), \
             patch.object(Bot, "ETORO_FEE_SCHEDULE_CONFIRMED", True), \
             patch.object(Bot, "ETORO_ACCOUNT_CURRENCY_CONFIRMED", True), \
             patch.object(Bot, "ETORO_EXECUTION_PRICE_BASIS_CONFIRMED", True), \
             patch.object(Bot, "STRATEGY_VALIDATED", True), \
             patch.object(Bot, "classify_asset_economic", return_value={
                 "status": "ELIGIBLE", "blockers": []
             }):
            result = Bot.apply_validation_gate(candidate, portfolio)
        self.assertEqual(result["validation_status"], "BLOCKED")
        self.assertFalse(result["tradable"])
        self.assertEqual(result["data_validation_status"], "INVALID")
        self.assertIn(
            "Datos de mercado no validados o timestamp de datos no verificable",
            result["validation_reasons"],
        )

    def test_sized_admission_gate_blocks_unconfirmed_open_positions(self):
        sizing_result = {
            "status": "SIMULACION_NO_VALIDADA",
            "validated": False,
            "risk_amount_usd": 5.0,
            "position_risk_usd": 4.0,
            "fixed_costs_usd": 1.0,
            "total_reserved_risk_usd": 5.0,
        }
        candidate = {**self.candidate, "sizing_result": sizing_result}
        portfolio = {
            "status": "OK",
            "open_positions": None,
            "positions_confirmed": False,
        }

        with patch.object(Bot, "ALLOW_POSITION_SIZING", True), \
             patch.object(Bot, "CLOSE_FEES_CONFIRMED", True), \
             patch.object(Bot, "SPREADS_CONFIRMED", True), \
             patch.object(Bot, "FX_CONVERSION_CONFIRMED", True), \
             patch.object(Bot, "ETORO_EXECUTION_TYPES_CONFIRMED", True), \
             patch.object(Bot, "ETORO_FEE_SCHEDULE_CONFIRMED", True), \
             patch.object(Bot, "ETORO_ACCOUNT_CURRENCY_CONFIRMED", True), \
             patch.object(Bot, "ETORO_EXECUTION_PRICE_BASIS_CONFIRMED", True), \
             patch.object(Bot, "STRATEGY_VALIDATED", True):
            result = Bot.apply_validation_gate(candidate, portfolio)

        self.assertEqual(result["validation_status"], "BLOCKED")
        self.assertFalse(result["tradable"])
        self.assertFalse(result["portfolio_admission"]["allowed"])
        self.assertIn("posiciones abiertas no están confirmadas", result["portfolio_admission"]["reason"])


    def test_approved_candidate_is_included_in_telegram_report(self):
        candidate = {
            "action": "BUY CANDIDATE",
            "validation_status": "APPROVED",
            "asset": "AAPL",
            "timeframe": "1d",
            "score": 90,
            "entry": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "provisional_risk_pct": 0.005,
            "generated_at": "2026-11-21T10:00:00+00:00",
            "expires_at": "2026-11-21T12:00:00+00:00",
            "strategy_validated": True,
            "data_timestamp": "2026-11-21T10:00:00+00:00",
            "data_age_minutes": 1,
            "data_provider": "test",
            "price_adjustment_policy": "none",
            "reasons": ["test reason"],
            "validation_reasons": [],
        }
        scan = {
            "timestamp": "2026-11-21T10:00:00+00:00",
            "analyses_completed": 1,
            "technical_candidates_found": 1,
            "candidates_blocked": 0,
            "validated_candidates": 1,
            "no_trade_count": 0,
            "portfolio_status": {"status": "OK"},
            "results": [candidate],
        }
        with patch.object(
            Bot,
            "estimate_verified_registry_trade_costs",
            return_value={
                "valid": True,
                "complete": False,
                "unpriced_costs": ["live_market_bid_ask_spread_missing"],
            },
        ):
            messages = Bot.build_telegram_messages(scan)

        self.assertEqual(len(messages), 2)
        self.assertIn("SEÑAL TÉCNICA", messages[1])
        self.assertIn("CONFIRMA COSTES", messages[1])



class TestTwelveDataDailyRequestSuppression(unittest.TestCase):

    @patch("Bot.download_data")
    def test_unverified_daily_twelvedata_is_skipped_before_api_request(
        self,
        mock_download,
    ):
        result = Bot.analyze_asset("TSLA", "1d")
        mock_download.assert_not_called()
        self.assertEqual(result["action"], "NO TRADE")
        self.assertIn("no se solicita histórico", result["reason"])

    @patch("Bot.download_data", return_value=None)
    def test_manual_m15_lab_still_requests_twelvedata(
        self,
        mock_download,
    ):
        result = Bot.analyze_asset("TSLA", "15m")
        mock_download.assert_called_once_with("TSLA", "15m")
        self.assertEqual(result["action"], "NO TRADE")



    def test_legacy_telegram_path_never_sends_blocked_candidates(self):
        scan = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "analyses_completed": 1,
            "technical_candidates_found": 1,
            "candidates_blocked": 1,
            "validated_candidates": 0,
            "no_trade_count": 0,
            "results": [{
                "action": "BUY CANDIDATE",
                "asset": "AAPL",
                "timeframe": "1d",
                "validation_status": "BLOCKED",
                "tradable": False,
                "validated": False,
                "economic_eligibility": {
                    "status": "COSTS_UNVERIFIED",
                    "blockers": ["round_trip_fee_evidence_missing"],
                },
            }],
        }
        with (
            patch.object(Bot, "TELEGRAM_ENABLED", True),
            patch.object(Bot, "SIGNAL_ALERTS_ENABLED", True),
            patch.object(Bot, "STRATEGY_VALIDATED", True),
            patch.object(Bot, "send_telegram_message") as send,
        ):
            result = Bot.send_telegram_notifications(scan)

        self.assertEqual(result, "held:no_eligible_candidates")
        send.assert_not_called()

    def test_legacy_telegram_path_requires_global_alert_and_strategy_gates(self):
        with (
            patch.object(Bot, "TELEGRAM_ENABLED", True),
            patch.object(Bot, "SIGNAL_ALERTS_ENABLED", False),
            patch.object(Bot, "STRATEGY_VALIDATED", True),
            patch.object(Bot, "send_telegram_message") as send,
        ):
            result = Bot.send_telegram_notifications({"results": []})

        self.assertEqual(result, "held:global_validation_gate")
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
