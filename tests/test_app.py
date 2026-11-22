
import unittest
from unittest.mock import patch

from Bot import app, run_scan
from config import ASSETS, SWING_INTERVAL


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

    @patch("Bot.start_scan")
    def test_scan_endpoint(self, mock_start_scan):
        mock_start_scan.return_value = True

        response = self.client.get("/scan")

        self.assertEqual(response.status_code, 202)

        data = response.get_json()

        self.assertTrue(data["scan_started"])
        self.assertFalse(data["automatic_execution"])
        self.assertFalse(data["real_trading_authorized"])

        mock_start_scan.assert_called_once()

    @patch("Bot.start_scan")
    def test_scan_already_running(self, mock_start_scan):
        mock_start_scan.return_value = False

        response = self.client.get("/scan")

        self.assertEqual(response.status_code, 200)

        data = response.get_json()

        self.assertFalse(data["scan_started"])
        self.assertFalse(data["automatic_execution"])
        self.assertFalse(data["real_trading_authorized"])

        mock_start_scan.assert_called_once()

    def test_scan_status_endpoint(self):
        response = self.client.get("/scan/status")

        self.assertEqual(response.status_code, 200)

        data = response.get_json()

        self.assertIn("status", data)
        self.assertIn("analyses_completed", data)
        self.assertIn("total_analyses", data)
        self.assertFalse(data["automatic_execution"])
        self.assertFalse(data["real_trading_authorized"])

    @patch("Bot.analyze_asset")
    def test_default_scan_runs_daily_swing_only(
        self,
        mock_analyze,
    ):
        mock_analyze.return_value = {
            "action": "NO TRADE",
            "mode": "PAPER_ONLY",
        }

        with patch("Bot.is_intraday_market_open") as market_open:
            result = run_scan()

        expected_assets = [
            asset for asset, settings in ASSETS.items()
            if settings.get("enabled", False)
        ]
        expected_count = len(expected_assets)

        self.assertEqual(result["timeframes"], [SWING_INTERVAL])
        self.assertEqual(result["analyses_completed"], expected_count)
        self.assertEqual(mock_analyze.call_count, expected_count)
        self.assertEqual(result["no_trade_count"], expected_count)
        self.assertEqual(result["candidates_found"], 0)
        self.assertFalse(result["automatic_execution"])
        market_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
