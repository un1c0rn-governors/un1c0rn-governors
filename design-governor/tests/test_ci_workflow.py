from __future__ import annotations

import contextlib
import io
import json
import tempfile
import types
import unittest
from pathlib import Path

try:
    from .test_support import load_governor
except ImportError:
    from test_support import load_governor


class CiWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dg = load_governor()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry_path = self.workspace / "design_governor_registry.json"
        self.project_config_path = self.workspace / "design_governor_project.json"

        self.dg.ROOT = self.workspace
        self.dg.ROOT_RESOLVED = self.workspace.resolve()
        self.dg.REGISTRY_PATH = self.registry_path
        self.dg.PROJECT_CONFIG_PATH = self.project_config_path
        self.dg.SNAPSHOT_DIR = self.workspace / "design_snapshots"
        self.dg.RUN_DIR = self.workspace / "design_runs"

        (self.workspace / "styles.css").write_text(
            ".hero {\n"
            "  color: red;\n"
            "}\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_registry(self) -> dict:
        registry = {
            "system_name": "test-governor",
            "saved_at": "",
            "workspace_root": str(self.workspace),
            "impact_map": {
                "file_to_contracts": {},
                "token_to_contracts": {},
                "selector_to_contracts": {},
                "surface_to_contracts": {},
            },
            "contracts": [
                {
                    "contract_id": "example.site.hero.v1",
                    "design_name": "Hero",
                    "contract_name": "Hero",
                    "status": "locked",
                    "owner_files": ["styles.css"],
                    "css_tokens": [],
                    "selectors": [".hero"],
                    "copy_blocks": [],
                    "surface_targets": [],
                    "visual_states": [],
                    "interaction_states": [],
                    "animation_rules": [],
                    "visual_invariants": [],
                    "state_evidence": [],
                    "policy_checks": [],
                    "visual_proof_specs": [],
                    "allowed_edits": [],
                    "blocked_edits": [],
                    "allowed_change_kinds": [],
                    "blocked_change_kinds": [],
                    "approved_notes": [],
                    "approved_proof": {},
                    "last_approved_hash": "",
                    "last_approved_snapshot": "",
                    "proof_status": "none",
                    "stale_detected_at": "",
                    "stale_reasons": [],
                    "updated_at": "",
                }
            ],
        }
        registry = self.dg.refresh_impact_map(registry, persist=False)
        self.registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        return registry

    def write_project_config(self, *, request_globs: list[str], fail_if_no_requests: bool = True) -> None:
        self.project_config_path.write_text(
            json.dumps(
                {
                    "request_globs": request_globs,
                    "required_checks": ["check"],
                    "fail_if_no_requests": fail_if_no_requests,
                    "enabled_adapters": ["css", "html", "jsx", "vue", "svelte"],
                    "strict_surface_mode": False,
                    "ci_commands": ["design-governor ci-check"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_ci_check_blocks_unknown_contract_request(self) -> None:
        self.write_registry()
        self.write_project_config(request_globs=["design_change_request.active.json"])
        (self.workspace / "design_change_request.active.json").write_text(
            json.dumps(
                {
                    "request_id": "unknown-contract",
                    "requested_contract_ids": ["missing.contract.v1"],
                    "planned_file_touches": ["styles.css"],
                    "planned_token_touches": [],
                    "planned_selector_touches": [],
                    "planned_surface_touches": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = self.dg.cmd_ci_check(types.SimpleNamespace(expert=True, json_output=False))
        self.assertEqual(exit_code, 2)
        self.assertIn("BLOCKED", output.getvalue())
        self.assertIn("requested contract id is not in the registry", output.getvalue())

    def test_ci_check_fails_when_no_request_files_match(self) -> None:
        self.write_registry()
        self.write_project_config(request_globs=["missing.request.json"], fail_if_no_requests=True)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = self.dg.cmd_ci_check(types.SimpleNamespace())
        self.assertEqual(exit_code, 2)
        self.assertIn("No request files matched project config", output.getvalue())


if __name__ == "__main__":
    unittest.main()
