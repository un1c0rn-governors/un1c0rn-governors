from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files as package_files
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
import zipfile

try:
    import cssutils
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Missing dependency: cssutils. Run `python -m pip install -r requirements.txt` first."
    ) from exc

try:
    from bs4 import BeautifulSoup
    from bs4.element import NavigableString, Tag
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Missing dependency: beautifulsoup4. Run `python -m pip install -r requirements.txt` first."
    ) from exc


cssutils.log.setLevel(logging.CRITICAL)
sys.dont_write_bytecode = True


VERSION = "1.1.0"
ROOT = Path.cwd()
ROOT_RESOLVED = ROOT.resolve()
REGISTRY_PATH = ROOT / "design_governor_registry.json"
PROJECT_CONFIG_PATH = ROOT / "design_governor_project.json"
CHANGE_REQUEST_TEMPLATE_PATH = ROOT / "design_change_request.template.json"
AGENTS_TEMPLATE_PATH = ROOT / "AGENTS.md"
HOW_IT_WORKS_PATH = ROOT / "HOW_IT_WORKS.md"
SNAPSHOT_DIR = ROOT / "design_snapshots"
RUN_DIR = ROOT / "design_runs"
TEMPLATE_DIR = package_files("design_governor").joinpath("templates")
BLOCKED_RELEASE_PARTS = {"__pycache__", "build", "dist"}
BLOCKED_RELEASE_SUFFIXES = (".pyc", ".pyo")
IGNORED_RELEASE_AUDIT_PATH_PREFIXES = (
    "design_governor/__pycache__",
    "tests/__pycache__",
    "design_governor_installable/design_governor/__pycache__",
)
PROJECT_BOOT_FILES = {
    "design_governor_registry.json": "design_governor_registry.template.json",
    "design_change_request.template.json": "design_change_request.template.json",
    "AGENTS.md": "AGENTS_TEMPLATE.md",
    "HOW_IT_WORKS.md": "HOW_IT_WORKS.md",
}
PROJECT_AUTOMATION_FILES = {
    "design_governor_project.json": "design_governor_project.template.json",
    ".github/workflows/design-governor.yml": "github_workflow.design-governor.yml",
    ".pre-commit-config.yaml": "pre-commit-config.template.yaml",
    "DESIGN_GOVERNOR_AGENT_WORKFLOWS.md": "DESIGN_GOVERNOR_AGENT_WORKFLOWS.md",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def workspace_path(value: str) -> Path:
    raw_path = Path(str(value or "").strip().replace("\\", "/"))
    candidate = raw_path if raw_path.is_absolute() else (ROOT / raw_path)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(ROOT_RESOLVED)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {value}") from exc
    return resolved


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def read_packaged_text(name: str) -> str:
    return TEMPLATE_DIR.joinpath(name).read_text(encoding="utf-8")


def load_packaged_json(name: str) -> dict[str, Any]:
    return json.loads(read_packaged_text(name))


def save_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(value, encoding="utf-8")
    temp_path.replace(path)


def validate_template_manifest(manifest: dict[str, str]) -> None:
    available = {item.name for item in TEMPLATE_DIR.iterdir() if item.is_file()}
    missing = sorted(template_name for template_name in manifest.values() if template_name not in available)
    if missing:
        raise FileNotFoundError(f"Missing packaged starter templates: {', '.join(missing)}")


def validate_boot_templates() -> None:
    validate_template_manifest(PROJECT_BOOT_FILES)
    validate_template_manifest(PROJECT_AUTOMATION_FILES)

    agents_template = read_packaged_text("AGENTS_TEMPLATE.md")
    if "HOW_IT_WORKS.md" in agents_template and "HOW_IT_WORKS.md" not in PROJECT_BOOT_FILES:
        raise ValueError("AGENTS_TEMPLATE.md requires HOW_IT_WORKS.md, but it is not listed in PROJECT_BOOT_FILES.")


def write_template_manifest(manifest: dict[str, str], *, force: bool) -> list[Path]:
    validate_template_manifest(manifest)
    written: list[Path] = []
    for output_name, template_name in manifest.items():
        target_path = ROOT / output_name
        if target_path.exists() and not force:
            continue
        contents = read_packaged_text(template_name).rstrip() + "\n"
        save_text(target_path, contents)
        written.append(target_path)
    return written


def write_project_boot_files(*, force: bool) -> list[Path]:
    return write_template_manifest(PROJECT_BOOT_FILES, force=force)


def write_project_automation_files(*, force: bool) -> list[Path]:
    return write_template_manifest(PROJECT_AUTOMATION_FILES, force=force)


def load_project_config() -> dict[str, Any]:
    if not PROJECT_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing project config: {PROJECT_CONFIG_PATH}")
    return load_json(PROJECT_CONFIG_PATH)


def load_optional_project_config() -> dict[str, Any]:
    if not PROJECT_CONFIG_PATH.exists():
        return {}
    return load_json(PROJECT_CONFIG_PATH)


def configured_request_files(config: dict[str, Any]) -> list[Path]:
    patterns = normalize_values(list(config.get("request_globs") or []))
    matches: list[Path] = []
    seen = set()
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            matches.append(resolved)
    return sorted(matches)


def normalize_values(values: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for item in values:
        value = str(item or "").strip().replace("\\", "/")
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def normalize_contract_ids(values: list[str]) -> list[str]:
    return normalize_values(values)


def registry_contracts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get("contracts") or [])


def registry_impact_map(payload: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    return dict(payload.get("impact_map") or {})


def contract_by_id(payload: dict[str, Any], contract_id: str) -> dict[str, Any]:
    for contract in registry_contracts(payload):
        if contract.get("contract_id") == contract_id:
            return contract
    raise KeyError(f"Unknown contract: {contract_id}")


def locked_contracts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        contract
        for contract in registry_contracts(payload)
        if str(contract.get("status") or "").lower() == "locked"
    ]


def contract_files(contract: dict[str, Any]) -> list[str]:
    return normalize_values(list(contract.get("owner_files") or []))


def contract_tokens(contract: dict[str, Any]) -> list[str]:
    return normalize_values(list(contract.get("css_tokens") or []))


def contract_selectors(contract: dict[str, Any]) -> list[str]:
    return normalize_values(list(contract.get("selectors") or []))


def contract_states(contract: dict[str, Any]) -> list[str]:
    return normalize_values(list(contract.get("visual_states") or []))


def contract_copy_blocks(contract: dict[str, Any]) -> list[str]:
    return normalize_values(list(contract.get("copy_blocks") or []))


def contract_surface_targets(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return list(contract.get("surface_targets") or [])


def contract_policy_checks(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return list(contract.get("policy_checks") or [])


def contract_visual_proof_specs(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return list(contract.get("visual_proof_specs") or [])


def contract_governed_files(contract: dict[str, Any]) -> list[str]:
    governed = list(contract_files(contract))
    for target in contract_surface_targets(contract):
        governed.extend(surface_target_files(target))
    for rule in contract_policy_checks(contract):
        governed.extend(normalize_values(list(rule.get("files") or [])))
    return normalize_values(governed)


def approved_proof(contract: dict[str, Any]) -> dict[str, Any]:
    return dict(contract.get("approved_proof") or {})


def proof_status(contract: dict[str, Any]) -> str:
    explicit = str(contract.get("proof_status") or "").strip().lower()
    if explicit:
        return explicit
    if approved_proof(contract).get("composite_hash"):
        return "approved"
    if str(contract.get("status") or "").lower() == "locked":
        return "stale"
    return "none"


def safe_read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def normalize_inline_whitespace(value: str) -> str:
    return " ".join(str(value or "").split())


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def project_adapter_names(config: dict[str, Any] | None = None) -> set[str]:
    payload = config if config is not None else load_optional_project_config()
    names = normalize_values(list(payload.get("enabled_adapters") or []))
    if not names:
        return {"css", "html", "jsx", "vue", "svelte"}
    return {name.lower() for name in names}


def strict_surface_mode(config: dict[str, Any] | None = None) -> bool:
    payload = config if config is not None else load_optional_project_config()
    return bool(payload.get("strict_surface_mode", False))


def surface_target_kind(target: dict[str, Any]) -> str:
    return str(target.get("kind") or "selector").strip().lower()


def surface_target_query(target: dict[str, Any]) -> str:
    for key in ("query", "selector", "copy_block", "token"):
        value = str(target.get(key) or "").strip()
        if value:
            return value
    return ""


def surface_target_files(target: dict[str, Any]) -> list[str]:
    return normalize_values(list(target.get("files") or []))


def surface_target_signature(target: dict[str, Any], *, index: int = 0) -> str:
    target_id = str(target.get("target_id") or "").strip()
    if target_id:
        return target_id
    kind = surface_target_kind(target)
    query = surface_target_query(target)
    files = surface_target_files(target)
    parts = [kind or "selector", query or f"surface-{index}"]
    if files:
        parts.append(",".join(files))
    return "|".join(parts)


def append_map_value(mapping: dict[str, list[str]], key: str, contract_id: str) -> None:
    values = mapping.setdefault(key, [])
    if contract_id not in values:
        values.append(contract_id)


def build_impact_map_from_contracts(contracts: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    file_map: dict[str, list[str]] = {}
    token_map: dict[str, list[str]] = {}
    selector_map: dict[str, list[str]] = {}
    surface_map: dict[str, list[str]] = {}
    for contract in contracts:
        contract_id = str(contract.get("contract_id") or "").strip()
        if not contract_id:
            continue
        for file_path in contract_governed_files(contract):
            append_map_value(file_map, file_path, contract_id)
        for token in contract_tokens(contract):
            append_map_value(token_map, token, contract_id)
        for selector in contract_selectors(contract):
            append_map_value(selector_map, selector, contract_id)
        for index, target in enumerate(contract_surface_targets(contract), start=1):
            append_map_value(surface_map, surface_target_signature(target, index=index), contract_id)
    return {
        "file_to_contracts": file_map,
        "token_to_contracts": token_map,
        "selector_to_contracts": selector_map,
        "surface_to_contracts": surface_map,
    }


def refresh_impact_map(registry: dict[str, Any], *, persist: bool) -> dict[str, Any]:
    generated = build_impact_map_from_contracts(registry_contracts(registry))
    if registry.get("impact_map") != generated:
        registry["impact_map"] = generated
        if persist:
            save_json(REGISTRY_PATH, registry)
    return registry


def line_start(text: str, index: int) -> int:
    return text.rfind("\n", 0, index) + 1


def line_end(text: str, index: int) -> int:
    end = text.find("\n", index)
    return len(text) if end == -1 else end


def simple_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][\w:-]*", str(value or "").strip()))


def looks_like_selector(value: str) -> bool:
    stripped = str(value or "").strip()
    if not stripped:
        return False
    if stripped.startswith(("#", ".", "[", ":")):
        return True
    if any(char in stripped for char in (" ", ">", "+", "~", "[", "]", ":", "=")):
        return True
    return False


def normalize_selector_parts(selector_text: str) -> list[str]:
    parts = [normalize_inline_whitespace(part) for part in str(selector_text or "").split(",")]
    return sorted(part for part in parts if part)


def canonicalize_css_declarations(style: Any) -> dict[str, dict[str, str]]:
    declarations: dict[str, dict[str, str]] = {}
    for prop in style:
        name = str(getattr(prop, "name", "") or "").strip().lower()
        if not name:
            continue
        declarations[name] = {
            "value": normalize_inline_whitespace(prop.propertyValue.cssText),
            "priority": str(getattr(prop, "priority", "") or "").strip().lower(),
        }
    return {name: declarations[name] for name in sorted(declarations)}


def canonicalize_css_rule(rule: Any) -> dict[str, Any] | None:
    rule_type = getattr(rule, "type", None)
    if rule_type == rule.STYLE_RULE:
        return {
            "type": "style",
            "selectors": normalize_selector_parts(rule.selectorText),
            "declarations": canonicalize_css_declarations(rule.style),
        }
    if rule_type == rule.MEDIA_RULE:
        inner_rules = [item for item in (canonicalize_css_rule(child) for child in rule.cssRules) if item]
        return {
            "type": "media",
            "media": normalize_inline_whitespace(rule.media.mediaText),
            "rules": inner_rules,
        }
    if rule_type == rule.FONT_FACE_RULE:
        return {
            "type": "font-face",
            "declarations": canonicalize_css_declarations(rule.style),
        }
    if rule_type == rule.PAGE_RULE:
        return {
            "type": "page",
            "selector": normalize_inline_whitespace(getattr(rule, "selectorText", "") or ""),
            "declarations": canonicalize_css_declarations(rule.style),
        }
    if rule_type == rule.IMPORT_RULE:
        return {
            "type": "import",
            "href": normalize_inline_whitespace(getattr(rule, "href", "") or ""),
            "media": normalize_inline_whitespace(rule.media.mediaText),
        }
    return None


def parse_css_sheet(text: str) -> Any:
    return cssutils.parseString(text or "")


def canonicalize_css_sheet(text: str) -> list[dict[str, Any]]:
    try:
        sheet = parse_css_sheet(text)
    except Exception:
        return [{"type": "raw", "text": normalize_inline_whitespace(text)}]
    return [item for item in (canonicalize_css_rule(rule) for rule in sheet.cssRules) if item]


def normalize_css_value(value: Any) -> str:
    raw = normalize_inline_whitespace(value)
    if not raw:
        return ""
    try:
        style = cssutils.parseStyle(f"x: {raw};")
        normalized = normalize_inline_whitespace(style.getPropertyValue("x"))
        return normalized or raw
    except Exception:
        return raw


def safe_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return sanitized.strip("._") or "artifact"


def visual_base_url() -> str:
    return str(__import__("os").environ.get("DESIGN_GOVERNOR_VISUAL_BASE_URL") or "").strip()


def load_pillow() -> tuple[Any, Any]:
    try:
        from PIL import Image, ImageChops
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Missing dependency: pillow. Run `python -m pip install -r requirements.txt` first."
        ) from exc
    return Image, ImageChops


def load_playwright() -> tuple[Any, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Missing dependency: playwright. Run `python -m pip install -r requirements.txt` "
            "and `python -m playwright install chromium` first."
        ) from exc
    return sync_playwright, PlaywrightError


def normalize_visual_viewport(spec: dict[str, Any]) -> dict[str, int]:
    viewport = dict(spec.get("viewport") or {})
    width = int(viewport.get("width") or 1440)
    height = int(viewport.get("height") or 1024)
    if width <= 0 or height <= 0:
        raise ValueError("visual proof viewport must use positive width and height")
    return {"width": width, "height": height}


def resolve_visual_proof_url(spec: dict[str, Any], override_base_url: str = "") -> str:
    route = str(spec.get("route") or "").strip()
    if not route:
        raise ValueError("visual proof spec is missing route")

    parsed = urlparse(route)
    if parsed.scheme:
        return route

    base_url = str(override_base_url or spec.get("base_url") or visual_base_url()).strip()
    if not base_url:
        raise ValueError("visual proof spec requires base_url, DESIGN_GOVERNOR_VISUAL_BASE_URL, or an absolute route")
    return urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))


def current_visual_artifact_root(*, compare_to_baseline: bool) -> Path:
    if compare_to_baseline:
        return RUN_DIR / "visual_proofs_live"
    return SNAPSHOT_DIR / "visual_proofs"


def relative_workspace_path(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT_RESOLVED)).replace("\\", "/")


