"""Store-level examples of the second delivery: D-07, D-08, D-14, D-16.

The store is exercised through its real file path in a temporary
directory; failures are injected at the named checkpoints of the atomic
write path.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from custom_components.hausman_hub.adapters.core.control_store import (
    FileControlStore,
    StoreImageError,
)
from custom_components.hausman_hub.application.core.command_service import (
    ClosedRegistration,
    ClosedTargetRegistration,
    CommitConflict,
)
from custom_components.hausman_hub.domain.core.models import (
    Authority,
    AutomaticScope,
    ControlRecord,
    ManualHold,
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


def make_closed_state() -> dict:
    state = make_state(control_revision=12, commit_revision=0)
    state["closed_registrations"] = {
        "shadow.room": ClosedRegistration(
            branch_id="shadow.room", import_id="init", source_identity="legacy",
            source_revision=1, source_payload_digest="0" * 64,
            targets=(ClosedTargetRegistration(
                target_id="main", resource_id=MAIN_RESOURCE, entity_id="light.main",
                property_name="on", allowed_actions=("turn_on", "turn_off"),
                binding_revision=3, config_revision=2,
            ),),
        )
    }
    return state


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
    assert restored["closed_registrations"] == {}


def test_b07_explicit_shadow_initialization_writes_a_matched_disk_pair(tmp_path):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    state = make_state(control_revision=12, commit_revision=0)
    state["closed_registrations"] = {
        "shadow.room": ClosedRegistration(
            branch_id="shadow.room", import_id="init", source_identity="legacy",
            source_revision=1, source_payload_digest="0" * 64,
            targets=(ClosedTargetRegistration(
                target_id="main", resource_id=MAIN_RESOURCE, entity_id="light.main",
                property_name="on", allowed_actions=("turn_on",),
                binding_revision=3, config_revision=2,
            ),),
        )
    }
    store.initialize_shadow("shadow.room", state)
    marker = json.loads((tmp_path / f"{STORE_FILE}.expected").read_text())
    image = json.loads(path.read_text())
    assert marker["image_required"] is True
    assert image["schema_version"] == 2
    assert image["image_id"] == marker["image_id"]
    assert FileControlStore(path).load()["commit_revision"] == 1


@pytest.mark.parametrize("damage", ("missing", "wrong_id", "corrupt_marker"))
def test_b07_required_pair_damage_closes_a_new_store(tmp_path, damage):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    state = make_state(control_revision=12, commit_revision=0)
    state["closed_registrations"] = {
        "shadow.room": ClosedRegistration(
            branch_id="shadow.room", import_id="init", source_identity="legacy",
            source_revision=1, source_payload_digest="0" * 64,
            targets=(ClosedTargetRegistration(
                target_id="main", resource_id=MAIN_RESOURCE, entity_id="light.main",
                property_name="on", allowed_actions=("turn_on",), binding_revision=3,
                config_revision=2,
            ),),
        )
    }
    store.initialize_shadow("shadow.room", state)
    marker_path = tmp_path / f"{STORE_FILE}.expected"
    if damage == "missing":
        path.unlink()
    elif damage == "wrong_id":
        image = json.loads(path.read_text())
        image["image_id"] = "wrong"
        path.write_text(json.dumps(image))
    else:
        marker_path.write_text("{")
    with pytest.raises(StoreImageError):
        FileControlStore(path)


def test_b07_schema2_requires_a_sentinel_and_old_codec_refuses_it(tmp_path):
    path = tmp_path / STORE_FILE
    image = make_state(control_revision=12, commit_revision=0)
    store = FileControlStore(path)
    store.commit(0, image)
    raw = json.loads(path.read_text())
    raw["schema_version"] = 2
    raw["image_id"] = "new-image"
    path.write_text(json.dumps(raw))
    with pytest.raises(StoreImageError):
        FileControlStore(path).load()
    from custom_components.hausman_hub.adapters.core.control_store import validate_image_shape
    with pytest.raises(StoreImageError):
        validate_image_shape(raw, supported_versions=(1,))


def test_b07_valid_closed_schema1_migrates_with_backup_and_readback(tmp_path):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    state = make_state(control_revision=12, commit_revision=0)
    state["closed_registrations"] = {
        "shadow.room": ClosedRegistration(
            branch_id="shadow.room", import_id="old", source_identity="legacy",
            source_revision=1, source_payload_digest="0" * 64,
            targets=(ClosedTargetRegistration(
                target_id="main", resource_id=MAIN_RESOURCE, entity_id="light.main",
                property_name="on", allowed_actions=("turn_on",), binding_revision=3,
                config_revision=2,
            ),),
        )
    }
    store.commit(0, state)
    old = json.loads(path.read_text())
    FileControlStore(path).migrate_legacy_closed("shadow.room")
    assert json.loads((tmp_path / f"{STORE_FILE}.schema1.backup").read_text()) == old
    restarted = FileControlStore(path)
    assert restarted.load()["records"][MAIN_RESOURCE].control_revision == 12
    assert restarted.export_closed_schema1()["schema_version"] == 1
    assert (tmp_path / f"{STORE_FILE}.expected").exists()


def test_b07_closed_export_keeps_the_latest_durable_manual_action(tmp_path):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    state = make_state(control_revision=12, commit_revision=0)
    state["closed_registrations"] = {
        "shadow.room": ClosedRegistration(
            branch_id="shadow.room", import_id="old", source_identity="legacy",
            source_revision=1, source_payload_digest="0" * 64,
            targets=(ClosedTargetRegistration(
                target_id="main", resource_id=MAIN_RESOURCE, entity_id="light.main",
                property_name="on", allowed_actions=("turn_on",), binding_revision=3,
                config_revision=2,
            ),),
        )
    }
    store.commit(0, state)
    store.migrate_legacy_closed("shadow.room")
    latest = store.load()
    latest["records"][MAIN_RESOURCE] = replace(
        latest["records"][MAIN_RESOURCE],
        manual=ManualHold("latest-manual", "off", "tablet", 20, 30, 40, 1),
    )
    store.commit(store.commit_revision, latest)
    exported = store.export_closed_schema1()
    assert exported["records"][MAIN_RESOURCE]["manual"]["action_id"] == "latest-manual"
    assert json.loads((tmp_path / f"{STORE_FILE}.expected").read_text())["image_required"] is True


def test_b07_interrupted_initialization_before_or_between_pair_stays_closed(tmp_path, monkeypatch):
    path = tmp_path / STORE_FILE
    state = make_closed_state()
    before_marker = FileControlStore(path)
    monkeypatch.setattr(before_marker, "_write_json_atomic", lambda *args: (_ for _ in ()).throw(OSError("marker")))
    with pytest.raises(Exception):
        before_marker.initialize_shadow("shadow.room", state)
    assert FileControlStore(path).load() is None

    between_pair = FileControlStore(path)
    real_write = between_pair._write_atomic
    monkeypatch.setattr(between_pair, "_write_atomic", lambda *args: (_ for _ in ()).throw(OSError("image")))
    with pytest.raises(Exception):
        between_pair.initialize_shadow("shadow.room", state)
    with pytest.raises(StoreImageError, match="required shadow image"):
        FileControlStore(path)
    monkeypatch.setattr(between_pair, "_write_atomic", real_write)


def test_b07_crash_after_image_before_ack_recovers_from_the_verified_pair(tmp_path, monkeypatch):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    state = make_closed_state()
    real_read_marker = store._read_sentinel
    reads = 0

    def fail_ack():
        nonlocal reads
        reads += 1
        if reads == 1:
            raise OSError("ack")
        return real_read_marker()

    monkeypatch.setattr(store, "_read_sentinel", fail_ack)
    with pytest.raises(Exception):
        store.initialize_shadow("shadow.room", state)
    # A new process judges only the durable pair, never an in-memory ack.
    assert FileControlStore(path).load()["commit_revision"] == 1


def test_closed_registration_roundtrips_with_the_same_whole_image(tmp_path):
    store = FileControlStore(tmp_path / STORE_FILE)
    state = make_state(control_revision=12, commit_revision=0)
    state["closed_registrations"] = {
        "synthetic.room": ClosedRegistration(
            branch_id="synthetic.room",
            import_id="import-1",
            source_identity="legacy-image-1",
            source_revision=7,
                source_payload_digest="0" * 64,
            targets=(ClosedTargetRegistration(
                target_id="light.synthetic",
                resource_id=MAIN_RESOURCE,
                entity_id="light.synthetic",
                property_name="on",
                allowed_actions=("turn_on", "turn_off"),
                binding_revision=3,
                config_revision=2,
            ),),
        )
    }
    store.commit(0, state)
    restored = FileControlStore(tmp_path / STORE_FILE).load()
    assert restored is not None
    registration = restored["closed_registrations"]["synthetic.room"]
    assert registration.source_revision == 7
    assert registration.targets[0].entity_id == "light.synthetic"


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
    from custom_components.hausman_hub.application.core.command_service import (
        StorageUnavailable,
    )

    for point in (
        "tmp_open",
        "tmp_write",
        "file_fsync",
        "replace",
        "dir_fsync",
        "reread",
    ):
        injector.faults[point] = OSError(point, "injected")
        with pytest.raises(StorageUnavailable):
            store.commit(store.commit_revision, make_state(control_revision=11, commit_revision=0))
        # The failed store stays closed until an explicit reload: the
        # write outcome was undefined (SEC-S04-01).
        with pytest.raises(StorageUnavailable):
            store.commit(store.commit_revision, make_state(control_revision=11, commit_revision=0))
        # No half-written temporary file is left behind.
        leftovers = [
            p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")
        ]
        assert leftovers == []
        # An explicit reload re-syncs with whatever whole image is on
        # disk; it is never a mix.
        store = FileControlStore(path, fault_injector=injector)
        reloaded = store.load()
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
    from conftest import OTHER_RESOURCE

    bad_state["records"][OTHER_RESOURCE] = {"generation": True}
    with pytest.raises(Exception):
        store.commit(store.commit_revision, bad_state)
    # The last good image, including the untouched other record, stays.
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


@pytest.mark.parametrize("part", ("image", "marker"))
def test_closed_pair_rejects_duplicate_json_keys(tmp_path, part):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    store.initialize_shadow("shadow.room", make_closed_state())
    damaged = path if part == "image" else path.with_suffix(".json.expected")
    original = damaged.read_text()
    key = "commit_revision" if part == "image" else "image_required"
    value = "1" if part == "image" else "true"
    damaged.write_text('{"' + key + '":' + value + ',' + original[1:])
    with pytest.raises(StoreImageError):
        FileControlStore(path)


@pytest.mark.parametrize("damage", ("bool_version", "other_room", "missing_marker", "corrupt_image"))
def test_closed_pair_rejects_invalid_identity(tmp_path, damage):
    path = tmp_path / STORE_FILE
    FileControlStore(path).initialize_shadow("shadow.room", make_closed_state())
    marker_path = path.with_suffix(".json.expected")
    marker = json.loads(marker_path.read_text())
    if damage == "bool_version":
        marker["format_version"] = True
        marker_path.write_text(json.dumps(marker))
    elif damage == "other_room":
        marker["room_id"] = "other.room"
        marker_path.write_text(json.dumps(marker))
    elif damage == "missing_marker":
        marker_path.unlink()
    else:
        path.write_text("{")
    with pytest.raises(StoreImageError):
        FileControlStore(path)


@pytest.mark.parametrize("damage", ("section_type", "branch_key", "target", "resource", "entity", "cross_branch_target", "cross_branch_entity", "cross_branch_resource"))
def test_closed_codec_refuses_ambiguous_disk_registrations(tmp_path, damage):
    import copy

    path = tmp_path / STORE_FILE
    FileControlStore(path).commit(0, make_closed_state())
    raw = json.loads(path.read_text())
    registration = raw["closed_registrations"]["shadow.room"]
    if damage == "section_type":
        raw["closed_registrations"] = []
    elif damage == "branch_key":
        registration["branch_id"] = "other.room"
    elif damage.startswith("cross_branch"):
        other = copy.deepcopy(registration)
        other["branch_id"] = "other.room"
        other["targets"][0]["resource_id"] = "other.resource"
        if damage == "cross_branch_target":
            other["targets"][0]["entity_id"] = "light.other"
        elif damage == "cross_branch_entity":
            other["targets"][0]["target_id"] = "other"
        else:
            other["targets"][0].update(target_id="other", entity_id="light.other", resource_id=MAIN_RESOURCE)
        raw["closed_registrations"]["other.room"] = other
    else:
        target = copy.deepcopy(registration["targets"][0])
        target.update(target_id="other", resource_id="other.resource", entity_id="light.other")
        field = {"target": "target_id", "resource": "resource_id", "entity": "entity_id"}[damage]
        target[field] = registration["targets"][0][field]
        registration["targets"].append(target)
    path.write_text(json.dumps(raw))
    with pytest.raises(StoreImageError):
        FileControlStore(path)


@pytest.mark.parametrize("operation", ("commit", "initialize"))
@pytest.mark.parametrize("damage", ("boolean_revision", "bad_actions", "branch_key", "wrong_room", "duplicate_target"))
def test_invalid_closed_dto_is_refused_before_any_write(tmp_path, operation, damage):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    if operation == "commit":
        store.commit(0, make_state(10, 0))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    state = make_closed_state()
    registration = state["closed_registrations"]["shadow.room"]
    if damage == "boolean_revision":
        registration = replace(registration, source_revision=True)
    elif damage == "bad_actions":
        registration = replace(registration, targets=(replace(registration.targets[0], allowed_actions=[]),))
    elif damage == "branch_key":
        registration = replace(registration, branch_id="other.room")
    elif damage == "duplicate_target":
        registration = replace(registration, targets=registration.targets * 2)
    elif operation == "commit":
        # A mismatched branch key is invalid even without a schema-2 marker.
        registration = replace(registration, branch_id="other.room")
    state["closed_registrations"]["shadow.room"] = registration
    with pytest.raises(StoreImageError):
        if operation == "initialize":
            store.initialize_shadow("other.room" if damage == "wrong_room" else "shadow.room", state)
        else:
            store.commit(store.commit_revision, state)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert store.healthy()


def test_commit_does_not_recreate_a_lost_marker(tmp_path):
    from custom_components.hausman_hub.application.core.command_service import StorageUnavailable

    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    store.initialize_shadow("shadow.room", make_closed_state())
    before = path.read_bytes()
    path.with_suffix(".json.expected").unlink()
    with pytest.raises(StorageUnavailable):
        store.commit(store.commit_revision, make_closed_state())
    assert path.read_bytes() == before
    assert not store.healthy()


@pytest.mark.parametrize("section", ("records", "commit_revision"))
def test_initialization_refuses_incomplete_state_before_marker(tmp_path, section):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    state = make_closed_state()
    del state[section]
    with pytest.raises(StoreImageError):
        store.initialize_shadow("shadow.room", state)
    assert list(tmp_path.iterdir()) == []
    assert store.healthy()


@pytest.mark.parametrize("conflict", (True, False))
def test_crossbranch_resource_is_checked_before_commit_preserving_manual_history(tmp_path, conflict):
    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    original = make_closed_state()
    original["records"][MAIN_RESOURCE] = replace(
        original["records"][MAIN_RESOURCE],
        manual=ManualHold("latest-manual", "off", "tablet", 20, 30, 40, 1),
    )
    store.commit(0, original)
    before = path.read_bytes()
    revision = store.commit_revision
    state = store.load()
    first = state["closed_registrations"]["shadow.room"]
    other_resource = MAIN_RESOURCE if conflict else "other.resource"
    other_target = replace(first.targets[0], target_id="other", entity_id="light.other", resource_id=other_resource)
    state["closed_registrations"]["other.room"] = replace(first, branch_id="other.room", targets=(other_target,))
    if conflict:
        with pytest.raises(StoreImageError):
            store.commit(revision, state)
        assert path.read_bytes() == before
        assert store.commit_revision == revision
    else:
        state["records"][other_resource] = replace(state["records"][MAIN_RESOURCE], resource_id=other_resource)
        state["config_revisions"][other_resource] = 2
        store.commit(revision, state)
        assert store.commit_revision == revision + 1
    reloaded = FileControlStore(path).load()
    assert reloaded["records"][MAIN_RESOURCE].manual.action_id == "latest-manual"
    assert store.healthy()
