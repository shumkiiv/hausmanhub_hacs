"""Fail-closed, restart-safe protection after a manual light switch-off."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

from ..domain.manual_light_off_protection import (
    EffectivePolicySource,
    LightProtectionDecision,
    ManualLightOffProtectionSettings,
    ManualLightOffProtectionViolation,
    ProtectionState,
    ScenarioCommandAttribution,
    parse_settings,
    resolve_manual_off_policy_with_source,
)

if TYPE_CHECKING:
    from ..domain.scenarios import ScenarioAction
    from .scenarios import ScenarioCatalog

MAX_PROFILES = 64
MAX_ACTIVE_PROTECTIONS = 64
MAX_COMPLETED_PROTECTIONS = 256
MAX_IDEMPOTENCY_RECEIPTS = 128
_FRESH_SENSOR_SECONDS = 300
_LOGGER = logging.getLogger(__name__)


class ManualLightOffProtectionError(Exception):
    """Base class for safe, typed API failures."""


class ManualLightOffProtectionValidationError(ManualLightOffProtectionError, ValueError):
    pass


class ManualLightOffProtectionRevisionConflict(ManualLightOffProtectionError, ValueError):
    pass


class ManualLightOffProtectionIdempotencyConflict(ManualLightOffProtectionError, ValueError):
    pass


class ManualLightOffProtectionNotFound(ManualLightOffProtectionError, ValueError):
    pass


class ManualLightOffProtectionPolicyConflict(ManualLightOffProtectionError, ValueError):
    pass


class ManualLightOffProtectionUnavailable(ManualLightOffProtectionError, RuntimeError):
    pass


class ManualLightOffProtectionPersistenceError(ManualLightOffProtectionError, OSError):
    """Storage failed. The original exception deliberately stays internal."""


class ManualLightOffProtectionStore(Protocol):
    async def async_load(self) -> object | None: ...
    async def async_save(self, payload: dict[str, object]) -> None: ...


class ManualLightOffProtectionCoordinator:
    """Own bounded policy state without issuing physical commands."""

    def __init__(
        self,
        store: ManualLightOffProtectionStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._settings = _default_settings()
        self._settings_revision = 0
        self._state_revision = 0
        self._protections: dict[str, dict[str, object]] = {}
        self._frozen_sensor_ids: dict[str, tuple[str, ...]] = {}
        self._completed: list[dict[str, object]] = []
        self._receipts: dict[str, dict[str, object]] = {}
        self._receipt_metadata: dict[str, tuple[str, str]] = {}
        self._sensor_states: dict[str, tuple[str, datetime]] = {}
        self._event_entity_listeners: set[Callable[[], None]] = set()
        self._loaded = False
        self.unhealthy = False
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        try:
            payload = await self._store.async_load()
            if payload is None:
                self._loaded = True
                return
            if not valid_manual_light_off_protection_payload(payload):
                raise RuntimeError("manual light-off protection store is corrupt")
            self._settings = parse_settings(payload["settings"])
            self._settings_revision = int(payload["settingsRevision"])
            self._state_revision = int(payload["stateRevision"])
            self._protections = {
                _protection_key(item): copy.deepcopy(item)
                for item in payload["protections"]
            }
            self._frozen_sensor_ids = {
                str(item["protectionId"]): tuple(item["presenceSensorIds"])
                for item in payload["frozenSensors"]
            }
            self._completed = copy.deepcopy(payload["completed"])
            # Old records have no request fingerprint and cannot safely be
            # replayed. Drop them before the next save instead of emitting an
            # invalid empty wrapper or claiming a replay we cannot prove.
            verified_receipts = [
                item for item in payload["receipts"]
                if "operation" in item and "payloadFingerprint" in item
            ]
            self._receipts = {
                str(item["requestId"]): copy.deepcopy(item["receipt"])
                for item in verified_receipts
            }
            self._receipt_metadata = {
                str(item["requestId"]): (str(item["operation"]), str(item["payloadFingerprint"]))
                for item in verified_receipts
            }
        except Exception:  # persistence evidence must never open automatic control
            self.unhealthy = True
        finally:
            self._loaded = True

    async def async_replace_settings(
        self, request_id: str, expected_revision: int, settings: Mapping[str, object]
    ) -> dict[str, object]:
        async with self._lock:
            self._require_healthy()
            fingerprint = _request_fingerprint(settings)
            existing = self._receipt(request_id, "settings_updated", fingerprint)
            if existing is not None:
                return existing
            if type(expected_revision) is not int or expected_revision != self._settings_revision:
                raise ManualLightOffProtectionRevisionConflict("settings revision conflict")
            try:
                parsed = parse_settings(settings)
            except (TypeError, ValueError, ManualLightOffProtectionViolation) as error:
                raise ManualLightOffProtectionValidationError from error
            self._settings = parsed
            self._settings_revision += 1
            receipt = _receipt(
                request_id, "settings_updated", self._settings_revision,
                settings=parsed.as_wire(),
            )
            await self._persist_with_receipt(request_id, receipt, "settings_updated", fingerprint)
            self._notify_event_entity_listeners()
            return copy.deepcopy(receipt)

    async def async_note_state_transition(
        self,
        entity_id: str, old_state: object, new_state: object,
        attribution: ScenarioCommandAttribution | None,
    ) -> None:
        async with self._lock:
            if self.unhealthy or not self._loaded:
                return
            old = _state(old_state)
            new = _state(new_state)
            now = _utc(self._now())
            affected = [
                key for key, sensor_ids in self._frozen_sensor_ids.items()
                if entity_id in sensor_ids
            ]
            if affected:
                observed = _state_timestamp(new_state, now)
                if new == "off" and (now - observed).total_seconds() <= _FRESH_SENSOR_SECONDS:
                    self._sensor_states[entity_id] = ("off", observed)
                else:
                    self._sensor_states[entity_id] = (new, observed)
                for record_key in affected:
                    record = self._protections[record_key]
                    absence_since = (
                        _wire_time(observed)
                        if new == "off" and (now - observed).total_seconds() <= _FRESH_SENSOR_SECONDS
                        else None
                    )
                    if record["absenceSince"] != absence_since:
                        updated = copy.deepcopy(record)
                        updated["absenceSince"] = absence_since
                        updated["revision"] = int(record["revision"]) + 1
                        self._protections[record_key] = updated
                        self._state_revision += 1
                        await self._save()
            if old == "on" and new == "off" and not _automatic(attribution):
                profile = _profile_for_light(self._settings, entity_id)
                if profile is not None:
                    await self._activate(profile, entity_id, now, attribution)
            elif old == "off" and new == "on" and not _automatic(attribution):
                for key, record in tuple(self._protections.items()):
                    if entity_id in record["lightIds"]:
                        updated = copy.deepcopy(record)
                        updated["state"] = ProtectionState.CANCELLED_BY_MANUAL_ON.value
                        updated["revision"] = int(record["revision"]) + 1
                        await self._complete(key, updated)

    async def async_decide_entity(
        self, entity_id: str, *, automatic: bool, dry_run: bool
    ) -> LightProtectionDecision:
        async with self._lock:
            if not automatic:
                return LightProtectionDecision(True)
            if self.unhealthy or not self._loaded:
                return LightProtectionDecision(False, "manual_off_protection_unhealthy")
            matching = [
                (key, item) for key, item in self._protections.items()
                if entity_id in item["lightIds"]
            ]
            if not matching:
                return LightProtectionDecision(True)
            now = _utc(self._now())
            releasable: list[tuple[str, dict[str, object]]] = []
            for key, record in matching:
                sensor_ids = self._frozen_sensor_ids.get(key)
                if sensor_ids is None:
                    return LightProtectionDecision(False, "manual_off_protection_scope_unavailable", key)
                if not _may_release(record, sensor_ids, self._sensor_states, now):
                    reason = (
                        "manual_off_protection_active"
                        if now < _parse_time(record["notBefore"])
                        else "manual_off_protection_absence_required"
                    )
                    return LightProtectionDecision(
                        False,
                        reason,
                        key,
                        {
                            "roomId": record["roomId"],
                            "profileId": record["profileId"],
                            "state": record["state"],
                            "lightIds": list(record["lightIds"]),
                            "policyFingerprint": record["policyFingerprint"],
                            "protectionRevision": record["revision"],
                            "remainingSeconds": max(
                                0,
                                int((_parse_time(record["notBefore"]) - now).total_seconds()),
                            ),
                            "attribution": {
                                "source": record["attributionSource"],
                                "id": record["attributionId"],
                            },
                        },
                    )
                releasable.append((key, record))
            if not dry_run:
                await self._complete_many(releasable)
            return LightProtectionDecision(
                True, "manual_off_protection_released", releasable[0][0]
            )

    async def async_decide(
        self, action: ScenarioAction, catalog: ScenarioCatalog, *, automatic: bool,
        dry_run: bool, trigger_context: Mapping[str, object] | None,
    ) -> LightProtectionDecision:
        """Adapter kept side-effect free until scenario wiring is added."""

        del trigger_context
        target_id = getattr(action, "target_id", None)
        device = getattr(catalog, "device", lambda _: None)(target_id)
        entity_id = getattr(device, "entity_id", None)
        if not isinstance(entity_id, str):
            return LightProtectionDecision(not automatic, "manual_off_protection_unknown_target")
        return await self.async_decide_entity(entity_id, automatic=automatic, dry_run=dry_run)

    async def async_release(
        self, request_id: str, protection_id: str, expected_protection_revision: int
    ) -> dict[str, object]:
        async with self._lock:
            self._require_healthy()
            fingerprint = _request_fingerprint({"protectionId": protection_id, "expectedProtectionRevision": expected_protection_revision})
            existing = self._receipt(request_id, "manual_release", fingerprint)
            if existing is not None:
                return existing
            record = self._protections.get(protection_id)
            if record is None:
                raise ManualLightOffProtectionNotFound("protection is not active")
            if not record["effectivePolicy"]["allowManualRelease"]:
                raise ManualLightOffProtectionPolicyConflict("manual release is disabled")
            if type(expected_protection_revision) is not int or expected_protection_revision != record["revision"]:
                raise ManualLightOffProtectionRevisionConflict("protection revision conflict")
            updated = copy.deepcopy(record)
            updated["state"] = ProtectionState.RELEASED.value
            updated["revision"] = int(record["revision"]) + 1
            await self._complete(protection_id, updated, request_id=request_id)
            receipt = _receipt(request_id, "manual_release", int(updated["revision"]), protection=updated)
            await self._persist_with_receipt(request_id, receipt, "manual_release", fingerprint)
            self._notify_event_entity_listeners()
            return copy.deepcopy(receipt)

    async def async_release_profile(
        self, request_id: str, room_id: str, profile_id: str,
        expected_protection_revision: int,
    ) -> dict[str, object]:
        """Release one active record by the public profile identity.

        Storage keys are internal hashes and deliberately never cross the API
        boundary.  This method retains the same durable receipt semantics as
        the key-based operation while resolving the current record under its
        lock, so a stale browser snapshot cannot release a replacement record.
        """

        async with self._lock:
            self._require_healthy()
            fingerprint = _request_fingerprint({"roomId": room_id, "profileId": profile_id, "expectedProtectionRevision": expected_protection_revision})
            existing = self._receipt(request_id, "manual_release", fingerprint)
            if existing is not None:
                return existing
            item = next(
                ((key, value) for key, value in self._protections.items()
                 if value["roomId"] == room_id and value["profileId"] == profile_id),
                None,
            )
            if item is None:
                raise ManualLightOffProtectionNotFound("protection is not active")
            key, record = item
            if (
                type(expected_protection_revision) is not int
                or expected_protection_revision != record["revision"]
            ):
                raise ManualLightOffProtectionRevisionConflict("protection revision conflict")
            if not record["effectivePolicy"]["allowManualRelease"]:
                raise ManualLightOffProtectionPolicyConflict("manual release is disabled")
            updated = copy.deepcopy(record)
            updated["state"] = ProtectionState.RELEASED.value
            updated["revision"] = int(record["revision"]) + 1
            await self._complete(key, updated, request_id=request_id)
            receipt = _receipt(request_id, "manual_release", int(updated["revision"]), protection=updated)
            await self._persist_with_receipt(request_id, receipt, "manual_release", fingerprint)
            self._notify_event_entity_listeners()
            return copy.deepcopy(receipt)

    def snapshot(self) -> dict[str, object]:
        now = _utc(self._now())
        protections = []
        for stored in self._protections.values():
            record = copy.deepcopy(stored)
            record["remainingMinimumSeconds"] = max(
                0, math.ceil((_parse_time(record["notBefore"]) - now).total_seconds())
            )
            protections.append(record)
        return {
            "contract": {"name": "hausman-hub-manual-light-off-protection", "version": 1},
            "revision": self._settings_revision,
            "updatedAt": _wire_time(now),
            "settings": self._settings.as_wire(),
            "protections": protections,
        }

    def event_entity_ids(self) -> frozenset[str]:
        """Return configured and frozen entities needed for protected events."""

        entity_ids = {
            entity_id
            for profile in self._settings.profiles
            for entity_id in (*profile.light_ids, *profile.presence_sensor_ids)
        }
        for key, sensor_ids in self._frozen_sensor_ids.items():
            entity_ids.update(sensor_ids)
            record = self._protections.get(key)
            if record is not None:
                entity_ids.update(record["lightIds"])
        return frozenset(entity_ids)

    def add_event_entity_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a local callback for exact event-subscription changes."""

        self._event_entity_listeners.add(listener)

        def _remove() -> None:
            self._event_entity_listeners.discard(listener)

        return _remove

    def mark_unhealthy(self) -> None:
        """Fail closed after a cross-store protection cleanup failure."""

        self.unhealthy = True

    async def _activate(self, profile, entity_id: str, now: datetime, attribution) -> None:
        current_item = next(
            ((key, record) for key, record in self._protections.items()
             if record["roomId"] == profile.room_id and record["profileId"] == profile.profile_id
             and entity_id in record["lightIds"]),
            None,
        )
        if current_item is None:
            policy, source = resolve_manual_off_policy_with_source(
                self._settings.as_wire(), profile.room_id, profile.profile_id
            )
            key = _profile_key(
                profile.room_id, profile.profile_id,
                entity_id if policy.protected_scope.value == "source" else None,
            )
            current = None
        else:
            key, current = current_item
            policy = parse_settings({"globalPolicy": current["effectivePolicy"], "roomOverrides": {}, "profileOverrides": {}, "profiles": []}).global_policy
            source = EffectivePolicySource(current["effectivePolicySource"])
        if not policy.enabled:
            return
        if current is not None and not policy.extend_on_repeated_manual_off:
            return
        if current is None and len(self._protections) >= MAX_ACTIVE_PROTECTIONS:
            self.unhealthy = True
            raise RuntimeError("manual light-off protection store is full")
        revision = int(current["revision"]) + 1 if current is not None else 0
        record = {
            "roomId": profile.room_id, "profileId": profile.profile_id,
            "lightIds": (
                list(current["lightIds"])
                if current is not None
                else ([entity_id] if policy.protected_scope.value == "source" else list(profile.light_ids))
            ), "startedAt": _wire_time(now),
            "notBefore": _wire_time(now.timestamp() + policy.minimum_interval_seconds),
            "absenceSince": None, "effectivePolicy": policy.as_wire(),
            "effectivePolicySource": source.value, "policyFingerprint": policy.fingerprint,
            "reason": "manual_off", "attributionSource": "manual_command" if attribution else "state_transition",
            "attributionId": attribution.attribution_id if attribution else entity_id,
            "revision": revision, "state": ProtectionState.ACTIVE.value,
        }
        self._protections[key] = record
        if current is None:
            self._frozen_sensor_ids[key] = tuple(profile.presence_sensor_ids)
        self._state_revision += 1
        await self._save()
        self._notify_event_entity_listeners()

    async def _complete(self, key: str, record: dict[str, object], *, request_id: str | None = None) -> None:
        self._protections.pop(key, None)
        self._frozen_sensor_ids.pop(key, None)
        self._completed.append(record)
        self._completed = self._completed[-MAX_COMPLETED_PROTECTIONS:]
        self._state_revision += 1
        if request_id is None:
            await self._save()
            self._notify_event_entity_listeners()

    async def _complete_many(self, items: list[tuple[str, dict[str, object]]]) -> None:
        for key, record in items:
            updated = copy.deepcopy(record)
            updated["state"] = ProtectionState.RELEASED.value
            updated["revision"] = int(record["revision"]) + 1
            self._protections.pop(key, None)
            self._frozen_sensor_ids.pop(key, None)
            self._completed.append(updated)
        self._completed = self._completed[-MAX_COMPLETED_PROTECTIONS:]
        self._state_revision += len(items)
        await self._save()
        self._notify_event_entity_listeners()

    async def _persist_with_receipt(self, request_id: str, receipt: dict[str, object], operation: str, fingerprint: str) -> None:
        self._receipts[request_id] = receipt
        self._receipt_metadata[request_id] = (operation, fingerprint)
        if len(self._receipts) > MAX_IDEMPOTENCY_RECEIPTS:
            evicted_request_id = next(iter(self._receipts))
            self._receipts.pop(evicted_request_id)
            self._receipt_metadata.pop(evicted_request_id, None)
        await self._save()

    async def _save(self) -> None:
        try:
            await self._store.async_save(self._payload())
        except Exception as error:
            self.unhealthy = True
            _LOGGER.exception("manual light-off protection persistence failed")
            raise ManualLightOffProtectionPersistenceError("persistence failed") from error

    def _payload(self) -> dict[str, object]:
        return {
            "version": 1, "settingsRevision": self._settings_revision,
            "stateRevision": self._state_revision, "settings": self._settings.as_wire(),
            "protections": list(self._protections.values()), "completed": self._completed,
            "receipts": [
                {"requestId": key, "receipt": value, "operation": metadata[0], "payloadFingerprint": metadata[1]}
                for key, value in self._receipts.items()
                if (metadata := self._receipt_metadata.get(key)) is not None
            ],
            "frozenSensors": [
                {"protectionId": key, "presenceSensorIds": list(sensor_ids)}
                for key, sensor_ids in self._frozen_sensor_ids.items()
            ],
        }

    def _notify_event_entity_listeners(self) -> None:
        for listener in tuple(self._event_entity_listeners):
            listener()

    def _receipt(self, request_id: str, operation: str, fingerprint: str) -> dict[str, object] | None:
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ManualLightOffProtectionValidationError("request id is invalid")
        existing = self._receipts.get(request_id)
        if existing is None:
            return None
        if self._receipt_metadata.get(request_id) != (operation, fingerprint):
            raise ManualLightOffProtectionIdempotencyConflict("idempotency conflict")
        return copy.deepcopy(existing)

    def _require_healthy(self) -> None:
        if self.unhealthy or not self._loaded:
            raise ManualLightOffProtectionUnavailable


