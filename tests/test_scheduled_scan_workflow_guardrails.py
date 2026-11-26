"""Regression tests for the scheduled scanner's external-data consent gate."""

from pathlib import Path
import unittest


class ScheduledScanWorkflowGuardrailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repository_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repository_root
            / ".github"
            / "workflows"
            / "scheduled_scan.yml"
        )
        cls.workflow = workflow_path.read_text(encoding="utf-8")

    def test_every_scan_requires_explicit_market_data_credit_authorization(self):
        gate = next(
            line.strip()
            for line in self.workflow.splitlines()
            if line.strip().startswith("if:")
        )
        self.assertIn(
            "vars.MARKET_DATA_CREDITS_AUTHORIZED == 'true'",
            gate,
        )

    def test_scheduled_scans_also_require_recurring_automation_opt_in(self):
        gate = next(
            line.strip()
            for line in self.workflow.splitlines()
            if line.strip().startswith("if:")
        )
        self.assertIn("github.event_name == 'workflow_dispatch'", gate)
        self.assertIn(
            "vars.SIGNAL_AUTOMATION_ENABLED == 'true'",
            gate,
        )
        self.assertEqual(
            gate,
            "if: ${{ vars.MARKET_DATA_CREDITS_AUTHORIZED == 'true' && "
            "(github.event_name == 'workflow_dispatch' || "
            "vars.SIGNAL_AUTOMATION_ENABLED == 'true') }}",
        )

    def test_manual_dispatch_does_not_bypass_credit_authorization(self):
        gate = next(
            line.strip()
            for line in self.workflow.splitlines()
            if line.strip().startswith("if:")
        )
        credit_gate_position = gate.index(
            "vars.MARKET_DATA_CREDITS_AUTHORIZED == 'true'"
        )
        dispatch_position = gate.index(
            "github.event_name == 'workflow_dispatch'"
        )
        self.assertLess(credit_gate_position, dispatch_position)

    def test_workflow_keeps_read_only_repository_permissions(self):
        self.assertIn("permissions:\n  contents: read", self.workflow)


if __name__ == "__main__":
    unittest.main()
