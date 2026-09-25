"""Command-free CRUD service for room lighting configurations.

The service validates, versions and stores configuration documents. It never
issues a Home Assistant service call or a physical command; execution stays
outside this module.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import time
from typing import Mapping

from ..domain.room_lighting import (
    ROOM_LIGHTING_CONFIG_NAME,
    ROOM_LIGHTING_CONFIG_VERSION,
    RoomLightingConfig,
    RoomLightingViolation,
    config_from_payload,
    room_lighting_entity_collisions,
    room_lighting_violations,
)

DEFAULT_TEMPLATE_ID = "day_profile"
TEMPLATE_CREATE_ONLY_VERSION = 9007199254740991

_OVERLAY_KEYS = frozenset(
    {
        "name",
        "devices",
        "schedule",
        "switchBindings",
        "illumination",
        "dimming",
        "manualOffProtection",
        "awayBehavior",
        "autoAdopt",
        "timers",
        "behaviors",
        "templateId",
        "overrides",
    }
)


def _template_document() -> dict[str, object]:
    """Return the built-in day-profile document matching the template schema."""

    return {
        "contract": {
            "name": "hausman-hub-room-lighting-template",
            "version": 1,
        },
        "id": DEFAULT_TEMPLATE_ID,
        "title": "Суточный профиль освещения",
        "description": (
            "Базовый суточный профиль: включение по присутствию, вечерний "
            "сценарий и ночная подсветка."
        ),
        "category": "day_profile",
        "devices": {
            "sensors": [
                {
                    "id": "sensor_demo_presence",
                    "name": "Датчик присутствия",
                    "kind": "presence",
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "light_demo_main",
                    "name": "Основной свет",
                    "kind": "light",
                    "role": "main",
                    "groupId": "grp_demo_main",
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": {
                "id": "switch_demo_power",
                "name": "Питание света",
                "autoAdoptOverride": None,
            },
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_day",
                "title": "День",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "sunrise", "offsetMinutes": 0},
                },
                "targets": {
                    "lightTargets": ["light_demo_main"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": 60,
                    "colorTemperature": 4000,
                    "fade": True,
                    "mode": "on_presence",
                    "minOnSeconds": 0,
                },
            },
            {
                "id": "sch_evening",
                "title": "Вечер",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "sunset", "offsetMinutes": -30},
                },
                "targets": {
                    "lightTargets": ["light_demo_main"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": 40,
                    "colorTemperature": 3000,
                    "fade": True,
                    "mode": "on_presence",
                    "minOnSeconds": 0,
                },
            },
            {
                "id": "sch_night",
                "title": "Ночная подсветка",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "02:00", "offsetMinutes": 0},
                },
                "targets": {
                    "lightTargets": ["light_demo_main"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": 10,
                    "colorTemperature": 2700,
                    "fade": True,
                    "mode": "night_light",
                    "minOnSeconds": 600,
                },
            },
        ],
        "switchBindings": [],
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 20,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {
            "mode": "room_off",
            "return": {"restore": "by_current_conditions"},
        },
        "autoAdopt": True,
    }


ROOM_LIGHTING_TEMPLATES: dict[str, dict[str, object]] = {
    DEFAULT_TEMPLATE_ID: _template_document(),
}


class RoomLightingStorageError(RuntimeError):
    """Room lighting configuration storage did not durably save."""


class RoomLightingRevisionConflict(RoomLightingViolation):
    """A configuration write did not match the locked room revision."""

    def __init__(self, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            f"room lighting revision conflict: expected {expected_revision}, "
            f"actual {actual_revision}"
        )


class RoomLightingService:
    """Validate, version and persist room lighting configurations only."""

    def __init__(self, store: object, *, mutation_lock: asyncio.Lock | None = None) -> None:
        self._store = store
        self._lock = mutation_lock or asyncio.Lock()

    async def async_get_config(self, room_id: str) -> RoomLightingConfig | None:
        return await self._store.async_get(room_id)  # type: ignore[attr-defined]

    async def async_list_configs(self) -> tuple[RoomLightingConfig, ...]:
        return await self._store.async_load_all()  # type: ignore[attr-defined]

    async def async_put_config(
        self,
        config: RoomLightingConfig,
        *,
        expected_version: int | None = None,
    ) -> RoomLightingConfig:
        """Validate and atomically compare, version and persist a configuration."""

        if not isinstance(config, RoomLightingConfig):
            raise RoomLightingViolation("room lighting config is required")
        async with self._lock:
            violations = room_lighting_violations(config)
            if violations:
                raise RoomLightingViolation("; ".join(violations))
            existing = await self._store.async_get(config.room_id)  # type: ignore[attr-defined]
            if config.version == TEMPLATE_CREATE_ONLY_VERSION:
                if existing is not None:
                    raise RoomLightingRevisionConflict(
                        TEMPLATE_CREATE_ONLY_VERSION, existing.version
                    )
            elif (
                expected_version is not None
                and existing is not None
                and expected_version != existing.version
            ):
                raise RoomLightingRevisionConflict(expected_version, existing.version)
            others = tuple(
                existing
                for existing in await self.async_list_configs()
                if existing.room_id != config.room_id
            )
            collisions = room_lighting_entity_collisions((config, *others))
            if collisions:
                raise RoomLightingViolation("; ".join(collisions))
            if existing is not None and not _changed(existing, config):
                return existing
            version = 1 if existing is None else existing.version + 1
            stored = replace(
                config,
                version=version,
                updated_at=max(_now_seconds(), existing.updated_at + 1 if existing else 0),
            )
            saved = await self._store.async_upsert(stored)  # type: ignore[attr-defined]
            if saved is False:
                raise RoomLightingStorageError("room lighting config storage save failed")
            return stored

    async def async_delete_config(self, room_id: str) -> bool:
        async with self._lock:
            return await self._store.async_delete(room_id)  # type: ignore[attr-defined]

    async def async_apply_template(
        self,
        template_id: str,
        overrides: Mapping[str, object] | None = None,
        *,
        room_id: str | None = None,
        name: str | None = None,
        keep_devices: bool = False,
    ) -> RoomLightingConfig:
        """Build a valid configuration from a ready template and overrides."""

        template = ROOM_LIGHTING_TEMPLATES.get(template_id)
        if template is None:
            raise RoomLightingViolation(f"unknown room lighting template: {template_id}")
        payload = deepcopy(template)
        payload["contract"] = {
            "name": ROOM_LIGHTING_CONFIG_NAME,
            "version": ROOM_LIGHTING_CONFIG_VERSION,
        }
        payload["roomId"] = room_id or "room_demo_template"
        payload["name"] = name or str(template["title"])
        payload["version"] = 1
        payload["updatedAt"] = _now_seconds()
        payload["templateId"] = template_id
        kept_stored_devices = False
        if overrides is not None:
            if not isinstance(overrides, Mapping):
                raise RoomLightingViolation("template overrides must be a mapping")
            for key, value in overrides.items():
                if key in _OVERLAY_KEYS:
                    payload[key] = deepcopy(value)
        if room_id is not None:
            async with self._lock:
                stored = await self._store.async_get(room_id)  # type: ignore[attr-defined]
                if stored is None:
                    # This transient token is converted to ordinary version 1
                    # by async_put_config after its locked absence check.
                    payload["version"] = TEMPLATE_CREATE_ONLY_VERSION
                elif keep_devices:
                    kept_stored_devices = True
                    if overrides is not None and "devices" in overrides:
                        raise RoomLightingViolation(
                            "template devices override conflicts with keepDevices"
                        )
                    stored_payload = stored.to_dict()
                    payload["devices"] = stored_payload["devices"]
                    # The draft carries the exact revision of the device snapshot
                    # it preserves, so a stale PUT cannot overwrite another edit.
                    payload["version"] = stored_payload["version"]
                    payload["updatedAt"] = stored_payload["updatedAt"]
                    if overrides is None or "switchBindings" not in overrides:
                        payload["switchBindings"] = deepcopy(stored_payload["switchBindings"])
                    # Keep the kept devices and their auxiliary policy together:
                    # the fan policy references a device from this list. A
                    # template-level policy is dropped so a kept fan device never
                    # survives without its thresholds.
                    payload.pop("auxiliary", None)
                    if stored_payload.get("auxiliary") is not None:
                        payload["auxiliary"] = stored_payload["auxiliary"]
                    if overrides is None or "schedule" not in overrides:
                        _remap_template_schedule(template, payload, stored_payload["devices"])
        config = config_from_payload(payload)
        if kept_stored_devices:
            _validate_selected_target_capabilities(config)
        return config


def _remap_template_schedule(
    template: Mapping[str, object], payload: dict[str, object], devices: object
) -> None:
    """Map built-in template direct targets onto explicitly kept devices.

    An exact target ID is retained. Otherwise only a single kept target with
    the same role is safe to select. Group and role selectors remain unchanged
    and are validated by the configuration domain against the kept devices.
    """

    schedule = payload.get("schedule")
    template_devices = template.get("devices")
    if not isinstance(schedule, list) or not isinstance(template_devices, Mapping):
        return
    template_targets = template_devices.get("light_targets")
    if not isinstance(template_targets, list) or not isinstance(devices, Mapping):
        raise RoomLightingViolation("kept room devices cannot remap template schedule")
    kept_targets = devices.get("light_targets")
    if not isinstance(kept_targets, list):
        raise RoomLightingViolation("kept room light targets cannot remap template schedule")

    by_id = {
        item.get("id"): item
        for item in kept_targets
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    template_by_id = {
        item.get("id"): item
        for item in template_targets
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    for entry in schedule:
        if not isinstance(entry, Mapping):
            continue
        targets = entry.get("targets")
        if not isinstance(targets, Mapping):
            continue
        direct_targets = targets.get("lightTargets")
        if not isinstance(direct_targets, list):
            continue
        remapped: list[object] = []
        for target_id in direct_targets:
            if not isinstance(target_id, str):
                remapped.append(target_id)
                continue
            entry_id = entry.get("id", "unknown")
            if target_id in by_id:
                if by_id[target_id].get("autoControl") is False:
                    raise RoomLightingViolation(
                        f"template schedule entry {entry_id} maps manual-only target: {target_id}"
                    )
                remapped.append(target_id)
                continue
            source = template_by_id.get(target_id)
            role = source.get("role") if isinstance(source, Mapping) else None
            candidates = [
                item
                for item in kept_targets
                if isinstance(item, Mapping) and item.get("role") == role and role is not None
            ]
            if len(candidates) != 1 or not isinstance(candidates[0].get("id"), str):
                raise RoomLightingViolation(
                    f"template schedule entry {entry_id} cannot safely map target: {target_id}"
                )
            if candidates[0].get("autoControl") is False:
                raise RoomLightingViolation(
                    f"template schedule entry {entry_id} maps manual-only target: "
                    f"{candidates[0]['id']}"
                )
            remapped.append(candidates[0]["id"])
        targets["lightTargets"] = remapped


def _validate_selected_target_capabilities(config: RoomLightingConfig) -> None:
    """Reject brightness/CCT settings unsupported by any selected target."""

    def selected(targets: object) -> tuple[object, ...]:
        direct = set(targets.light_targets)
        groups = set(targets.group_ids)
        roles = set(targets.roles)
        return tuple(
            target
            for target in config.devices.light_targets
            if target.id in direct
            or target.group_id in groups
            or target.role in roles
        )

    for entry in config.schedule:
        for target in selected(entry.targets):
            if entry.how.brightness is not None and not target.brightness:
                raise RoomLightingViolation(
                    f"schedule entry {entry.id} sets brightness on an "
                    f"unregulated target: {target.id}"
                )
            if entry.how.color_temperature is not None and not target.color_temperature:
                raise RoomLightingViolation(
                    f"schedule entry {entry.id} sets colour temperature on an "
                    f"unregulated target: {target.id}"
                )

    for binding in config.switch_bindings:
        for target in selected(binding.targets):
            if binding.brightness is not None and not target.brightness:
                raise RoomLightingViolation(
                    f"switch binding {binding.switch_id} sets brightness on an "
                    f"unregulated target: {target.id}"
                )
            if binding.color_temperature is not None and not target.color_temperature:
                raise RoomLightingViolation(
                    f"switch binding {binding.switch_id} sets colour temperature on an "
                    f"unregulated target: {target.id}"
                )


def _changed(existing: RoomLightingConfig, candidate: RoomLightingConfig) -> bool:
    def comparable(config: RoomLightingConfig) -> dict[str, object]:
        payload = config.to_dict()
        payload.pop("version", None)
        payload.pop("updatedAt", None)
        return payload

    return comparable(existing) != comparable(candidate)


def _now_seconds() -> int:
    return int(time.time())
