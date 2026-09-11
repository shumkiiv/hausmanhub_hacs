"""Command-free CRUD service for room lighting configurations.

The service validates, versions and stores configuration documents. It never
issues a Home Assistant service call or a physical command; execution stays
outside this module.
"""

from __future__ import annotations

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
    room_lighting_violations,
)

DEFAULT_TEMPLATE_ID = "day_profile"

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
                    "entityId": "binary_sensor.demo_presence",
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "light_demo_main",
                    "name": "Основной свет",
                    "kind": "light",
                    "entityId": "light.demo_main",
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
                "entityId": "switch.demo_power",
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


class RoomLightingService:
    """Validate, version and persist room lighting configurations only."""

    def __init__(self, store: object) -> None:
        self._store = store

    async def async_get_config(self, room_id: str) -> RoomLightingConfig | None:
        return await self._store.async_get(room_id)  # type: ignore[attr-defined]

    async def async_list_configs(self) -> tuple[RoomLightingConfig, ...]:
        return await self._store.async_load_all()  # type: ignore[attr-defined]

    async def async_put_config(self, config: RoomLightingConfig) -> RoomLightingConfig:
        """Validate, bump the version on a real change and persist."""

        if not isinstance(config, RoomLightingConfig):
            raise RoomLightingViolation("room lighting config is required")
        violations = room_lighting_violations(config)
        if violations:
            raise RoomLightingViolation("; ".join(violations))
        existing = await self._store.async_get(config.room_id)  # type: ignore[attr-defined]
        if existing is not None and not _changed(existing, config):
            return existing
        version = 1 if existing is None else existing.version + 1
        stored = replace(
            config,
            version=version,
            updated_at=max(_now_seconds(), existing.updated_at + 1 if existing else 0),
        )
        await self._store.async_upsert(stored)  # type: ignore[attr-defined]
        return stored

    async def async_delete_config(self, room_id: str) -> bool:
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
        if overrides is not None:
            if not isinstance(overrides, Mapping):
                raise RoomLightingViolation("template overrides must be a mapping")
            for key, value in overrides.items():
                if key in _OVERLAY_KEYS:
                    payload[key] = deepcopy(value)
        if keep_devices and room_id is not None:
            stored = await self._store.async_get(room_id)  # type: ignore[attr-defined]
            if stored is not None:
                payload["devices"] = stored.to_dict()["devices"]
        return config_from_payload(payload)


def _changed(existing: RoomLightingConfig, candidate: RoomLightingConfig) -> bool:
    def comparable(config: RoomLightingConfig) -> dict[str, object]:
        payload = config.to_dict()
        payload.pop("version", None)
        payload.pop("updatedAt", None)
        return payload

    return comparable(existing) != comparable(candidate)


def _now_seconds() -> int:
    return int(time.time())
