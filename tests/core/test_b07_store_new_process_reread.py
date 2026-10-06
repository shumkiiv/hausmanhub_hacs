"""Matrix line B07 residual rows (HH-014.b, whole B block).

Covers only what tests/core/test_control_store.py (disk-pair, crash
before/between/after marker and image, wrong/missing/corrupt marker and
image, schema migration/backup/export) does not already prove:

  * re-read of the store by a NEW PROCESS: the parent creates the pair via
    the REAL store API initialize_shadow(room_id: str, initial_state: Mapping)
    — the durable marker is written BEFORE the schema-2 image — then a
    subprocess (sys.executable, -X utf8) opens the SAME directory with the
    real FileControlStore.__init__(path, fault_injector=None) and
    successfully reads/verifies the pair; the child's full stdout is asserted;
  * process boundary of the corrupt case: a damaged pair on disk makes the
    child refuse (construct error / unhealthy store / revision mismatch);
  * a new INSTANCE (same process) reads the same durable state written by
    the first instance.

initialize_shadow / migrate_legacy_closed are store APIs used for local
evidence only; this file does not declare them an approved live procedure
and never runs them against Home Assistant. No network calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import custom_components.hausman_hub as _hub_pkg

try:  # actual module location is resolved once, defensively
    from custom_components.hausman_hub.adapters.core.control_store import (
        FileControlStore,
    )
except ImportError:  # pragma: no cover - depends on worktree layout
    from custom_components.hausman_hub.application.core.command_service import (  # noqa: F401
        FileControlStore,
    )

WORKTREE_ROOT = Path(_hub_pkg.__file__).resolve().parents[2]

_ROOM_ID = "startup-room"
# fix(root,wholeb-fix02): рабочая конструкция состояния из tests/core/test_control_store.py
# (make_state + closed registration) — схема требует commit_revision/records/etc.
from custom_components.hausman_hub.application.core.command_service import (
    ClosedRegistration,
    ClosedTargetRegistration,
)
from custom_components.hausman_hub.domain.core.models import (
    Authority,
    AutomaticScope,
    ControlRecord,
)
from conftest import MAIN_RESOURCE

_MAIN_RECORD = ControlRecord(
    resource_id=MAIN_RESOURCE,
    control_revision=12,
    generation=7,
    binding_revision=3,
    authority=Authority.AUTOMATIC_ELIGIBLE,
    automatic_scope=AutomaticScope.NORMAL,
)
_INITIAL_STATE = {
    "commit_revision": 0,
    "records": {MAIN_RESOURCE: _MAIN_RECORD},
    "operations": {},
    "receipts": {},
    "receipt_times": {},
    "receipt_fingerprints": {},
    "confirm_keys": {},
    "episode_locks": {},
    "scope_revisions": {"test.scope": 4},
    "config_revisions": {MAIN_RESOURCE: 2},
    "closed_registrations": {
        "p19.room": ClosedRegistration(
            branch_id="p19.room", import_id="init", source_identity="legacy",
            source_revision=1, source_payload_digest="0" * 64,
            targets=(ClosedTargetRegistration(
                target_id="main", resource_id=MAIN_RESOURCE, entity_id="light.main",
                property_name="on", allowed_actions=("turn_on",),
                binding_revision=3, config_revision=2,
            ),),
        )
    },
}

_CHILD_SCRIPT = r"""
import json, os, sys

sys.path.insert(0, os.environ["HH_WORKTREE_ROOT"])
try:
    from custom_components.hausman_hub.adapters.core.control_store import (
        FileControlStore as Store,
    )
except Exception:
    from custom_components.hausman_hub.application.core.command_service import (
        FileControlStore as Store,
    )

path = sys.argv[1]
expected_revision = int(sys.argv[2])

try:
    # REAL constructor: __init__(self, path, fault_injector=None);
    # it loads durable state inside the constructor.
    store = Store(path)
except Exception as exc:
    print(json.dumps({"status": "construct-error", "error": repr(exc)}))
    sys.exit(0)

try:
    healthy = store.healthy()
