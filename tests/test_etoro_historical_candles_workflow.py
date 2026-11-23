import unittest
from pathlib import Path


class EtoroHistoricalCandleWorkflowTests(unittest.TestCase):
    def test_weekly_historical_capture_is_market_only_and_retained(self):
        workflow = Path(".github/workflows/etoro_historical_candles.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("push:", workflow)
        self.assertIn("branches:", workflow)
        self.assertIn("- main", workflow)
        self.assertIn("etoro_historical_candles.py", workflow)
        self.assertIn("etoro_read_only.py", workflow)
        self.assertIn("ETORO_API_KEY: ${{ secrets.ETORO_API_KEY }}", workflow)
        self.assertIn("python - <<'PY'", workflow)
        self.assertIn("hashFiles('artifacts/etoro_historical_candles.json')", workflow)
        self.assertIn('cron: "15 0 * * 0"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("--interval 1d --count 1000 --min-candles 220", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertNotIn("trading/execution", workflow)


if __name__ == "__main__":
    unittest.main()
