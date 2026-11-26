"""Keep live broker-account access out of signal and paper-scanning entrypoints."""

import ast
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ENTRYPOINTS = ("Bot.py", "scheduled_scan.py")
FORBIDDEN_MODULES = {"etoro_read_only"}
FORBIDDEN_ACCOUNT_SYMBOLS = {
    "EtoroReadOnlyClient",
    "calculate_available_cash",
    "get_account_snapshot",
    "normalize_account_snapshot",
}


class PaperBrokerAccountIsolationTests(unittest.TestCase):
    def test_signal_and_scheduled_scan_entrypoints_do_not_import_broker_account_client(self):
        for relative_path in RUNTIME_ENTRYPOINTS:
            with self.subTest(path=relative_path):
                source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
                tree = ast.parse(source, filename=relative_path)

                imported_modules = set()
                imported_symbols = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported_modules.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported_modules.add(node.module)
                        imported_symbols.update(alias.name for alias in node.names)

                self.assertTrue(
                    FORBIDDEN_MODULES.isdisjoint(imported_modules),
                    f"{relative_path} must not import the live eToro account client",
                )
                self.assertTrue(
                    FORBIDDEN_ACCOUNT_SYMBOLS.isdisjoint(imported_symbols),
                    f"{relative_path} must not import live account-data functions",
                )

    def test_signal_and_scheduled_scan_entrypoints_do_not_reference_account_access_symbols(self):
        for relative_path in RUNTIME_ENTRYPOINTS:
            with self.subTest(path=relative_path):
                source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
                tree = ast.parse(source, filename=relative_path)
                referenced_names = {
                    node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
                }
                referenced_attributes = {
                    node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                }
                references = referenced_names | referenced_attributes

                self.assertTrue(
                    FORBIDDEN_ACCOUNT_SYMBOLS.isdisjoint(references),
                    f"{relative_path} must remain independent of live broker-account data",
                )


if __name__ == "__main__":
    unittest.main()
