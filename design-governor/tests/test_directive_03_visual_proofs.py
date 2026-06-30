from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import load_governor
except ImportError:
    from test_support import load_governor


class Directive03VisualProofTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_visual_page(self, color: str) -> Path:
        page_path = self.workspace / "visual.html"
        page_path.write_text(
            "<!DOCTYPE html>\n"
            "<html><body style=\"margin:0;background:#000;\">"
            f"<div id=\"proof\" style=\"width:140px;height:140px;background:{color};"
            "margin:20px;border-radius:18px;\"></div>"
            "</body></html>\n",
            encoding="utf-8",
        )
        return page_path

    def write_registry(self, contract: dict) -> tuple[dict, dict]:
        registry = {
            "system_name": "test-governor",
            "saved_at": "",
            "workspace_root": str(self.workspace),
            "impact_map": {
                "file_to_contracts": {},
                "token_to_contracts": {},
                "selector_to_contracts": {},
            },
            "contracts": [contract],
        }
        registry = self.dg.refresh_impact_map(registry, persist=False)
        self.registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        return registry, registry["contracts"][0]

    def contract_template(self, route: str) -> dict:
        return {
            "contract_id": "example.site.visual.v1",
            "design_name": "Visual",
            "contract_name": "Visual proof test",
            "status": "locked",
            "owner_files": [],
            "css_tokens": [],
            "selectors": [],
            "copy_blocks": [],
            "visual_states": ["default"],
            "interaction_states": [],
            "animation_rules": [],
            "visual_invariants": [],
            "state_evidence": [],
            "policy_checks": [],
            "visual_proof_specs": [
                {
                    "proof_id": "box.desktop",
                    "route": route,
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

    def test_snapshot_creates_visual_artifacts_and_hashes(self) -> None:
        page_path = self.write_visual_page("#ff0044")
        registry, contract = self.write_registry(self.contract_template(page_path.as_uri()))
        snapshot_path = self.dg.approve_contract_state(
            registry,
            contract,
            approved_by="test",
            reason="visual approval",
            visual_proof_paths=[],
            lock_after=True,
        )
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        proof_results = snapshot["visual_proof_results"]
        self.assertIn("box.desktop", proof_results)
        artifact_path = self.workspace / proof_results["box.desktop"]["artifact_path"]
        self.assertTrue(artifact_path.exists())
        self.assertTrue(proof_results["box.desktop"]["artifact_hash"])

    def test_missing_visual_target_blocks_snapshot(self) -> None:
        page_path = self.write_visual_page("#2288ff")
        contract = self.contract_template(page_path.as_uri())
        contract["visual_proof_specs"][0]["target_selector"] = "#missing"
        contract["visual_proof_specs"][0]["ready_selector"] = "#missing"
        registry, working_contract = self.write_registry(contract)
        with self.assertRaises(ValueError):
            self.dg.approve_contract_state(
                registry,
                working_contract,
                approved_by="test",
                reason="should fail",
                visual_proof_paths=[],
                lock_after=True,
            )

    def test_same_visual_state_keeps_lock_clean(self) -> None:
        page_path = self.write_visual_page("#33aa66")
        registry, contract = self.write_registry(self.contract_template(page_path.as_uri()))
        self.dg.approve_contract_state(
            registry,
            contract,
            approved_by="test",
            reason="baseline",
            visual_proof_paths=[],
            lock_after=True,
        )
        stale = self.dg.compare_live_to_approved(contract)
        self.assertIsNone(stale)

    def test_visual_drift_reports_exact_proof_id(self) -> None:
        page_path = self.write_visual_page("#ff8800")
        registry, contract = self.write_registry(self.contract_template(page_path.as_uri()))
        self.dg.approve_contract_state(
            registry,
            contract,
            approved_by="test",
            reason="baseline",
            visual_proof_paths=[],
            lock_after=True,
        )
        self.write_visual_page("#0055ff")
        stale = self.dg.compare_live_to_approved(contract)
        self.assertIsNotNone(stale)
        joined = " | ".join(stale.reasons)
        self.assertIn("box.desktop", joined)

    def test_refresh_proof_status_marks_drifted_lock_as_stale(self) -> None:
        page_path = self.write_visual_page("#ffaa00")
        registry, contract = self.write_registry(self.contract_template(page_path.as_uri()))
        self.dg.approve_contract_state(
            registry,
            contract,
            approved_by="test",
            reason="baseline",
            visual_proof_paths=[],
            lock_after=True,
        )
        self.write_visual_page("#0044ff")
        stale_locks = self.dg.refresh_proof_status(registry)
        self.assertEqual(len(stale_locks), 1)
        self.assertIn("box.desktop", " | ".join(stale_locks[0].reasons))
        saved_registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_registry["contracts"][0]["proof_status"], "stale")


if __name__ == "__main__":
    unittest.main()
