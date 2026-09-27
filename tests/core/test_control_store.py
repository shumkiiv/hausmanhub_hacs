"""Store-level examples of the second delivery: D-07, D-08, D-14, D-16.

The store is exercised through its real file path in a temporary
directory; failures are injected at the named checkpoints of the atomic
write path.
"""

from __future__ import annotations

import json

import pytest

from custom_components.hausman_hub.adapters.core.control_store import (
    FileControlStore,
    StoreImageError,
)
from custom_components.hausman_hub.application.core.command_service import (
    CommitConflict,
)
from custom_components.hausman_hub.domain.core.models import (
    Authority,
    AutomaticScope,
    ControlRecord,
)

from conftest import MAIN_RESOURCE, FaultInjector

STORE_FILE = "control_store.json"


def make_state(control_revision: int, commit_revision: int) -> dict:
    record = ControlRecord(
        resource_id=MAIN_RESOURCE,
        control_revision=control_revision,
        generation=7,
        binding_revision=3,
        authority=Authority.AUTOMATIC_ELIGIBLE,
        automatic_scope=AutomaticScope.NORMAL,
    )
    return {
        "commit_revision": commit_revision,
        "records": {MAIN_RESOURCE: record},
        "operations": {},
        "receipts": {},
        "receipt_times": {},
        "receipt_fingerprints": {},
        "confirm_keys": {},
        "episode_locks": {},
        "scope_revisions": {"test.scope": 4},
        "config_revisions": {MAIN_RESOURCE: 2},
    }


def test_roundtrip_of_a_typed_image(tmp_path):
    store = FileControlStore(tmp_path / STORE_FILE)
    assert store.load() is None
    store.commit(0, make_state(control_revision=12, commit_revision=0))
    assert store.commit_revision == 1
    restored = FileControlStore(tmp_path / STORE_FILE).load()
    assert restored is not None
    record = restored["records"][MAIN_RESOURCE]
    assert record.control_revision == 12
    assert record.generation == 7
    assert restored["scope_revisions"]["test.scope"] == 4


def test_d07_damaged_images_close_the_store(tmp_path):
    # A truncated document cannot be parsed.
    broken = tmp_path / STORE_FILE
    broken.write_text('{"schema_version":', encoding="utf-8")
    with pytest.raises(Exception):
        FileControlStore(broken).load()
    # An unsupported future schema version is refused, not ignored.
    broken.write_text(
        json.dumps({"schema_version": 2, "commit_revision": 1}),
        encoding="utf-8",
    )
    with pytest.raises(StoreImageError):
        FileControlStore(broken).load()
    # A wrong field type inside the image is rejected wholesale.
    broken.write_text(
        json.dumps({"schema_version": 1, "commit_revision": True}),
        encoding="utf-8",
    )
    with pytest.raises(StoreImageError):
        FileControlStore(broken).load()
    # An invalid stored record value is rejected during decode.
    bad_record = tmp_path / "bad_record.json"
    bad_record.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "commit_revision": 3,
                "records": {
                    "test.main": {
                        "resource_id": "test.main",
                        "control_revision": 12,
                        "generation": True,
                        "binding_revision": 3,
                        "authority": "automatic_eligible",
                        "automatic_scope": "normal",
                        "manual": None,
                        "auto_proof": None,
                        "uncertainty_reason": None,
                    }
                },
                "operations": {},
                "receipts": {},
                "receipt_times": {},
                "receipt_fingerprints": {},
                "confirm_keys": {},
                "episode_locks": {},
                "scope_revisions": {},
                "config_revisions": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StoreImageError):
        FileControlStore(bad_record).load()

def test_d08_atomic_write_survives_injected_faults(tmp_path):
    import json

    path = tmp_path / STORE_FILE
    injector = FaultInjector()
    store = FileControlStore(path, fault_injector=injector)
    store.commit(0, make_state(control_revision=10, commit_revision=0))
    for point in (
        "tmp_open",
        "tmp_write",
        "file_fsync",
        "replace",
        "dir_fsync",
        "reread",
    ):
        injector.faults[point] = OSError(point, "injected")
        with pytest.raises(OSError):
            store.commit(store.commit_revision, make_state(control_revision=11, commit_revision=0))
        # No half-written temporary file is left behind.
        leftovers = [
            p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")
        ]
        assert leftovers == []
        # Whatever is on disk is a whole image, never a mix.
        reloaded = FileControlStore(path).load()
        assert reloaded["records"][MAIN_RESOURCE].control_revision in (10, 11)
    # A clean commit works afterwards and lands completely.
    store.commit(store.commit_revision, make_state(control_revision=11, commit_revision=0))
    reloaded = FileControlStore(path).load()
    assert reloaded["records"][MAIN_RESOURCE].control_revision == 11
    assert store.commit_revision == reloaded["commit_revision"]


def test_d16_next_image_with_invalid_record_is_rejected_wholesale(tmp_path):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    store.commit(0, make_state(control_revision=10, commit_revision=0))
    # A "next state" carrying a raw dict instead of a typed record is a
    # programming error: the commit is refused before anything is written
    # and the last good image stays intact (D-16 store half).
    bad_state = make_state(control_revision=11, commit_revision=0)
    bad_state["records"][MAIN_RESOURCE] = {"generation": True}
    with pytest.raises(Exception):
        store.commit(store.commit_revision, bad_state)
    reloaded = FileControlStore(path).load()
    assert reloaded["records"][MAIN_RESOURCE].control_revision == 10


def test_d14_cas_conflict_and_image_immutability(tmp_path):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    first = make_state(control_revision=10, commit_revision=0)
    store.commit(0, first)
    assert store.commit_revision == 1
    # A stale expected revision is refused without touching the file.
    with pytest.raises(CommitConflict):
        store.commit(0, make_state(control_revision=11, commit_revision=0))
    reloaded = FileControlStore(path).load()
    assert reloaded["records"][MAIN_RESOURCE].control_revision == 10
    # Mutating the already committed state object cannot rewrite history.
    first["scope_revisions"]["test.scope"] = 99
    reloaded = FileControlStore(path).load()
    assert reloaded["scope_revisions"]["test.scope"] == 4
    assert reloaded["commit_revision"] == 1
    # Revisions never reset or rewind after a reload.
    fresh = FileControlStore(path)
    fresh.commit(fresh.commit_revision, make_state(control_revision=11, commit_revision=0))
    assert FileControlStore(path).load()["commit_revision"] == 2
    assert fresh.commit_revision == 2


def test_storage_failure_is_reported_as_unavailable(tmp_path):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    store.fail_next_commit = True
    with pytest.raises(Exception) as info:
        store.commit(0, make_state(control_revision=12, commit_revision=0))
    from custom_components.hausman_hub.application.core.command_service import (
        StorageUnavailable,
    )

    assert isinstance(info.value, StorageUnavailable)
    assert store.load() is None
