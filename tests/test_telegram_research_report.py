import unittest

import Bot


class TestTelegramResearchReport(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "action": "BUY CANDIDATE",
            "asset": "NVDA",
            "timeframe": "15m",
            "validation_status": "BLOCKED",
            "score": 85,
            "entry_reference": 180.0,
            "stop": 178.2,
            "target": 183.6,
            "risk_reward": 2.0,
            "provisional_risk_pct": 0.0025,
            "data_timestamp": "2026-11-21T11:45:00+00:00",
            "data_age_minutes": 3.5,
            "reasons": ["EMA20 favorable", "MACD bullish"],
            "validation_reasons": ["Spread not confirmed"],
        }
        self.scan = {
            "timestamp": "2026-11-21T12:00:00+00:00",
            "analyses_completed": 24,
            "technical_candidates_found": 1,
            "candidates_blocked": 1,
            "validated_candidates": 0,
            "no_trade_count": 23,
            "portfolio_status": {"status": "UNKNOWN"},
            "results": [self.candidate],
        }

    def test_report_contains_gross_reward_risk_and_reference_capital(self):
        messages = Bot.build_telegram_messages(self.scan)
        self.assertEqual(len(messages), 2)
        report = messages[1]
        self.assertIn("SOLO INVESTIGACIÓN", report)
        self.assertIn("NO OPERAR", report)
        self.assertIn("+2.00%", report)
        self.assertIn("1.00%", report)
        self.assertIn("2.50 USD", report)
        self.assertIn("5.00 USD", report)
        self.assertIn("suponiendo que el tamaño se ajusta al riesgo", report)
        self.assertIn("antes de costes y slippage", report)

    def test_report_separates_strategy_reasons_from_validation_blockers(self):
        report = Bot.build_telegram_messages(self.scan)[1]
        self.assertIn("JUSTIFICACIÓN TÉCNICA", report)
        self.assertIn("EMA20 favorable", report)
        self.assertIn("VALIDACIONES PENDIENTES", report)
        self.assertIn("Spread not confirmed", report)


    def test_report_displays_data_provider_and_ohlc_policy(self):
        self.candidate["data_provider"] = "twelvedata"
        self.candidate["price_adjustment_policy"] = "raw_unadjusted_intraday"
        report = Bot.build_telegram_messages(self.scan)[1]
        self.assertIn("Proveedor: <b>twelvedata</b>", report)
        self.assertIn("raw_unadjusted_intraday", report)

    def test_report_discloses_unvalidated_probability_and_expiry(self):
        report = Bot.build_telegram_messages(self.scan)[1]
        self.assertIn("Probabilidad histórica: <b>NO VALIDADA</b>", report)
        self.assertIn("Caducidad de la señal: <b>NO CONFIGURADA</b>", report)
        self.assertIn("Noticias y costes: <b>NO INCORPORADOS AÚN</b>", report)


    def test_strategy_validated_report_still_requires_manual_cost_checks(self):
        self.candidate["strategy_validated"] = True
        report = Bot.build_telegram_messages(self.scan)[1]
        self.assertIn("SEÑAL TÉCNICA · DECISIÓN MANUAL", report)
        self.assertIn("CONFIRMA COSTES Y CONDICIONES", report)
        self.assertNotIn("NO OPERAR — ESTRATEGIA NO VALIDADA", report)
        self.assertIn("La decisión y ejecución siguen siendo manuales", report)

    def test_unknown_portfolio_is_explained_without_broker_access(self):
        self.candidate["validation_reasons"] = [
            "Cartera no confirmada o riesgo de cartera bloqueado"
        ]
        report = Bot.build_telegram_messages(self.scan)[1]
        self.assertIn("el bot no consulta posiciones", report)
        self.assertIn("límite del 3%", report)

    def test_report_escapes_untrusted_text(self):
        self.candidate["reasons"] = ["Breakout <script>"]
        report = Bot.build_telegram_messages(self.scan)[1]
        self.assertIn("Breakout &lt;script&gt;", report)
        self.assertNotIn("<script>", report)

    def test_no_candidate_summary_is_clear(self):
        self.scan["results"] = []
        messages = Bot.build_telegram_messages(self.scan)
        self.assertEqual(len(messages), 2)
        self.assertIn("No se han detectado candidatas técnicas", messages[1])


    def test_report_displays_calendar_aware_signal_expiry(self):
        self.candidate["expires_at"] = "2026-11-21T12:30:00+00:00"
        report = Bot.build_telegram_messages(self.scan)[1]
        self.assertIn("Caducidad de la señal:", report)
        self.assertNotIn("Caducidad de la señal: <b>NO CONFIGURADA</b>", report)

    def test_candidate_without_valid_levels_does_not_crash(self):
        self.candidate["entry_reference"] = None
        self.candidate["stop"] = None
        self.candidate["target"] = None
        report = Bot.build_telegram_messages(self.scan)[1]
        self.assertIn("Potencial bruto hasta objetivo: <b>N/D</b>", report)
        self.assertIn("Movimiento adverso hasta stop: <b>N/D</b>", report)


if __name__ == "__main__":
    unittest.main()