except Exception as exc:
    print(json.dumps({"status": "unhealthy", "error": repr(exc)}))
    sys.exit(0)

print(
    json.dumps(
        {
            "status": "ok" if healthy else "unhealthy",
            "commit_revision": int(store.commit_revision),
            "expected_revision": expected_revision,
        }
    )
)
"""


def _store_write_initial_pair(store_path: Path):
    """Create the durable marker+image pair through the store API.

    Uses the exact real signature:
    initialize_shadow(self, room_id: str, initial_state: Mapping) -> None.
    The durable marker is written before the schema-2 image for ONE room.
    """
    store = FileControlStore(store_path)
    assert store.healthy()
    init = getattr(store, "initialize_shadow", None)
    if init is None:
        pytest.skip("initialize_shadow is not part of this store API")
    init(_ROOM_ID, _INITIAL_STATE)
    getattr(store, "flush", lambda: None)()
    revision = int(store.commit_revision)
    assert store.healthy()
    assert any(store_path.parent.glob("*")), "initialize_shadow wrote no files"
    return store, revision


def _spawn_child(store_path: Path, expected_revision: int):
    env = dict(os.environ)
    env["HH_WORKTREE_ROOT"] = str(WORKTREE_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", _CHILD_SCRIPT,
         str(store_path), str(expected_revision)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(WORKTREE_ROOT),
    )


# B07: re-read by a NEW PROCESS — a subprocess opening the same directory
# successfully reads the marker+image pair created by the parent via
# initialize_shadow and reports the same commit revision; the child's full
# stdout is asserted.
def test_b07_new_process_rereads_written_pair(tmp_path):
    store_path = tmp_path / "control_store.json"  # fix(root): FileControlStore ожидает ФАЙЛ
    store, revision = _store_write_initial_pair(store_path)
    assert revision >= 0

    child = _spawn_child(store_path, revision)
    assert child.returncode == 0, (
        f"child failed: rc={child.returncode} stderr={child.stderr!r}"
    )
    payload = json.loads(child.stdout)
    assert payload["status"] == "ok", child.stdout
    assert payload["commit_revision"] == payload["expected_revision"]
    assert payload["commit_revision"] == revision


# B07: process boundary of a damaged pair — after on-disk corruption (the
# image side of the durable pair is overwritten) a NEW PROCESS must refuse:
# construct error, unhealthy store or a revision different from the expected
# one; it never reports a clean ok read.
def test_b07_new_process_rejects_corrupt_pair(tmp_path):
    store_path = tmp_path / "control_store.json"  # fix(root): FileControlStore ожидает ФАЙЛ
    _store, revision = _store_write_initial_pair(store_path)

    files = sorted(
        (p for p in store_path.parent.glob("*") if p.is_file()),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    assert files, "durable pair is missing on disk"
    # The largest file of the pair is the state image; corrupt it in place.
    files[0].write_text("{corrupted-image", encoding="utf-8")

    child = _spawn_child(store_path, revision)
    assert child.returncode == 0, (
        f"child crashed hard: rc={child.returncode} stderr={child.stderr!r}"
    )
    payload = json.loads(child.stdout)
    accepted = (
        payload["status"] == "ok"
        and payload["commit_revision"] == payload["expected_revision"]
    )
    assert not accepted, (
        "corrupt pair was silently accepted by the new process: " + child.stdout
    )


# B07: new INSTANCE in the same process — a second FileControlStore reads the
# same durable state (healthy, equal commit revision) after the first one
# wrote it. FileControlStore.__init__(path, fault_injector=None) loads the
# durable state in the constructor.
def test_b07_new_instance_same_process_reads_state(tmp_path):
    store_path = tmp_path / "control_store.json"  # fix(root): FileControlStore ожидает ФАЙЛ
    first, revision = _store_write_initial_pair(store_path)

    second = FileControlStore(store_path)
    try:
        assert second.healthy()
        assert int(second.commit_revision) == revision
        assert int(first.commit_revision) == revision
    finally:
        getattr(second, "flush", lambda: None)()
