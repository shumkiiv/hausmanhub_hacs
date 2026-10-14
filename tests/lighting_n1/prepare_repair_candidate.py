"""Seal exact installed basis plus the bounded runtime diff, never a whole checkout."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import os

from source_bootstrap import verify_source

PATHS = tuple("custom_components/hausman_hub/" + name for name in (
    "__init__.py", "manual_light_authority_events.py", "manual_light_off_protection_events.py",
    "application/room_lighting_runtime.py", "application/room_lighting_ha_state.py",
    "domain/room_lighting_engine.py"))
REPAIR_BASE = "e7335c93bd4b5b33e60f7dc1e3b0ed619f0d5a8b"


def prepare(basis, destination):
    source, original_digest = verify_source(basis)
    destination = Path(destination).resolve()
    if destination.exists():
        raise AssertionError("refuse overwriting an existing candidate")
    checkout = Path(__file__).resolve().parents[2]
    patch = subprocess.check_output(["git", "diff", REPAIR_BASE, "--", *PATHS], cwd=checkout)
    if not patch:
        raise AssertionError("repair diff is empty")
    shutil.copytree(source, destination / "files")
    # Do not let Git discover the workspace repository and silently skip paths
    # outside its cwd prefix. This directory is only a sealed package, not Git.
    environment = dict(os.environ, GIT_CEILING_DIRECTORIES=str(destination))
    subprocess.run(["git", "apply", "--check", "-"], cwd=destination / "files",
                   env=environment, input=patch, check=True)
    subprocess.run(["git", "apply", "-"], cwd=destination / "files",
                   env=environment, input=patch, check=True)
    original = json.loads((Path(basis) / "verification.json").read_text())["files"]
    declared = {}
    changed = []
    for relative, old in original.items():
        content = (destination / "files" / relative).read_bytes()
        entry = dict(old, sha256=hashlib.sha256(content).hexdigest(), size=len(content))
        declared[relative] = entry
        if entry != old:
            changed.append(relative)
    if set(changed) != set(PATHS):
        raise AssertionError(f"unexpected repair file set: {changed}")
    digest = hashlib.sha256(json.dumps(declared, sort_keys=True).encode()).hexdigest()
    (destination / "verification.json").write_text(json.dumps({
        "schema_version": 1, "status": "SEALED_LOCAL_TEST_ONLY", "published": False,
        "basis_digest": original_digest, "files": declared,
        "repair_paths": changed, "repair_patch_sha256": hashlib.sha256(patch).hexdigest(),
    }, indent=2) + "\n")
    verify_source(destination, expected_digest=digest)
    verify_source(basis)
    print(json.dumps({"basis": original_digest, "candidate": digest, "changed": changed,
                      "files": len(declared)}, indent=2))


if __name__ == "__main__":
    prepare(*sys.argv[1:])
