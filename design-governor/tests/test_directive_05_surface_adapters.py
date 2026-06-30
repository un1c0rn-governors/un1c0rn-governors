from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import REPO_ROOT, load_governor
except ImportError:
    from test_support import REPO_ROOT, load_governor


INSTALLABLE_README_PATH = REPO_ROOT / "README.md"


class Directive05SurfaceAdapterTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def contract_template(self, owner_files: list[str]) -> dict:
        return {
            "contract_id": "example.site.surface.v1",
            "design_name": "Surface",
            "contract_name": "Surface coverage",
            "status": "draft",
            "owner_files": owner_files,
            "css_tokens": [],
            "selectors": [],
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

    def write_registry(self, contracts: list[dict]) -> dict:
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
            "contracts": contracts,
        }
        registry = self.dg.refresh_impact_map(registry, persist=False)
        self.registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        return registry

    def test_css_module_semantic_hash_ignores_formatting_only(self) -> None:
        first = ".hero{color:red;background:#000;}\n"
        second = ".hero {\n  color: red;\n  background: #000000;\n}\n"
        material_a = self.dg.semantic_file_material(first, "Button.module.css")
        material_b = self.dg.semantic_file_material(second, "Button.module.css")
        self.assertEqual(material_a, material_b)

    def test_tsx_surface_targets_and_tailwind_markup_hashes_work(self) -> None:
        component_path = self.workspace / "Card.tsx"
        component_path.write_text(
            "export function Card() {\n"
            "  return (\n"
            "    <section data-governor=\"hero\" className=\"rounded-xl bg-black text-white\">\n"
            "      <h1 id=\"headline\">BUY N0W</h1>\n"
            "    </section>\n"
            "  );\n"
            "}\n",
            encoding="utf-8",
        )
        contract = self.contract_template(["Card.tsx"])
        contract["selectors"] = ['section[data-governor="hero"]']
        contract["copy_blocks"] = ["#headline"]
        contract["surface_targets"] = [
            {
                "target_id": "hero.surface",
                "kind": "selector",
                "query": 'section[data-governor="hero"]',
                "files": ["Card.tsx"],
            }
        ]
        registry = self.write_registry([contract])
        proof_before = self.dg.build_contract_proof(registry["contracts"][0])
        self.assertIn("hero.surface", proof_before["surface_hashes"])
        request = {
            "request_id": "tsx-surface",
            "requested_contract_ids": ["example.site.surface.v1"],
            "planned_file_touches": [],
            "planned_token_touches": [],
            "planned_selector_touches": [],
            "planned_surface_touches": ["hero.surface"],
        }
        affected = self.dg.resolve_affected_contracts(registry, request)
        self.assertEqual([item.contract_id for item in affected], ["example.site.surface.v1"])

        component_path.write_text(
            "export function Card() {\n"
            "  return (\n"
            "    <section data-governor=\"hero\" className=\"rounded-xl bg-black text-cyan-200\">\n"
            "      <h1 id=\"headline\">BUY N0W</h1>\n"
            "    </section>\n"
            "  );\n"
            "}\n",
            encoding="utf-8",
        )
        proof_after = self.dg.build_contract_proof(registry["contracts"][0])
        self.assertNotEqual(
            proof_before["selector_hashes"]['section[data-governor="hero"]'],
            proof_after["selector_hashes"]['section[data-governor="hero"]'],
        )
        self.assertNotEqual(proof_before["surface_hashes"]["hero.surface"], proof_after["surface_hashes"]["hero.surface"])

    def test_tsx_one_line_return_is_governed(self) -> None:
        text = (
            "export function Card() {\n"
            "  return <section data-governor=\"hero\"><h1 id=\"headline\">BUY N0W</h1></section>;\n"
            "}\n"
        )
        self.assertEqual(self.dg.selector_count(text, 'section[data-governor="hero"]', "Card.tsx"), 1)
        self.assertNotEqual(self.dg.collect_copy_material(text, "#headline", "Card.tsx"), "")

    def test_tsx_arrow_implicit_return_is_governed(self) -> None:
        text = (
            "export const Card = () => <section data-governor=\"hero\">"
            "<h1 id=\"headline\">BUY N0W</h1></section>;\n"
        )
        self.assertEqual(self.dg.selector_count(text, 'section[data-governor="hero"]', "Card.tsx"), 1)
        self.assertNotEqual(self.dg.collect_copy_material(text, "#headline", "Card.tsx"), "")

    def test_tsx_fragment_wrapper_is_governed(self) -> None:
        text = (
            "export function Card() {\n"
            "  return <><section data-governor=\"hero\"></section><aside data-governor=\"side\"></aside></>;\n"
            "}\n"
        )
        self.assertEqual(self.dg.selector_count(text, 'section[data-governor="hero"]', "Card.tsx"), 1)
        self.assertEqual(self.dg.selector_count(text, 'aside[data-governor="side"]', "Card.tsx"), 1)

    def test_tsx_conditional_branches_are_collected(self) -> None:
        text = (
            "export function Card({ ready }: { ready: boolean }) {\n"
            "  return ready\n"
            "    ? <section data-governor=\"hero\"><h1>READY</h1></section>\n"
            "    : <section data-governor=\"hero\"><h1>WAIT</h1></section>;\n"
            "}\n"
        )
        self.assertEqual(self.dg.selector_count(text, 'section[data-governor="hero"]', "Card.tsx"), 2)

    def test_installable_readme_marks_jsx_tsx_support_as_basic(self) -> None:
        readme = INSTALLABLE_README_PATH.read_text(encoding="utf-8")
        self.assertIn("Basic now: JSX and TSX.", readme)
        self.assertIn("best-effort", readme)

    def test_vue_basic_selector_count_works(self) -> None:
        text = (
            "<template>\n"
            "  <section class=\"hero\"><h1>ARCHIVE</h1></section>\n"
            "</template>\n"
        )
        self.assertEqual(self.dg.selector_count(text, "section.hero", "Card.vue"), 1)

    def test_vue_selector_style_policy_passes(self) -> None:
        (self.workspace / "Card.vue").write_text(
            "<template>\n"
            "  <section class=\"hero\"><h1>ARCHIVE</h1></section>\n"
            "</template>\n"
            "<style>\n"
            ".hero {\n"
            "  color: red;\n"
            "}\n"
            "</style>\n",
            encoding="utf-8",
        )
        contract = self.contract_template(["Card.vue"])
        contract["selectors"] = [".hero"]
        contract["policy_checks"] = [
            {
                "rule_id": "vue.hero.color",
                "kind": "selector_style",
                "selector": ".hero",
                "expects": {"color": "red"},
                "files": ["Card.vue"],
            }
        ]
        results = self.dg.evaluate_policy_checks(contract, self.dg.read_contract_file_contents(contract))
        self.assertEqual(results["vue.hero.color"]["status"], "pass")

    def test_svelte_copy_block_proof_works(self) -> None:
        (self.workspace / "Artifact.svelte").write_text(
            "<script>\n"
            "  let value = 'A';\n"
            "</script>\n"
            "<section data-copy-block=\"headline\">\n"
            "  <h1>ARCHIVED MOMENT</h1>\n"
            "</section>\n"
            "<style>\n"
            "section { color: white; }\n"
            "</style>\n",
            encoding="utf-8",
        )
        contract = self.contract_template(["Artifact.svelte"])
        contract["copy_blocks"] = ["headline"]
        proof = self.dg.build_contract_proof(contract)
        self.assertNotEqual(proof["copy_hashes"]["headline"], self.dg.sha256_text("missing"))

    def test_svelte_basic_selector_count_works(self) -> None:
        text = (
            "<script>\n"
            "  let value = 'A';\n"
            "</script>\n"
            "<section class=\"hero\"><h1>ARCHIVED MOMENT</h1></section>\n"
        )
        self.assertEqual(self.dg.selector_count(text, "section.hero", "Artifact.svelte"), 1)

    def test_strict_surface_mode_blocks_unsupported_file_types(self) -> None:
        self.project_config_path.write_text(
            json.dumps(
                {
                    "enabled_adapters": ["css", "html", "jsx", "vue", "svelte"],
                    "strict_surface_mode": True,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.workspace / "Scene.astro").write_text("<main>Unsupported</main>\n", encoding="utf-8")
        contract = self.contract_template(["Scene.astro"])
        failures = self.dg.policy_failures_for_contract(contract)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].kind, "surface_adapter")
        with self.assertRaises(ValueError):
            self.dg.approve_contract_state(
                self.write_registry([contract]),
                contract,
                approved_by="test",
                reason="strict mode block",
                visual_proof_paths=[],
                lock_after=True,
            )


if __name__ == "__main__":
    unittest.main()
