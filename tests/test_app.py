
import unittest
from unittest.mock import patch

from Bot import app, run_scan
from config import ASSETS


class TestTradingBotApp(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def test_home_endpoint(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertEqual(data["version"], "3.0")
        self.assertEqual(data["mode"], "PAPER_ONLY")
        self.assertFalse(data["automatic_execution"])

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["version"], "3.0")
        self.assertEqual(data["mode"], "PAPER_ONLY")

    @patch("Bot.run_scan")
    def test_scan_endpoint(self, mock_run_scan):
        mock_run_scan.return_value = {
            "version": "3.0",
            "status": "analysis_completed",
            "analyses_completed": 24,
            "candidates_found": 0,
            "no_trade_count": 24,
            "results": [],
            "mode": "PAPER_ONLY",
            "telegram_alerts_enabled": False,
            "automatic_execution": False,
            "real_trading_authorized": False,
        }

        response = self.client.get("/scan")
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertEqual(data["status"], "analysis_completed")
        self.assertEqual(data["mode"], "PAPER_ONLY")
        self.assertFalse(data["automatic_execution"])
        self.assertFalse(data["real_trading_authorized"])

        mock_run_scan.assert_called_once()

    @patch("Bot.analyze_asset")
    def test_scan_analyzes_all_enabled_assets_and_timeframes(self, mock_analyze):
        mock_analyze.return_value = {
            "action": "NO TRADE",
            "mode": "PAPER_ONLY",
        }

        result = run_scan()

        expected_assets = [
            asset for asset, settings in ASSETS.items()
            if settings.get("enabled", False)
        ]

        expected_count = len(expected_assets) * 2

        self.assertEqual(
            result["analyses_completed"],
            expected_count
        )

        self.assertEqual(
            mock_analyze.call_count,
            expected_count
        )

        self.assertEqual(
            result["no_trade_count"],
            expected_count
        )

        self.assertEqual(result["candidates_found"], 0)
        self.assertFalse(result["automatic_execution"])


if __name__ == "__main__":
    unittest.main()
