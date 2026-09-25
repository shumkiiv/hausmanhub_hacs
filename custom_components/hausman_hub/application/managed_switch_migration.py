"""Verified, restart-safe migration for release-owned switch scenarios."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING, Mapping

from .native_automation_migration import NativeAutomationNotReady
from .scenario_consolidation_inventory import (
    REGISTRY_DISPOSITION_COUNTS,
    REGISTRY_DISPOSITION_HASH,
    REGISTRY_DISPOSITION_VERSION,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

MIGRATION_ID = "managed-switches"
MIGRATION_VERSION = 4
MANAGED_TOPOLOGY = "managed-three-node-v1"
_KNOWN_COMPLETED_V2_RECEIPT = {
    "migrationId": MIGRATION_ID,
    "version": 2,
    "state": "completed",
    "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
}

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ManagedSwitchMigrationEntry:
    scenario_id: str
    legacy_revision: int
    legacy_source_hash: str
    legacy_topology: str
    legacy_input_target_ids: tuple[str, ...]
    input_target_ids: tuple[str, ...]
    new_source_hash: str
    source_file: str
    source: str = ""
    operation: str = "replace"
    expected_revision: int | None = 0
    activation_ready: bool = False


# Public defaults intentionally use synthetic role-template ids only. Production
# setup replaces them with the per-entry protected topology snapshot before
# startup, receipt validation, hashing, and execution.
_SHOWER_LEGACY_INPUTS = (
    "protected_shower_presence",
    "protected_shower_humidity",
    "protected_shared_sun",
    "protected_shower_main",
    "protected_shower_extra",
    "protected_shower_fan",
    "protected_shower_cabinet",
)
_SHOWER_INPUTS = _SHOWER_LEGACY_INPUTS
_SMALL_LEGACY_INPUTS = (
    "protected_small_motion_old",
    "protected_shared_sun",
    "protected_small_lux_old",
    "protected_small_relay_old",
    "protected_small_switch_old",
    "protected_small_chandelier_old",
)
_SMALL_INPUTS = (
    "protected_small_motion",
    "protected_shared_sun",
    "protected_small_lux",
    "protected_small_relay",
    "protected_small_chandelier",
)
_TAMBUR_LEGACY_INPUTS = (
    "protected_tambur_motion",
    "protected_tambur_presence_primary_old",
    "protected_shared_sun",
    "protected_small_lux_old",
    "protected_tambur_chandelier",
    "protected_tambur_points",
    "protected_tambur_mirror",
    "protected_tambur_power",
    "protected_tambur_entry",
)
_TAMBUR_INPUTS = (
    "protected_tambur_motion",
    "protected_tambur_presence_secondary",
    "protected_tambur_presence_primary",
    "protected_shared_sun",
    "protected_tambur_chandelier",
    "protected_tambur_points",
    "protected_tambur_mirror",
    "protected_tambur_power",
    "protected_tambur_entry",
)
_TOILET_INPUTS = (
    "protected_toilet_motion_primary",
    "protected_toilet_motion_secondary",
    "protected_toilet_main",
    "protected_toilet_night",
    "protected_toilet_fan",
    "protected_shared_sun",
    "protected_toilet_away",
)
_BATHROOM_INPUTS = (
    "protected_bathroom_light_primary",
    "protected_bathroom_light_secondary",
    "protected_bathroom_humidity",
    "protected_bathroom_fan",
)
_STORAGE_INPUTS = ("protected_storage_motion", "protected_storage_light")
_CABINET_INPUTS = (
    "protected_office_light",
    "protected_office_relay",
    "protected_small_lux",
    "protected_shared_sun",
)
_CURTAIN_INPUTS = (
    "protected_curtain_living",
    "protected_curtain_kitchen",
    "protected_curtain_room",
    "protected_curtain_office",
)

FULL_MIGRATION_MANIFEST: tuple[ManagedSwitchMigrationEntry, ...] = (
    ManagedSwitchMigrationEntry(
        "system-shower-comfort-controller", 4,
        "757bde711c85ebad4826c2ec0bf2695d0034f7dd820c9ec7c30816f3f37c1551",
        MANAGED_TOPOLOGY, _SHOWER_LEGACY_INPUTS, _SHOWER_INPUTS,
        "cef285d2567522304d3425c4877373dd3d01b9b8316e28ca3b97d019ffde74be",
        "shower_controller.js", expected_revision=4, activation_ready=True,
    ),
    ManagedSwitchMigrationEntry(
        "system-small-corridor-light-controller", 3,
        "bc9a2c7883046e568a428e355af312953d70f0f504393b063130f516fe5052b1",
        MANAGED_TOPOLOGY, _SMALL_LEGACY_INPUTS, _SMALL_INPUTS,
        "718f13b5a6eb08f9a1eea3d5f844b841795d1089a4fa38e74fc08006b956ae0c",
        "small_corridor_controller.js", expected_revision=3, activation_ready=True,
    ),
    ManagedSwitchMigrationEntry(
        "system-tambur-adaptive-controller", 8,
        "4daef9ac2de8dc1c95dd2da6887e178751a65d0e47bcf48443635f68eb1ba5dc",
        MANAGED_TOPOLOGY, _TAMBUR_LEGACY_INPUTS, _TAMBUR_INPUTS,
        "d4577011013a1e93dc03b5bbc72c47c9a02573858b4b363701b8fb57658f31dc",
        "tambur_controller.js", expected_revision=8, activation_ready=True,
    ),
    ManagedSwitchMigrationEntry(
        "system-toilet-comfort-controller", 0,
        "218fa449363cb8d69cf366a670bf207846a7def9846da6fa130fa4157efb279d",
        MANAGED_TOPOLOGY, _TOILET_INPUTS, _TOILET_INPUTS,
        "e479089bce712f4f90b0e97388221fff2b9fa5b3837e162c1091d5f85c463cfa",
        "toilet_controller.js", operation="create", expected_revision=None,
        activation_ready=True,
    ),
    ManagedSwitchMigrationEntry(
        "system-bathroom-exhaust-controller", 0,
        "2b413c28d18f61f6730a3b31831bb73f98d690cb4d937ed75856f165475ebd74",
        MANAGED_TOPOLOGY, _BATHROOM_INPUTS, _BATHROOM_INPUTS,
        "38b5aede291a5c2e45af61831a1fae39f69344cc7dc41998ae64896aca218f0f",
        "bathroom_controller.js", operation="create", expected_revision=None,
        activation_ready=True,
    ),
    ManagedSwitchMigrationEntry(
        "system-storage-light-controller", 0,
        "04e1673e931b17da998f184a55645b302ea0b8de8707c3e10280c92e9fa65c31",
        MANAGED_TOPOLOGY, _STORAGE_INPUTS, _STORAGE_INPUTS,
        "04e1673e931b17da998f184a55645b302ea0b8de8707c3e10280c92e9fa65c31",
        "storage_controller.js", operation="create", expected_revision=None,
        activation_ready=True,
    ),
    ManagedSwitchMigrationEntry(
        "system-cabinet-light-controller", 0,
        "07e08025f24e98dfb67cef2f424a63a161b45b01d67dbe90456aad6055bf34ff",
        MANAGED_TOPOLOGY, _CABINET_INPUTS, _CABINET_INPUTS,
        "b5a5ba2637ae33f5b7aa96a957ad439632f3fa2b7137a9487afa52165c09c299",
        "cabinet_controller.js", operation="create", expected_revision=None,
        activation_ready=True,
    ),
    ManagedSwitchMigrationEntry(
        "system-curtains-privacy-controller", 0,
        "f0e93a8e5dd88a7828c0070878e28e0ef9df4fc54349ba1dca64bc2e76299b13",
        MANAGED_TOPOLOGY, _CURTAIN_INPUTS, _CURTAIN_INPUTS,
        "f0e93a8e5dd88a7828c0070878e28e0ef9df4fc54349ba1dca64bc2e76299b13",
        "curtains_controller.js", operation="create", expected_revision=None,
        activation_ready=True,
    ),
)
MIGRATION_MANIFEST = FULL_MIGRATION_MANIFEST
_RELEASE_SOURCE_FILES = frozenset(item.source_file for item in MIGRATION_MANIFEST)
_MAX_RELEASE_SOURCE_BYTES = 512 * 1024
LEGACY_MANAGED_SWITCHES = {
    item.scenario_id: (item.legacy_revision, item.legacy_source_hash)
    for item in FULL_MIGRATION_MANIFEST
}


class ManagedSwitchMigrationConflict(RuntimeError):
    """The durable receipt or live CAS evidence does not match the manifest."""


@dataclass(frozen=True, slots=True)
class ManagedSwitchActivation:
    """Prepared runtime callbacks and their coordinator-owned activation gate."""

    cleanup: Callable[[], None]
    commit: Callable[[], None]
    revoke: Callable[[], None]


def _manifest_hash_for(manifest: tuple[ManagedSwitchMigrationEntry, ...]) -> str:
    payload = [
        {
            "scenarioId": item.scenario_id,
            "operation": item.operation,
            "expectedRevision": item.expected_revision,
            "legacyRevision": item.legacy_revision,
            "legacySourceHash": item.legacy_source_hash,
            "legacyTopology": item.legacy_topology,
            "legacyInputs": item.legacy_input_target_ids,
            "inputs": item.input_target_ids,
            "newSourceHash": item.new_source_hash,
            "sourceFile": item.source_file,
            "activationReady": item.activation_ready,
        }
        for item in manifest
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


MANIFEST_HASH = _manifest_hash_for(MIGRATION_MANIFEST)
FULL_MANIFEST_HASH = _manifest_hash_for(FULL_MIGRATION_MANIFEST)


def valid_managed_switch_migration_payload(
    value: object,
    manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
) -> bool:
    manifest_hash = _manifest_hash_for(manifest)
    required = {
        "migrationId",
        "version",
        "state",
        "manifestHash",
        "dispositionVersion",
        "dispositionHash",
        "dispositionCounts",
    }
    revision = value.get("revision") if isinstance(value, Mapping) else None
    return bool(
        isinstance(value, Mapping)
        and required <= set(value)
        and set(value) - required <= {"journal", "revision"}
        and (revision is None or (type(revision) is int and revision >= 0))
        and value.get("migrationId") == MIGRATION_ID
        and value.get("version") == MIGRATION_VERSION
        and value.get("state") in {"prepared", "completed"}
        and value.get("manifestHash") == manifest_hash
        and value.get("dispositionVersion") == REGISTRY_DISPOSITION_VERSION
        and value.get("dispositionHash") == REGISTRY_DISPOSITION_HASH
        and value.get("dispositionCounts") == REGISTRY_DISPOSITION_COUNTS
        and (
            _valid_migration_journal(value.get("journal"), manifest)
            and (
                value.get("state") != "completed"
                or (
                    value["journal"].get("after") is not None
                    and value["journal"]["registry"].get("state") == "applied"
                    and all(
                        record.get("state") == "applied"
                        for record in value["journal"]["operations"].values()
                    )
                )
            )
        )
    )


def _valid_registry_image(value: object) -> bool:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"version", "scenarios"}
        or not isinstance(value.get("version"), int)
        or not isinstance(value.get("scenarios"), list)
    ):
        return False
    scenario_ids = [
        item.get("id") if isinstance(item, Mapping) else None
        for item in value["scenarios"]
    ]
    return bool(
        all(isinstance(scenario_id, str) and scenario_id for scenario_id in scenario_ids)
        and len(scenario_ids) == len(set(scenario_ids))
    )


def _valid_flow_image(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("state") == "absent":
        return set(value) == {"state"}
    return bool(
        set(value) == {
            "state", "flowId", "sourceHash", "source", "topology"
        }
        and value.get("state") == "present"
        and all(
            isinstance(value.get(key), str) and bool(value.get(key))
            for key in ("flowId", "sourceHash", "source", "topology")
        )
    )


def _valid_capture_image(
    value: object,
    manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"registry", "flows"}
        and _valid_registry_image(value.get("registry"))
        and isinstance(value.get("flows"), Mapping)
        and set(value["flows"]) == {
            item.scenario_id for item in manifest
        }
        and all(_valid_flow_image(item) for item in value["flows"].values())
    )


_OPERATION_KEYS = {
    "kind",
    "state",
    "flowId",
    "flowRevision",
    "expectedSourceHash",
    "newSourceHash",
    "previousSource",
}


def _valid_staged_operation(
    scenario_id: str, value: object, before: Mapping[str, object]
) -> bool:
    if (
        not isinstance(value, Mapping)
        or set(value) != _OPERATION_KEYS
        or value.get("kind") not in {"replace", "create"}
        or value.get("state") not in {"pending", "intent", "applied"}
        or not isinstance(value.get("newSourceHash"), str)
        or not value.get("newSourceHash")
    ):
        return False
    before_flow = before["flows"].get(scenario_id)
    if value["kind"] == "replace":
        return bool(
            isinstance(before_flow, Mapping)
            and before_flow.get("state") == "present"
            and value.get("flowId") == before_flow.get("flowId")
            and value.get("expectedSourceHash") == before_flow.get("sourceHash")
            and value.get("previousSource") == before_flow.get("source")
            and (
                value.get("flowRevision") is None
                or isinstance(value.get("flowRevision"), int)
            )
        )
    return bool(
        isinstance(before_flow, Mapping)
        and before_flow.get("state") == "absent"
        and value.get("expectedSourceHash") is None
        and value.get("previousSource") is None
        and (
            value.get("flowId") is None
            or isinstance(value.get("flowId"), str)
        )
        and (
            value.get("flowRevision") is None
            or isinstance(value.get("flowRevision"), int)
        )
        and (
            value.get("state") != "applied"
            or (
                isinstance(value.get("flowId"), str)
                and bool(value.get("flowId"))
                and isinstance(value.get("flowRevision"), int)
            )
        )
    )


def _valid_migration_journal(
    value: object,
    manifest_entries: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
) -> bool:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"before", "after", "operations", "registry"}
        or not _valid_capture_image(value.get("before"), manifest_entries)
        or (
            value.get("after") is not None
            and not _valid_capture_image(value.get("after"), manifest_entries)
        )
        or not isinstance(value.get("operations"), Mapping)
        or not isinstance(value.get("registry"), Mapping)
    ):
        return False
    manifest = {item.scenario_id: item for item in manifest_entries}
    operations = value["operations"]
    if set(operations) != set(manifest):
        return False
    for scenario_id, operation in operations.items():
        item = manifest[scenario_id]
        if (
            not _valid_staged_operation(
                scenario_id,
                operation,
                value["before"],
            )
            or operation.get("kind") != item.operation
            or operation.get("newSourceHash") != item.new_source_hash
        ):
            return False
    registry = value["registry"]
    if (
        set(registry) != {"state", "beforeHash", "afterHash"}
        or registry.get("state") not in {"pending", "intent", "applied"}
        or registry.get("beforeHash")
        != _storage_hash(value["before"]["registry"])
    ):
        return False
    after_hash = registry.get("afterHash")
    if registry["state"] == "pending":
        if after_hash is not None:
            return False
    elif not isinstance(after_hash, str) or not after_hash:
        return False
    after = value.get("after")
    if after is None:
        return True
    if (
        registry["state"] != "applied"
        or after_hash != _storage_hash(after["registry"])
    ):
        return False
    return all(
        after["flows"][scenario_id].get("state") == "present"
        and after["flows"][scenario_id].get("sourceHash")
        == item.new_source_hash
        and after["flows"][scenario_id].get("topology")
        == item.legacy_topology
        for scenario_id, item in manifest.items()
    )


def _storage_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def _clone_json(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _initial_journal(
    before: Mapping[str, object],
    manifest: tuple[ManagedSwitchMigrationEntry, ...],
) -> dict[str, object]:
    operations: dict[str, object] = {}
    flows = before["flows"]
    for item in manifest:
        flow = flows[item.scenario_id]
        operations[item.scenario_id] = {
            "kind": item.operation,
            "state": "pending",
            "flowId": flow.get("flowId") if item.operation == "replace" else None,
            "flowRevision": None,
            "expectedSourceHash": (
                flow.get("sourceHash") if item.operation == "replace" else None
            ),
            "newSourceHash": item.new_source_hash,
            "previousSource": (
                flow.get("source") if item.operation == "replace" else None
            ),
        }
    return {
        "before": _clone_json(before),
        "after": None,
        "operations": operations,
        "registry": {
            "state": "pending",
            "beforeHash": _storage_hash(before["registry"]),
            "afterHash": None,
        },
    }


def _is_known_completed_v2_receipt(value: object) -> bool:
    """Allow exactly the receipt observed before the v3 migration format."""

    return isinstance(value, Mapping) and dict(value) == _KNOWN_COMPLETED_V2_RECEIPT


def _valid_hash_text(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


_SUPERSEDED_RECEIPT_KEYS = {
    "migrationId",
    "version",
    "state",
    "revision",
    "manifestHash",
    "supersededManifestHash",
    "supersededState",
    "supersededAt",
    "dispositionVersion",
    "dispositionHash",
    "dispositionCounts",
}
_SUPERSEDED_RECEIPT_OPTIONAL_KEYS = {"supersededReceipt"}
_SUPERSEDED_PREVIOUS_STATES = {"prepared", "completed", "superseded"}
_SUPERSEDED_PREVIOUS_KEYS = {"state", "manifestHash", "revision", "journal"}


def _is_valid_superseded_previous(value: object) -> bool:
    """Validate the preserved previous completed receipt in a re-arm marker."""

    return bool(
        isinstance(value, Mapping)
        and set(value) == _SUPERSEDED_PREVIOUS_KEYS
        and value.get("state") == "completed"
        and _valid_hash_text(value.get("manifestHash"))
        and type(value.get("revision")) is int
        and value["revision"] >= 0
        and isinstance(value.get("journal"), Mapping)
    )


def _is_valid_superseded_receipt(value: object) -> bool:
    """Validate the explicit re-arm marker that replaces a stale receipt.

    The marker is intentionally manifest-agnostic: it records the newly
    expected ``manifestHash`` from a local-admin re-arm together with the
    previous receipt facts.  The startup path only proceeds when the fresh
    manifest is recomputed and its hash equals ``manifestHash``; any other
    value still fails closed.  A completed previous receipt may be preserved
    under ``supersededReceipt`` so startup can prove that the already-migrated
    registry did not drift before reconciling it.
    """

    if not isinstance(value, Mapping):
        return False
    keys = set(value)
    if not (
        _SUPERSEDED_RECEIPT_KEYS
        <= keys
        <= (_SUPERSEDED_RECEIPT_KEYS | _SUPERSEDED_RECEIPT_OPTIONAL_KEYS)
    ):
        return False
    revision = value.get("revision")
    previous_state = value.get("supersededState")
    return bool(
        value.get("migrationId") == MIGRATION_ID
        and value.get("version") == MIGRATION_VERSION
        and value.get("state") == "superseded"
        and type(revision) is int
        and revision >= 0
        and _valid_hash_text(value.get("manifestHash"))
        and (
            value.get("supersededManifestHash") is None
            or _valid_hash_text(value.get("supersededManifestHash"))
        )
        and (
            previous_state is None
            or previous_state in _SUPERSEDED_PREVIOUS_STATES
        )
        and isinstance(value.get("supersededAt"), str)
        and bool(value.get("supersededAt"))
        and value.get("dispositionVersion") == REGISTRY_DISPOSITION_VERSION
        and value.get("dispositionHash") == REGISTRY_DISPOSITION_HASH
        and value.get("dispositionCounts") == REGISTRY_DISPOSITION_COUNTS
        and (
            "supersededReceipt" not in value
            or _is_valid_superseded_previous(value["supersededReceipt"])
        )
    )


def _receipt(state: str) -> dict[str, object]:
    return {
        "migrationId": MIGRATION_ID,
        "version": MIGRATION_VERSION,
        "state": state,
        "manifestHash": MANIFEST_HASH,
        "dispositionVersion": REGISTRY_DISPOSITION_VERSION,
        "dispositionHash": REGISTRY_DISPOSITION_HASH,
        "dispositionCounts": dict(REGISTRY_DISPOSITION_COUNTS),
    }


def _safe_release_source(root: Path, source_file: str) -> bytes:
    """Read one bounded release asset without resolving a symlink."""

    if (
        source_file not in _RELEASE_SOURCE_FILES
        or Path(source_file).name != source_file
    ):
        raise ManagedSwitchMigrationConflict("release-owned source is unavailable")
    candidate = root / source_file
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    except AttributeError as error:
        raise ManagedSwitchMigrationConflict("release-owned source is unavailable") from error
    descriptor: int | None = None
    try:
        descriptor = os.open(candidate, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ManagedSwitchMigrationConflict("release-owned source is unavailable")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            source_bytes = handle.read(_MAX_RELEASE_SOURCE_BYTES + 1)
    except (OSError, ValueError) as error:
        raise ManagedSwitchMigrationConflict("release-owned source is unavailable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(source_bytes) > _MAX_RELEASE_SOURCE_BYTES:
        raise ManagedSwitchMigrationConflict("release-owned source exceeds safe bound")
    return source_bytes


def _entries_with_sources(
    manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
    *,
    root: Path | None = None,
) -> tuple[ManagedSwitchMigrationEntry, ...]:
    root = (root or Path(__file__).resolve().parents[1] / "managed_scenarios").resolve()
    entries: list[ManagedSwitchMigrationEntry] = []
    for item in manifest:
        source_bytes = _safe_release_source(root, item.source_file)
        if hashlib.sha256(source_bytes).hexdigest() != item.new_source_hash:
            raise ManagedSwitchMigrationConflict("release-owned source hash mismatch")
        try:
            source = source_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ManagedSwitchMigrationConflict("release-owned source is invalid") from error
        entries.append(
            ManagedSwitchMigrationEntry(
                item.scenario_id, item.legacy_revision, item.legacy_source_hash,
                item.legacy_topology, item.legacy_input_target_ids,
                item.input_target_ids, item.new_source_hash, item.source_file, source,
                item.operation, item.expected_revision, item.activation_ready,
            )
        )
    return tuple(entries)


async def async_load_managed_switch_migration_entries(
    add_executor_job: Callable[..., Awaitable[object]],
    manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
) -> tuple[ManagedSwitchMigrationEntry, ...]:
    entries = await add_executor_job(_entries_with_sources, manifest)
    if not isinstance(entries, tuple) or not all(
        isinstance(item, ManagedSwitchMigrationEntry) for item in entries
    ):
        raise ManagedSwitchMigrationConflict(
            "release-owned source loading returned invalid data"
        )
    return entries


def managed_switch_manifest_from_protected_topology(
    protected_topology: object,
) -> tuple[ManagedSwitchMigrationEntry, ...]:
    """Resolve migration entries from per-install protected roles.

    The public package keeps only scenario/source metadata.  Input target ids
    must come from the protected topology document; missing roles make startup
    fail closed instead of falling back to a baked-in target map.
    """

    expectations_getter = getattr(protected_topology, "migration_expectations", None)
    targets_getter = getattr(protected_topology, "targets_for_roles", None)
    if not callable(expectations_getter) or not callable(targets_getter):
        raise ManagedSwitchMigrationConflict("protected managed topology is unavailable")
    expectations = expectations_getter("managedSwitches")
    if not isinstance(expectations, tuple) or len(expectations) != len(MIGRATION_MANIFEST):
        raise ManagedSwitchMigrationConflict("protected managed topology scenario set is invalid")
    expected_scenarios = tuple(item.scenario_id for item in MIGRATION_MANIFEST)
    supplied_scenarios = tuple(
        item.get("scenarioId") if isinstance(item, Mapping) else None
        for item in expectations
    )
    if supplied_scenarios != expected_scenarios:
        raise ManagedSwitchMigrationConflict("protected managed topology scenario order is invalid")
    for item, release in zip(expectations, MIGRATION_MANIFEST, strict=True):
        if not isinstance(item, Mapping):
            raise ManagedSwitchMigrationConflict("protected managed topology is invalid")
        if (
            item.get("legacyRevision") != release.legacy_revision
            or item.get("legacySourceHash") != release.legacy_source_hash
            or item.get("legacyTopology") != release.legacy_topology
            or item.get("newSourceHash") != release.new_source_hash
            or item.get("sourceFile") != release.source_file
            or item.get("operation") != release.operation
            or item.get("expectedRevision") != release.expected_revision
            or item.get("activationReady") != release.activation_ready
        ):
            raise ManagedSwitchMigrationConflict("protected managed topology differs from release manifest")
    # The release manifest intentionally does not prescribe per-home
    # legacy/input role order. Resolving an unapproved tuple could wire a
    # controller to a different device, so no protected image may pass until
    # that mapping has a separately reviewed source of truth.
    raise ManagedSwitchMigrationConflict(
        "protected managed topology role order is not approved"
    )


class ManagedSwitchStartupCoordinator:
    def __init__(
        self,
        service: object,
        migration: object,
        activate: Callable[[], Awaitable[object]],
        *,
        binding_migration: object | None = None,
        status_publisher: Callable[[dict[str, str]], None] | None = None,
        manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
    ) -> None:
        self._service = service
        self._migration = migration
        self._binding_migration = binding_migration
        self._activate = activate
        self._status_publisher = status_publisher or (lambda _status: None)
        self._manifest = manifest
        self._remove_observer: Callable[[], None] | None = None
        self._activation_task: asyncio.Task[object] | None = None
        self._activation_cleanup: Callable[[], None] | None = None
        self._activation_commit: Callable[[], None] | None = None
        self._activation_revoke: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self._cancelled = False
        self._terminal = False
        self.activation_authorized = False
        self.ready = False

    async def async_start(self) -> None:
        if self._started or self._cancelled:
            return
        self._started = True
        add_observer = getattr(
            self._service, "add_catalog_warmup_observer", None
        )
        if not callable(add_observer):
            self._terminal = True
            self._publish("blocked", "catalog_warmup_unavailable")
            return
        self._remove_observer = add_observer(self._async_catalog_snapshot)
        await self._async_attempt(self._service.current_catalog(), final=False)

    def cancel(self) -> None:
        self._cancelled = True
        self.activation_authorized = False
        self.ready = False
        self._unsubscribe()
        activation_task = self._activation_task
        self._adopt_completed_activation(activation_task)
        if (
            activation_task is not None
            and not activation_task.done()
            and activation_task is not asyncio.current_task()
        ):
            activation_task.cancel()
        self._revoke_activation()
        self._cleanup_activation()

    async def _async_catalog_snapshot(self, catalog: object, final: bool) -> None:
        await self._async_attempt(catalog, final=final)

    async def _async_attempt(self, catalog: object, *, final: bool) -> None:
        async with self._lock:
            if self._cancelled or self._terminal or self.ready:
                return
            if not all(item.activation_ready for item in self._manifest):
                self._terminal = True
                self._unsubscribe()
                self._publish("blocked", "managed_controller_content_incomplete")
                return
            if not self._catalog_has_required_targets(catalog, self._manifest):
                if final:
                    self._terminal = True
                    self._unsubscribe()
                    self._publish("blocked", "catalog_retry_exhausted")
                else:
                    self._publish("waiting", "catalog_warmup")
                return
            try:
                await self._migration.async_apply()
                if self._binding_migration is not None:
                    await self._binding_migration.async_apply()
            except asyncio.CancelledError:
                raise
            except NativeAutomationNotReady:
                if final:
                    self._terminal = True
                    self._unsubscribe()
                    self._publish("blocked", "native_automation_retry_exhausted")
                else:
                    self._publish("waiting", "native_automation_warmup")
                return
            except Exception:  # noqa: BLE001
                self._terminal = True
                self._unsubscribe()
                self._publish("blocked", "verified_migration_failed")
                _LOGGER.error("Release-owned managed switch migration is blocked")
                return
            if self._cancelled:
                return
            self.activation_authorized = True
            activation_task = asyncio.create_task(self._activate())
            self._activation_task = activation_task
            try:
                activation = self._activation_from_result(await activation_task)
            except asyncio.CancelledError:
                self.activation_authorized = False
                self._revoke_activation()
                self._cleanup_activation()
                raise
            except Exception:  # noqa: BLE001
                self.activation_authorized = False
                self._revoke_activation()
                self._cleanup_activation()
                self._terminal = True
                self._unsubscribe()
                self._publish("blocked", "runtime_activation_failed")
                _LOGGER.error("Managed switch runtime activation is blocked")
                return
            finally:
                if self._activation_task is activation_task:
                    self._activation_task = None
            if self._cancelled or not self.activation_authorized:
                self._revoke_activation()
                self._cleanup_activation()
                return
            self._set_activation(activation)
            try:
                self._commit_activation()
            except Exception:  # noqa: BLE001
                self.activation_authorized = False
                self._revoke_activation()
                self._cleanup_activation()
                self._terminal = True
                self._unsubscribe()
                self._publish("blocked", "runtime_activation_failed")
                _LOGGER.error("Managed switch runtime activation is blocked")
                return
            self.ready = True
            self._terminal = True
            self._unsubscribe()
            self._publish("completed")

    @staticmethod
    def _catalog_has_required_targets(
        catalog: object,
        manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
    ) -> bool:
        resolve = getattr(catalog, "device", None)
        if not callable(resolve):
            return False
        return all(
            getattr(resolve(target_id), "target_id", None) == target_id
            for target_id in dict.fromkeys(
                target_id
                for entry in manifest
                for target_id in entry.input_target_ids
            )
        )

    def _publish(self, state: str, reason: str | None = None) -> None:
        payload = {"state": state}
        if reason is not None:
            payload["reason"] = reason
        self._status_publisher(payload)

    def _unsubscribe(self) -> None:
        remove = self._remove_observer
        self._remove_observer = None
        if remove is not None:
            remove()

    def _cleanup_activation(self) -> None:
        cleanup = self._activation_cleanup
        self._activation_cleanup = None
        if cleanup is None:
            return
        try:
            cleanup()
        except Exception:  # noqa: BLE001
            _LOGGER.error("Managed switch activation cleanup failed")

    @staticmethod
    def _activation_from_result(result: object) -> ManagedSwitchActivation:
        if isinstance(result, ManagedSwitchActivation):
            return result
        if callable(result):
            return ManagedSwitchActivation(result, lambda: None, lambda: None)
        cleanup = getattr(result, "cleanup", None)
        commit = getattr(result, "commit", None)
        revoke = getattr(result, "revoke", None)
        if all(callable(item) for item in (cleanup, commit, revoke)):
            return ManagedSwitchActivation(cleanup, commit, revoke)
        if result is None:
            return ManagedSwitchActivation(lambda: None, lambda: None, lambda: None)
        raise TypeError("managed switch activation returned an invalid handle")

    def _adopt_completed_activation(self, task: asyncio.Task[object] | None) -> None:
        if task is None or not task.done() or self._activation_cleanup is not None:
            return
        try:
            activation = self._activation_from_result(task.result())
        except (asyncio.CancelledError, Exception):
            return
        self._set_activation(activation)

    def _set_activation(self, activation: ManagedSwitchActivation) -> None:
        self._activation_cleanup = activation.cleanup
        self._activation_commit = activation.commit
        self._activation_revoke = activation.revoke

    def _commit_activation(self) -> None:
        commit = self._activation_commit
        self._activation_commit = None
        if commit is None:
            raise RuntimeError("managed switch activation was not prepared")
        commit()

    def _revoke_activation(self) -> None:
        revoke = self._activation_revoke
        self._activation_revoke = None
        if revoke is None:
            return
        try:
            revoke()
        except Exception:  # noqa: BLE001
            _LOGGER.error("Managed switch activation revoke failed")


class HomeAssistantManagedSwitchMigrationStore:
    """Verified atomic HA storage for the migration receipt."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
    ) -> None:
        from homeassistant.helpers.storage import Store  # noqa: PLC0415
        from ..verified_safety_storage import VerifiedSafetyStore  # noqa: PLC0415

        store: Store[dict[str, object]] = Store(
            hass, 1, f"hausman_hub.managed_switch_migration.{entry_id}", atomic_writes=True
        )
        self._store = VerifiedSafetyStore(
            store, hass.async_add_executor_job,
            payload_validator=lambda value: (
                valid_managed_switch_migration_payload(value, manifest)
                or _is_known_completed_v2_receipt(value)
                or _is_valid_superseded_receipt(value)
            ),
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)


