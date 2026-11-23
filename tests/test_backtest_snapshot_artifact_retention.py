import unittest
from pathlib import Path


class BacktestSnapshotArtifactRetentionTests(unittest.TestCase):
    def test_downloadable_backtest_artifact_includes_raw_snapshots_and_manifest(self):
        workflow = Path(".github/workflows/backtest_v3.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('manifest = directory / "manifest.json"', workflow)
        self.assertIn('if not manifest.is_file():', workflow)

        marker = "      - name: Guardar resultados descargables"
        self.assertIn(marker, workflow)
        upload_step = workflow.split(marker, 1)[1]

        self.assertIn("uses: actions/upload-artifact@v4", upload_step)
        self.assertIn("historicos_backtest_v3/", upload_step)
        self.assertIn("retention-days: 90", upload_step)


if __name__ == "__main__":
    unittest.main()
