"""Content provenance shared by exhaustive HACS QA tools.

The release candidate can be a dirty prepared tree. Its evidence is bound to
deterministic audited content, not an unrelated base commit or a future commit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("hacs-interactions.json")


def version(root: Path = ROOT) -> str:
    value = json.loads((root / "custom_components/hausman_hub/manifest.json").read_text(encoding="utf-8"))
    result = value.get("version") if isinstance(value, dict) else None
    if not isinstance(result, str):
        raise ValueError("integration manifest has no version")
    return result


def _manifest_qa_inputs(root: Path) -> tuple[list, list]:
    value = json.loads((root / "qa/full-functional/hacs-interactions.json").read_text(encoding="utf-8"))
    interactions = value.get("interactions") if isinstance(value, dict) else None
    intents = value.get("interaction_intents") if isinstance(value, dict) else None
    if not isinstance(interactions, list):
        raise ValueError("interaction manifest has no interaction list")
    if not isinstance(intents, list) or not intents:
        raise ValueError("interaction manifest has no intent registry")
    return interactions, intents


def audited_paths(root: Path = ROOT) -> tuple[Path, ...]:
    """Inputs whose changed bytes require a new exhaustive runtime report."""

    frontend = root / "custom_components/hausman_hub/frontend"
    return tuple(sorted(
        [*frontend.glob("*.js"), *frontend.glob("*.css"),
         root / "tests/browser/hausman-hub-full-interaction.spec.js",
         root / "tests/visual/hausman-hub-panel-harness.html"],
        key=lambda item: item.relative_to(root).as_posix(),
    ))


def content_digest(root: Path = ROOT) -> str:
    """SHA-256 of every audited byte plus the observed interaction inventory."""

    digest = hashlib.sha256()
    digest.update(f"version\0{version(root)}\0".encode("utf-8"))
    interactions, intents = _manifest_qa_inputs(root)
    for name, value in (("interactions", interactions), ("interaction_intents", intents)):
        digest.update(f"{name}\0".encode("utf-8"))
        digest.update(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        digest.update(b"\0")
    for path in audited_paths(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def provenance(root: Path = ROOT) -> dict[str, str]:
    return {"version": version(root), "content_digest": content_digest(root)}


def is_release_provenance(value: object, root: Path = ROOT) -> bool:
    return isinstance(value, dict) and value == provenance(root)
