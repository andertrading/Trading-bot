import unittest
from pathlib import Path


class EtoroMarketEvidenceWorkflowTests(unittest.TestCase):
    def test_daily_capture_is_read_only_manual_and_retained(self):
        workflow = Path(".github/workflows/etoro_market_evidence.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('cron: "0 15 * * 1-5"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertIn("python etoro_market_evidence.py --sample-amount-usd 100", workflow)


if __name__ == "__main__":
    unittest.main()