def valid_manual_light_off_protection_payload(value: object) -> bool:
    try:
        if not isinstance(value, Mapping) or set(value) != {"version", "settingsRevision", "stateRevision", "settings", "protections", "completed", "receipts", "frozenSensors"}:
            return False
        if value["version"] != 1 or type(value["settingsRevision"]) is not int or type(value["stateRevision"]) is not int:
            return False
        settings = parse_settings(value["settings"])
        if len(settings.profiles) > MAX_PROFILES:
            return False
        if not isinstance(value["protections"], list) or len(value["protections"]) > MAX_ACTIVE_PROTECTIONS:
            return False
        if not isinstance(value["completed"], list) or len(value["completed"]) > MAX_COMPLETED_PROTECTIONS:
            return False
        if not isinstance(value["receipts"], list) or len(value["receipts"]) > MAX_IDEMPOTENCY_RECEIPTS:
            return False
        if not isinstance(value["frozenSensors"], list) or len(value["frozenSensors"]) > MAX_ACTIVE_PROTECTIONS:
            return False
        active = [_valid_protection(item) for item in value["protections"]]
        completed = [_valid_protection(item) for item in value["completed"]]
        if not all(active) or not all(completed):
            return False
        if any(item["state"] not in {"active", "ready_to_release"} for item in value["protections"]):
            return False
        if not all(_valid_receipt(item) for item in value["receipts"]):
            return False
        active_keys = {_protection_key(item) for item in value["protections"]}
        frozen = value["frozenSensors"]
        return (
            len(active_keys) == len(value["protections"])
            and len({item["protectionId"] for item in frozen}) == len(frozen)
            and all(_valid_frozen_sensors(item) for item in frozen)
            and {item["protectionId"] for item in frozen} == active_keys
        )
    except (KeyError, TypeError, ValueError, ManualLightOffProtectionViolation):
        return False


