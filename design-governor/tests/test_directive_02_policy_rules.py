from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import load_governor
except ImportError:
    from test_support import load_governor


class Directive02PolicyRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dg = load_governor()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry_path = self.workspace / "design_governor_registry.json"
        self.snapshot_dir = self.workspace / "design_snapshots"
        self.run_dir = self.workspace / "design_runs"

        self.dg.ROOT = self.workspace
        self.dg.ROOT_RESOLVED = self.workspace.resolve()
        self.dg.REGISTRY_PATH = self.registry_path
        self.dg.SNAPSHOT_DIR = self.snapshot_dir
        self.dg.RUN_DIR = self.run_dir

        (self.workspace / "styles.css").write_text(
            ":root {\n"
            "  --bg: #000000;\n"
            "  --text: #ffffff;\n"
            "}\n"
            "#moment-input {\n"
            "  text-transform: uppercase;\n"
            "}\n",
            encoding="utf-8",
        )
        (self.workspace / "index.html").write_text(
            "<main><label class=\"input-wrap\" for=\"moment-input\">"
            "<input id=\"moment-input\" maxlength=\"280\"></label></main>",
            encoding="utf-8",
        )
        (self.workspace / "script.js").write_text(
            "const lockedStarBrightness = 147;\n"
            "function brandText(value) {\n"
            "  return String(value || \"\")\n"
            "    .toUpperCase()\n"
            "    .replace(/I/g, \"1\")\n"
            "    .replace(/O/g, \"0\");\n"
            "}\n"
            "function qualifiesForUniverse() {\n"
            "  return input.dataset.entryMode !== \"pasted\" && countWords(input.value) >= 2;\n"
            "}\n"
            "function computeStarOpacity(intervalMs, holdMs = null) {\n"
            "  return intervalMs + (holdMs || 0);\n"
            "}\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_registry(self, contracts: list[dict]) -> dict:
        registry = {
            "system_name": "test-governor",
            "saved_at": "",
            "workspace_root": str(self.workspace),
            "impact_map": {
                "file_to_contracts": {},
                "token_to_contracts": {},
                "selector_to_contracts": {},
            },
            "contracts": contracts,
        }
        registry = self.dg.refresh_impact_map(registry, persist=False)
        self.registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        return registry

    def test_selector_style_passes_with_semantic_css_value_match(self) -> None:
        contract = {
            "contract_id": "example.site.brand.v1",
            "design_name": "Brand",
            "contract_name": "Brand tokens",
            "status": "locked",
            "owner_files": ["styles.css"],
            "css_tokens": ["--bg", "--text"],
            "selectors": [],
            "copy_blocks": [],
            "visual_states": [],
            "interaction_states": [],
            "animation_rules": [],
            "visual_invariants": [],
            "state_evidence": [],
            "policy_checks": [
                {
                    "rule_id": "brand.bg",
                    "kind": "selector_style",
                    "selector": ":root",
                    "expects": {"--bg": "#000"},
                    "files": ["styles.css"],
                },
                {
                    "rule_id": "brand.text",
                    "kind": "selector_style",
                    "selector": ":root",
                    "expects": {"--text": "#fff"},
                    "files": ["styles.css"],
                },
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
        results = self.dg.evaluate_policy_checks(contract, self.dg.read_contract_file_contents(contract))
        self.assertEqual(results["brand.bg"]["status"], "pass")
        self.assertEqual(results["brand.text"]["status"], "pass")

    def test_selector_style_fails_when_css_drift_is_real(self) -> None:
        contract = {
            "contract_id": "example.site.brand-drift.v1",
            "design_name": "Brand Drift",
            "contract_name": "Brand drift rule",
            "status": "locked",
            "owner_files": ["styles.css"],
            "css_tokens": [],
            "selectors": [],
            "copy_blocks": [],
            "visual_states": [],
            "interaction_states": [],
            "animation_rules": [],
            "visual_invariants": [],
            "state_evidence": [],
            "policy_checks": [
                {
                    "rule_id": "brand.input.lowercase",
                    "kind": "selector_style",
                    "selector": "#moment-input",
                    "expects": {"text-transform": "lowercase"},
                    "files": ["styles.css"],
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
        results = self.dg.evaluate_policy_checks(contract, self.dg.read_contract_file_contents(contract))
        self.assertEqual(results["brand.input.lowercase"]["status"], "fail")
        self.assertIn("selector_style did not match expected declarations", results["brand.input.lowercase"]["reason"])

    def test_html_count_passes_when_expected_count_matches(self) -> None:
        contract = {
            "contract_id": "example.site.html-count-pass.v1",
            "design_name": "HTML Count Pass",
            "contract_name": "HTML count pass",
            "status": "locked",
            "owner_files": ["index.html"],
            "css_tokens": [],
            "selectors": [],
            "copy_blocks": [],
            "visual_states": [],
            "interaction_states": [],
            "animation_rules": [],
            "visual_invariants": [],
            "state_evidence": [],
            "policy_checks": [
                {
                    "rule_id": "input.count",
                    "kind": "html_count",
                    "selector": "#moment-input",
                    "expects": 1,
                    "files": ["index.html"],
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
        results = self.dg.evaluate_policy_checks(contract, self.dg.read_contract_file_contents(contract))
        self.assertEqual(results["input.count"]["status"], "pass")

    def test_html_count_fails_when_expected_count_drifts(self) -> None:
        contract = {
            "contract_id": "example.site.html-count-fail.v1",
            "design_name": "HTML Count Fail",
            "contract_name": "HTML count fail",
            "status": "locked",
            "owner_files": ["index.html"],
            "css_tokens": [],
            "selectors": [],
            "copy_blocks": [],
            "visual_states": [],
            "interaction_states": [],
            "animation_rules": [],
            "visual_invariants": [],
            "state_evidence": [],
            "policy_checks": [
                {
                    "rule_id": "input.count",
                    "kind": "html_count",
                    "selector": ".input-wrap",
                    "expects": 2,
                    "files": ["index.html"],
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
        results = self.dg.evaluate_policy_checks(contract, self.dg.read_contract_file_contents(contract))
        self.assertEqual(results["input.count"]["status"], "fail")
        self.assertIn("html_count expected 2", results["input.count"]["reason"])

    def test_unknown_rule_kind_fails_closed(self) -> None:
        contract = {
            "contract_id": "example.site.bad-rule.v1",
            "design_name": "Bad Rule",
            "contract_name": "Bad rule test",
            "status": "locked",
            "owner_files": ["styles.css"],
            "css_tokens": [],
            "selectors": [],
            "copy_blocks": [],
            "visual_states": [],
            "interaction_states": [],
            "animation_rules": [],
            "visual_invariants": [],
            "state_evidence": [],
            "policy_checks": [
                {
                    "rule_id": "bad.kind",
                    "kind": "made_up_kind",
                    "files": ["styles.css"],
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
        failures = self.dg.policy_failures_for_contract(contract)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].rule_id, "bad.kind")
        self.assertIn("unknown policy rule kind", failures[0].reason)

    def test_request_can_fail_policy_even_when_file_scope_is_allowed(self) -> None:
        contract = {
            "contract_id": "example.site.input.v1",
            "design_name": "Input",
            "contract_name": "Input law",
            "status": "locked",
            "owner_files": ["styles.css", "index.html"],
            "css_tokens": [],
            "selectors": ["#moment-input"],
            "copy_blocks": [],
            "visual_states": [],
            "interaction_states": [],
            "animation_rules": [],
            "visual_invariants": [],
            "state_evidence": [],
            "policy_checks": [
                {
                    "rule_id": "input.lowercase-required",
                    "kind": "selector_style",
                    "selector": "#moment-input",
                    "expects": {"text-transform": "lowercase"},
                    "files": ["styles.css"],
                }
            ],
            "allowed_edits": [],
            "blocked_edits": ["do not break input law"],
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
        registry = self.write_registry([contract])
        request = {
            "request_id": "test-request",
            "requested_contract_ids": ["example.site.input.v1"],
            "planned_file_touches": ["styles.css"],
            "planned_token_touches": [],
            "planned_selector_touches": ["#moment-input"],
        }
        blocked = self.dg.blocked_touches(registry, request)
        failures = self.dg.policy_failures_for_request(registry, request)
        self.assertEqual(blocked, [])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].rule_id, "input.lowercase-required")

    def test_snapshot_stores_policy_results(self) -> None:
        contract = {
            "contract_id": "example.site.snapshot.v1",
            "design_name": "Snapshot",
            "contract_name": "Snapshot proof",
            "status": "draft",
            "owner_files": ["styles.css", "script.js"],
            "css_tokens": [],
            "selectors": [],
            "copy_blocks": [],
            "visual_states": [],
            "interaction_states": [],
            "animation_rules": [],
            "visual_invariants": [],
            "state_evidence": [],
            "policy_checks": [
                {
                    "rule_id": "snapshot.brand-conversion",
                    "kind": "file_contains",
                    "text": ".replace(/I/g, \"1\")",
                    "files": ["script.js"],
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
        registry = self.write_registry([contract])
        working_contract = registry["contracts"][0]
        snapshot_path = self.dg.approve_contract_state(
            registry,
            working_contract,
            approved_by="test",
            reason="snapshot test",
            visual_proof_paths=[],
            lock_after=True,
        )
        saved_registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        approved = saved_registry["contracts"][0]["approved_proof"]
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        self.assertIn("policy_results", approved)
        self.assertEqual(approved["policy_results"]["snapshot.brand-conversion"]["status"], "pass")
        self.assertIn("policy_results", snapshot["proof"])


if __name__ == "__main__":
    unittest.main()
