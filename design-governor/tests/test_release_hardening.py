from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .test_support import REPO_ROOT, load_governor
except ImportError:
    from test_support import REPO_ROOT, load_governor


WORKFLOW_TEMPLATE_PATH = REPO_ROOT / "design_governor" / "templates" / "github_workflow.design-governor.yml"
PROJECT_TEMPLATE_PATH = REPO_ROOT / "design_governor" / "templates" / "design_governor_project.template.json"


class ReleaseHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dg = load_governor()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry_path = self.workspace / "design_governor_registry.json"
        self.snapshot_dir = self.workspace / "design_snapshots"
        self.run_dir = self.workspace / "design_runs"
        self.project_config_path = self.workspace / "design_governor_project.json"

        self.dg.ROOT = self.workspace
        self.dg.ROOT_RESOLVED = self.workspace.resolve()
        self.dg.REGISTRY_PATH = self.registry_path
        self.dg.PROJECT_CONFIG_PATH = self.project_config_path
        self.dg.SNAPSHOT_DIR = self.snapshot_dir
        self.dg.RUN_DIR = self.run_dir

        (self.workspace / "styles.css").write_text(
            ".hero {\n"
            "  color: red;\n"
            "}\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_registry(self, contract: dict) -> dict:
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
            "contracts": [contract],
        }
        registry = self.dg.refresh_impact_map(registry, persist=False)
        self.registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        return registry

    def contract_template(self) -> dict:
        return {
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

    def test_named_contract_without_touches_fails_closed(self) -> None:
        registry = self.write_registry(self.contract_template())
        request = {
            "request_id": "empty-touch-plan",
            "requested_contract_ids": ["example.site.hero.v1"],
            "planned_file_touches": [],
            "planned_token_touches": [],
            "planned_selector_touches": [],
            "planned_surface_touches": [],
        }
        failures = self.dg.policy_failures_for_request(registry, request)
        reasons = [item.reason for item in failures]
        self.assertTrue(any("declares no planned touches" in reason for reason in reasons))

    def test_named_contract_policy_runs_even_without_impact_match(self) -> None:
        registry = self.write_registry(self.contract_template())
        request = {
            "request_id": "named-policy-scope",
            "requested_contract_ids": ["example.site.hero.v1"],
            "planned_file_touches": ["other.css"],
            "planned_token_touches": [],
            "planned_selector_touches": [],
            "planned_surface_touches": [],
        }
        failures = self.dg.policy_failures_for_request(registry, request)
        self.assertTrue(any(item.rule_id == "hero.color" for item in failures))

    def test_unknown_requested_contract_blocks(self) -> None:
        registry = self.write_registry(self.contract_template())
        request = {
            "request_id": "unknown-contract",
            "requested_contract_ids": ["missing.contract.v1"],
            "planned_file_touches": ["styles.css"],
            "planned_token_touches": [],
            "planned_selector_touches": [],
            "planned_surface_touches": [],
        }
        failures = self.dg.policy_failures_for_request(registry, request)
        reasons = [item.reason for item in failures]
        self.assertTrue(any("requested contract id is not in the registry" in reason for reason in reasons))

    def test_workflow_template_installs_playwright_browser(self) -> None:
        workflow = WORKFLOW_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("python -m playwright install --with-deps chromium", workflow)
        self.assertIn("Start app for visual proof", workflow)
        self.assertIn("DESIGN_GOVERNOR_VISUAL_BASE_URL", workflow)

    def test_project_template_has_visual_proof_ci_group(self) -> None:
        project_template = json.loads(PROJECT_TEMPLATE_PATH.read_text(encoding="utf-8"))
        visual = dict(project_template.get("visual_proof_ci") or {})
        self.assertEqual(visual.get("enabled"), False)
        self.assertEqual(visual.get("start_command"), "")
        self.assertEqual(visual.get("base_url"), "")
        self.assertEqual(visual.get("install_browser_with_deps"), True)

    def test_visual_proof_can_use_environment_base_url_with_tiny_server(self) -> None:
        page_path = self.workspace / "visual.html"
        page_path.write_text(
            "<!DOCTYPE html>\n"
            "<html><body style=\"margin:0;background:#000;\">"
            "<div id=\"proof\" style=\"width:140px;height:140px;background:#33aaff;"
            "margin:20px;border-radius:18px;\"></div>"
            "</body></html>\n",
            encoding="utf-8",
        )

        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                return

        original_cwd = Path.cwd()
        server = None
        thread = None
        original_base_url = os.environ.get("DESIGN_GOVERNOR_VISUAL_BASE_URL")
        try:
            os.chdir(self.workspace)
            server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            os.environ["DESIGN_GOVERNOR_VISUAL_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"

            contract = {
                "contract_id": "example.site.visual-env.v1",
                "design_name": "Visual Env",
                "contract_name": "Visual env proof",
                "status": "draft",
                "owner_files": [],
                "css_tokens": [],
                "selectors": [],
                "copy_blocks": [],
                "surface_targets": [],
                "visual_states": ["default"],
                "interaction_states": [],
                "animation_rules": [],
                "visual_invariants": [],
                "state_evidence": [],
                "policy_checks": [],
                "visual_proof_specs": [
                    {
                        "proof_id": "env.desktop",
                        "route": "/visual.html",
                        "state_name": "default",
                        "viewport": {"width": 220, "height": 220},
                        "ready_selector": "#proof",
                        "target_selector": "#proof",
                        "wait_ms": 50,
                        "mask_selectors": [],
                        "required": True,
                        "max_diff_ratio": 0.0,
                    }
                ],
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
            registry = self.write_registry(contract)
            snapshot_path = self.dg.approve_contract_state(
                registry,
                registry["contracts"][0],
                approved_by="test",
                reason="env base url proof",
                visual_proof_paths=[],
                lock_after=True,
            )
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            proof = snapshot["visual_proof_results"]["env.desktop"]
            self.assertTrue(str(proof.get("url") or "").startswith("http://127.0.0.1:"))
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=5)
            if original_base_url is None:
                os.environ.pop("DESIGN_GOVERNOR_VISUAL_BASE_URL", None)
            else:
                os.environ["DESIGN_GOVERNOR_VISUAL_BASE_URL"] = original_base_url
            os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