def _default_settings() -> ManualLightOffProtectionSettings:
    return parse_settings({"globalPolicy": {"enabled": True, "minimumIntervalSeconds": 600, "releaseMode": "timer_and_absence", "stableAbsenceSeconds": 30, "extendOnRepeatedManualOff": True, "noSensorFallback": "timer_only", "protectedScope": "profile", "allowManualRelease": True}, "roomOverrides": {}, "profileOverrides": {}, "profiles": []})


def _profile_for_light(settings, entity_id):
    return next((item for item in settings.profiles if entity_id in item.light_ids), None)


def _profile_key(room_id: str, profile_id: str, entity_id: str | None = None) -> str:
    encoded = json.dumps(
        [room_id, profile_id, entity_id], separators=(",", ":"), ensure_ascii=True
    ).encode()
    return f"p_{hashlib.sha256(encoded).hexdigest()}"


def _protection_key(value: Mapping[str, object]) -> str:
    source = value["effectivePolicy"]["protectedScope"] == "source"
    return _profile_key(
        str(value["roomId"]), str(value["profileId"]),
        str(value["lightIds"][0]) if source else None,
    )


def _state(value: object) -> str:
    return str(getattr(value, "state", "")).casefold()


def _automatic(attribution: ScenarioCommandAttribution | None) -> bool:
    return attribution is not None and attribution.source == "automatic"


