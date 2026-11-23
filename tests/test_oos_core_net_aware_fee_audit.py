import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from research.oos_core_net_aware_fee_audit import audit_core_net_aware_oos


class CoreNetAwareFeeAuditTests(unittest.TestCase):
    def test_rejects_wrong_artifact_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "wrong.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("strategy_research_status.json", json.dumps({"status": "COMPLETE"}))
            with self.assertRaises(ValueError):
                audit_core_net_aware_oos(path)


if __name__ == "__main__":
    unittest.main()
