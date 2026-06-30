from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from .test_support import REPO_ROOT, load_governor
except ImportError:
    from test_support import REPO_ROOT, load_governor


INSTALLABLE_README_PATH = REPO_ROOT / "README.md"
INSTALLABLE_MANIFEST_PATH = REPO_ROOT / "MANIFEST.in"
SOURCE_GOVERNOR_PATH = REPO_ROOT / "design_governor" / "__init__.py"


class ReleaseAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dg = load_governor()

    def test_repo_hygiene_audit_flags_blocked_generated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pkg" / "__pycache__").mkdir(parents=True)
            (root / "pkg" / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"x")
            (root / "pkg.egg-info").mkdir()
            (root / "dist").mkdir()
            problems = self.dg.audit_release_tree(root)
        joined = "\n".join(problems)
        self.assertIn("__pycache__", joined)
        self.assertIn(".egg-info", joined)
        self.assertIn("dist", joined)

    def test_installable_runtime_cache_path_is_ignored_in_tree_audit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "design_governor" / "__pycache__").mkdir(parents=True)
            (root / "design_governor" / "__pycache__" / "__init__.cpython-311.pyc").write_bytes(b"x")
            problems = self.dg.audit_release_tree(root)
        self.assertEqual(problems, [])

    def test_installable_test_cache_path_is_ignored_in_tree_audit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tests" / "__pycache__").mkdir(parents=True)
            (root / "tests" / "__pycache__" / "test_release_audit.cpython-311.pyc").write_bytes(b"x")
            problems = self.dg.audit_release_tree(root)
        self.assertEqual(problems, [])

    def test_built_sdist_and_wheel_are_clean(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            artifacts = self.dg.build_release_artifacts(REPO_ROOT, Path(td))
            self.assertTrue(any(path.suffix == ".whl" for path in artifacts))
            self.assertTrue(any(path.name.endswith(".tar.gz") for path in artifacts))
            for artifact in artifacts:
                self.assertEqual(self.dg.audit_release_artifact(artifact), [], artifact.name)

    def test_release_readme_names_release_audit(self) -> None:
        readme = INSTALLABLE_README_PATH.read_text(encoding="utf-8")
        self.assertIn("release-audit", readme)
        self.assertIn("repo tree", readme)

    def test_release_docs_name_playwright_browser_install(self) -> None:
        readme = INSTALLABLE_README_PATH.read_text(encoding="utf-8")
        testing = (REPO_ROOT / "TESTING.md").read_text(encoding="utf-8")
        self.assertIn("python -m playwright install --with-deps chromium", readme)
        self.assertIn("python -m playwright install --with-deps chromium", testing)

    def test_public_installable_copy_includes_tests_folder(self) -> None:
        manifest = INSTALLABLE_MANIFEST_PATH.read_text(encoding="utf-8")
        self.assertIn("recursive-include tests *.py", manifest)
        self.assertTrue((REPO_ROOT / "tests" / "test_release_audit.py").exists())

    def test_source_runner_disables_bytecode_writes(self) -> None:
        source = SOURCE_GOVERNOR_PATH.read_text(encoding="utf-8")
        self.assertIn("sys.dont_write_bytecode = True", source)

    def test_release_audit_prints_friendly_build_message(self) -> None:
        args = SimpleNamespace(repo_root=".", project_root=".", json_output=False)
        output = io.StringIO()
        with mock.patch.object(
            self.dg,
            "release_audit_payload",
            side_effect=RuntimeError(self.dg.missing_build_release_message()),
        ):
            with mock.patch("sys.stdout", output):
                exit_code = self.dg.cmd_release_audit(args)
        self.assertEqual(exit_code, 2)
        text = output.getvalue()
        self.assertIn("Release audit needs the build package.", text)
        self.assertIn("python -m pip install build", text)
        self.assertIn('design-governor[release]', text)


if __name__ == "__main__":
    unittest.main()