def _state_timestamp(value: object, fallback: datetime) -> datetime:
    candidate = getattr(value, "last_updated", None) or getattr(value, "last_changed", None)
    return _utc(candidate) if isinstance(candidate, datetime) else fallback


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _wire_time(value: datetime | float) -> str:
    if isinstance(value, (float, int)):
        value = datetime.fromtimestamp(value, timezone.utc)
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is invalid")
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _may_release(record, sensor_ids, sensors, now: datetime) -> bool:
    policy = record["effectivePolicy"]
    timer = now >= _parse_time(record["notBefore"])
    if sensor_ids and any(
        state is None
        or state[0] not in {"on", "off"}
        or (now - state[1]).total_seconds() > _FRESH_SENSOR_SECONDS
        for sensor_id in sensor_ids
        for state in (sensors.get(sensor_id),)
    ):
        return False
    if policy["releaseMode"] == "timer_only":
        return timer
    if not sensor_ids:
        if policy["noSensorFallback"] == "manual_release":
            return False
        absence = timer
    else:
        absence_times = []
        for sensor_id in sensor_ids:
            state = sensors.get(sensor_id)
            if state is None or state[0] != "off" or (now - state[1]).total_seconds() > _FRESH_SENSOR_SECONDS:
                return False
            absence_times.append(state[1])
        absence = bool(absence_times) and (now - max(absence_times)).total_seconds() >= policy["stableAbsenceSeconds"]
    return absence if policy["releaseMode"] == "absence_only" else timer and absence


