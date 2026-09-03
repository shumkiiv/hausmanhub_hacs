"""Fail-closed, restart-safe protection after a manual light switch-off."""

from __future__ import annotations

import asyncio
import copy
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
        self._completed: list[dict[str, object]] = []
        self._receipts: dict[str, dict[str, object]] = {}
        self._sensor_states: dict[str, tuple[str, datetime]] = {}
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
            self._completed = copy.deepcopy(payload["completed"])
            self._receipts = {
                str(item["requestId"]): copy.deepcopy(item["receipt"])
                for item in payload["receipts"]
            }
        except Exception:  # persistence evidence must never open automatic control
            self.unhealthy = True
        finally:
            self._loaded = True

    async def async_replace_settings(
        self, request_id: str, expected_revision: int, settings: Mapping[str, object]
    ) -> dict[str, object]:
        async with self._lock:
            existing = self._receipt(request_id)
            if existing is not None:
                return existing
            self._require_healthy()
            if type(expected_revision) is not int or expected_revision != self._settings_revision:
                raise ValueError("settings revision conflict")
            parsed = parse_settings(settings)
            self._settings = parsed
            self._settings_revision += 1
            receipt = _receipt(
                request_id, "settings_updated", self._settings_revision,
                settings=parsed.as_wire(),
            )
            await self._persist_with_receipt(request_id, receipt)
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
            for profile in self._settings.profiles:
                if entity_id in profile.presence_sensor_ids:
                    observed = _state_timestamp(new_state, now)
                    if new == "off" and (now - observed).total_seconds() <= _FRESH_SENSOR_SECONDS:
                        self._sensor_states[entity_id] = ("off", observed)
                    else:
                        self._sensor_states[entity_id] = (new, observed)
                    if new == "off":
                        key = _profile_key(profile.room_id, profile.profile_id)
                        record = self._protections.get(key)
                        if record is not None:
                            updated = copy.deepcopy(record)
                            updated["absenceSince"] = _wire_time(observed)
                            updated["revision"] = int(record["revision"]) + 1
                            self._protections[key] = updated
                            self._state_revision += 1
                            await self._save()
            if old == "on" and new == "off" and not _automatic(attribution):
                profile = _profile_for_light(self._settings, entity_id)
                if profile is not None:
                    await self._activate(profile, entity_id, now, attribution)
            elif old == "off" and new == "on" and not _automatic(attribution):
                profile = _profile_for_light(self._settings, entity_id)
                if profile is not None:
                    key = _profile_key(profile.room_id, profile.profile_id)
                    record = self._protections.get(key)
                    if record is not None:
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
            profile = _profile_for_light(self._settings, entity_id)
            if profile is None:
                return LightProtectionDecision(True)
            key = _profile_key(profile.room_id, profile.profile_id)
            record = self._protections.get(key)
            if record is None:
                return LightProtectionDecision(True)
            now = _utc(self._now())
            if _may_release(record, profile, self._sensor_states, now):
                if not dry_run:
                    updated = copy.deepcopy(record)
                    updated["state"] = ProtectionState.RELEASED.value
                    updated["revision"] = int(record["revision"]) + 1
                    await self._complete(key, updated)
                return LightProtectionDecision(True, "manual_off_protection_released", key)
            reason = (
                "manual_off_protection_active"
                if now < _parse_time(record["notBefore"])
                else "manual_off_protection_absence_required"
            )
            return LightProtectionDecision(False, reason, key)

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
            existing = self._receipt(request_id)
            if existing is not None:
                return existing
            self._require_healthy()
            record = self._protections.get(protection_id)
            if record is None:
                raise ValueError("protection is not active")
            if not record["effectivePolicy"]["allowManualRelease"]:
                raise ValueError("manual release is disabled")
            if type(expected_protection_revision) is not int or expected_protection_revision != record["revision"]:
                raise ValueError("protection revision conflict")
            updated = copy.deepcopy(record)
            updated["state"] = ProtectionState.RELEASED.value
            updated["revision"] = int(record["revision"]) + 1
            await self._complete(protection_id, updated, request_id=request_id)
            receipt = _receipt(request_id, "manual_release", int(updated["revision"]), protection=updated)
            self._receipts[request_id] = receipt
            await self._save()
            return copy.deepcopy(receipt)

    def snapshot(self) -> dict[str, object]:
        return {
            "revision": self._settings_revision,
            "settings": self._settings.as_wire(),
            "protections": copy.deepcopy([*self._protections.values(), *self._completed]),
            "unhealthy": self.unhealthy,
        }

    async def _activate(self, profile, entity_id: str, now: datetime, attribution) -> None:
        key = _profile_key(profile.room_id, profile.profile_id)
        current = self._protections.get(key)
        if current is None:
            policy, source = resolve_manual_off_policy_with_source(
                self._settings.as_wire(), profile.room_id, profile.profile_id
            )
        else:
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
            "lightIds": list(profile.light_ids), "startedAt": _wire_time(now),
            "notBefore": _wire_time(now.timestamp() + policy.minimum_interval_seconds),
            "absenceSince": None, "effectivePolicy": policy.as_wire(),
            "effectivePolicySource": source.value, "policyFingerprint": policy.fingerprint,
            "reason": "manual_off", "attributionSource": "manual_command" if attribution else "state_transition",
            "attributionId": attribution.attribution_id if attribution else entity_id,
            "revision": revision, "state": ProtectionState.ACTIVE.value,
        }
        self._protections[key] = record
        self._state_revision += 1
        await self._save()

    async def _complete(self, key: str, record: dict[str, object], *, request_id: str | None = None) -> None:
        self._protections.pop(key, None)
        self._completed.append(record)
        self._completed = self._completed[-MAX_COMPLETED_PROTECTIONS:]
        self._state_revision += 1
        if request_id is None:
            await self._save()

    async def _persist_with_receipt(self, request_id: str, receipt: dict[str, object]) -> None:
        self._receipts[request_id] = receipt
        if len(self._receipts) > MAX_IDEMPOTENCY_RECEIPTS:
            self._receipts.pop(next(iter(self._receipts)))
        await self._save()

    async def _save(self) -> None:
        try:
            await self._store.async_save(self._payload())
        except Exception:
            self.unhealthy = True
            raise

    def _payload(self) -> dict[str, object]:
        return {
            "version": 1, "settingsRevision": self._settings_revision,
            "stateRevision": self._state_revision, "settings": self._settings.as_wire(),
            "protections": list(self._protections.values()), "completed": self._completed,
            "receipts": [{"requestId": key, "receipt": value} for key, value in self._receipts.items()],
        }

    def _receipt(self, request_id: str) -> dict[str, object] | None:
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError("request id is invalid")
        return copy.deepcopy(self._receipts.get(request_id))

    def _require_healthy(self) -> None:
        if self.unhealthy or not self._loaded:
            raise RuntimeError("manual light-off protection is unhealthy")


