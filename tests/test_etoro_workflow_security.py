"""Regression tests for least-privilege eToro GitHub Actions workflows."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    ".github/workflows/etoro_historical_market_probe.yml": "Request historical market candles only",
    ".github/workflows/etoro_market_evidence.yml": "Capture market-only evidence",
    ".github/workflows/etoro_historical_candles.yml": "Capture historical market candles only",
}


class EtoroWorkflowSecurityTests(unittest.TestCase):
    def test_credentials_are_scoped_only_to_the_api_request_step(self):
        for relative_path, expected_step in WORKFLOWS.items():
            with self.subTest(workflow=relative_path):
                lines = (ROOT / relative_path).read_text(encoding="utf-8").splitlines()
                secret_indexes = [
                    index for index, line in enumerate(lines)
                    if "secrets.ETORO_API_KEY" in line or "secrets.ETORO_USER_KEY" in line
                ]
                self.assertEqual(len(secret_indexes), 2)

                for index in secret_indexes:
                    self.assertTrue(lines[index].startswith("          ETORO_"))
                    previous_steps = [
                        line.strip()
                        for line in lines[:index]
                        if line.startswith("      - name:")
                    ]
                    self.assertTrue(previous_steps)
                    self.assertEqual(previous_steps[-1], f"- name: {expected_step}")
                    step_start = max(
                        i for i, line in enumerate(lines[:index])
                        if line.startswith("      - name:")
                    )
                    self.assertIn("        env:", lines[step_start:index])

                steps_start = next(i for i, line in enumerate(lines) if line == "    steps:")
                for index in secret_indexes:
                    self.assertGreater(index, steps_start)

    def test_historical_probe_input_is_validated_and_never_interpolated_into_shell(self):
        path = ROOT / ".github/workflows/etoro_historical_market_probe.yml"
        content = path.read_text(encoding="utf-8")
        self.assertIn("CANDLES_COUNT: ${{ inputs.candles_count }}", content)
        self.assertIn('re.fullmatch(r"(?:[1-9][0-9]{0,2}|1000)", count)', content)
        self.assertIn('subprocess.run(', content)
        self.assertIn('"--candles-count", count', content)
        self.assertNotIn('python etoro_historical_market_probe.py --candles-count "${{ inputs.candles_count }}"', content)
        self.assertNotIn("  schedule:", content)


if __name__ == "__main__":
    unittest.main()
