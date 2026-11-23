import unittest
from pathlib import Path


class EtoroHistoricalCandleWorkflowTests(unittest.TestCase):
    def test_weekly_historical_capture_is_market_only_and_retained(self):
        workflow = Path(".github/workflows/etoro_historical_candles.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('cron: "15 0 * * 0"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("--interval 1d --count 1000 --min-candles 220", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertNotIn("trading/execution", workflow)


if __name__ == "__main__":
    unittest.main()
