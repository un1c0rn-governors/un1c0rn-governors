from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import REPO_ROOT, load_governor
except ImportError:
    from test_support import REPO_ROOT, load_governor


PROJECT_TEMPLATE_PATH = REPO_ROOT / "design_governor" / "templates" / "design_governor_project.template.json"
AGENTS_TEMPLATE_PATH = REPO_ROOT / "design_governor" / "templates" / "AGENTS_TEMPLATE.md"
WORKFLOW_DOCS_PATH = REPO_ROOT / "design_governor" / "templates" / "DESIGN_GOVERNOR_AGENT_WORKFLOWS.md"


class ExplanationModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dg = load_governor()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry_path = self.workspace / "design_governor_registry.json"
        self.project_config_path = self.workspace / "design_governor_project.json"
        self.request_path = self.workspace / "design_change_request.active.json"

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
                    "contract_name": "Hero surface",
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
                    "policy_checks": [
                        {
                            "rule_id": "hero.color",
                            "kind": "selector_style",
                            "selector": ".hero",
                            "expects": {"color": "blue"},
                            "files": ["styles.css"],
                        }
                    ],
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

        self.request_path.write_text(
            json.dumps(
                {
                    "request_id": "hero-check",
                    "requested_contract_ids": ["example.site.hero.v1"],
                    "planned_file_touches": ["styles.css"],
                    "planned_token_touches": [],
                    "planned_selector_touches": [".hero"],
                    "planned_surface_touches": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_project_config(self, *, show_technical_details: bool = False) -> None:
        self.project_config_path.write_text(
            json.dumps(
                {
                    "explanation_mode": "plain",
                    "available_modes": ["plain", "expert", "ci"],
                    "default_user_level": "beginner",
                    "show_technical_details": show_technical_details,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_project_template_defaults_to_plain_mode(self) -> None:
        project_template = json.loads(PROJECT_TEMPLATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(project_template.get("explanation_mode"), "plain")
        self.assertEqual(project_template.get("default_user_level"), "beginner")
        self.assertEqual(project_template.get("show_technical_details"), False)

    def test_plain_mode_is_default_and_puts_simple_explanation_first(self) -> None:
        self.write_project_config(show_technical_details=False)
        args = self.dg.build_parser().parse_args(["check", "--request", str(self.request_path)])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = args.func(args)
        text = output.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertTrue(text.startswith("Blocked"))
        self.assertIn("What happened:", text)
        self.assertIn("Technical details:", text)
        self.assertLess(text.index("What happened:"), text.index("Technical details:"))

    def test_expert_flag_overrides_plain_default(self) -> None:
        self.write_project_config(show_technical_details=False)
        args = self.dg.build_parser().parse_args(["check", "--request", str(self.request_path), "--expert"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = args.func(args)
        text = output.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertIn("Rule: hero.color | selector_style", text)
        self.assertIn(".hero", text)

    def test_json_flag_returns_machine_output(self) -> None:
        self.write_project_config(show_technical_details=False)
        args = self.dg.build_parser().parse_args(["check", "--request", str(self.request_path), "--json"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = args.func(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["mode"], "ci")
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["policy_failures"][0]["rule_id"], "hero.color")

    def test_templates_state_plain_english_is_default(self) -> None:
        agents_template = AGENTS_TEMPLATE_PATH.read_text(encoding="utf-8")
        workflow_docs = WORKFLOW_DOCS_PATH.read_text(encoding="utf-8")
        self.assertIn("Default explanation mode is plain English.", agents_template)
        self.assertIn("Plain English is the default.", workflow_docs)


if __name__ == "__main__":
    unittest.main()