def baseline_visual_result(contract: dict[str, Any], proof_id: str) -> dict[str, Any]:
    approved = approved_proof(contract)
    return dict((approved.get("visual_proof_results") or {}).get(proof_id) or {})


def compare_visual_images(baseline_path: Path, current_path: Path) -> dict[str, Any]:
    Image, ImageChops = load_pillow()
    baseline_image = Image.open(baseline_path).convert("RGBA")
    current_image = Image.open(current_path).convert("RGBA")
    try:
        if baseline_image.size != current_image.size:
            return {
                "diff_ratio": 1.0,
                "baseline_size": list(baseline_image.size),
                "current_size": list(current_image.size),
                "reason": "visual proof image size changed",
            }
        diff = ImageChops.difference(baseline_image, current_image)
        total_pixels = baseline_image.size[0] * baseline_image.size[1]
        changed_pixels = sum(1 for pixel in diff.getdata() if any(channel != 0 for channel in pixel))
        diff_ratio = changed_pixels / total_pixels if total_pixels else 0.0
        return {
            "diff_ratio": diff_ratio,
            "baseline_size": list(baseline_image.size),
            "current_size": list(current_image.size),
            "reason": "",
        }
    finally:
        baseline_image.close()
        current_image.close()


def capture_visual_proof_results(
    contract: dict[str, Any],
    *,
    compare_to_baseline: bool,
    override_base_url: str = "",
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    specs = contract_visual_proof_specs(contract)
    if not specs:
        return results

    sync_playwright, PlaywrightError = load_playwright()
    artifact_root = current_visual_artifact_root(compare_to_baseline=compare_to_baseline)
    artifact_dir = artifact_root / safe_name(str(contract.get("contract_id") or "contract"))
    artifact_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        for index, spec in enumerate(specs, start=1):
            proof_id = str(spec.get("proof_id") or f"{contract.get('contract_id')}.visual.{index}").strip()
            browser_name = str(spec.get("browser") or "chromium").strip().lower()
            viewport = normalize_visual_viewport(spec)
            route = str(spec.get("route") or "").strip()
            state_name = str(spec.get("state_name") or "").strip()
            ready_selector = str(spec.get("ready_selector") or "").strip()
            target_selector = str(spec.get("target_selector") or "").strip()
            wait_ms = max(0, int(spec.get("wait_ms") or 0))
            full_page = bool(spec.get("full_page", False))
            required = bool(spec.get("required", True))
            disable_animations = bool(spec.get("disable_animations", True))
            max_diff_ratio = float(spec.get("max_diff_ratio") or 0.0)
            mask_selectors = normalize_values(list(spec.get("mask_selectors") or []))
            proof_stamp = utc_now()
            file_name = (
                f"{safe_name(proof_id)}.png"
                if compare_to_baseline
                else f"{safe_name(proof_id)}__{safe_name(proof_stamp)}.png"
            )
            artifact_path = artifact_dir / file_name

            result: dict[str, Any] = {
                "proof_id": proof_id,
                "runner": "playwright",
                "captured_at": proof_stamp,
                "route": route,
                "state_name": state_name,
                "viewport": viewport,
                "ready_selector": ready_selector,
                "target_selector": target_selector,
                "wait_ms": wait_ms,
                "mask_selectors": mask_selectors,
                "max_diff_ratio": max_diff_ratio,
                "artifact_path": relative_workspace_path(artifact_path),
                "artifact_hash": "",
                "baseline_artifact_path": "",
                "baseline_artifact_hash": "",
                "diff_ratio": 0.0,
                "status": "pass",
                "reason": "",
            }

            browser = None
            context = None
            try:
                browser_type = getattr(playwright, browser_name)
                browser = browser_type.launch(headless=bool(spec.get("headless", True)))
                context = browser.new_context(viewport=viewport)
                page = context.new_page()
                url = resolve_visual_proof_url(spec, override_base_url=override_base_url)
                result["url"] = url
                page.goto(url, wait_until="networkidle")
                if ready_selector:
                    page.wait_for_selector(ready_selector, timeout=max(wait_ms + 5000, 5000))
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                masks = [page.locator(selector) for selector in mask_selectors]
                screenshot_args: dict[str, Any] = {"path": str(artifact_path)}
                if disable_animations:
                    screenshot_args["animations"] = "disabled"
                if masks:
                    screenshot_args["mask"] = masks
                if target_selector:
                    locator = page.locator(target_selector)
                    locator.wait_for(timeout=max(wait_ms + 5000, 5000))
                    locator.screenshot(**screenshot_args)
                else:
                    screenshot_args["full_page"] = full_page
                    page.screenshot(**screenshot_args)
                result["artifact_hash"] = file_hash(artifact_path)
                if compare_to_baseline:
                    baseline = baseline_visual_result(contract, proof_id)
                    baseline_path_value = str(baseline.get("artifact_path") or "").strip()
                    if not baseline_path_value:
                        raise ValueError(f"visual proof baseline is missing for {proof_id}")
                    baseline_path = workspace_path(baseline_path_value)
                    if not baseline_path.exists():
                        raise FileNotFoundError(f"visual proof baseline file is missing for {proof_id}: {baseline_path}")
                    result["baseline_artifact_path"] = baseline_path_value.replace("\\", "/")
                    result["baseline_artifact_hash"] = str(baseline.get("artifact_hash") or file_hash(baseline_path))
                    diff = compare_visual_images(baseline_path, artifact_path)
                    result["diff_ratio"] = float(diff.get("diff_ratio") or 0.0)
                    if diff.get("reason"):
                        result["reason"] = str(diff.get("reason") or "")
                    if result["diff_ratio"] > max_diff_ratio:
                        result["status"] = "fail"
                        result["reason"] = (
                            result["reason"]
                            or f"visual proof drift exceeded threshold for {proof_id}: "
                            f"{result['diff_ratio']:.6f} > {max_diff_ratio:.6f}"
                        )
                results[proof_id] = result
            except (PlaywrightError, FileNotFoundError, ValueError) as exc:
                result["status"] = "fail" if required else "skip"
                result["reason"] = str(exc)
                results[proof_id] = result
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()

    return results


def comparable_visual_proof_results(results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    comparable: dict[str, dict[str, Any]] = {}
    for proof_id, result in sorted(results.items()):
        comparable[proof_id] = {
            "proof_id": proof_id,
            "runner": str(result.get("runner") or ""),
            "route": str(result.get("route") or ""),
            "state_name": str(result.get("state_name") or ""),
            "viewport": dict(result.get("viewport") or {}),
            "ready_selector": str(result.get("ready_selector") or ""),
            "target_selector": str(result.get("target_selector") or ""),
            "wait_ms": int(result.get("wait_ms") or 0),
            "mask_selectors": list(result.get("mask_selectors") or []),
            "max_diff_ratio": float(result.get("max_diff_ratio") or 0.0),
            "artifact_hash": str(result.get("artifact_hash") or ""),
            "diff_ratio": float(result.get("diff_ratio") or 0.0),
            "status": str(result.get("status") or ""),
            "reason": str(result.get("reason") or ""),
            "url": str(result.get("url") or ""),
        }
    return comparable


class UnsupportedSurfaceError(ValueError):
    pass


def collect_css_selector_material(text: str, selector: str) -> str:
    wanted = normalize_inline_whitespace(selector)
    try:
        sheet = parse_css_sheet(text)
    except Exception:
        return ""
    matches = []
    for rule in sheet.cssRules:
        canonical = canonicalize_css_rule(rule)
        if not canonical or canonical.get("type") != "style":
            continue
        selectors = list(canonical.get("selectors") or [])
        if wanted in selectors:
            matches.append(canonical)
    return stable_json(matches) if matches else ""


def collect_css_token_material(text: str, token: str) -> str:
    wanted = str(token or "").strip()
    try:
        sheet = parse_css_sheet(text)
    except Exception:
        return ""
    matches = []
    for rule in sheet.cssRules:
        canonical = canonicalize_css_rule(rule)
        if not canonical or canonical.get("type") != "style":
            continue
        declarations = dict(canonical.get("declarations") or {})
        matched_declarations = {
            name: payload
            for name, payload in declarations.items()
            if name == wanted or wanted in str(payload.get("value") or "")
        }
        if matched_declarations:
            matches.append(
                {
                    "selectors": list(canonical.get("selectors") or []),
                    "declarations": matched_declarations,
                }
            )
    return stable_json(matches) if matches else ""


def canonicalize_html_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for name, value in sorted((attrs or {}).items()):
        if isinstance(value, list):
            normalized[name] = sorted(normalize_inline_whitespace(item) for item in value if normalize_inline_whitespace(item))
        else:
            normalized[name] = normalize_inline_whitespace(value)
    return normalized


def canonicalize_html_node(node: Any) -> Any:
    if isinstance(node, NavigableString):
        text = normalize_inline_whitespace(str(node))
        return text or None
    if not isinstance(node, Tag):
        return None
    children = []
    for child in node.children:
        canonical_child = canonicalize_html_node(child)
        if canonical_child is not None:
            children.append(canonical_child)
    return {
        "tag": node.name,
        "attrs": canonicalize_html_attrs(node.attrs),
        "children": children,
    }


def canonicalize_html_document(text: str) -> list[Any]:
    soup = BeautifulSoup(text or "", "html.parser")
    roots = soup.body.contents if soup.body else soup.contents
    items = []
    for node in roots:
        canonical = canonicalize_html_node(node)
        if canonical is not None:
            items.append(canonical)
    return items


def html_text_values(element: Tag) -> list[str]:
    values = []
    text_value = normalize_inline_whitespace(element.get_text(" ", strip=True))
    if text_value:
        values.append(text_value)
    for attr_name in ("placeholder", "value", "aria-label", "title", "alt"):
        attr_value = normalize_inline_whitespace(element.get(attr_name, ""))
        if attr_value:
            values.append(attr_value)
    seen = set()
    normalized = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def collect_html_selector_material(text: str, selector: str) -> str:
    soup = BeautifulSoup(text or "", "html.parser")
    try:
        matches = soup.select(selector)
    except Exception:
        return ""
    payload = [canonicalize_html_node(match) for match in matches]
    payload = [item for item in payload if item is not None]
    return stable_json(payload) if payload else ""


def collect_html_copy_material(text: str, copy_block: str) -> str:
    soup = BeautifulSoup(text or "", "html.parser")
    key = str(copy_block or "").strip()
    if not key:
        return ""

    matches: list[str] = []
    seen = set()

    def append_values(values: list[str]) -> None:
        for value in values:
            normalized = normalize_inline_whitespace(value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            matches.append(normalized)

    if looks_like_selector(key):
        try:
            selected = soup.select(key)
        except Exception:
            selected = []
        for element in selected:
            append_values(html_text_values(element))

    if not matches and simple_identifier(key):
        selector = f"#{key}, .{key}, [data-copy-block=\"{key}\"], [name=\"{key}\"]"
        for element in soup.select(selector):
            append_values(html_text_values(element))

    if not matches:
        for element in soup.find_all(True):
            values = html_text_values(element)
            if any(value == key for value in values):
                append_values(values)

    return stable_json(matches) if matches else ""


def normalize_html_attr_value(value: Any) -> str:
    if isinstance(value, list):
        normalized = [normalize_inline_whitespace(item) for item in value if normalize_inline_whitespace(item)]
        return " ".join(sorted(normalized))
    return normalize_inline_whitespace(value)


def selector_attr_matches_in_html(text: str, selector: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(text or "", "html.parser")
    try:
        matches = soup.select(selector)
    except Exception:
        return []
    payload = []
    for element in matches:
        attrs = {
            name: normalize_html_attr_value(value)
            for name, value in sorted((element.attrs or {}).items())
        }
        payload.append(attrs)
    return payload


def extract_tag_inner_blocks(text: str, tag_name: str) -> list[str]:
    pattern = re.compile(rf"<{tag_name}\b[^>]*>(.*?)</{tag_name}>", re.IGNORECASE | re.DOTALL)
    return [str(match.group(1) or "") for match in pattern.finditer(text or "")]


def strip_tag_blocks(text: str, tag_names: tuple[str, ...]) -> str:
    cleaned = text or ""
    for tag_name in tag_names:
        cleaned = re.sub(
            rf"<{tag_name}\b[^>]*>.*?</{tag_name}>",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return cleaned


def normalize_component_markup_source(text: str) -> str:
    normalized = str(text or "")
    normalized = normalized.replace("className=", "class=")
    normalized = normalized.replace("htmlFor=", "for=")
    normalized = normalized.replace("<>", "<fragment>")
    normalized = normalized.replace("</>", "</fragment>")
    normalized = re.sub(r'=\{\s*"([^"]*)"\s*\}', r'="\1"', normalized)
    normalized = re.sub(r"=\{\s*'([^']*)'\s*\}", r'="\1"', normalized)
    normalized = re.sub(r"\{\s*\"([^\"]*)\"\s*\}", r"\1", normalized)
    normalized = re.sub(r"\{\s*'([^']*)'\s*\}", r"\1", normalized)
    normalized = re.sub(
        r"=\{\s*([^{}]+?)\s*\}",
        lambda match: f'="{normalize_inline_whitespace(match.group(1))}"',
        normalized,
    )
    normalized = re.sub(r"\{[^{}]*\}", "", normalized)
    return normalized


@dataclass
class ParsedJsxTag:
    name: str
    closing: bool
    self_closing: bool
    end_index: int


def jsx_name_char(char: str) -> bool:
    return char.isalnum() or char in {"-", "_", ":", "."}


def skip_quoted_javascript(text: str, start_index: int) -> int:
    quote = text[start_index]
    index = start_index + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    return len(text)


def skip_braced_javascript(text: str, start_index: int) -> int:
    depth = 0
    index = start_index
    while index < len(text):
        char = text[index]
        if char in {'"', "'", "`"}:
            index = skip_quoted_javascript(text, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def parse_jsx_tag(text: str, start_index: int) -> ParsedJsxTag | None:
    if start_index >= len(text) or text[start_index] != "<":
        return None
    index = start_index + 1
    closing = False
    if index < len(text) and text[index] == "/":
        closing = True
        index += 1
    if index < len(text) and text[index] == ">":
        return ParsedJsxTag(name="#fragment", closing=closing, self_closing=False, end_index=index + 1)
    if index >= len(text) or not text[index].isalpha():
        return None

    name_start = index
    while index < len(text) and jsx_name_char(text[index]):
        index += 1
    name = text[name_start:index]
    if not name:
        return None

    brace_depth = 0
    quote = ""
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {'"', "'", "`"}:
            quote = char
            index += 1
            continue
        if char == "{":
            brace_depth += 1
            index += 1
            continue
        if char == "}" and brace_depth > 0:
            brace_depth -= 1
            index += 1
            continue
        if char == ">" and brace_depth == 0:
            raw_tag = text[start_index:index]
            self_closing = not closing and raw_tag.rstrip().endswith("/")
            return ParsedJsxTag(
                name=name,
                closing=closing,
                self_closing=self_closing,
                end_index=index + 1,
            )
        index += 1
    return None


def extract_jsx_fragment_at(text: str, start_index: int) -> str:
    opening = parse_jsx_tag(text, start_index)
    if opening is None or opening.closing:
        return ""
    if opening.self_closing:
        return text[start_index:opening.end_index]

    stack = [opening.name]
    index = opening.end_index
    while index < len(text):
        char = text[index]
        if char in {'"', "'", "`"}:
            index = skip_quoted_javascript(text, index)
            continue
        if char == "{":
            index = skip_braced_javascript(text, index)
            continue
        if char == "<":
            tag = parse_jsx_tag(text, index)
            if tag is None:
                index += 1
                continue
            if tag.closing:
                if stack and tag.name == stack[-1]:
                    stack.pop()
                    index = tag.end_index
                    if not stack:
                        return text[start_index:index]
                    continue
                index = tag.end_index
                continue
            if not tag.self_closing:
                stack.append(tag.name)
            index = tag.end_index
            continue
        index += 1
    return ""


def extract_probable_jsx_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in {'"', "'", "`"}:
            index = skip_quoted_javascript(text, index)
            continue
        if char == "<":
            fragment = extract_jsx_fragment_at(text, index)
            if fragment:
                fragments.append(fragment)
                index += len(fragment)
                continue
        index += 1
    return fragments


def component_markup_source(text: str, file_path: str) -> str:
    lower_path = str(file_path or "").replace("\\", "/").lower()
    if lower_path.endswith(".vue"):
        blocks = extract_tag_inner_blocks(text, "template")
        return "\n".join(blocks)
    if lower_path.endswith(".svelte"):
        return strip_tag_blocks(text, ("script", "style"))
    if lower_path.endswith((".jsx", ".tsx")):
        return "\n".join(extract_probable_jsx_fragments(text or ""))
    return text


def component_style_blocks(text: str, file_path: str) -> list[str]:
    lower_path = str(file_path or "").replace("\\", "/").lower()
    if lower_path.endswith((".vue", ".svelte")):
        return [block for block in extract_tag_inner_blocks(text, "style") if normalize_inline_whitespace(block)]
    return []


def canonicalize_markup_fragment(text: str) -> list[Any]:
    soup = BeautifulSoup(text or "", "html.parser")
    roots = soup.contents
    items = []
    for node in roots:
        if not isinstance(node, Tag):
            continue
        canonical = canonicalize_html_node(node)
        if canonical is not None:
            items.append(canonical)
    return items


def html_selector_material(text: str, selector: str) -> list[Any]:
    soup = BeautifulSoup(text or "", "html.parser")
    try:
        matches = soup.select(selector)
    except Exception:
        return []
    payload = [canonicalize_html_node(match) for match in matches]
    return [item for item in payload if item is not None]


class SurfaceAdapter:
    name = ""
    suffixes: tuple[str, ...] = ()

    def matches_file(self, file_path: str) -> bool:
        lowered = str(file_path or "").replace("\\", "/").lower()
        return any(lowered.endswith(suffix) for suffix in self.suffixes)

    def semantic_file_material(self, text: str, file_path: str) -> str:
        return text

    def collect_selector_material(self, text: str, selector: str, file_path: str) -> str:
        return ""

    def collect_copy_material(self, text: str, copy_block: str, file_path: str) -> str:
        return ""

    def selector_style_entries(self, text: str, selector: str, file_path: str) -> list[dict[str, Any]]:
        return []

    def selector_count(self, text: str, selector: str, file_path: str) -> int:
        return 0

    def selector_attrs(self, text: str, selector: str, file_path: str) -> list[dict[str, str]]:
        return []


class CssSurfaceAdapter(SurfaceAdapter):
    name = "css"
    suffixes = (".css",)

    def semantic_file_material(self, text: str, file_path: str) -> str:
        return stable_json(canonicalize_css_sheet(text))

    def collect_selector_material(self, text: str, selector: str, file_path: str) -> str:
        if selector.strip().startswith("--"):
            return collect_css_token_material(text, selector)
        return collect_css_selector_material(text, selector)

    def selector_style_entries(self, text: str, selector: str, file_path: str) -> list[dict[str, Any]]:
        material = collect_css_selector_material(text, selector)
        if not material:
            return []
        return list(json.loads(material))


class HtmlSurfaceAdapter(SurfaceAdapter):
    name = "html"
    suffixes = (".html", ".htm")

    def semantic_file_material(self, text: str, file_path: str) -> str:
        return stable_json(canonicalize_html_document(text))

    def collect_selector_material(self, text: str, selector: str, file_path: str) -> str:
        if not looks_like_selector(selector):
            return ""
        payload = html_selector_material(text, selector)
        return stable_json(payload) if payload else ""

    def collect_copy_material(self, text: str, copy_block: str, file_path: str) -> str:
        return collect_html_copy_material(text, copy_block)

    def selector_count(self, text: str, selector: str, file_path: str) -> int:
        return html_selector_count(text, selector)

    def selector_attrs(self, text: str, selector: str, file_path: str) -> list[dict[str, str]]:
        return selector_attr_matches_in_html(text, selector)


class ComponentSurfaceAdapter(SurfaceAdapter):
    def markup_source(self, text: str, file_path: str) -> str:
        return normalize_component_markup_source(component_markup_source(text, file_path))

    def style_blocks(self, text: str, file_path: str) -> list[str]:
        return component_style_blocks(text, file_path)

    def semantic_file_material(self, text: str, file_path: str) -> str:
        markup = self.markup_source(text, file_path)
        styles = self.style_blocks(text, file_path)
        payload = {
            "markup": canonicalize_markup_fragment(markup),
            "styles": [canonicalize_css_sheet(style) for style in styles],
        }
        return stable_json(payload)

    def collect_selector_material(self, text: str, selector: str, file_path: str) -> str:
        if selector.strip().startswith("--"):
            token_matches = []
            for style_text in self.style_blocks(text, file_path):
                material = collect_css_token_material(style_text, selector)
                if not material:
                    continue
                token_matches.extend(list(json.loads(material)))
            return stable_json(token_matches) if token_matches else ""
        style_matches = self.selector_style_entries(text, selector, file_path)
        markup_matches = []
        if looks_like_selector(selector):
            markup_matches = html_selector_material(self.markup_source(text, file_path), selector)
        if not style_matches and not markup_matches:
            return ""
        return stable_json({"style": style_matches, "markup": markup_matches})

    def collect_copy_material(self, text: str, copy_block: str, file_path: str) -> str:
        return collect_html_copy_material(self.markup_source(text, file_path), copy_block)

    def selector_style_entries(self, text: str, selector: str, file_path: str) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for style_text in self.style_blocks(text, file_path):
            material = collect_css_selector_material(style_text, selector)
            if not material:
                continue
            matches.extend(list(json.loads(material)))
        return matches

    def selector_count(self, text: str, selector: str, file_path: str) -> int:
        return html_selector_count(self.markup_source(text, file_path), selector)

    def selector_attrs(self, text: str, selector: str, file_path: str) -> list[dict[str, str]]:
        return selector_attr_matches_in_html(self.markup_source(text, file_path), selector)


class JsxSurfaceAdapter(ComponentSurfaceAdapter):
    name = "jsx"
    suffixes = (".jsx", ".tsx")


class VueSurfaceAdapter(ComponentSurfaceAdapter):
    name = "vue"
    suffixes = (".vue",)


class SvelteSurfaceAdapter(ComponentSurfaceAdapter):
    name = "svelte"
    suffixes = (".svelte",)


ALL_SURFACE_ADAPTERS: list[SurfaceAdapter] = [
    CssSurfaceAdapter(),
    HtmlSurfaceAdapter(),
    JsxSurfaceAdapter(),
    VueSurfaceAdapter(),
    SvelteSurfaceAdapter(),
]


def surface_adapter_for_file(file_path: str, *, config: dict[str, Any] | None = None) -> SurfaceAdapter | None:
    enabled = project_adapter_names(config)
    for adapter in ALL_SURFACE_ADAPTERS:
        if adapter.name not in enabled:
            continue
        if adapter.matches_file(file_path):
            return adapter
    return None


def selector_style_entries(text: str, selector: str, file_path: str) -> list[dict[str, Any]]:
    adapter = surface_adapter_for_file(file_path)
    if adapter is None:
        return []
    return adapter.selector_style_entries(text, selector, file_path)


def selector_count(text: str, selector: str, file_path: str) -> int:
    adapter = surface_adapter_for_file(file_path)
    if adapter is None:
        return 0
    return adapter.selector_count(text, selector, file_path)


def selector_attrs(text: str, selector: str, file_path: str) -> list[dict[str, str]]:
    adapter = surface_adapter_for_file(file_path)
    if adapter is None:
        return []
    return adapter.selector_attrs(text, selector, file_path)


def semantic_file_material(text: str, file_path: str) -> str:
    adapter = surface_adapter_for_file(file_path)
    if adapter is not None:
        return adapter.semantic_file_material(text, file_path)
    if strict_surface_mode():
        raise UnsupportedSurfaceError(f"Unsupported governed surface in strict mode: {file_path}")
    return text


def extract_balanced_brace_region(text: str, open_index: int) -> str:
    depth = 0
    end_index = len(text)
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end_index = index + 1
                break
    block_start = line_start(text, open_index)
    return text[block_start:end_index]


def extract_css_block_for_occurrence(text: str, occurrence: int) -> str:
    prior_open = text.rfind("{", 0, occurrence)
    prior_close = text.rfind("}", 0, occurrence)
    if prior_open != -1 and prior_open > prior_close:
        return extract_balanced_brace_region(text, prior_open)

    next_open = text.find("{", occurrence)
    if next_open != -1:
        return extract_balanced_brace_region(text, next_open)

    start = line_start(text, occurrence)
    end = line_end(text, occurrence)
    return text[start:end]


def extract_html_block_for_occurrence(text: str, occurrence: int) -> str:
    start_tag = text.rfind("<", 0, occurrence)
    if start_tag == -1:
        return text[line_start(text, occurrence):line_end(text, occurrence)]
    tag_close = text.find(">", start_tag)
    if tag_close == -1:
        return text[line_start(text, occurrence):line_end(text, occurrence)]

    tag_name_start = start_tag + 1
    while tag_name_start < len(text) and text[tag_name_start] in {"/", "!", "?"}:
        tag_name_start += 1
    tag_name_end = tag_name_start
    while tag_name_end < len(text) and (text[tag_name_end].isalnum() or text[tag_name_end] in {"-", ":"}):
        tag_name_end += 1
    tag_name = text[tag_name_start:tag_name_end]
    if not tag_name:
        return text[line_start(text, occurrence):line_end(text, occurrence)]

    close_tag = f"</{tag_name}>"
    close_index = text.find(close_tag, tag_close)
    if close_index == -1:
        return text[start_tag:tag_close + 1]
    return text[start_tag:close_index + len(close_tag)]


def extract_code_block_for_occurrence(text: str, occurrence: int) -> str:
    prior_open = text.rfind("{", 0, occurrence)
    prior_close = text.rfind("}", 0, occurrence)
    if prior_open != -1 and prior_open > prior_close:
        return extract_balanced_brace_region(text, prior_open)
    start = max(0, line_start(text, occurrence))
    end = len(text)
    lower_bound = start
    for _ in range(0, 6):
        lower_bound = text.rfind("\n", 0, lower_bound - 1) + 1 if lower_bound > 0 else 0
    upper_bound = line_end(text, occurrence)
    for _ in range(0, 6):
        next_break = text.find("\n", upper_bound + 1)
        if next_break == -1:
            upper_bound = len(text)
            break
        upper_bound = next_break
    return text[lower_bound:upper_bound]


def collect_scoped_material(text: str, pattern: str, file_path: str) -> str:
    if not text or not pattern:
        return ""
    adapter = surface_adapter_for_file(file_path)
    if adapter is not None:
        material = adapter.collect_selector_material(text, pattern, file_path)
        if material:
            return material
    elif strict_surface_mode():
        raise UnsupportedSurfaceError(f"Unsupported governed surface in strict mode: {file_path}")
    suffix = Path(file_path).suffix.lower()
    matches: list[str] = []
    seen = set()
    search_from = 0
    while True:
        occurrence = text.find(pattern, search_from)
        if occurrence == -1:
            break
        if suffix == ".css":
            snippet = extract_css_block_for_occurrence(text, occurrence)
        elif suffix in {".html", ".htm"}:
            snippet = extract_html_block_for_occurrence(text, occurrence)
        else:
            snippet = extract_code_block_for_occurrence(text, occurrence)
        if snippet and snippet not in seen:
            seen.add(snippet)
            matches.append(snippet)
        search_from = occurrence + len(pattern)
    return "\n---\n".join(matches)


def collect_copy_material(text: str, copy_block: str, file_path: str) -> str:
    if not text or not copy_block:
        return ""
    adapter = surface_adapter_for_file(file_path)
    if adapter is not None:
        material = adapter.collect_copy_material(text, copy_block, file_path)
        if material:
            return material
    elif strict_surface_mode():
        raise UnsupportedSurfaceError(f"Unsupported governed surface in strict mode: {file_path}")
    suffix = Path(file_path).suffix.lower()
    if copy_block not in text:
        return ""
    matches: list[str] = []
    seen = set()
    search_from = 0
    while True:
        occurrence = text.find(copy_block, search_from)
        if occurrence == -1:
            break
        if suffix in {".html", ".htm"}:
            snippet = extract_html_block_for_occurrence(text, occurrence)
        else:
            snippet = text[line_start(text, occurrence):line_end(text, occurrence)]
        if snippet and snippet not in seen:
            seen.add(snippet)
            matches.append(snippet)
        search_from = occurrence + len(copy_block)
    return "\n---\n".join(matches)


def read_contract_file_contents(contract: dict[str, Any]) -> dict[str, str]:
    file_contents: dict[str, str] = {}
    for file_path in contract_governed_files(contract):
        full_path = workspace_path(file_path)
        file_contents[file_path] = safe_read_text(full_path)
    return file_contents


def scoped_policy_file_contents(file_contents: dict[str, str], rule: dict[str, Any]) -> dict[str, str]:
    target_files = normalize_values(list(rule.get("files") or []))
    if not target_files:
        return dict(file_contents)
    return {file_path: contents for file_path, contents in file_contents.items() if file_path in target_files}


def html_selector_count(text: str, selector: str) -> int:
    soup = BeautifulSoup(text or "", "html.parser")
    try:
        return len(soup.select(selector))
    except Exception:
        return 0


def validate_contract_surface_coverage(contract: dict[str, Any]) -> list[str]:
    if not strict_surface_mode():
        return []
    failures = []
    for file_path in contract_governed_files(contract):
        if surface_adapter_for_file(file_path) is None:
            failures.append(f"unsupported governed surface in strict mode: {file_path}")
    return failures


def collect_surface_target_material(
    contract: dict[str, Any],
    file_contents: dict[str, str],
    target: dict[str, Any],
) -> str:
    kind = surface_target_kind(target)
    query = surface_target_query(target)
    target_files = surface_target_files(target) or contract_files(contract)
    materials: list[str] = []
    for file_path in target_files:
        contents = file_contents.get(file_path, "")
        if kind in {"copy", "copy_block"}:
            material = collect_copy_material(contents, query, file_path)
        elif kind in {"token", "css_token"}:
            material = collect_scoped_material(contents, query, file_path)
        else:
            material = collect_scoped_material(contents, query, file_path)
        if material:
            materials.append(material)
    return "\n".join(materials)


def evaluate_policy_rule(
    contract: dict[str, Any],
    rule: dict[str, Any],
    file_contents: dict[str, str],
    *,
    index: int,
) -> dict[str, Any]:
    rule_id = str(rule.get("rule_id") or f"{contract.get('contract_id')}.rule.{index}").strip()
    kind = str(rule.get("kind") or "").strip()
    scoped_contents = scoped_policy_file_contents(file_contents, rule)

    if not kind:
        return {
            "rule_id": rule_id,
            "kind": kind,
            "status": "fail",
            "reason": "policy rule is missing kind",
            "evidence": {},
        }

    if kind == "selector_style":
        selector = str(rule.get("selector") or "").strip()
        expects = dict(rule.get("expects") or {})
        if not selector or not expects:
            return {
                "rule_id": rule_id,
                "kind": kind,
                "status": "fail",
                "reason": "selector_style requires selector and expects",
                "evidence": {},
            }

        for file_path, contents in scoped_contents.items():
            rules = selector_style_entries(contents, selector, file_path)
            if not rules:
                continue
            for entry in rules:
                declarations = dict(entry.get("declarations") or {})
                matched = True
                actuals: dict[str, str] = {}
                for name, expected_value in expects.items():
                    actual_value = normalize_css_value((declarations.get(name) or {}).get("value") or "")
                    actuals[name] = actual_value
                    if actual_value != normalize_css_value(expected_value):
                        matched = False
                if matched:
                    return {
                        "rule_id": rule_id,
                        "kind": kind,
                        "status": "pass",
                        "reason": "",
                        "evidence": {
                            "file": file_path,
                            "selector": selector,
                            "actual": actuals,
                        },
                    }
        return {
            "rule_id": rule_id,
            "kind": kind,
            "status": "fail",
            "reason": f"selector_style did not match expected declarations for {selector}",
            "evidence": {"selector": selector, "expects": expects},
        }

    if kind == "html_count":
        selector = str(rule.get("selector") or "").strip()
        expects = int(rule.get("expects") or 0)
        count = 0
        matched_files: list[str] = []
        for file_path, contents in scoped_contents.items():
            file_count = selector_count(contents, selector, file_path)
            if file_count:
                matched_files.append(file_path)
            count += file_count
        status = "pass" if count == expects else "fail"
        return {
            "rule_id": rule_id,
            "kind": kind,
            "status": status,
            "reason": "" if status == "pass" else f"html_count expected {expects} for {selector}, found {count}",
            "evidence": {"selector": selector, "count": count, "files": matched_files},
        }

    if kind == "html_attr":
        selector = str(rule.get("selector") or "").strip()
        expects = dict(rule.get("expects") or {})
        for file_path, contents in scoped_contents.items():
            for attrs in selector_attrs(contents, selector, file_path):
                actuals: dict[str, str] = {}
                matched = True
                for attr_name, expected_value in expects.items():
                    actual_value = normalize_inline_whitespace(attrs.get(attr_name, ""))
                    actuals[attr_name] = actual_value
                    if actual_value != normalize_inline_whitespace(str(expected_value)):
                        matched = False
                if matched:
                    return {
                        "rule_id": rule_id,
                        "kind": kind,
                        "status": "pass",
                        "reason": "",
                        "evidence": {"file": file_path, "selector": selector, "actual": actuals},
                    }
        return {
            "rule_id": rule_id,
            "kind": kind,
            "status": "fail",
            "reason": f"html_attr did not match expected attributes for {selector}",
            "evidence": {"selector": selector, "expects": expects},
        }

    if kind == "file_contains":
        text = str(rule.get("text") or "")
        for file_path, contents in scoped_contents.items():
            if text and text in contents:
                return {
                    "rule_id": rule_id,
                    "kind": kind,
                    "status": "pass",
                    "reason": "",
                    "evidence": {"file": file_path, "text": text},
                }
        return {
            "rule_id": rule_id,
            "kind": kind,
            "status": "fail",
            "reason": f"file_contains could not find required text: {text}",
            "evidence": {"text": text},
        }

    return {
        "rule_id": rule_id,
        "kind": kind,
        "status": "fail",
        "reason": f"unknown policy rule kind: {kind}",
        "evidence": {},
    }


def evaluate_policy_checks(contract: dict[str, Any], file_contents: dict[str, str]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for index, rule in enumerate(contract_policy_checks(contract), start=1):
        result = evaluate_policy_rule(contract, rule, file_contents, index=index)
        results[result["rule_id"]] = result
    return results


def known_workspace_files(registry: dict[str, Any]) -> list[str]:
    impact_map = registry_impact_map(registry)
    file_map = dict(impact_map.get("file_to_contracts") or {})
    return normalize_values(list(file_map.keys()))


def file_hash(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_contract_proof(
    contract: dict[str, Any],
    *,
    include_file_contents: bool = False,
    approved_by: str = "",
    reason: str = "",
    visual_proof_paths: list[str] | None = None,
    compare_visual_to_approved: bool = False,
    override_visual_base_url: str = "",
) -> dict[str, Any]:
    owner_files = contract_files(contract)
    selectors = contract_selectors(contract)
    tokens = contract_tokens(contract)
    copy_blocks = contract_copy_blocks(contract)
    surface_targets = contract_surface_targets(contract)

    file_contents = read_contract_file_contents(contract)
    file_hashes: dict[str, str] = {}
    exact_file_hashes: dict[str, str] = {}
    for file_path in owner_files:
        full_path = workspace_path(file_path)
        contents = file_contents[file_path]
        file_hashes[file_path] = sha256_text(semantic_file_material(contents, file_path)) if contents else "missing"
        exact_file_hashes[file_path] = file_hash(full_path)

    selector_hashes: dict[str, str] = {}
    for selector in selectors:
        scoped = "\n".join(
            collect_scoped_material(file_contents[file_path], selector, file_path)
            for file_path in owner_files
        )
        selector_hashes[selector] = sha256_text(scoped)

    token_hashes: dict[str, str] = {}
    for token in tokens:
        scoped = "\n".join(
            collect_scoped_material(file_contents[file_path], token, file_path)
            for file_path in owner_files
        )
        token_hashes[token] = sha256_text(scoped)

    copy_hashes: dict[str, str] = {}
    for copy_block in copy_blocks:
        scoped = "\n".join(
            collect_copy_material(file_contents[file_path], copy_block, file_path)
            for file_path in owner_files
        )
        copy_hashes[copy_block] = sha256_text(scoped or "missing")

    surface_hashes: dict[str, str] = {}
    for index, target in enumerate(surface_targets, start=1):
        signature = surface_target_signature(target, index=index)
        scoped = collect_surface_target_material(contract, file_contents, target)
        surface_hashes[signature] = sha256_text(scoped or "missing")

    visual_hashes: dict[str, str] = {}
    for visual_path in normalize_values(list(visual_proof_paths or [])):
        full_path = workspace_path(visual_path)
        visual_hashes[visual_path] = file_hash(full_path)

    visual_results = capture_visual_proof_results(
        contract,
        compare_to_baseline=compare_visual_to_approved,
        override_base_url=override_visual_base_url,
    )
    comparable_visual_results = comparable_visual_proof_results(visual_results)
    for proof_id, result in visual_results.items():
        visual_hashes[proof_id] = str(result.get("artifact_hash") or result.get("status") or "")

    policy_results = evaluate_policy_checks(contract, file_contents)

    proof_core = {
        "contract_id": str(contract.get("contract_id") or ""),
        "file_hashes": file_hashes,
        "exact_file_hashes": exact_file_hashes,
        "selector_hashes": selector_hashes,
        "token_hashes": token_hashes,
        "copy_hashes": copy_hashes,
        "surface_hashes": surface_hashes,
        "policy_results": policy_results,
        "visual_proof_hashes": visual_hashes,
        "visual_proof_results": comparable_visual_results,
        "proof_runner": "playwright" if visual_results else "",
    }
    composite_hash = sha256_text(json.dumps(proof_core, sort_keys=True))
    payload: dict[str, Any] = {
        **proof_core,
        "composite_hash": composite_hash,
        "captured_at": utc_now(),
        "approved_by": str(approved_by or "").strip(),
        "reason": str(reason or "").strip(),
    }
    if include_file_contents:
        payload["file_contents"] = file_contents
    if visual_results:
        payload["visual_proof_results_full"] = visual_results
    return payload


@dataclass(slots=True)
class AffectedContract:
    contract_id: str
    design_name: str
    reasons: list[str]


@dataclass(slots=True)
class BlockedTouch:
    contract_id: str
    design_name: str
    reasons: list[str]
    blocked_edits: list[str]


@dataclass(slots=True)
class StaleLock:
    contract_id: str
    design_name: str
    reasons: list[str]


@dataclass(slots=True)
class PolicyFailure:
    contract_id: str
    design_name: str
    rule_id: str
    kind: str
    reason: str


def append_reason(reasons: list[str], label: str, values: list[str]) -> None:
    if values:
        reasons.append(f"{label}: {', '.join(values)}")


def contracts_for_file(impact_map: dict[str, dict[str, list[str]]], file_path: str) -> list[str]:
    file_map = dict(impact_map.get("file_to_contracts") or {})
    return normalize_contract_ids(list(file_map.get(file_path) or []))


def contracts_for_token(impact_map: dict[str, dict[str, list[str]]], token: str) -> list[str]:
    token_map = dict(impact_map.get("token_to_contracts") or {})
    return normalize_contract_ids(list(token_map.get(token) or []))


def contracts_for_selector(impact_map: dict[str, dict[str, list[str]]], selector: str) -> list[str]:
    selector_map = dict(impact_map.get("selector_to_contracts") or {})
    return normalize_contract_ids(list(selector_map.get(selector) or []))


def contracts_for_surface(impact_map: dict[str, dict[str, list[str]]], surface: str) -> list[str]:
    surface_map = dict(impact_map.get("surface_to_contracts") or {})
    return normalize_contract_ids(list(surface_map.get(surface) or []))


def request_has_planned_touches(request: dict[str, Any]) -> bool:
    return any(
        normalize_values(list(request.get(field) or []))
        for field in (
            "planned_file_touches",
            "planned_token_touches",
            "planned_selector_touches",
            "planned_surface_touches",
        )
    )


def request_contract_ids(registry: dict[str, Any], request: dict[str, Any]) -> list[str]:
    contract_ids: list[str] = []
    seen = set()
    for affected in resolve_affected_contracts(registry, request):
        if affected.contract_id in seen:
            continue
        seen.add(affected.contract_id)
        contract_ids.append(affected.contract_id)
    for contract_id in normalize_contract_ids(list(request.get("requested_contract_ids") or [])):
        if contract_id in seen:
            continue
        seen.add(contract_id)
        contract_ids.append(contract_id)
    return contract_ids


def resolve_affected_contracts(registry: dict[str, Any], request: dict[str, Any]) -> list[AffectedContract]:
    impact_map = registry_impact_map(registry)
    planned_files = normalize_values(list(request.get("planned_file_touches") or []))
    planned_tokens = normalize_values(list(request.get("planned_token_touches") or []))
    planned_selectors = normalize_values(list(request.get("planned_selector_touches") or []))
    planned_surfaces = normalize_values(list(request.get("planned_surface_touches") or []))
    by_contract: dict[str, AffectedContract] = {}

    def upsert(contract_id: str, design_name: str) -> AffectedContract:
        existing = by_contract.get(contract_id)
        if existing:
            return existing
        item = AffectedContract(contract_id=contract_id, design_name=design_name, reasons=[])
        by_contract[contract_id] = item
        return item

    for file_path in planned_files:
        contract_ids = contracts_for_file(impact_map, file_path)
        for contract_id in contract_ids:
            contract = contract_by_id(registry, contract_id)
            item = upsert(contract_id, str(contract.get("design_name") or ""))
            append_reason(item.reasons, "files", [file_path])

    for token in planned_tokens:
        contract_ids = contracts_for_token(impact_map, token)
        for contract_id in contract_ids:
            contract = contract_by_id(registry, contract_id)
            item = upsert(contract_id, str(contract.get("design_name") or ""))
            append_reason(item.reasons, "tokens", [token])

    for selector in planned_selectors:
        contract_ids = contracts_for_selector(impact_map, selector)
        for contract_id in contract_ids:
            contract = contract_by_id(registry, contract_id)
            item = upsert(contract_id, str(contract.get("design_name") or ""))
            append_reason(item.reasons, "selectors", [selector])

    for surface in planned_surfaces:
        contract_ids = contracts_for_surface(impact_map, surface)
        for contract_id in contract_ids:
            contract = contract_by_id(registry, contract_id)
            item = upsert(contract_id, str(contract.get("design_name") or ""))
            append_reason(item.reasons, "surfaces", [surface])

    return list(by_contract.values())


def blocked_touches(registry: dict[str, Any], request: dict[str, Any]) -> list[BlockedTouch]:
    named_contracts = set(normalize_contract_ids(list(request.get("requested_contract_ids") or [])))
    affected = resolve_affected_contracts(registry, request)
    blocked: list[BlockedTouch] = []
    for item in affected:
        contract = contract_by_id(registry, item.contract_id)
        if str(contract.get("status") or "").lower() != "locked":
            continue
        if item.contract_id in named_contracts:
            continue
        blocked.append(
            BlockedTouch(
                contract_id=item.contract_id,
                design_name=item.design_name,
                reasons=item.reasons,
                blocked_edits=normalize_values(list(contract.get("blocked_edits") or [])),
            )
        )
    return blocked


def policy_failures_for_contract(contract: dict[str, Any]) -> list[PolicyFailure]:
    file_contents = read_contract_file_contents(contract)
    failures: list[PolicyFailure] = []
    for index, reason in enumerate(validate_contract_surface_coverage(contract), start=1):
        failures.append(
            PolicyFailure(
                contract_id=str(contract.get("contract_id") or ""),
                design_name=str(contract.get("design_name") or ""),
                rule_id=f"{contract.get('contract_id')}.surface.{index}",
                kind="surface_adapter",
                reason=reason,
            )
        )
    results = evaluate_policy_checks(contract, file_contents)
    for rule_id, result in results.items():
        if str(result.get("status") or "") == "pass":
            continue
        failures.append(
            PolicyFailure(
                contract_id=str(contract.get("contract_id") or ""),
                design_name=str(contract.get("design_name") or ""),
                rule_id=rule_id,
                kind=str(result.get("kind") or ""),
                reason=str(result.get("reason") or "policy rule failed"),
            )
        )
    return failures


def policy_failures_for_request(registry: dict[str, Any], request: dict[str, Any]) -> list[PolicyFailure]:
    failures: list[PolicyFailure] = []
    seen = set()
    named_contracts = normalize_contract_ids(list(request.get("requested_contract_ids") or []))

    if named_contracts and not request_has_planned_touches(request):
        failures.append(
            PolicyFailure(
                contract_id="(request)",
                design_name="Change Request",
                rule_id=f"{str(request.get('request_id') or 'unnamed-request').strip() or 'unnamed-request'}.planned_touches",
                kind="request_scope",
                reason="request names contract ids but declares no planned touches",
            )
        )

    for contract_id in named_contracts:
        try:
            contract_by_id(registry, contract_id)
        except KeyError:
            failures.append(
                PolicyFailure(
                    contract_id=contract_id,
                    design_name="Unknown Contract",
                    rule_id=f"{contract_id}.exists",
                    kind="request_scope",
                    reason=f"requested contract id is not in the registry: {contract_id}",
                )
            )

    for contract_id in request_contract_ids(registry, request):
        try:
            contract = contract_by_id(registry, contract_id)
        except KeyError:
            continue
        for failure in policy_failures_for_contract(contract):
            key = (failure.contract_id, failure.rule_id)
            if key in seen:
                continue
            seen.add(key)
            failures.append(failure)
    return failures


def compare_live_to_approved(contract: dict[str, Any]) -> StaleLock | None:
    if str(contract.get("status") or "").lower() != "locked":
        return None

    approved = approved_proof(contract)
    if not approved.get("composite_hash"):
        return StaleLock(
            contract_id=str(contract.get("contract_id") or ""),
            design_name=str(contract.get("design_name") or ""),
            reasons=["locked contract has no approved proof baseline"],
        )

    try:
        live = build_contract_proof(contract, compare_visual_to_approved=True)
    except UnsupportedSurfaceError as exc:
        return StaleLock(
            contract_id=str(contract.get("contract_id") or ""),
            design_name=str(contract.get("design_name") or ""),
            reasons=[str(exc)],
        )
    reasons: list[str] = []

    approved_files = dict(approved.get("file_hashes") or {})
    live_files = dict(live.get("file_hashes") or {})
    changed_files = sorted(
        file_path
        for file_path in set(approved_files) | set(live_files)
        if approved_files.get(file_path) != live_files.get(file_path)
    )
    if changed_files:
        reasons.append(f"file proof changed: {', '.join(changed_files)}")

    approved_selectors = dict(approved.get("selector_hashes") or {})
    live_selectors = dict(live.get("selector_hashes") or {})
    changed_selectors = sorted(
        selector
        for selector in set(approved_selectors) | set(live_selectors)
        if approved_selectors.get(selector) != live_selectors.get(selector)
    )
    if changed_selectors:
        reasons.append(f"selector proof changed: {', '.join(changed_selectors)}")

    approved_tokens = dict(approved.get("token_hashes") or {})
    live_tokens = dict(live.get("token_hashes") or {})
    changed_tokens = sorted(
        token
        for token in set(approved_tokens) | set(live_tokens)
        if approved_tokens.get(token) != live_tokens.get(token)
    )
    if changed_tokens:
        reasons.append(f"token proof changed: {', '.join(changed_tokens)}")

    approved_copy = dict(approved.get("copy_hashes") or {})
    live_copy = dict(live.get("copy_hashes") or {})
    changed_copy = sorted(
        block
        for block in set(approved_copy) | set(live_copy)
        if approved_copy.get(block) != live_copy.get(block)
    )
    if changed_copy:
        reasons.append(f"copy proof changed: {', '.join(changed_copy)}")

    approved_surfaces = dict(approved.get("surface_hashes") or {})
    live_surfaces = dict(live.get("surface_hashes") or {})
    changed_surfaces = sorted(
        surface
        for surface in set(approved_surfaces) | set(live_surfaces)
        if approved_surfaces.get(surface) != live_surfaces.get(surface)
    )
    if changed_surfaces:
        reasons.append(f"surface proof changed: {', '.join(changed_surfaces)}")

    approved_visual = dict(approved.get("visual_proof_hashes") or {})
    live_visual = dict(live.get("visual_proof_hashes") or {})
    changed_visual = sorted(
        proof_id
        for proof_id in set(approved_visual) | set(live_visual)
        if approved_visual.get(proof_id) != live_visual.get(proof_id)
    )
    if changed_visual:
        reasons.append(f"visual proof changed: {', '.join(changed_visual)}")

    live_visual_results = dict(live.get("visual_proof_results") or {})
    visual_failures = [
        f"{proof_id}: {str(result.get('reason') or 'visual proof failed')}"
        for proof_id, result in sorted(live_visual_results.items())
        if str(result.get("status") or "") not in {"pass", "skip"}
    ]
    for failure in visual_failures:
        reasons.append(f"visual proof failed: {failure}")

    approved_policy = dict(approved.get("policy_results") or {})
    live_policy = dict(live.get("policy_results") or {})
    changed_policy = sorted(
        rule_id
        for rule_id in set(approved_policy) | set(live_policy)
        if approved_policy.get(rule_id) != live_policy.get(rule_id)
    )
    if changed_policy:
        reasons.append(f"policy proof changed: {', '.join(changed_policy)}")

    if not reasons and approved.get("composite_hash") != live.get("composite_hash"):
        reasons.append("approved proof does not match live proof")

    if not reasons:
        return None

    return StaleLock(
        contract_id=str(contract.get("contract_id") or ""),
        design_name=str(contract.get("design_name") or ""),
        reasons=reasons,
    )


def refresh_proof_status(registry: dict[str, Any]) -> list[StaleLock]:
    stale_locks: list[StaleLock] = []
    changed = False
    for contract in locked_contracts(registry):
        stale = compare_live_to_approved(contract)
        if stale is None:
            if contract.get("proof_status") != "approved" or contract.get("stale_reasons"):
                contract["proof_status"] = "approved"
                contract["stale_detected_at"] = ""
                contract["stale_reasons"] = []
                changed = True
            continue
        stale_locks.append(stale)
        contract["proof_status"] = "stale"
        contract["stale_detected_at"] = utc_now()
        contract["stale_reasons"] = stale.reasons
        changed = True

    if changed:
        save_json(REGISTRY_PATH, registry)
    return stale_locks


def snapshot_name_for(contract_id: str) -> str:
    return f"{contract_id.replace('.', '_')}.json"


def approve_contract_state(
    registry: dict[str, Any],
    contract: dict[str, Any],
    *,
    approved_by: str,
    reason: str,
    visual_proof_paths: list[str],
    route: str = "",
    state_name: str = "",
    notes: str = "",
    lock_after: bool = False,
) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    failures = policy_failures_for_contract(contract)
    if failures:
        joined = "; ".join(f"{item.rule_id}: {item.reason}" for item in failures)
        raise ValueError(f"Policy failure: {joined}")
    proof = build_contract_proof(
        contract,
        include_file_contents=True,
        approved_by=approved_by,
        reason=reason,
        visual_proof_paths=visual_proof_paths,
    )
    visual_failures = [
        f"{proof_id}: {str(result.get('reason') or 'visual proof failed')}"
        for proof_id, result in sorted((proof.get("visual_proof_results") or {}).items())
        if str(result.get("status") or "") not in {"pass", "skip"}
    ]
    if visual_failures:
        raise ValueError("Visual proof failed: " + "; ".join(visual_failures))
    snapshot_payload = {
        "contract_id": str(contract.get("contract_id") or ""),
        "captured_at": utc_now(),
        "approved_by": str(approved_by or "").strip(),
        "reason": str(reason or "").strip(),
        "route": str(route or "").strip(),
        "state_name": str(state_name or "").strip(),
        "notes": str(notes or "").strip(),
        "proof_runner": str(proof.get("proof_runner") or ""),
        "visual_proof_results": dict(proof.get("visual_proof_results_full") or proof.get("visual_proof_results") or {}),
        "proof": proof,
    }
    snapshot_path = SNAPSHOT_DIR / snapshot_name_for(str(contract.get("contract_id") or ""))
    save_json(snapshot_path, snapshot_payload)

    contract["approved_proof"] = {
        "captured_at": proof["captured_at"],
        "approved_by": proof.get("approved_by") or "",
        "reason": proof.get("reason") or "",
        "file_hashes": dict(proof.get("file_hashes") or {}),
        "exact_file_hashes": dict(proof.get("exact_file_hashes") or {}),
        "selector_hashes": dict(proof.get("selector_hashes") or {}),
        "token_hashes": dict(proof.get("token_hashes") or {}),
        "copy_hashes": dict(proof.get("copy_hashes") or {}),
        "surface_hashes": dict(proof.get("surface_hashes") or {}),
        "policy_results": dict(proof.get("policy_results") or {}),
        "visual_proof_hashes": dict(proof.get("visual_proof_hashes") or {}),
        "visual_proof_results": dict(proof.get("visual_proof_results_full") or proof.get("visual_proof_results") or {}),
        "proof_runner": str(proof.get("proof_runner") or ""),
        "composite_hash": proof["composite_hash"],
    }
    contract["last_approved_hash"] = proof["composite_hash"]
    contract["last_approved_snapshot"] = snapshot_path.name
    contract["proof_status"] = "approved"
    contract["stale_detected_at"] = ""
    contract["stale_reasons"] = []
    if lock_after:
        contract["status"] = "locked"
    contract["updated_at"] = utc_now()
    contract.setdefault("approved_notes", []).append(
        {
            "written_at": utc_now(),
            "note": str(reason or "Approved state captured.").strip(),
        }
    )
    save_json(REGISTRY_PATH, registry)
    return snapshot_path


def explanation_modes(config: dict[str, Any] | None = None) -> list[str]:
    payload = config if config is not None else load_optional_project_config()
    configured = normalize_values(list(payload.get("available_modes") or []))
    if not configured:
        return ["plain", "expert", "ci"]
    return [mode.lower() for mode in configured]


def configured_explanation_mode(config: dict[str, Any] | None = None) -> str:
    payload = config if config is not None else load_optional_project_config()
    mode = str(payload.get("explanation_mode") or "plain").strip().lower()
    return mode if mode in explanation_modes(payload) else "plain"


def selected_output_mode(args: argparse.Namespace, config: dict[str, Any] | None = None) -> str:
    if getattr(args, "json_output", False):
        return "ci"
    if getattr(args, "expert", False):
        return "expert"
    return configured_explanation_mode(config)


def plain_technical_details_enabled(config: dict[str, Any] | None = None) -> bool:
    payload = config if config is not None else load_optional_project_config()
    return bool(payload.get("show_technical_details", False))


def serialize_affected_contract(item: AffectedContract) -> dict[str, Any]:
    return {"contract_id": item.contract_id, "design_name": item.design_name, "reasons": list(item.reasons)}


def serialize_blocked_touch(item: BlockedTouch) -> dict[str, Any]:
    return {
        "contract_id": item.contract_id,
        "design_name": item.design_name,
        "reasons": list(item.reasons),
        "blocked_edits": list(item.blocked_edits),
    }


def serialize_stale_lock(item: StaleLock) -> dict[str, Any]:
    return {"contract_id": item.contract_id, "design_name": item.design_name, "reasons": list(item.reasons)}


def serialize_policy_failure(item: PolicyFailure) -> dict[str, Any]:
    return {
        "contract_id": item.contract_id,
        "design_name": item.design_name,
        "rule_id": item.rule_id,
        "kind": item.kind,
        "reason": item.reason,
    }


def build_check_payload(
    request_path: Path,
    request: dict[str, Any],
    affected: list[AffectedContract],
    blocked: list[BlockedTouch],
    stale_locks: list[StaleLock],
    policy_failures: list[PolicyFailure],
) -> dict[str, Any]:
    status = "pass" if not blocked and not stale_locks and not policy_failures else "blocked"
    return {
        "command": "check",
        "request_path": str(request_path),
        "summary": str(request.get("summary") or ""),
        "affected_contract_count": len(affected),
        "affected_contracts": [serialize_affected_contract(item) for item in affected],
        "blocked_touches": [serialize_blocked_touch(item) for item in blocked],
        "stale_locks": [serialize_stale_lock(item) for item in stale_locks],
        "policy_failures": [serialize_policy_failure(item) for item in policy_failures],
        "status": status,
        "exit_code": 0 if status == "pass" else 2,
    }


def build_gate_payload(
    request_path: Path,
    request: dict[str, Any],
    *,
    phase: str,
    changed_files: list[str],
    blocked: list[BlockedTouch],
    stale_locks: list[StaleLock],
    policy_failures: list[PolicyFailure],
    unexpected_files: list[str],
) -> dict[str, Any]:
    status = "pass" if not stale_locks and not blocked and not policy_failures and not unexpected_files else "blocked"
    return {
        "command": "gate",
        "request_path": str(request_path),
        "summary": str(request.get("summary") or ""),
        "phase": phase,
        "changed_files": list(changed_files),
        "blocked_touches": [serialize_blocked_touch(item) for item in blocked],
        "stale_locks": [serialize_stale_lock(item) for item in stale_locks],
        "policy_failures": [serialize_policy_failure(item) for item in policy_failures],
        "unexpected_files": list(unexpected_files),
        "status": status,
        "exit_code": 0 if status == "pass" else 2,
    }


def print_json_payload(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2))
    return int(payload.get("exit_code") or 0)


def print_check_report(
    request_path: Path,
    request: dict[str, Any],
    affected: list[AffectedContract],
    blocked: list[BlockedTouch],
    stale_locks: list[StaleLock],
    policy_failures: list[PolicyFailure],
    *,
    mode: str = "expert",
    config: dict[str, Any] | None = None,
) -> int:
    payload = build_check_payload(request_path, request, affected, blocked, stale_locks, policy_failures)
    payload["mode"] = mode
    if mode == "ci":
        return print_json_payload(payload)
    if mode == "plain":
        print("Passed" if payload["status"] == "pass" else "Blocked")
        print("")
        if payload["status"] == "pass":
            print("This request stays inside the named design area.")
            print("")
            print("What happened:")
            print("The governor did not find a locked area outside your request.")
            print("")
            print("Next safe action:")
            print("You can continue with this change.")
        else:
            print("This change would break a protected design rule.")
            print("")
            print("What happened:")
            if stale_locks:
                print("A saved proof no longer matches the live design.")
            elif blocked:
                print("This request reaches into a locked area that was not named first.")
            else:
                print("A locked design rule failed during the check.")
            print("")
            print("Next safe action:")
            if stale_locks:
                print("Refresh the stale lock before making more design changes.")
            elif blocked:
                print("Shrink the change back to the named area, or name the contract you intend to change.")
            else:
                print("Fix the design rule that failed, or create a new request for the contract you mean to update.")
        print("")
        print("Technical details:")
        if plain_technical_details_enabled(config):
            print(f"Affected contracts: {payload['affected_contract_count']}")
            for item in payload["policy_failures"]:
                print(
                    f"Contract: {item['contract_id']} | Rule: {item['rule_id']} | "
                    f"Kind: {item['kind']} | Reason: {item['reason']}"
                )
        else:
            print("Run again with --expert for exact contract ids, rules, selectors, and files.")
        return int(payload["exit_code"])

    print(f"Change Request: {request_path}")
    print(f"Summary: {request.get('summary') or ''}")
    print("")
    print(f"Affected contracts: {len(affected)}")
    if not blocked and not stale_locks and not policy_failures:
        print("PASS")
        print("No locked contract outside the request would be changed.")
        return 0
    print("BLOCKED")
    if stale_locks:
        print("Locked proof drift was found. Review and refresh the lock.")
        print("")
        for item in stale_locks:
            print(f"Stale contract: {item.contract_id}")
            print(f"Design: {item.design_name}")
            for reason in item.reasons:
                print(reason)
            print("")
    if blocked:
        print("Locked contracts would be changed. Adjust course and report.")
        print("")
        for item in blocked:
            print(f"Contract: {item.contract_id}")
            print(f"Design: {item.design_name}")
            for reason in item.reasons:
                print(reason)
            if item.blocked_edits:
                print(f"Blocked edits: {', '.join(item.blocked_edits)}")
            print("")
    if policy_failures:
        print("Machine policy checks failed.")
        print("")
        for item in policy_failures:
            print(f"Contract: {item.contract_id}")
            print(f"Design: {item.design_name}")
            print(f"Rule: {item.rule_id} | {item.kind}")
            print(item.reason)
            print("")
    return 2


def print_gate_report(
    request_path: Path,
    request: dict[str, Any],
    *,
    phase: str,
    changed_files: list[str],
    blocked: list[BlockedTouch],
    stale_locks: list[StaleLock],
    policy_failures: list[PolicyFailure],
    unexpected_files: list[str],
    mode: str = "expert",
    config: dict[str, Any] | None = None,
) -> int:
    payload = build_gate_payload(
        request_path,
        request,
        phase=phase,
        changed_files=changed_files,
        blocked=blocked,
        stale_locks=stale_locks,
        policy_failures=policy_failures,
        unexpected_files=unexpected_files,
    )
    payload["mode"] = mode
    if mode == "ci":
        return print_json_payload(payload)
    if mode == "plain":
        print("Passed" if payload["status"] == "pass" else "Blocked")
        print("")
        if payload["status"] == "pass":
            if phase == "start":
                print("The gate is open. Only the approved area should move now.")
            else:
                print("The gate is closed and the final files stayed inside the allowed area.")
            print("")
            print("What happened:")
            print("The governor did not find any new design drift in this gate step.")
            print("")
            print("Next safe action:")
            print("Continue only inside the named change area.")
        else:
            print("The gate found design drift outside the safe boundary.")
            print("")
            print("What happened:")
            if stale_locks:
                print("A saved proof no longer matches the live design.")
            elif unexpected_files:
                print("Files changed outside the plan.")
            elif blocked:
                print("A locked area outside the request would move.")
            else:
                print("A locked design rule failed during the gate check.")
            print("")
            print("Next safe action:")
            print("Bring the change back inside the request, then run the gate again.")
        print("")
        print("Technical details:")
        if plain_technical_details_enabled(config):
            print(f"Phase: {phase}")
            print(f"Changed files: {', '.join(changed_files) or '(none)'}")
            if unexpected_files:
                print(f"Unexpected files: {', '.join(unexpected_files)}")
        else:
            print("Run again with --expert for exact contracts, files, and rule details.")
        return int(payload["exit_code"])

    print(f"Gate request: {request_path}")
    print(f"Summary: {request.get('summary') or ''}")
    print(f"Phase: {phase}")
    print("")
    if stale_locks or blocked or policy_failures or unexpected_files:
        print("BLOCKED")
        if stale_locks:
            print("Locked proof drift was found.")
            print("")
            for item in stale_locks:
                print(f"Stale contract: {item.contract_id}")
                for reason in item.reasons:
                    print(reason)
                print("")
        if blocked:
            print("Locked contracts outside the request would be changed.")
            print("")
            for item in blocked:
                print(f"Contract: {item.contract_id}")
                for reason in item.reasons:
                    print(reason)
                print("")
        if policy_failures:
            print("Machine policy checks failed.")
            print("")
            for item in policy_failures:
                print(f"Contract: {item.contract_id}")
                print(f"Rule: {item.rule_id} | {item.kind}")
                print(item.reason)
                print("")
        if unexpected_files:
            print(f"Unexpected changed files: {', '.join(unexpected_files)}")
        return 2
    print("PASS")
    if phase == "start":
        print("Gate opened. Only approved files may move.")
    else:
        print("Gate closed. Final files stayed inside the allowed area.")
        print(f"Changed files: {', '.join(changed_files) or '(none)'}")
    return 0


def print_status(registry: dict[str, Any]) -> int:
    print(f"Website Design Governor Registry: {REGISTRY_PATH}")
    print("")
    for contract in registry_contracts(registry):
        print(f"{contract['contract_id']} | {contract['status']} | {contract['design_name']}")
        print(f"Scope: {contract['contract_name']}")
        print(f"Files: {', '.join(contract_files(contract))}")
        print(f"Tokens: {', '.join(contract_tokens(contract)) or '(none)'}")
        print(f"Selectors: {', '.join(contract_selectors(contract)) or '(none)'}")
        print(
            "Surfaces: "
            + (
                ", ".join(
                    surface_target_signature(target, index=index)
                    for index, target in enumerate(contract_surface_targets(contract), start=1)
                )
                or "(none)"
            )
        )
        print(f"States: {', '.join(contract_states(contract)) or '(none)'}")
        print(f"Snapshot: {contract.get('last_approved_snapshot') or '(none)'}")
        print(f"Proof: {proof_status(contract)}")
        print("")
    return 0


def print_diff(request_path: Path, request: dict[str, Any], affected: list[AffectedContract]) -> int:
    print(f"Change Request: {request_path}")
    print(f"Summary: {request.get('summary') or ''}")
    print("")
    if not affected:
        print("No contracts were matched by the impact map.")
        return 0
    print("Affected contracts:")
    print("")
    for item in affected:
        print(f"{item.contract_id} | {item.design_name}")
        for reason in item.reasons:
            print(reason)
        print("")
    return 0


def changed_known_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        file_path
        for file_path in set(before) | set(after)
        if before.get(file_path) != after.get(file_path)
    )


def build_known_file_hashes(registry: dict[str, Any]) -> dict[str, str]:
    return {
        file_path: file_hash(workspace_path(file_path))
        for file_path in known_workspace_files(registry)
    }


def run_file_path(request: dict[str, Any]) -> Path:
    request_id = str(request.get("request_id") or "unnamed-request").strip() or "unnamed-request"
    safe_name = request_id.replace("/", "_").replace("\\", "_")
    return RUN_DIR / f"{safe_name}.json"


def blocked_release_reason(path_text: str, *, allow_egg_info: bool = False) -> str:
    normalized = str(path_text or "").replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part]
    for part in parts:
        if part in BLOCKED_RELEASE_PARTS:
            return f"blocked generated path: {normalized}"
        if part.endswith(".egg-info") and not allow_egg_info:
            return f"blocked egg-info path: {normalized}"
    leaf = parts[-1] if parts else normalized
    if leaf.endswith(BLOCKED_RELEASE_SUFFIXES):
        return f"blocked compiled file: {normalized}"
    return ""


def audit_release_tree(root: Path) -> list[str]:
    problems: list[str] = []
    for path in root.rglob("*"):
        relative = str(path.relative_to(root)).replace("\\", "/")
        if relative.startswith(".git/"):
            continue
        if any(relative == prefix or relative.startswith(prefix + "/") for prefix in IGNORED_RELEASE_AUDIT_PATH_PREFIXES):
            continue
        reason = blocked_release_reason(relative)
        if reason:
            problems.append(reason)
    return sorted(set(problems))


def audit_release_artifact(artifact_path: Path) -> list[str]:
    problems: list[str] = []
    suffixes = artifact_path.suffixes
    if artifact_path.suffix == ".whl" or artifact_path.suffix == ".zip":
        with zipfile.ZipFile(artifact_path) as archive:
            for member in archive.namelist():
                reason = blocked_release_reason(member, allow_egg_info=True)
                if reason:
                    problems.append(reason)
    elif suffixes[-2:] == [".tar", ".gz"] or artifact_path.suffix in {".tgz", ".tar"}:
        with tarfile.open(artifact_path) as archive:
            for member in archive.getnames():
                reason = blocked_release_reason(member, allow_egg_info=True)
                if reason:
                    problems.append(reason)
    return sorted(set(problems))


def missing_build_release_message() -> str:
    return (
        "Release audit needs the build package.\n"
        "Plain-English fix:\n"
        "Run: python -m pip install build\n"
        "Or install release tools with:\n"
        "pip install \"design-governor[release]\""
    )


def build_release_artifacts(project_root: Path, output_dir: Path) -> list[Path]:
    build_source = output_dir / "_release_build_source"
    shutil.copytree(project_root, build_source)
    command = [
        sys.executable,
        "-m",
        "build",
        "--sdist",
        "--wheel",
        "--outdir",
        str(output_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=str(build_source),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        if "No module named build" in output:
            raise RuntimeError(missing_build_release_message())
        raise RuntimeError(output or "release build failed")
    return sorted(path for path in output_dir.iterdir() if path.is_file())


def release_audit_payload(repo_root: Path, project_root: Path) -> dict[str, Any]:
    repo_problems = audit_release_tree(repo_root)
    artifact_reports: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        artifacts = build_release_artifacts(project_root, Path(temp_dir))
        for artifact in artifacts:
            artifact_reports.append(
                {
                    "artifact": artifact.name,
                    "problems": audit_release_artifact(artifact),
                }
            )
    blocked = bool(repo_problems or any(report["problems"] for report in artifact_reports))
    return {
        "command": "release-audit",
        "repo_root": str(repo_root),
        "project_root": str(project_root),
        "repo_problems": repo_problems,
        "artifact_reports": artifact_reports,
        "status": "blocked" if blocked else "pass",
        "exit_code": 2 if blocked else 0,
    }


def cmd_init(args: argparse.Namespace) -> int:
    if REGISTRY_PATH.exists() and not args.force:
        raise FileExistsError(f"Registry already exists: {REGISTRY_PATH}")
    validate_boot_templates()
    payload = load_packaged_json("design_governor_registry.template.json")
    payload["saved_at"] = utc_now()
    payload["workspace_root"] = str(ROOT_RESOLVED)
    payload = refresh_impact_map(payload, persist=False)
    save_json(REGISTRY_PATH, payload)
    written = write_project_boot_files(force=args.force)
    written.extend(write_project_automation_files(force=args.force))
    print(f"Initialized registry: {REGISTRY_PATH}")
    for path in written:
        if path == REGISTRY_PATH:
            continue
        print(f"Wrote starter file: {path}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    registry = load_json(REGISTRY_PATH)
    registry = refresh_impact_map(registry, persist=True)
    refresh_proof_status(registry)
    registry = load_json(REGISTRY_PATH)
    return print_status(registry)


def cmd_diff(args: argparse.Namespace) -> int:
    registry = load_json(REGISTRY_PATH)
    registry = refresh_impact_map(registry, persist=True)
    request_path = Path(args.request).resolve()
    request = load_json(request_path)
    affected = resolve_affected_contracts(registry, request)
    return print_diff(request_path, request, affected)


def cmd_check(args: argparse.Namespace) -> int:
    registry = load_json(REGISTRY_PATH)
    registry = refresh_impact_map(registry, persist=True)
    request_path = Path(args.request).resolve()
    request = load_json(request_path)
    config = load_optional_project_config()
    mode = selected_output_mode(args, config)
    stale_locks = refresh_proof_status(registry)
    registry = load_json(REGISTRY_PATH)
    affected = resolve_affected_contracts(registry, request)
    blocked = blocked_touches(registry, request)
    policy_failures = policy_failures_for_request(registry, request)
    return print_check_report(
        request_path,
        request,
        affected,
        blocked,
        stale_locks,
        policy_failures,
        mode=mode,
        config=config,
    )


def cmd_ci_check(_args: argparse.Namespace) -> int:
    config = load_project_config()
    mode = selected_output_mode(_args, config)
    registry = load_json(REGISTRY_PATH)
    registry = refresh_impact_map(registry, persist=True)
    stale_locks = refresh_proof_status(registry)
    registry = load_json(REGISTRY_PATH)
    request_paths = configured_request_files(config)
    fail_if_no_requests = bool(config.get("fail_if_no_requests", True))

    if not request_paths:
        print(f"No request files matched project config: {PROJECT_CONFIG_PATH}")
        return 2 if fail_if_no_requests else 0

    exit_code = 0
    for request_path in request_paths:
        request = load_json(request_path)
        affected = resolve_affected_contracts(registry, request)
        blocked = blocked_touches(registry, request)
        policy_failures = policy_failures_for_request(registry, request)
        exit_code = max(
            exit_code,
            print_check_report(
                request_path,
                request,
                affected,
                blocked,
                stale_locks,
                policy_failures,
                mode=mode,
                config=config,
            ),
        )
    return exit_code


def cmd_release_audit(args: argparse.Namespace) -> int:
    repo_root = Path(str(args.repo_root or ".")).resolve()
    project_root = Path(str(args.project_root or ".")).resolve()
    try:
        payload = release_audit_payload(repo_root, project_root)
    except RuntimeError as exc:
        message = str(exc).strip() or "Release audit failed."
        if getattr(args, "json_output", False):
            return print_json_payload(
                {
                    "command": "release-audit",
                    "repo_root": str(repo_root),
                    "project_root": str(project_root),
                    "repo_problems": [],
                    "artifact_reports": [],
                    "status": "blocked",
                    "exit_code": 2,
                    "error": message,
                }
            )
        print(message)
        return 2
    if getattr(args, "json_output", False):
        return print_json_payload(payload)
    if payload["status"] == "pass":
        print("Release audit passed.")
        print("The repo tree is clean and the built package artifacts are clean.")
        return 0
    print("Release audit blocked.")
    if payload["repo_problems"]:
        print("Repo tree problems:")
        for item in payload["repo_problems"]:
            print(item)
    artifact_problems = [report for report in payload["artifact_reports"] if report["problems"]]
    if artifact_problems:
        print("Artifact problems:")
        for report in artifact_problems:
            print(report["artifact"])
            for item in report["problems"]:
                print(item)
    return 2


def cmd_lock(args: argparse.Namespace) -> int:
    registry = load_json(REGISTRY_PATH)
    registry = refresh_impact_map(registry, persist=True)
    contract = contract_by_id(registry, args.contract_id)
    failures = policy_failures_for_contract(contract)
    if failures:
        for item in failures:
            print(f"Policy failure: {item.rule_id} | {item.kind}")
            print(item.reason)
        return 2
    snapshot_path = approve_contract_state(
        registry,
        contract,
        approved_by=str(args.approved_by or "").strip(),
        reason=str(args.reason or "Approved and locked.").strip(),
        visual_proof_paths=normalize_values(list(args.visual_proof or [])),
        lock_after=True,
    )
    print(f"Locked {args.contract_id}")
    print(f"Snapshot saved: {snapshot_path}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    registry = load_json(REGISTRY_PATH)
    registry = refresh_impact_map(registry, persist=True)
    contract = contract_by_id(registry, args.contract_id)
    failures = policy_failures_for_contract(contract)
    if failures:
        for item in failures:
            print(f"Policy failure: {item.rule_id} | {item.kind}")
            print(item.reason)
        return 2
    snapshot_path = approve_contract_state(
        registry,
        contract,
        approved_by=str(args.approved_by or "").strip(),
        reason=str(args.reason or "Approved snapshot refreshed.").strip(),
        visual_proof_paths=normalize_values(list(args.visual_proof or [])),
        route=str(args.route or "").strip(),
        state_name=str(args.state_name or "").strip(),
        notes=str(args.notes or "").strip(),
        lock_after=False,
    )
    print(f"Snapshot saved for {args.contract_id}: {snapshot_path}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    registry = load_json(REGISTRY_PATH)
    registry = refresh_impact_map(registry, persist=True)
    request_path = Path(args.request).resolve()
    request = load_json(request_path)
    config = load_optional_project_config()
    mode = selected_output_mode(args, config)
    stale_locks = refresh_proof_status(registry)
    registry = load_json(REGISTRY_PATH)

    if args.phase == "start":
        blocked = blocked_touches(registry, request)
        policy_failures = policy_failures_for_request(registry, request)
        if stale_locks or blocked or policy_failures:
            return print_gate_report(
                request_path,
                request,
                phase="start",
                changed_files=[],
                blocked=blocked,
                stale_locks=stale_locks,
                policy_failures=policy_failures,
                unexpected_files=[],
                mode=mode,
                config=config,
            )
        baseline_hashes = build_known_file_hashes(registry)
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        run_payload = {
            "request_path": str(request_path),
            "request_id": str(request.get("request_id") or ""),
            "started_at": utc_now(),
            "request": request,
            "known_file_hashes": baseline_hashes,
        }
        save_json(run_file_path(request), run_payload)
        return print_gate_report(
            request_path,
            request,
            phase="start",
            changed_files=[],
            blocked=blocked,
            stale_locks=stale_locks,
            policy_failures=policy_failures,
            unexpected_files=[],
            mode=mode,
            config=config,
        )

    run_path = run_file_path(request)
    if not run_path.exists():
        raise FileNotFoundError(f"Missing gate run file: {run_path}")

    run_payload = load_json(run_path)
    before_hashes = dict(run_payload.get("known_file_hashes") or {})
    after_hashes = build_known_file_hashes(registry)
    changed_files = changed_known_files(before_hashes, after_hashes)
    planned_files = set(normalize_values(list(request.get("planned_file_touches") or [])))
    unexpected_files = sorted(file_path for file_path in changed_files if file_path not in planned_files)
    actual_request = {
        "requested_contract_ids": list(request.get("requested_contract_ids") or []),
        "planned_file_touches": changed_files,
        "planned_token_touches": list(request.get("planned_token_touches") or []),
        "planned_selector_touches": list(request.get("planned_selector_touches") or []),
        "planned_surface_touches": list(request.get("planned_surface_touches") or []),
    }
    blocked = blocked_touches(registry, actual_request)
    policy_failures = policy_failures_for_request(registry, actual_request)
    return print_gate_report(
        request_path,
        request,
        phase="finish",
        changed_files=changed_files,
        blocked=blocked,
        stale_locks=stale_locks,
        policy_failures=policy_failures,
        unexpected_files=unexpected_files,
        mode=mode,
        config=config,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Design Governor by UN1C0RN with contract-indexed locking.")
    parser.add_argument("--version", action="version", version=f"Design Governor by UN1C0RN v{VERSION}")
    mode_parent = argparse.ArgumentParser(add_help=False)
    mode_parent.add_argument("--expert", action="store_true")
    mode_parent.add_argument("--json", dest="json_output", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a registry from the template.")
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(func=cmd_init)

    status_parser = subparsers.add_parser("status", help="Show current contracts.")
    status_parser.set_defaults(func=cmd_status)

    diff_parser = subparsers.add_parser("diff", help="Show affected contracts for a request.")
    diff_parser.add_argument("--request", required=True)
    diff_parser.set_defaults(func=cmd_diff)

    check_parser = subparsers.add_parser(
        "check",
        parents=[mode_parent],
        help="Check a request against locked contracts and live proof.",
    )
    check_parser.add_argument("--request", required=True)
    check_parser.set_defaults(func=cmd_check)

    ci_check_parser = subparsers.add_parser(
        "ci-check",
        parents=[mode_parent],
        help="Run configured request checks for CI and hooks.",
    )
    ci_check_parser.set_defaults(func=cmd_ci_check)

    release_audit_parser = subparsers.add_parser(
        "release-audit",
        parents=[mode_parent],
        help="Audit repo and built release artifacts for blocked clutter.",
    )
    release_audit_parser.add_argument("--repo-root", default=".")
    release_audit_parser.add_argument("--project-root", default=".")
    release_audit_parser.set_defaults(func=cmd_release_audit)

    gate_parser = subparsers.add_parser(
        "gate",
        parents=[mode_parent],
        help="Run the hard gate before and after edits.",
    )
    gate_parser.add_argument("--request", required=True)
    gate_parser.add_argument("--phase", choices=["start", "finish"], required=True)
    gate_parser.set_defaults(func=cmd_gate)

    lock_parser = subparsers.add_parser("lock", help="Lock an approved contract and store real proof.")
    lock_parser.add_argument("--contract-id", required=True)
    lock_parser.add_argument("--reason", default="")
    lock_parser.add_argument("--approved-by", default="")
    lock_parser.add_argument("--visual-proof", action="append", default=[])
    lock_parser.set_defaults(func=cmd_lock)

    snapshot_parser = subparsers.add_parser("snapshot", help="Store a real approved snapshot record.")
    snapshot_parser.add_argument("--contract-id", required=True)
    snapshot_parser.add_argument("--route", default="")
    snapshot_parser.add_argument("--state-name", default="")
    snapshot_parser.add_argument("--notes", default="")
    snapshot_parser.add_argument("--reason", default="")
    snapshot_parser.add_argument("--approved-by", default="")
    snapshot_parser.add_argument("--visual-proof", action="append", default=[])
    snapshot_parser.set_defaults(func=cmd_snapshot)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
