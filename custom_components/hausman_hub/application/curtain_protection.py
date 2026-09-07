"""Durable per-cover manual-open protection for managed curtains."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from .curtain_command_policy import (
    ALICE_CURTAIN_TARGET,
    KITCHEN_CURTAIN_TARGET,
    LIVING_CURTAIN_TARGET,
    OFFICE_CURTAIN_TARGET,
)


CURTAIN_SCENARIO_ID = "system-curtains-privacy-controller"
CURTAIN_TARGET_IDS = (
    LIVING_CURTAIN_TARGET,
    KITCHEN_CURTAIN_TARGET,
    ALICE_CURTAIN_TARGET,
    OFFICE_CURTAIN_TARGET,
)
_PROTECTION_VERSION = 2
_INTENT_PHASES = frozenset({"reserved", "dispatch_intent", "unconfirmed"})


@dataclass(frozen=True, slots=True)
class CurtainProtectionDecision:
    """One pre-dispatch decision made from durable server state."""

    allowed: bool
    reason: str | None = None
    token: str | None = None


def _empty_record(target_id: str, entity_id: str) -> dict[str, object]:
    return {
        "targetId": target_id,
        "entityId": entity_id,
        "generation": 0,
        "cycleId": "curtain-cycle.initial",
        "automaticCloseIntent": None,
        "confirmedAutomaticClose": None,
        "morningOpenIntent": None,
        "manualOpenEvidence": None,
        "latchedAtMs": None,
        "releaseSunriseAtMs": None,
        "lastProcessedSunriseMs": None,
    }


def valid_curtain_protection_payload(value: object) -> bool:
    """Validate the complete exact-target durable image."""

    if not isinstance(value, Mapping) or set(value) != {"version", "targets"}:
        return False
    if not isinstance(value.get("targets"), Mapping):
        return False
    if value.get("version") == 1:
        return _valid_v1_targets(value["targets"])
    if value.get("version") != _PROTECTION_VERSION:
        return False
    targets = value["targets"]
    if set(targets) != set(CURTAIN_TARGET_IDS):
        return False
    expected = {
        "targetId", "entityId", "generation", "cycleId",
        "automaticCloseIntent", "confirmedAutomaticClose",
        "morningOpenIntent", "manualOpenEvidence", "latchedAtMs",
        "releaseSunriseAtMs", "lastProcessedSunriseMs",
    }
    for target_id, record in targets.items():
        if (
            not isinstance(record, Mapping)
            or set(record) != expected
            or record.get("targetId") != target_id
            or not isinstance(record.get("entityId"), str)
            or not record.get("entityId")
            or type(record.get("generation")) is not int
            or not 0 <= int(record["generation"]) <= 2**31 - 1
            or not _valid_text(record.get("cycleId"))
        ):
            return False
        for key in ("latchedAtMs", "releaseSunriseAtMs", "lastProcessedSunriseMs"):
            item = record.get(key)
            if item is not None and (type(item) is not int or not 0 <= item <= 2**63 - 1):
                return False
        close = record.get("confirmedAutomaticClose")
        if close is not None and not (
            _valid_evidence(
                close,
                {
                    "receiptId", "evidenceRevision", "confirmedAtMs",
                    "positionProvenance",
                },
            )
            and close.get("positionProvenance") == "verified_device_report"
        ):
            return False
        for key in ("automaticCloseIntent", "morningOpenIntent"):
            intent = record.get(key)
            if intent is not None and not _valid_intent(intent):
                return False
        manual = record.get("manualOpenEvidence")
        if manual is not None and (
            not _valid_evidence(manual, {"receiptId", "evidenceRevision", "recordedAtMs", "outcome"})
            or manual.get("outcome") not in {"pending", "confirmed", "unknown", "external"}
        ):
            return False
        latched = record.get("latchedAtMs") is not None
        if latched != (record.get("manualOpenEvidence") is not None):
            return False
    return True


def _valid_v1_targets(targets: object) -> bool:
    """Accept the exact legacy image only long enough to migrate it safely."""

    if not isinstance(targets, Mapping) or set(targets) != set(CURTAIN_TARGET_IDS):
        return False
    expected = {
        "targetId", "entityId", "generation", "confirmedAutomaticClose",
        "manualOpenEvidence", "latchedAtMs", "releaseSunriseAtMs",
        "lastProcessedSunriseMs",
    }
    for target_id, record in targets.items():
        if (
            not isinstance(record, Mapping)
            or set(record) != expected
            or record.get("targetId") != target_id
            or not _valid_text(record.get("entityId"))
            or type(record.get("generation")) is not int
            or not 0 <= int(record["generation"]) <= 2**31 - 1
        ):
            return False
        for key in ("latchedAtMs", "releaseSunriseAtMs", "lastProcessedSunriseMs"):
            item = record.get(key)
            if item is not None and (
                type(item) is not int or not 0 <= item <= 2**63 - 1
            ):
                return False
        close = record.get("confirmedAutomaticClose")
        if close is not None and not _valid_evidence(
            close, {"receiptId", "evidenceRevision", "confirmedAtMs"}
        ):
            return False
        manual = record.get("manualOpenEvidence")
        if manual is not None and (
            not _valid_evidence(
                manual,
                {"receiptId", "evidenceRevision", "recordedAtMs", "outcome"},
            )
            or manual.get("outcome")
            not in {"pending", "confirmed", "unknown", "external"}
        ):
            return False
        if (record.get("latchedAtMs") is not None) != (manual is not None):
            return False
    return True


def _valid_intent(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value)
        == {
            "operationId", "identityDigest", "sourceHash", "phase",
            "recordedAtMs",
        }
        and all(
            _valid_text(value.get(key))
            for key in ("operationId", "identityDigest", "sourceHash")
        )
        and value.get("phase") in _INTENT_PHASES
        and type(value.get("recordedAtMs")) is int
        and 0 <= value["recordedAtMs"] <= 2**63 - 1
    )


def _valid_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 256


def _valid_evidence(value: object, keys: set[str]) -> bool:
    if not isinstance(value, Mapping) or set(value) != keys:
        return False
    for key, item in value.items():
        if key.endswith("AtMs"):
            if type(item) is not int or not 0 <= item <= 2**63 - 1:
                return False
        elif not isinstance(item, str) or not item or len(item) > 256:
            return False
    return True


def _identity_digest(target_id: str, entity_id: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [target_id, entity_id],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _source_hash(value: str | None) -> str:
    if _valid_text(value):
        return str(value)
    return hashlib.sha256(b"curtain-source.unspecified").hexdigest()


def _intent_token(generation: int, intent: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                generation,
                intent.get("operationId"),
                intent.get("identityDigest"),
                intent.get("sourceHash"),
                intent.get("phase"),
            ],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _preflight_token(
    generation: int,
    cycle_id: object,
    target_id: str,
    entity_id: str,
    receipt_id: str,
    source_hash: str | None,
) -> str:
    """Bind an in-memory preflight to the durable state it observed."""

    return hashlib.sha256(
        json.dumps(
            [
                generation,
                cycle_id,
                target_id,
                entity_id,
                receipt_id,
                _source_hash(source_hash),
            ],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class CurtainProtectionCoordinator:
    """Serialize short state transitions without the scenario decision lock."""

    def __init__(
        self,
        store: object,
        *,
        catalog_resolver: Callable[[str], object | None],
        next_sunrise_ms: Callable[[], int | None],
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._catalog_resolver = catalog_resolver
        self._next_sunrise_ms = next_sunrise_ms
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._targets: dict[str, dict[str, object]] = {}
        self._healthy = True
        self._reason: str | None = None
        self._lock = asyncio.Lock()
        self._trusted_sunrise_runs: dict[str, frozenset[str]] = {}

    def start(self, hass: object) -> Callable[[], None]:
        """Observe only real cover state transitions for external intervention."""

        bus = getattr(hass, "bus", None)
        if bus is None:
            return lambda: None
        entities = {
            str(record["entityId"]): target_id
            for target_id, record in self._targets.items()
        }

        async def changed(event: object) -> None:
            data = getattr(event, "data", {})
            if not isinstance(data, Mapping):
                return
            entity_id = data.get("entity_id")
            target_id = entities.get(entity_id) if isinstance(entity_id, str) else None
            old_position = _event_position(data.get("old_state"))
            new_position = _event_position(data.get("new_state"))
            if target_id is None or old_position is None or new_position is None:
                return
            revision = getattr(data.get("new_state"), "last_updated", None)
            if revision is None:
                return
            await self.async_handle_external_open(
                target_id=target_id,
                entity_id=entity_id,
                old_position=old_position,
                new_position=new_position,
                evidence_revision=(
                    revision.isoformat()
                    if callable(getattr(revision, "isoformat", None))
                    else str(revision)
                ),
            )

        return bus.async_listen("state_changed", changed)

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def payload(self) -> dict[str, object]:
        return {"version": _PROTECTION_VERSION, "targets": copy.deepcopy(self._targets)}

    async def async_load(self) -> None:
        """Load protection without making safe manual commands unavailable."""

        try:
            payload = await self._store.async_load()
        except Exception:  # noqa: BLE001
            self._initialize_empty()
            self._mark_unhealthy("curtain_protection_store_unreadable")
            return
        if payload is None:
            self._initialize_empty()
            try:
                await self._save()
            except Exception:  # noqa: BLE001
                self._mark_unhealthy("curtain_protection_store_write_failed")
            return
        if not valid_curtain_protection_payload(payload):
            self._initialize_empty()
            self._mark_unhealthy("curtain_protection_store_invalid")
            return
        migrated = self._migrate_payload(payload)
        self._targets = copy.deepcopy(dict(migrated["targets"]))
        if getattr(self._store, "recovered_previous", False):
            self._mark_unhealthy("curtain_protection_store_recovered_previous")
        if not self._identities_match():
            self._mark_unhealthy("curtain_protection_identity_changed")
        if payload.get("version") == 1 and self._healthy:
            try:
                await self._save()
            except Exception:  # noqa: BLE001
                self._mark_unhealthy("curtain_protection_store_write_failed")

    def _migrate_payload(self, payload: Mapping[str, object]) -> dict[str, object]:
        """Downgrade legacy HA readback ownership to an unconfirmed intent."""

        if payload.get("version") == _PROTECTION_VERSION:
            return copy.deepcopy(dict(payload))
        targets: dict[str, dict[str, object]] = {}
        for target_id, source in payload["targets"].items():
            record = copy.deepcopy(dict(source))
            entity_id = str(record["entityId"])
            close = record.get("confirmedAutomaticClose")
            recorded_at = (
                int(close["confirmedAtMs"])
                if isinstance(close, Mapping)
                and type(close.get("confirmedAtMs")) is int
                else 0
            )
            operation_id = (
                str(close["receiptId"])
                if isinstance(close, Mapping)
                and _valid_text(close.get("receiptId"))
                else "legacy-unattributed-close"
            )
            record.update(
                {
                    "cycleId": (
                        f"curtain-cycle.sunrise.{record['lastProcessedSunriseMs']}"
                        if type(record.get("lastProcessedSunriseMs")) is int
                        else "curtain-cycle.legacy"
                    ),
                    "automaticCloseIntent": (
                        {
                            "operationId": operation_id,
                            "identityDigest": _identity_digest(target_id, entity_id),
                            "sourceHash": hashlib.sha256(
                                b"legacy-unverified-readback"
                            ).hexdigest(),
                            "phase": "unconfirmed",
                            "recordedAtMs": recorded_at,
                        }
                        if close is not None
                        else None
                    ),
                    "confirmedAutomaticClose": None,
                    "morningOpenIntent": None,
                }
            )
            targets[str(target_id)] = record
        return {"version": _PROTECTION_VERSION, "targets": targets}

    def _initialize_empty(self) -> None:
        self._targets = {}
        for target_id in CURTAIN_TARGET_IDS:
            device = self._catalog_resolver(target_id)
            entity_id = getattr(device, "entity_id", None)
            self._targets[target_id] = _empty_record(
                target_id, entity_id if isinstance(entity_id, str) and entity_id else "unresolved"
            )

    def _identities_match(self) -> bool:
        return all(
            getattr(self._catalog_resolver(target_id), "entity_id", None)
            == record.get("entityId")
            for target_id, record in self._targets.items()
        )

    def _mark_unhealthy(self, reason: str) -> None:
        self._healthy = False
        self._reason = reason

    async def _save(self) -> None:
        await self._store.async_save(self.payload)

    @staticmethod
    def _opening(action_id: str, requested: object, current_position: int | None) -> bool:
        return action_id == "open_cover" or (
            action_id == "set_position"
            and type(requested) is int
            and requested > 0
            and (current_position is None or requested >= current_position)
        )

    @staticmethod
    def _closing(action_id: str, requested: object, current_position: int | None) -> bool:
        return action_id == "close_cover" or (
            action_id == "set_position"
            and type(requested) is int
            and (
                requested == 0
                or current_position is None
                or requested < current_position
            )
        )

    async def async_before_action(
        self,
        *,
        target_id: str,
        entity_id: str,
        action_id: str,
        requested: object,
        applied: object,
        current_position: int | None,
        automatic: bool,
        dry_run: bool,
        receipt_id: str,
        source_hash: str | None = None,
    ) -> CurtainProtectionDecision:
        """Latch manual intent or reject conflicting automatic close."""

        if target_id not in CURTAIN_TARGET_IDS:
            return CurtainProtectionDecision(True)
        opening = self._opening(
            action_id, requested if not automatic else applied, current_position
        )
        closing = self._closing(action_id, applied, current_position)
        async with self._lock:
            record = self._targets.get(target_id)
            if record is None or record.get("entityId") != entity_id:
                if automatic and closing:
                    return CurtainProtectionDecision(False, "curtain_protection_identity_changed")
                self._mark_unhealthy("curtain_protection_identity_changed")
                return CurtainProtectionDecision(True)
            if automatic and closing:
                if not self._healthy:
                    return CurtainProtectionDecision(False, self._reason or "curtain_protection_unhealthy")
                if record.get("latchedAtMs") is not None:
                    return CurtainProtectionDecision(False, "curtain_manual_open_latched")
                if (
                    record.get("automaticCloseIntent") is not None
                    or record.get("confirmedAutomaticClose") is not None
                ):
                    return CurtainProtectionDecision(
                        False, "curtain_close_already_attempted"
                    )
                if dry_run:
                    return CurtainProtectionDecision(True)
                return CurtainProtectionDecision(
                    True,
                    token=_preflight_token(
                        int(record["generation"]),
                        record.get("cycleId"),
                        target_id,
                        entity_id,
                        receipt_id,
                        source_hash,
                    ),
                )
            if automatic or not opening or dry_run:
                return CurtainProtectionDecision(True)
            if (
                record.get("confirmedAutomaticClose") is None
                and record.get("automaticCloseIntent") is None
            ):
                updated = copy.deepcopy(record)
                updated["generation"] = int(record["generation"]) + 1
                self._targets[target_id] = updated
                try:
                    await self._save()
                except Exception:  # noqa: BLE001
                    self._mark_unhealthy("curtain_protection_store_write_failed")
                return CurtainProtectionDecision(
                    True, token=str(updated["generation"])
                )
            now = self._now_ms()
            release = self._next_sunrise_ms()
            if type(release) is not int or release <= now:
                # The manual intent is still the safety fact when astronomy is
                # unavailable. Persist it with an unresolved boundary so a
                # restart cannot silently permit a later automatic close.
                release = None
            updated = copy.deepcopy(record)
            updated["generation"] = int(record["generation"]) + 1
            updated["manualOpenEvidence"] = {
                "receiptId": receipt_id,
                "evidenceRevision": "intent-before-dispatch",
                "recordedAtMs": now,
                "outcome": "pending",
            }
            updated["latchedAtMs"] = now
            updated["releaseSunriseAtMs"] = release
            self._targets[target_id] = updated
            try:
                await self._save()
            except Exception:  # noqa: BLE001
                self._mark_unhealthy("curtain_protection_store_write_failed")
            return CurtainProtectionDecision(True, token=str(updated["generation"]))

    async def async_validate_before_dispatch(
        self,
        *,
        target_id: str,
        entity_id: str,
        action_id: str,
        requested: object,
        applied: object,
        current_position: int | None,
        automatic: bool,
        token: str | None,
        receipt_id: str,
        source_hash: str | None = None,
    ) -> CurtainProtectionDecision:
        """Revalidate the protection generation at the physical boundary."""

        if target_id not in CURTAIN_TARGET_IDS:
            return CurtainProtectionDecision(True)
        opening = self._opening(
            action_id, requested if not automatic else applied, current_position
        )
        closing = self._closing(action_id, applied, current_position)
        if not (closing and automatic) and not (opening and not automatic):
            return CurtainProtectionDecision(True)
        async with self._lock:
            record = self._targets.get(target_id)
            if record is None or record.get("entityId") != entity_id:
                if automatic and closing:
                    return CurtainProtectionDecision(
                        False, "curtain_protection_identity_changed"
                    )
                return CurtainProtectionDecision(True)
            if automatic and closing:
                if record.get("automaticCloseIntent") is not None:
                    return CurtainProtectionDecision(
                        False, "curtain_close_already_attempted"
                    )
                if record.get("confirmedAutomaticClose") is not None:
                    return CurtainProtectionDecision(
                        False, "curtain_close_already_attempted"
                    )
                expected_token = _preflight_token(
                    int(record["generation"]),
                    record.get("cycleId"),
                    target_id,
                    entity_id,
                    receipt_id,
                    source_hash,
                )
                if token != expected_token:
                    return CurtainProtectionDecision(
                        False, "curtain_protection_generation_changed"
                    )
                if not self._healthy:
                    return CurtainProtectionDecision(
                        False,
                        self._reason or "curtain_protection_unhealthy",
                    )
                if record.get("latchedAtMs") is not None:
                    return CurtainProtectionDecision(
                        False, "curtain_manual_open_latched"
                    )
                intent = {
                    "operationId": receipt_id,
                    "identityDigest": _identity_digest(target_id, entity_id),
                    "sourceHash": _source_hash(source_hash),
                    "phase": "reserved",
                    "recordedAtMs": self._now_ms(),
                }
                reserved = copy.deepcopy(record)
                reserved["automaticCloseIntent"] = intent
                previous = self._targets[target_id]
                self._targets[target_id] = reserved
                try:
                    await self._save()
                except Exception:  # noqa: BLE001
                    self._targets[target_id] = previous
                    self._mark_unhealthy("curtain_protection_store_write_failed")
                    return CurtainProtectionDecision(False, self._reason)
                dispatch_intent = dict(intent)
                dispatch_intent["phase"] = "dispatch_intent"
                updated = copy.deepcopy(reserved)
                updated["automaticCloseIntent"] = dispatch_intent
                self._targets[target_id] = updated
                try:
                    await self._save()
                except Exception:  # noqa: BLE001
                    # The service call has not crossed its boundary. Try to
                    # release the durable reservation; if storage remains
                    # unavailable, the saved reservation deliberately blocks
                    # a retry after restart.
                    self._targets[target_id] = previous
                    try:
                        await self._save()
                    except Exception:  # noqa: BLE001
                        self._targets[target_id] = reserved
                    self._mark_unhealthy("curtain_protection_store_write_failed")
                    return CurtainProtectionDecision(False, self._reason)
                return CurtainProtectionDecision(
                    True,
                    token=_intent_token(
                        int(updated["generation"]), dispatch_intent
                    ),
                )
            if token != str(record.get("generation")):
                return CurtainProtectionDecision(
                    False, "curtain_protection_generation_changed"
                )
            return CurtainProtectionDecision(True, token=token)

    async def async_note_result(
        self,
        *,
        target_id: str,
        entity_id: str,
        action_id: str,
        requested: object,
        applied: object,
        current_position: int | None,
        automatic: bool,
        dry_run: bool,
        receipt_id: str,
        protection_generation: str | None,
        confirmed: bool,
        evidence_revision: str | None,
        physical_result_proven: bool = False,
    ) -> None:
        """Persist only attributable confirmed close or manual-open outcome."""

        if dry_run or target_id not in CURTAIN_TARGET_IDS:
            return
        opening = self._opening(
            action_id, requested if not automatic else applied, current_position
        )
        closing = self._closing(action_id, applied, current_position)
        async with self._lock:
            record = self._targets.get(target_id)
            if record is None or record.get("entityId") != entity_id:
                self._mark_unhealthy("curtain_protection_identity_changed")
                return
            updated = copy.deepcopy(record)
            changed = False
            if automatic and closing:
                intent = record.get("automaticCloseIntent")
                if (
                    not isinstance(intent, Mapping)
                    or intent.get("phase") != "dispatch_intent"
                    or protection_generation
                    != _intent_token(int(record["generation"]), intent)
                    or record.get("latchedAtMs") is not None
                    or not self._healthy
                ):
                    return
                if confirmed and physical_result_proven:
                    updated["generation"] = int(record["generation"]) + 1
                    updated["automaticCloseIntent"] = None
                    updated["confirmedAutomaticClose"] = {
                        "receiptId": receipt_id,
                        "evidenceRevision": (
                            evidence_revision or "verified-device-report"
                        ),
                        "confirmedAtMs": self._now_ms(),
                        "positionProvenance": "verified_device_report",
                    }
                else:
                    unconfirmed = dict(intent)
                    unconfirmed["phase"] = "unconfirmed"
                    updated["automaticCloseIntent"] = unconfirmed
                    updated["confirmedAutomaticClose"] = None
                changed = True
            elif automatic and opening:
                morning = record.get("morningOpenIntent")
                if isinstance(morning, Mapping):
                    unconfirmed = dict(morning)
                    unconfirmed["phase"] = "unconfirmed"
                    updated["morningOpenIntent"] = unconfirmed
                    changed = True
            elif not automatic and opening and isinstance(updated.get("manualOpenEvidence"), Mapping):
                evidence = dict(updated["manualOpenEvidence"])
                if (
                    protection_generation == str(record.get("generation"))
                    and evidence.get("receiptId") == receipt_id
                ):
                    evidence["outcome"] = "confirmed" if confirmed else "unknown"
                    evidence["evidenceRevision"] = evidence_revision or "result-without-revision"
                    updated["manualOpenEvidence"] = evidence
                    changed = True
            if not changed:
                return
            self._targets[target_id] = updated
            try:
                await self._save()
            except Exception:  # noqa: BLE001
                self._mark_unhealthy("curtain_protection_store_write_failed")

    async def async_handle_external_open(
        self,
        *,
        target_id: str,
        entity_id: str,
        old_position: int,
        new_position: int,
        evidence_revision: str,
    ) -> None:
        """Treat an unattributed opening after our close as manual intervention."""

        if new_position <= old_position or target_id not in CURTAIN_TARGET_IDS:
            return
        async with self._lock:
            record = self._targets.get(target_id)
            if (
                record is None
                or record.get("entityId") != entity_id
                or (
                    record.get("confirmedAutomaticClose") is None
                    and record.get("automaticCloseIntent") is None
                )
                or record.get("latchedAtMs") is not None
            ):
                return
            now = self._now_ms()
            release = self._next_sunrise_ms()
            if type(release) is not int or release <= now:
                release = None
            updated = copy.deepcopy(record)
            updated["generation"] = int(record["generation"]) + 1
            updated["manualOpenEvidence"] = {
                "receiptId": f"external:{evidence_revision}",
                "evidenceRevision": evidence_revision,
                "recordedAtMs": now,
                "outcome": "external",
            }
            updated["latchedAtMs"] = now
            updated["releaseSunriseAtMs"] = release
            self._targets[target_id] = updated
            try:
                await self._save()
            except Exception:  # noqa: BLE001
                self._mark_unhealthy("curtain_protection_store_write_failed")

    async def async_run_trusted_sunrise(
        self,
        occurred_at_ms: int,
        run_scenario: Callable[..., Awaitable[Mapping[str, object]]],
    ) -> Mapping[str, object]:
        """Durably release eligible latches before requesting morning opens."""

        if type(occurred_at_ms) is not int or occurred_at_ms < 0:
            raise ValueError("trusted sunrise timestamp is invalid")
        run_id = f"curtain.sunrise.{uuid.uuid4().hex}"
        async with self._lock:
            if not self._healthy:
                return {"status": "skipped", "reason": self._reason or "curtain_protection_unhealthy"}
            updated_targets = copy.deepcopy(self._targets)
            changed = False
            open_allowed: set[str] = set()
            for target_id, record in updated_targets.items():
                last = record.get("lastProcessedSunriseMs")
                if type(last) is int and occurred_at_ms <= last:
                    continue
                entity_id = str(record["entityId"])
                cycle_id = f"curtain-cycle.sunrise.{occurred_at_ms}"
                record["generation"] = int(record["generation"]) + 1
                record["cycleId"] = cycle_id
                record["automaticCloseIntent"] = None
                record["confirmedAutomaticClose"] = None
                record["manualOpenEvidence"] = None
                record["latchedAtMs"] = None
                record["releaseSunriseAtMs"] = None
                record["morningOpenIntent"] = {
                    "operationId": f"{run_id}:{target_id}",
                    "identityDigest": _identity_digest(target_id, entity_id),
                    "sourceHash": hashlib.sha256(
                        f"{CURTAIN_SCENARIO_ID}:{cycle_id}".encode("utf-8")
                    ).hexdigest(),
                    "phase": "dispatch_intent",
                    "recordedAtMs": occurred_at_ms,
                }
                open_allowed.add(target_id)
                record["lastProcessedSunriseMs"] = occurred_at_ms
                changed = True
            if not changed:
                return {"status": "skipped", "reason": "curtain_sunrise_already_processed"}
            if changed:
                previous = self._targets
                self._targets = updated_targets
                try:
                    await self._save()
                except Exception:  # noqa: BLE001
                    self._targets = previous
                    self._mark_unhealthy("curtain_protection_store_write_failed")
                    return {"status": "skipped", "reason": self._reason}
            if not open_allowed:
                return {"status": "skipped", "reason": "curtain_sunrise_release_pending"}
            self._trusted_sunrise_runs[run_id] = frozenset(open_allowed)
        try:
            return await run_scenario(
                CURTAIN_SCENARIO_ID,
                correlation_id=run_id,
                trigger_context={
                    "source": "curtain_schedule",
                    "trigger_id": "curtain_trusted_sunrise",
                    "recovery": False,
                },
            )
        finally:
            async with self._lock:
                self._trusted_sunrise_runs.pop(run_id, None)

    async def async_control_state(
        self, run_id: str, trigger: Mapping[str, object]
    ) -> dict[str, object]:
        """Return the bounded server snapshot consumed by the curtain source."""

        async with self._lock:
            trusted = (
                trigger.get("source") == "curtain_schedule"
                and trigger.get("trigger_id") == "curtain_trusted_sunrise"
                and run_id in self._trusted_sunrise_runs
            )
            open_allowed = self._trusted_sunrise_runs.get(run_id, frozenset())
            return {
                "ready": self._healthy,
                "transition": "trusted_sunrise" if trusted else "curtain_snapshot",
                "trustedSunrise": trusted,
                "targets": {
                    target_id: {
                        "entityId": record["entityId"],
                        "generation": record["generation"],
                        "latched": record.get("latchedAtMs") is not None,
                        "automaticCloseAllowed": bool(
                            self._healthy
                            and record.get("latchedAtMs") is None
                            and record.get("automaticCloseIntent") is None
                            and record.get("confirmedAutomaticClose") is None
                        ),
                        "morningOpenAllowed": bool(
                            trusted and target_id in open_allowed
                        ),
                    }
                    for target_id, record in self._targets.items()
                },
                **({"reason": self._reason} if self._reason is not None else {}),
            }


def _event_position(state: object | None) -> int | None:
    if state is None or str(getattr(state, "state", "unknown")) not in {
        "open", "opening", "closed", "closing"
    }:
        return None
    attributes = getattr(state, "attributes", {})
    value = attributes.get("current_position") if isinstance(attributes, Mapping) else None
    if type(value) is not int:
        return None
    return value if 0 <= value <= 100 else None