def _valid_protection(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"roomId", "profileId", "lightIds", "startedAt", "notBefore", "absenceSince", "effectivePolicy", "effectivePolicySource", "policyFingerprint", "reason", "attributionSource", "attributionId", "revision", "state"}:
        return False
    if not isinstance(value["roomId"], str) or not isinstance(value["profileId"], str) or not isinstance(value["lightIds"], list) or not isinstance(value["revision"], int) or value["revision"] < 0:
        return False
    effective = parse_settings({"globalPolicy": value["effectivePolicy"], "roomOverrides": {}, "profileOverrides": {}, "profiles": [{"roomId": value["roomId"], "profileId": value["profileId"], "lightIds": value["lightIds"], "presenceSensorIds": []}]}).global_policy
    _parse_time(value["startedAt"]); _parse_time(value["notBefore"])
    if value["absenceSince"] is not None: _parse_time(value["absenceSince"])
    return value["effectivePolicySource"] in {item.value for item in EffectivePolicySource} and value["reason"] == "manual_off" and value["attributionSource"] in {"manual_command", "state_transition"} and isinstance(value["attributionId"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value["attributionId"]) is not None and value["state"] in {item.value for item in ProtectionState} and value["policyFingerprint"] == effective.fingerprint


def _valid_receipt(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) not in ({"requestId", "receipt"}, {"requestId", "receipt", "operation", "payloadFingerprint"}):
        return False
    request_id, receipt = value["requestId"], value["receipt"]
    if ("operation" in value and (value["operation"] not in {"settings_updated", "manual_release"} or not isinstance(value["payloadFingerprint"], str) or re.fullmatch(r"[a-f0-9]{64}", value["payloadFingerprint"]) is None)):
        return False
    if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id) is None or not isinstance(receipt, Mapping):
        return False
    if set(receipt) - {"contract", "requestId", "operation", "accepted", "confirmed", "status", "revision", "settings", "protection"}:
        return False
    if receipt.get("contract") != {"name": "hausman-hub-manual-light-off-protection-command-receipt", "version": 1} or receipt.get("requestId") != request_id or receipt.get("status") != "confirmed" or receipt.get("accepted") is not True or receipt.get("confirmed") is not True or type(receipt.get("revision")) is not int or receipt["revision"] < 0:
        return False
    if "operation" in value and receipt.get("operation") != value["operation"]:
        return False
    if receipt.get("operation") == "settings_updated":
        try: parse_settings(receipt["settings"])
        except (KeyError, TypeError, ValueError, ManualLightOffProtectionViolation): return False
        return "protection" not in receipt
    if receipt.get("operation") == "manual_release":
        return "settings" not in receipt and _valid_protection(receipt.get("protection"))
    return False


def _request_fingerprint(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _valid_frozen_sensors(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"protectionId", "presenceSensorIds"}:
        return False
    protection_id, sensor_ids = value["protectionId"], value["presenceSensorIds"]
    return (
        isinstance(protection_id, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", protection_id) is not None
        and isinstance(sensor_ids, list)
        and len(sensor_ids) <= 8
        and len(set(sensor_ids)) == len(sensor_ids)
        and all(
            isinstance(sensor_id, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", sensor_id) is not None
            for sensor_id in sensor_ids
        )
    )


def _receipt(request_id: str, operation: str, revision: int, *, settings=None, protection=None) -> dict[str, object]:
    result = {"contract": {"name": "hausman-hub-manual-light-off-protection-command-receipt", "version": 1}, "requestId": request_id, "operation": operation, "accepted": True, "confirmed": True, "status": "confirmed", "revision": revision}
    if settings is not None: result["settings"] = settings
    if protection is not None: result["protection"] = protection
    return result
