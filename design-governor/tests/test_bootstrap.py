from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path

try:
    from .test_support import load_governor
except ImportError:
    from test_support import load_governor


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dg = load_governor()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)

        self.dg.ROOT = self.workspace
        self.dg.ROOT_RESOLVED = self.workspace.resolve()
        self.dg.REGISTRY_PATH = self.workspace / "design_governor_registry.json"
        self.dg.PROJECT_CONFIG_PATH = self.workspace / "design_governor_project.json"
        self.dg.CHANGE_REQUEST_TEMPLATE_PATH = self.workspace / "design_change_request.template.json"
        self.dg.AGENTS_TEMPLATE_PATH = self.workspace / "AGENTS.md"
        self.dg.HOW_IT_WORKS_PATH = self.workspace / "HOW_IT_WORKS.md"
        self.dg.SNAPSHOT_DIR = self.workspace / "design_snapshots"
        self.dg.RUN_DIR = self.workspace / "design_runs"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_init_writes_all_boot_files(self) -> None:
        exit_code = self.dg.cmd_init(types.SimpleNamespace(force=False))
        self.assertEqual(exit_code, 0)

        expected_files = [
            "design_governor_registry.json",
            "design_change_request.template.json",
            "AGENTS.md",
            "HOW_IT_WORKS.md",
            "design_governor_project.json",
            ".github/workflows/design-governor.yml",
            ".pre-commit-config.yaml",
            "DESIGN_GOVERNOR_AGENT_WORKFLOWS.md",
        ]
        for relative_path in expected_files:
            self.assertTrue((self.workspace / relative_path).exists(), relative_path)

        registry = json.loads((self.workspace / "design_governor_registry.json").read_text(encoding="utf-8"))
        self.assertIn("impact_map", registry)


if __name__ == "__main__":
    unittest.main()
