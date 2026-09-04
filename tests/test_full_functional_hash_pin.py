"""Regression tests for exhaustive HACS QA content provenance."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).parents[1]
PIN_MODULE = ROOT / "qa" / "full-functional" / "release_pin.py"
spec = importlib.util.spec_from_file_location("release_pin", PIN_MODULE)
assert spec and spec.loader
release_pin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_pin)
sys.modules.setdefault("release_pin", release_pin)
GENERATOR_MODULE = ROOT / "qa" / "full-functional" / "generate_interaction_manifest.py"
generator_spec = importlib.util.spec_from_file_location("generate_interaction_manifest", GENERATOR_MODULE)
assert generator_spec and generator_spec.loader
generator = importlib.util.module_from_spec(generator_spec)
generator_spec.loader.exec_module(generator)


def load_manifest() -> dict:
    return json.loads((ROOT / "qa/full-functional/hacs-interactions.json").read_text(encoding="utf-8"))


def test_interaction_intents_are_technical_and_complete() -> None:
    manifest = load_manifest()
    assert manifest["interaction_intents"]
    seen = set()
    for item in manifest["interaction_intents"]:
        assert item["intent"] in {"ui-only", "command", "blocked"}
        assert item["key"] and item["state"] and "label" not in item and "locale" not in item
        assert item["key"] not in seen
        seen.add(item["key"])
        if item["intent"] == "ui-only":
            assert item.get("effect")
        if item["intent"] == "command":
            request = item.get("request")
            assert set(request or {}) == {"method", "path"}
            assert request["method"] in {"POST", "PUT", "PATCH", "DELETE"}
            assert request["path"].startswith("/api/") and "?" not in request["path"]
        if item["intent"] == "blocked":
            assert "request" not in item


def test_manifest_covers_overview_and_hero_controls() -> None:
    manifest = load_manifest()
    keys = {item["key"] for item in manifest["interaction_intents"]}
    expected = {
        "overview:events", "overview:fullscreen", "overview:refresh",
        "overview:hero-details", "overview:star", "overview:more",
        "overview:home-filled", "overview:rooms", "overview:scenarios",
        "overview:weather", "overview:comfort", "overview:all-scenarios",
        "hero-room:previous", "hero-room:previous-slide", "hero-room:home",
        "hero-room:next", "hero-room:next-slide",
        "hero-room:dot:home", "hero-room:dot:living", "hero-room:dot:bedroom",
        "hero-room:dot:kitchen", "hero-room:dot:office",
    }
    assert expected <= keys


def test_mass_light_off_entrypoints_are_never_clicked_by_the_gate() -> None:
    manifest = load_manifest()
    intents = {item["key"]: item["intent"] for item in manifest["interaction_intents"]}
    assert intents["lighting:room-power"] == "blocked"
    assert intents["lighting:side-action"] == "blocked"


def test_manifest_generator_preserves_intent_registry() -> None:
    intents = [{"key": "navigation:rooms", "state": "shared", "intent": "ui-only", "effect": {"kind": "dom-change"}}]
    document = generator.build_document([{"source_id": "source"}], intents)
    assert document["interaction_intents"] == intents


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


def test_digest_changes_when_an_interaction_intent_changes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory)
        _prepared_tree(candidate)
        original = release_pin.content_digest(candidate)
        manifest_path = candidate / "qa" / "full-functional" / "hacs-interactions.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["interaction_intents"][0]["state"] = "provenance-check"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        assert release_pin.content_digest(candidate) != original


def test_provenance_is_repeatable_without_git_head() -> None:
    first = release_pin.provenance()
    second = release_pin.provenance()
    assert first == second
    assert first["version"] == "1.52.210"
    assert len(first["content_digest"]) == 64
