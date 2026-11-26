"""Protect the complete local import graph of paper/signal entrypoints."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = ("Bot.py", "scheduled_scan.py")
FORBIDDEN_MODULES = {"etoro_read_only", "etoro_position_risk"}
FORBIDDEN_SYMBOLS = {
    "EtoroReadOnlyClient",
    "EtoroAccountSnapshot",
    "calculate_available_cash",
    "get_account_snapshot",
    "normalize_account_snapshot",
}


def _local_module_path(module_name):
    """Resolve a Python module only when its source exists in this repository."""
    parts = module_name.split(".")
    module_file = ROOT.joinpath(*parts).with_suffix(".py")
    package_file = ROOT.joinpath(*parts, "__init__.py")
    if module_file.is_file():
        return module_file
    if package_file.is_file():
        return package_file
    return None


def _import_closure(entrypoint):
    """Return all repository-local Python files transitively imported."""
    pending = [ROOT / entrypoint]
    visited = set()
    violations = []

    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            module_names = []
            imported_symbols = []
            if isinstance(node, ast.Import):
                module_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_names.append(node.module)
                imported_symbols.extend(alias.name for alias in node.names)

            for module_name in module_names:
                root_name = module_name.split(".")[0]
                if module_name in FORBIDDEN_MODULES or root_name in FORBIDDEN_MODULES:
                    violations.append((str(path.relative_to(ROOT)), module_name))
                local_path = _local_module_path(module_name)
                if local_path is not None:
                    pending.append(local_path)

            for symbol in imported_symbols:
                if symbol in FORBIDDEN_SYMBOLS:
                    violations.append((str(path.relative_to(ROOT)), symbol))

        # Also catch dynamically referenced imported account-access symbols
        # when a local import is aliased or accessed as a module attribute.
        referenced = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for symbol in FORBIDDEN_SYMBOLS:
            if symbol in referenced:
                violations.append((str(path.relative_to(ROOT)), symbol))

    return visited, violations


class TransitivePaperAccountIsolationTests(unittest.TestCase):
    def test_paper_and_signal_entrypoints_have_no_account_access_in_import_closure(self):
        for entrypoint in ENTRYPOINTS:
            with self.subTest(entrypoint=entrypoint):
                visited, violations = _import_closure(entrypoint)
                self.assertTrue(visited)
                self.assertEqual(
                    violations,
                    [],
                    f"{entrypoint} reaches forbidden broker-account code: {violations}",
                )


if __name__ == "__main__":
    unittest.main()
