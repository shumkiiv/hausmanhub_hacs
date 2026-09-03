"""Regression tests for exhaustive HACS QA content provenance."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).parents[1]
PIN_MODULE = ROOT / "qa" / "full-functional" / "release_pin.py"
spec = importlib.util.spec_from_file_location("release_pin", PIN_MODULE)
assert spec and spec.loader
release_pin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_pin)


def _prepared_tree(destination: Path) -> None:
    shutil.copytree(ROOT / "custom_components" / "hausman_hub" / "frontend", destination / "custom_components" / "hausman_hub" / "frontend")
    (destination / "custom_components" / "hausman_hub").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "custom_components" / "hausman_hub" / "manifest.json", destination / "custom_components" / "hausman_hub" / "manifest.json")
    (destination / "qa" / "full-functional").mkdir(parents=True)
    shutil.copy2(ROOT / "qa" / "full-functional" / "hacs-interactions.json", destination / "qa" / "full-functional" / "hacs-interactions.json")
    (destination / "tests" / "browser").mkdir(parents=True)
    (destination / "tests" / "visual").mkdir(parents=True)
    shutil.copy2(ROOT / "tests" / "browser" / "hausman-hub-full-interaction.spec.js", destination / "tests" / "browser" / "hausman-hub-full-interaction.spec.js")
    shutil.copy2(ROOT / "tests" / "visual" / "hausman-hub-panel-harness.html", destination / "tests" / "visual" / "hausman-hub-panel-harness.html")


def test_manifest_provenance_matches_exact_prepared_content() -> None:
    manifest = json.loads((ROOT / "qa/full-functional/hacs-interactions.json").read_text(encoding="utf-8"))
    assert release_pin.is_release_provenance(manifest.get("provenance"))


def test_digest_changes_for_an_audited_release_input() -> None:
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory)
        _prepared_tree(candidate)
        original = release_pin.content_digest(candidate)
        audited = candidate / "custom_components" / "hausman_hub" / "frontend" / "hausman-hub-rooms.js"
        audited.write_text(audited.read_text(encoding="utf-8") + "\n/* QA provenance mutation */\n", encoding="utf-8")
        assert release_pin.content_digest(candidate) != original


def test_provenance_is_repeatable_without_git_head() -> None:
    first = release_pin.provenance()
    second = release_pin.provenance()
    assert first == second
    assert first["version"] == "1.52.208"
    assert len(first["content_digest"]) == 64