def valid_manual_light_off_protection_payload(value: object) -> bool:
    try:
        if not isinstance(value, Mapping) or set(value) != {"version", "settingsRevision", "stateRevision", "settings", "protections", "completed", "receipts"}:
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
        active = [_valid_protection(item) for item in value["protections"]]
        completed = [_valid_protection(item) for item in value["completed"]]
        if not all(active) or not all(completed):
            return False
        if any(item["state"] not in {"active", "ready_to_release"} for item in value["protections"]):
            return False
        if not all(_valid_receipt(item) for item in value["receipts"]):
            return False
        return len({_protection_key(item) for item in [*value["protections"], *value["completed"]]}) == len(value["protections"]) + len(value["completed"])
    except (KeyError, TypeError, ValueError, ManualLightOffProtectionViolation):
        return False


def _default_settings() -> ManualLightOffProtectionSettings:
    return parse_settings({"globalPolicy": {"enabled": True, "minimumIntervalSeconds": 600, "releaseMode": "timer_and_absence", "stableAbsenceSeconds": 30, "extendOnRepeatedManualOff": True, "noSensorFallback": "timer_only", "protectedScope": "profile", "allowManualRelease": True}, "roomOverrides": {}, "profileOverrides": {}, "profiles": []})


def _profile_for_light(settings, entity_id):
    return next((item for item in settings.profiles if entity_id in item.light_ids), None)


def _profile_key(room_id: str, profile_id: str) -> str:
    return f"{room_id}:{profile_id}"


def _protection_key(value: Mapping[str, object]) -> str:
    return _profile_key(str(value["roomId"]), str(value["profileId"]))


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


def _may_release(record, profile, sensors, now: datetime) -> bool:
    policy = record["effectivePolicy"]
    timer = now >= _parse_time(record["notBefore"])
    if not profile.presence_sensor_ids:
        if policy["noSensorFallback"] == "manual_release":
            return False
        absence = timer
    else:
        absence_times = []
        for sensor_id in profile.presence_sensor_ids:
            state = sensors.get(sensor_id)
            if state is None or state[0] != "off" or (now - state[1]).total_seconds() > _FRESH_SENSOR_SECONDS:
                return False
            absence_times.append(state[1])
        absence = bool(absence_times) and (now - max(absence_times)).total_seconds() >= policy["stableAbsenceSeconds"]
    mode = policy["releaseMode"]
    return timer if mode == "timer_only" else absence if mode == "absence_only" else timer and absence


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
    if not isinstance(value, Mapping) or set(value) != {"requestId", "receipt"}:
        return False
    request_id, receipt = value["requestId"], value["receipt"]
    if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id) is None or not isinstance(receipt, Mapping):
        return False
    if set(receipt) - {"contract", "requestId", "operation", "accepted", "confirmed", "status", "revision", "settings", "protection"}:
        return False
    return receipt.get("requestId") == request_id and receipt.get("operation") in {"settings_updated", "manual_release"} and receipt.get("status") == "confirmed" and receipt.get("accepted") is True and receipt.get("confirmed") is True and type(receipt.get("revision")) is int


def _receipt(request_id: str, operation: str, revision: int, *, settings=None, protection=None) -> dict[str, object]:
    result = {"contract": {"name": "hausman-hub-manual-light-off-protection-command-receipt", "version": 1}, "requestId": request_id, "operation": operation, "accepted": True, "confirmed": True, "status": "confirmed", "revision": revision}
    if settings is not None: result["settings"] = settings
    if protection is not None: result["protection"] = protection
    return result
