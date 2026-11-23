import unittest
from pathlib import Path


class EtoroSpreadHistoryWorkflowTests(unittest.TestCase):
    def test_weekly_aggregator_is_github_artifact_read_only(self):
        workflow = Path(".github/workflows/etoro_spread_history.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('cron: "0 16 * * 1"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("--lookback-days 90", workflow)
        self.assertIn("--min-sessions 30", workflow)
        self.assertIn("etoro-forward-spread-history", workflow)
        self.assertNotIn("ETORO_API_KEY", workflow)
        self.assertNotIn("trading/execution", workflow)


if __name__ == "__main__":
    unittest.main()
