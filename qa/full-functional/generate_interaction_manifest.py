#!/usr/bin/env python3
"""Build the HACS interaction manifest from source sites observed by the harness."""
from __future__ import annotations
import json, os, re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("hacs-interactions.json")
from release_pin import provenance, is_release_provenance
CONTROL = re.compile(r"\bel\(\s*['\"](button|input|select|textarea|a)['\"]|document\.createElement\(\s*['\"](button|input|select|textarea|a)['\"]")
EVENT = re.compile(r"\.addEventListener\(\s*['\"](click|change|input|submit)['\"]")
SOURCE_ID = re.compile(r"^(custom_components/hausman_hub/frontend/[^:]+\.js):(\d+):(create|listener):(\d+)$")

def section_for(path: str) -> str:
    name = Path(path).stem.removeprefix("hausman-hub-")
    return next((s for s in ("overview", "lighting", "climate", "rooms", "media", "security", "devices", "energy", "scenarios", "settings") if s in name), "shared")

def source_construct(path: str, line: int, construct: str, ordinal: int) -> bool:
    text = (ROOT / path).read_text(encoding="utf-8").splitlines()[line - 1]
    return len(CONTROL.findall(text) if construct == "create" else EVENT.findall(text)) >= ordinal

def load_interaction_intents() -> list[dict]:
    existing = json.loads(OUT.read_text(encoding="utf-8"))
    intents = existing.get("interaction_intents")
    if not isinstance(intents, list) or not intents:
        raise SystemExit("existing interaction_intents registry is required")
    return intents

def build_document(interactions: list[dict], intents: list[dict]) -> dict:
    return {"schema_version": 3, "source": "captured runtime stacks verified against frontend source", "interactions": interactions, "missing_evidence": [], "runtime_report_required": True, "interaction_intents": intents}

def main() -> int:
    report_path = os.environ.get("HACS_RUNTIME_REPORT")
    if not report_path: raise SystemExit("HACS_RUNTIME_REPORT is required; runtime stacks are the inventory source")
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not is_release_provenance(report.get("provenance")): raise SystemExit("runtime report content provenance mismatch")
    invalid, interactions = [], []
    for source_id in sorted(set(report.get("observed_source_ids", []))):
        match = SOURCE_ID.fullmatch(source_id)
        if not match or not source_construct(match[1], int(match[2]), match[3], int(match[4])):
            invalid.append(source_id); continue
        interactions.append({"source_id": source_id, "source_ref": f"{match[1]}:{match[2]}", "screen": section_for(match[1]), "module": Path(match[1]).name, "construct": match[3], "classification": "observed-runtime-control-source", "evidence": {"source": True, "runtime": True}})
    if invalid: raise SystemExit("runtime stacks do not name a source construct: " + ", ".join(invalid[:8]))
    if not interactions or any(n > 1 for n in Counter(row["source_id"] for row in interactions).values()): raise SystemExit("empty or duplicate observed interaction inventory")
    document = build_document(interactions, load_interaction_intents())
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    document["provenance"] = provenance(ROOT)
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {OUT}: {len(interactions)} observed source sites")
    return 0
if __name__ == "__main__": raise SystemExit(main())
