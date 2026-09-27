"""Durable file-backed ControlStore of the core (S-04, S-05).

One whole-image document per store: encode the typed state, validate the
whole next image, write it through the existing atomic helper of the
integration, add the missing directory sync and a verification re-read,
and load typed state back. The store never decides rights and never
sends anything; it does not introduce a database (S-04).
"""

from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping

from ...application.core.command_service import (
    CommitConflict,
    ManualReceipt,
    ManualStatus,
    StorageUnavailable,
)
from ...domain.core.models import (
    Authority,
    AutomaticScope,
    AutoProof,
    ControlRecord,
    Evidence,
    LightStateTarget,
    ManualHold,
    ObservationSnapshot,
    Operation,
    OperationPhase,
    Origin,
    SwitchStateTarget,
    Target,
)

IMAGE_SCHEMA_VERSION = 1

_COMMIT = "commit_revision"
_RECORDS = "records"
_OPERATIONS = "operations"
_RECEIPTS = "receipts"
_RECEIPT_TIMES = "receipt_times"
_RECEIPT_FINGERPRINTS = "receipt_fingerprints"
_CONFIRM_KEYS = "confirm_keys"
_EPISODE_LOCKS = "episode_locks"
_SCOPE_REVISIONS = "scope_revisions"
_CONFIG_REVISIONS = "config_revisions"
_BOOT_ID = "boot_id"

# Checkpoints where a test may inject an OSError/TimeoutError to emulate
# a failing or interrupted file operation (S-T04, D-08, D-12).
FAULT_POINTS = (
    "tmp_open",
    "tmp_write",
    "file_fsync",
    "replace",
    "dir_fsync",
    "reread",
)


class StoreImageError(Exception):
    """The stored image is damaged or of an unsupported version (S-04):
    the touched store is closed for changes until a human decides."""


_REQUIRED_SECTIONS = (
    _COMMIT,
    _RECORDS,
    _OPERATIONS,
    _RECEIPTS,
    _RECEIPT_TIMES,
    _RECEIPT_FINGERPRINTS,
    _CONFIRM_KEYS,
    _EPISODE_LOCKS,
    _SCOPE_REVISIONS,
    _CONFIG_REVISIONS,
)


def _strict_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise StoreImageError(f"{name} must be an exact int: {value!r}")
    return value


def validate_image_shape(raw: Mapping) -> None:
    """Strict whole-image validation before writing and after reading
    (SEC-S04-02): exact types, mandatory sections, no unknown sections."""

    if not isinstance(raw, Mapping):
        raise StoreImageError("stored image is not an object")
    unknown = set(raw) - {
        "schema_version",
        *_REQUIRED_SECTIONS,
        _BOOT_ID,
    }
    if unknown:
        raise StoreImageError(f"unknown image sections: {sorted(unknown)}")
    if raw.get("schema_version") != IMAGE_SCHEMA_VERSION or type(
        raw.get("schema_version")
    ) is not int:
        raise StoreImageError(
            f"unsupported schema_version: {raw.get('schema_version')!r}"
        )
    _strict_int(raw.get(_COMMIT), _COMMIT)
    for section in (
        _RECORDS,
        _OPERATIONS,
        _RECEIPTS,
        _RECEIPT_TIMES,
        _RECEIPT_FINGERPRINTS,
        _CONFIRM_KEYS,
        _EPISODE_LOCKS,
        _SCOPE_REVISIONS,
        _CONFIG_REVISIONS,
    ):
        if not isinstance(raw.get(section), Mapping):
            raise StoreImageError(f"section {section} must be an object")