class ManagedSwitchMigration:
    def __init__(
        self,
        service: object,
        store: object,
        *,
        source_loader: Callable[
            [], Awaitable[tuple[ManagedSwitchMigrationEntry, ...]]
        ]
        | None = None,
        native_automation_migration: object | None = None,
        manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
        mutation_lock: asyncio.Lock | None = None,
    ) -> None:
        self._service = service
        self._store = store
        self._source_loader = source_loader
        self._native_automation_migration = native_automation_migration
        self._manifest = manifest
        self._manifest_hash = _manifest_hash_for(manifest)
        self._revision = 0
        self._mutation_lock = mutation_lock or asyncio.Lock()

    def _receipt(
        self, state: str, journal: dict[str, object] | None = None
    ) -> dict[str, object]:
        # Every durable write advances the revision so an old admin snapshot of
        # ``expectedReceiptRevision`` can never re-arm a receipt that has since
        # moved to another prepared/completed image.
        self._revision += 1
        receipt: dict[str, object] = {
            "migrationId": MIGRATION_ID,
            "version": MIGRATION_VERSION,
            "state": state,
            "revision": self._revision,
            "manifestHash": self._manifest_hash,
            "dispositionVersion": REGISTRY_DISPOSITION_VERSION,
            "dispositionHash": REGISTRY_DISPOSITION_HASH,
            "dispositionCounts": dict(REGISTRY_DISPOSITION_COUNTS),
        }
        if journal is not None:
            receipt["journal"] = journal
        return receipt

    @staticmethod
    async def _async_verify(
        verify: Callable[..., Awaitable[object]],
        entries: tuple[ManagedSwitchMigrationEntry, ...],
        journal: Mapping[str, object],
        *,
        require_final: bool,
    ) -> None:
        parameters = inspect.signature(verify).parameters
        keyword_arguments: dict[str, object] = {}
        if "journal" in parameters:
            keyword_arguments["journal"] = journal
        if "require_final" in parameters:
            keyword_arguments["require_final"] = require_final
        await verify(entries, **keyword_arguments)

    @staticmethod
    async def _async_commit(
        commit: Callable[..., Awaitable[object]],
        entries: tuple[ManagedSwitchMigrationEntry, ...],
        journal: Mapping[str, object],
    ) -> None:
        keyword_arguments: dict[str, object] = {}
        if "journal" in inspect.signature(commit).parameters:
            keyword_arguments["journal"] = journal
        await commit(entries, **keyword_arguments)

    async def async_apply(self) -> str:
        async with self._mutation_lock:
            return await self._async_apply_locked()

    async def _async_apply_locked(self) -> str:
        loaded = await self._store.async_load()
        legacy_completed = _is_known_completed_v2_receipt(loaded)
        superseded = _is_valid_superseded_receipt(loaded)
        if (
            isinstance(loaded, Mapping)
            and type(loaded.get("revision")) is int
            and loaded["revision"] >= 0
        ):
            self._revision = loaded["revision"]
        if loaded is not None and not legacy_completed and not superseded and not (
            valid_managed_switch_migration_payload(loaded, self._manifest)
            and loaded.get("manifestHash") == self._manifest_hash
        ):
            raise ManagedSwitchMigrationConflict("migration receipt is invalid")
        if superseded and loaded.get("manifestHash") != self._manifest_hash:
            raise ManagedSwitchMigrationConflict("migration receipt is invalid")
        completed = (
            not legacy_completed
            and not superseded
            and isinstance(loaded, Mapping)
            and loaded.get("state") == "completed"
            and _valid_migration_journal(loaded.get("journal"), self._manifest)
        )
        apply = getattr(self._service, "async_apply_managed_switch_migration", None)
        if not callable(apply):
            raise ManagedSwitchMigrationConflict("scenario migration CAS is unavailable")
        entries = (
            await self._source_loader()
            if self._source_loader is not None
            else _entries_with_sources(self._manifest)
        )
        if self._native_automation_migration is not None:
            require_ready = getattr(
                self._native_automation_migration, "async_require_ready", None
            )
            if callable(require_ready):
                await require_ready()
        if superseded and loaded.get("supersededState") == "completed":
            return await self._async_reconcile_completed(loaded, entries)
        capture = getattr(
            self._service, "async_capture_managed_switch_migration", None
        )
        if not callable(capture):
            raise ManagedSwitchMigrationConflict("scenario migration journal is unavailable")
        if completed:
            journal = _clone_json(loaded["journal"])
            verify = getattr(
                self._service, "async_verify_managed_switch_migration", None
            )
            if not callable(verify):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration final CAS verification is unavailable"
                )
            try:
                await self._async_verify(
                    verify, entries, journal, require_final=True
                )
            except Exception as error:
                raise ManagedSwitchMigrationConflict(
                    "scenario migration completed image drifted"
                ) from error
            current = await capture(entries)
            if _clone_json(current) != journal["after"]:
                raise ManagedSwitchMigrationConflict(
                    "scenario migration completed image drifted"
                )
            if self._native_automation_migration is not None:
                native_verify = getattr(
                    self._native_automation_migration,
                    "async_verify_completed",
                    None,
                )
                if not callable(native_verify) or not await native_verify():
                    raise ManagedSwitchMigrationConflict(
                        "native automation completion drifted"
                    )
            commit = getattr(
                self._service, "async_commit_managed_switch_migration", None
            )
            if callable(commit):
                await self._async_commit(commit, entries, journal)
            return "completed"

        if (
            isinstance(loaded, Mapping)
            and loaded.get("state") == "prepared"
            and _valid_migration_journal(loaded.get("journal"), self._manifest)
        ):
            journal = _clone_json(loaded["journal"])
        else:
            before = await capture(entries)
            if not _valid_capture_image(before, self._manifest):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration before-image is invalid"
                )
            journal = _initial_journal(before, entries)
            await self._store.async_save(self._receipt("prepared", journal))

        stage = getattr(self._service, "async_stage_managed_switch_migration", None)
        applied = False
        native_applied = False
        try:
            if callable(stage):
                async def persist_staged(staged: Mapping[str, object]) -> None:
                    journal["operations"] = _clone_json(staged)
                    await self._store.async_save(
                        self._receipt("prepared", journal)
                    )

                stage_parameters = inspect.signature(stage).parameters
                if "journal" in stage_parameters:
                    await stage(
                        entries,
                        journal=journal,
                        on_staged=persist_staged,
                    )
                else:
                    await stage(entries, on_staged=persist_staged)
            apply_parameters = inspect.signature(apply).parameters

            async def persist_registry(state: Mapping[str, object]) -> None:
                journal["registry"] = _clone_json(state)
                await self._store.async_save(self._receipt("prepared", journal))

            if "journal" in apply_parameters:
                apply_state = await apply(
                    entries,
                    journal=journal,
                    on_registry=persist_registry,
                )
            else:
                apply_state = await apply(entries)
            applied = True
            verify = getattr(
                self._service,
                "async_verify_managed_switch_migration",
                None,
            )
            if not callable(verify):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration final CAS verification is unavailable"
                )
            await self._async_verify(
                verify,
                entries,
                journal,
                require_final=apply_state == "finalized",
            )
            if self._native_automation_migration is not None:
                native_apply = getattr(
                    self._native_automation_migration, "async_apply", None
                )
                if not callable(native_apply):
                    raise ManagedSwitchMigrationConflict(
                        "native automation handover is unavailable"
                    )
                await native_apply()
                native_applied = True
            finalize = getattr(
                self._service,
                "async_finalize_managed_switch_migration",
                None,
            )
            if not callable(finalize):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration finalization is unavailable"
                )
            finalize_parameters = inspect.signature(finalize).parameters
            if "journal" in finalize_parameters:
                await finalize(
                    entries,
                    journal=journal,
                    on_registry=persist_registry,
                )
            else:
                await finalize(entries)
            after = await capture(entries)
            if not _valid_capture_image(after, self._manifest):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration after-image is invalid"
                )
            journal["after"] = _clone_json(after)
            await self._store.async_save(self._receipt("completed", journal))
            await self._async_verify(
                verify, entries, journal, require_final=True
            )
            if _clone_json(await capture(entries)) != journal["after"]:
                raise ManagedSwitchMigrationConflict(
                    "scenario migration final image changed"
                )
            if self._native_automation_migration is not None:
                native_verify = getattr(
                    self._native_automation_migration,
                    "async_verify_completed",
                    None,
                )
                if not callable(native_verify) or not await native_verify():
                    raise ManagedSwitchMigrationConflict(
                        "native automation final image changed"
                    )
            commit = getattr(
                self._service, "async_commit_managed_switch_migration", None
            )
            if callable(commit):
                await self._async_commit(commit, entries, journal)
        except BaseException as error:
            try:
                await asyncio.shield(
                    self._store.async_save(self._receipt("prepared", journal))
                )
            except Exception:  # noqa: BLE001
                pass
            rollback = getattr(
                self._service,
                "async_rollback_managed_switch_migration",
                None,
            )
            rollback_complete = False
            if callable(rollback):
                try:
                    rollback_parameters = inspect.signature(rollback).parameters
                    rollback_call = (
                        rollback(entries, journal=journal)
                        if "journal" in rollback_parameters
                        else rollback(entries)
                    )
                    rollback_complete = (
                        await asyncio.shield(rollback_call) is True
                    )
                except BaseException:  # noqa: BLE001
                    rollback_complete = False
            if not rollback_complete:
                raise ManagedSwitchMigrationConflict(
                    "managed switch migration recovery is required"
                ) from error
            if native_applied:
                native_rollback = getattr(
                    self._native_automation_migration, "async_rollback", None
                )
                if not callable(native_rollback) or not await native_rollback():
                    raise ManagedSwitchMigrationConflict(
                        "native automation recovery is required"
                    ) from error
            reset = _initial_journal(journal["before"], entries)
            try:
                await asyncio.shield(
                    self._store.async_save(self._receipt("prepared", reset))
                )
            except BaseException as recovery_error:
                raise ManagedSwitchMigrationConflict(
                    "managed switch migration journal recovery is required"
                ) from recovery_error
            raise
        return "completed"

    async def _async_reconcile_completed(
        self,
        loaded: Mapping[str, object],
        entries: tuple[ManagedSwitchMigrationEntry, ...],
    ) -> str:
        """Re-adopt an already-completed migration under a new manifest.

        Only input-target changes are provable without touching Node-RED: the
        preserved completed journal must still validate against the fresh
        manifest, the service re-checks that the live registry and flows equal
        the completed ``after`` image, and then only scenario input metadata is
        updated.  A changed source hash, operation or scenario set fails closed.
        """

        previous = loaded.get("supersededReceipt")
        if not _is_valid_superseded_previous(previous):
            raise ManagedSwitchMigrationConflict(
                "completed migration re-arm lacks the previous receipt"
            )
        journal = previous["journal"]  # type: ignore[index]
        if not _valid_migration_journal(journal, self._manifest):
            raise ManagedSwitchMigrationConflict(
                "completed migration journal is not compatible with the manifest"
            )
        reconcile = getattr(
            self._service,
            "async_reconcile_completed_managed_switch_migration",
            None,
        )
        if not callable(reconcile):
            raise ManagedSwitchMigrationConflict(
                "completed migration reconcile is unavailable"
            )
        try:
            after = await reconcile(entries, journal)
        except ManagedSwitchMigrationConflict:
            raise
        except Exception as error:  # noqa: BLE001
            raise ManagedSwitchMigrationConflict(
                "completed migration reconcile failed"
            ) from error
        if not _valid_capture_image(after, self._manifest):
            raise ManagedSwitchMigrationConflict(
                "completed migration reconcile image is invalid"
            )
        reconciled = _clone_json(journal)
        reconciled["after"] = _clone_json(after)
        reconciled["registry"] = {
            "state": "applied",
            "beforeHash": journal["registry"]["beforeHash"],  # type: ignore[index]
            "afterHash": _storage_hash(after["registry"]),  # type: ignore[index]
        }
        await self._store.async_save(self._receipt("completed", reconciled))
        capture = getattr(
            self._service, "async_capture_managed_switch_migration", None
        )
        if not callable(capture) or _clone_json(await capture(entries)) != reconciled["after"]:
            raise ManagedSwitchMigrationConflict(
                "completed migration reconciled image changed"
            )
        if self._native_automation_migration is not None:
            native_verify = getattr(
                self._native_automation_migration, "async_verify_completed", None
            )
            if not callable(native_verify) or not await native_verify():
                raise ManagedSwitchMigrationConflict(
                    "native automation completion drifted"
                )
        return "completed"
