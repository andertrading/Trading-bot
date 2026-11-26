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


def _dynamic_import_modules(tree):
    """Find literal module names passed to common dynamic-import functions."""
    modules = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue

        function = node.func
        is_dynamic_import = (
            isinstance(function, ast.Name)
            and function.id in {"__import__", "import_module"}
        ) or (
            isinstance(function, ast.Attribute)
            and function.attr in {"__import__", "import_module"}
        )
        first_argument = node.args[0]
        if is_dynamic_import and isinstance(first_argument, ast.Constant):
            if isinstance(first_argument.value, str):
                modules.append(first_argument.value)
    return modules


def _import_closure(entrypoint):
    """Return repository-local files transitively imported, including literal dynamic imports."""
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

        # Follow literal dynamic imports as well as ordinary import statements.
        for module_name in _dynamic_import_modules(tree):
            root_name = module_name.split(".")[0]
            if module_name in FORBIDDEN_MODULES or root_name in FORBIDDEN_MODULES:
                violations.append((str(path.relative_to(ROOT)), module_name))
            local_path = _local_module_path(module_name)
            if local_path is not None:
                pending.append(local_path)

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

    def test_literal_dynamic_imports_are_detected(self):
        tree = ast.parse(
            "import importlib\n"
            "importlib.import_module('etoro_read_only')\n"
            "__import__('etoro_position_risk')\n"
            "import_module('local_helper')\n"
        )
        self.assertEqual(
            _dynamic_import_modules(tree),
            ["etoro_read_only", "etoro_position_risk", "local_helper"],
        )


if __name__ == "__main__":
    unittest.main()