class FileControlStore:
    """File implementation of the ControlStore port."""

    def __init__(
        self,
        path: str | Path,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._path = Path(path)
        self._fault_injector = fault_injector
        self.fail_next_commit = False
        self.fail_forever = False
        self.fail_at_commit_revisions: set[int] = set()
        self._closed = False
        self._commit_revision = 0
        loaded = self.load()
        if loaded is not None:
            self._commit_revision = int(loaded[_COMMIT])

    @property
    def commit_revision(self) -> int:
        return self._commit_revision

    def healthy(self) -> bool:
        # After an undefined write the store stays closed until an
        # explicit reload re-syncs it with the durable truth (S-04).
        return not self._closed

    # -- port ------------------------------------------------------------

    def load(self) -> dict | None:
        """Read and fully validate the stored image; ``None`` when the
        store is empty. A damaged image raises and closes the store."""

        if not self._path.exists():
            return None
        try:
            raw = json.loads(
                self._path.read_text(encoding="utf-8"),
                parse_constant=lambda name: (_ for _ in ()).throw(
                    StoreImageError(f"non-finite number: {name}")
                ),
            )
        except StoreImageError:
            self._closed = True
            raise
        except Exception as exc:
            # A failing load closes the existing instance too: it must
            # never overwrite a file it could not verify (SEC-S04-02).
            self._closed = True
            raise StoreImageError(f"stored image is unreadable: {exc}") from exc
        if not isinstance(raw, Mapping):
            self._closed = True
            raise StoreImageError("stored image is not an object")
        validate_image_shape(raw)
        state = decode_state(raw)
        self._commit_revision = _strict_int(state[_COMMIT], _COMMIT)
        self._closed = False
        return state

    def commit(self, expected_commit_revision: int, next_state: Mapping) -> None:
        if self._closed:
            raise StorageUnavailable("store closed after an earlier failure")
        if self.fail_forever:
            raise StorageUnavailable("file store outage")
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise StorageUnavailable("file store commit failure")
        if self._commit_revision + 1 in self.fail_at_commit_revisions:
            raise StorageUnavailable("file store commit failure")
        if expected_commit_revision != self._commit_revision:
            raise CommitConflict(
                f"expected {expected_commit_revision}, "
                f"actual {self._commit_revision}"
            )
        # The ORIGINAL next state is checked before encoding: a missing or
        # unknown section, or a wrong revision type, is refused without
        # any silent normalization (SEC-S04-02).
        if not isinstance(next_state, Mapping):
            raise StoreImageError("next state must be an object")
        missing = [
            section
            for section in _REQUIRED_SECTIONS
            if section not in next_state
        ]
        if missing:
            raise StoreImageError(f"missing sections: {missing}")
        unknown_sections = set(next_state) - {
            *_REQUIRED_SECTIONS,
            "schema_version",
            _BOOT_ID,
        }
        if unknown_sections:
            raise StoreImageError(
                f"unknown next-state sections: {sorted(unknown_sections)}"
            )
        if "commit_revision" in next_state:
            _strict_int(next_state["commit_revision"], _COMMIT)
        try:
            encoded = encode_state(next_state)
            if not isinstance(encoded, dict):
                raise StorageUnavailable(
                    "image encoding produced a non-object"
                )
            encoded[_COMMIT] = expected_commit_revision + 1
            encoded["schema_version"] = IMAGE_SCHEMA_VERSION
            validate_image_shape(encoded)
        except StorageUnavailable:
            raise
        except Exception as exc:
            # A refused next image changes nothing on disk (D-16).
            raise StoreImageError(f"next image is invalid: {exc}") from exc
        try:
            self._write_atomic(encoded)
        except Exception as exc:
            # Any failure of the file stage — including a verification
            # re-read mismatch — leaves the outcome undefined, so the
            # store closes until an explicit reload (SEC-S04-01).
            self._closed = True
            if isinstance(exc, StorageUnavailable):
                raise
            raise StorageUnavailable(f"file store write failed: {exc}") from exc
        # durable only after verified re-read of the whole image
        self._commit_revision = expected_commit_revision + 1

    # -- atomic file path ------------------------------------------------

    def _inject(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    def _write_atomic(self, encoded: Mapping) -> None:
        target = self._path
        target.parent.mkdir(parents=True, exist_ok=True)
        self._inject("tmp_open")
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        try:
            self._inject("tmp_write")
            with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                json.dump(
                    encoded,
                    destination,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                destination.flush()
                self._inject("file_fsync")
                os.fsync(destination.fileno())
            os.chmod(temporary_path, 0o600)
            self._inject("replace")
            os.replace(temporary_path, target)
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
        self._inject("dir_fsync")
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self._inject("reread")
        reread = json.loads(self._path.read_text(encoding="utf-8"))
        if reread != encoded:
            raise StorageUnavailable(
                "file store verification re-read mismatched the image"
            )


# -- typed image encoding -------------------------------------------------


def _encode_target(target: Target) -> dict:
    if isinstance(target, SwitchStateTarget):
        return {"kind": "switch_state", "on": target.on}
    return {
        "kind": "light_state",
        "on": target.on,
        "brightness_pct": (
            None if target.brightness_pct is None
            else str(target.brightness_pct)
        ),
        "color_temperature_k": target.color_temperature_k,
    }


def _decode_target(raw: Mapping) -> Target:
    kind = raw.get("kind")
    on = raw.get("on")
    if type(on) is not bool:
        raise StoreImageError(f"target 'on' must be a bool: {on!r}")
    if kind == "switch_state":
        return SwitchStateTarget(on=on)
    if kind == "light_state":
        brightness = raw.get("brightness_pct")
        if brightness is not None:
            brightness = Decimal(str(brightness))
            if not brightness.is_finite():
                raise StoreImageError("brightness must be finite")
        return LightStateTarget(
            on=on,
            brightness_pct=brightness,
            color_temperature_k=raw.get("color_temperature_k"),
        )
    raise StoreImageError(f"unknown target kind: {kind!r}")


def _encode_fingerprint_part(part: object) -> object:
    if isinstance(part, (LightStateTarget, SwitchStateTarget)):
        return {"fingerprint_target": _encode_target(part)}
    return part


def _decode_fingerprint_part(part: object) -> object:
    if isinstance(part, Mapping) and "fingerprint_target" in part:
        return _decode_target(part["fingerprint_target"])
    return part


def encode_state(state: Mapping) -> dict:
    """Convert the typed service state into a JSON-safe whole image."""

    operations = {}
    for operation_id, operation in state.get("operations", {}).items():
        operations[operation_id] = {
            "operation_id": operation.operation_id,
            "resource_id": operation.resource_id,
            "decision_id": operation.decision_id,
            "origin": operation.origin.value,
            "target": _encode_target(operation.target),
            "generation": operation.generation,
            "control_revision": operation.control_revision,
            "config_revision": operation.config_revision,
            "binding_revision": operation.binding_revision,
            "scope_revisions": dict(operation.scope_revisions),
            "created_at_mono_ms": operation.created_at_mono_ms,
            "phase": operation.phase.value,
            "request_id": operation.request_id,
            "module_id": operation.module_id,
            "episode_id": operation.episode_id,
            "expires_at_mono_ms": operation.expires_at_mono_ms,
            "dispatch_started_at_mono_ms": (
                operation.dispatch_started_at_mono_ms
            ),
            "confirmation_deadline_mono_ms": (
                operation.confirmation_deadline_mono_ms
            ),
            "pre_dispatch_sequence": operation.pre_dispatch_sequence,
            "reason_code": operation.reason_code,
            "evidence_refs": list(operation.evidence_refs),
        }
    receipts = {}
    for request_id, receipt in state.get("receipts", {}).items():
        receipts[request_id] = {
            "request_id": receipt.request_id,
            "status": receipt.status.value,
            "reason_code": receipt.reason_code,
            "operation_id": receipt.operation_id,
            "control_revision": receipt.control_revision,
            "generation": receipt.generation,
            "idempotent_replay": receipt.idempotent_replay,
        }
    image = {
        _COMMIT: state[_COMMIT],
        _BOOT_ID: state.get(_BOOT_ID),
        _RECORDS: {
            resource_id: {
                "resource_id": record.resource_id,
                "control_revision": record.control_revision,
                "generation": record.generation,
                "binding_revision": record.binding_revision,
                "authority": record.authority.value,
                "automatic_scope": record.automatic_scope.value,
                "manual": None
                if record.manual is None
                else {
                    "action_id": record.manual.action_id,
                    "action": record.manual.action,
                    "source": record.manual.source,
                    "proven_at_utc_ms": record.manual.proven_at_utc_ms,
                    "priority_until_utc_ms": (
                        record.manual.priority_until_utc_ms
                    ),
                    "minimum_off_until_utc_ms": (
                        record.manual.minimum_off_until_utc_ms
                    ),
                    "policy_revision": record.manual.policy_revision,
                },
                "auto_proof": None
                if record.auto_proof is None
                else {
                    "operation_id": record.auto_proof.operation_id,
                    "observation_id": record.auto_proof.observation_id,
                    "confirmed_at_utc_ms": (
                        record.auto_proof.confirmed_at_utc_ms
                    ),
                    "generation": record.auto_proof.generation,
                    "binding_revision": record.auto_proof.binding_revision,
                    "confirmed_target": _encode_target(
                        record.auto_proof.confirmed_target
                    ),
                    "confirmed_observation": {
                        "observation_id": (
                            record.auto_proof.confirmed_observation.observation_id
                        ),
                        "values": dict(
                            record.auto_proof.confirmed_observation.values
                        ),
                        "evidence": (
                            record.auto_proof.confirmed_observation.evidence.value
                        ),
                    },
                    "source_evidence": record.auto_proof.source_evidence.value,
                },
                "uncertainty_reason": record.uncertainty_reason,
            }
            for resource_id, record in state.get(_RECORDS, {}).items()
        },
        _OPERATIONS: operations,
        _RECEIPTS: receipts,
        _RECEIPT_TIMES: dict(state.get(_RECEIPT_TIMES, {})),
        _RECEIPT_FINGERPRINTS: {
            request_id: [
                _encode_fingerprint_part(part) for part in fingerprint
            ]
            for request_id, fingerprint in state.get(
                _RECEIPT_FINGERPRINTS, {}
            ).items()
        },
        _CONFIRM_KEYS: {
            operation_id: list(key)
            for operation_id, key in state.get(_CONFIRM_KEYS, {}).items()
        },
        _EPISODE_LOCKS: dict(state.get(_EPISODE_LOCKS, {})),
        _SCOPE_REVISIONS: {
            key: _strict_int(value, f"scope_revisions[{key}]")
            for key, value in state.get(_SCOPE_REVISIONS, {}).items()
        },
        _CONFIG_REVISIONS: {
            key: _strict_int(value, f"config_revisions[{key}]")
            for key, value in state.get(_CONFIG_REVISIONS, {}).items()
        },
        _RECEIPT_TIMES: {
            key: _strict_int(value, f"receipt_times[{key}]")
            for key, value in state.get(_RECEIPT_TIMES, {}).items()
        },
    }
    return image


def decode_state(raw: Mapping) -> dict:
    """Rebuild the typed state from a stored image, validating every
    field; any deviation raises ``StoreImageError``."""

    try:
        records = {}
        for resource_id, raw_record in raw[_RECORDS].items():
            manual_raw = raw_record["manual"]
            proof_raw = raw_record["auto_proof"]
            auto_proof = None
            if proof_raw is not None:
                auto_proof = AutoProof(
                    operation_id=proof_raw["operation_id"],
                    observation_id=proof_raw["observation_id"],
                    confirmed_at_utc_ms=proof_raw["confirmed_at_utc_ms"],
                    generation=proof_raw["generation"],
                    binding_revision=proof_raw["binding_revision"],
                    confirmed_target=_decode_target(
                        proof_raw["confirmed_target"]
                    ),
                    confirmed_observation=ObservationSnapshot(
                        observation_id=(
                            proof_raw["confirmed_observation"][
                                "observation_id"
                            ]
                        ),
                        values=dict(
                            proof_raw["confirmed_observation"]["values"]
                        ),
                        evidence=Evidence(
                            proof_raw["confirmed_observation"]["evidence"]
                        ),
                    ),
                    source_evidence=Evidence(proof_raw["source_evidence"]),
                )
            records[resource_id] = ControlRecord(
                resource_id=raw_record["resource_id"],
                control_revision=raw_record["control_revision"],
                generation=raw_record["generation"],
                binding_revision=raw_record["binding_revision"],
                authority=Authority(raw_record["authority"]),
                automatic_scope=AutomaticScope(raw_record["automatic_scope"]),
                manual=None
                if manual_raw is None
                else ManualHold(
                    action_id=manual_raw["action_id"],
                    action=manual_raw["action"],
                    source=manual_raw["source"],
                    proven_at_utc_ms=manual_raw["proven_at_utc_ms"],
                    priority_until_utc_ms=manual_raw["priority_until_utc_ms"],
                    minimum_off_until_utc_ms=(
                        manual_raw["minimum_off_until_utc_ms"]
                    ),
                    policy_revision=manual_raw["policy_revision"],
                ),
                auto_proof=auto_proof,
                uncertainty_reason=raw_record["uncertainty_reason"],
            )
        operations = {}
        for operation_id, raw_operation in raw[_OPERATIONS].items():
            operations[operation_id] = Operation(
                operation_id=raw_operation["operation_id"],
                resource_id=raw_operation["resource_id"],
                decision_id=raw_operation["decision_id"],
                origin=Origin(raw_operation["origin"]),
                target=_decode_target(raw_operation["target"]),
                generation=raw_operation["generation"],
                control_revision=raw_operation["control_revision"],
                config_revision=raw_operation["config_revision"],
                binding_revision=raw_operation["binding_revision"],
                scope_revisions=dict(raw_operation["scope_revisions"]),
                created_at_mono_ms=raw_operation["created_at_mono_ms"],
                phase=OperationPhase(raw_operation["phase"]),
                request_id=raw_operation["request_id"],
                module_id=raw_operation["module_id"],
                episode_id=raw_operation["episode_id"],
                expires_at_mono_ms=raw_operation["expires_at_mono_ms"],
                dispatch_started_at_mono_ms=(
                    raw_operation["dispatch_started_at_mono_ms"]
                ),
                confirmation_deadline_mono_ms=(
                    raw_operation["confirmation_deadline_mono_ms"]
                ),
                pre_dispatch_sequence=raw_operation["pre_dispatch_sequence"],
                reason_code=raw_operation["reason_code"],
                evidence_refs=tuple(raw_operation["evidence_refs"]),
            )
        receipts = {}
        for request_id, raw_receipt in raw[_RECEIPTS].items():
            receipts[request_id] = ManualReceipt(
                request_id=raw_receipt["request_id"],
                status=ManualStatus(raw_receipt["status"]),
                reason_code=raw_receipt["reason_code"],
                operation_id=raw_receipt["operation_id"],
                control_revision=raw_receipt["control_revision"],
                generation=raw_receipt["generation"],
                idempotent_replay=raw_receipt["idempotent_replay"],
            )
        confirm_keys = {
            operation_id: tuple(key)
            for operation_id, key in raw[_CONFIRM_KEYS].items()
        }
        episode_locks = {}
        for composite, reason in raw[_EPISODE_LOCKS].items():
            resource_id, episode_id = composite.split("|", 1)
            episode_locks[(resource_id, episode_id)] = reason
        return {
            _COMMIT: raw[_COMMIT],
            _BOOT_ID: raw.get(_BOOT_ID),
            _RECORDS: records,
            _OPERATIONS: operations,
            _RECEIPTS: receipts,
            _RECEIPT_TIMES: dict(raw[_RECEIPT_TIMES]),
            _RECEIPT_FINGERPRINTS: {
                request_id: tuple(
                    _decode_fingerprint_part(part) for part in fingerprint
                )
                for request_id, fingerprint in raw[
                    _RECEIPT_FINGERPRINTS
                ].items()
            },
            _CONFIRM_KEYS: confirm_keys,
            _EPISODE_LOCKS: episode_locks,
            _SCOPE_REVISIONS: dict(raw[_SCOPE_REVISIONS]),
            _CONFIG_REVISIONS: dict(raw[_CONFIG_REVISIONS]),
        }
    except StoreImageError:
        raise
    except Exception as exc:
        raise StoreImageError(f"stored image is invalid: {exc}") from exc
