"""Durable first-seen notifications for newly registered HA devices."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Callable, Iterable


DEVICE_DISCOVERY_CONTRACT = "hausman-hub-device-discovery"
_MAX_KNOWN = 2048
_MAX_PENDING = 128


class DeviceDiscoveryViolation(ValueError):
    """The discovery state or requested action is invalid."""

    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    private_device_id: str
    device_id: str
    title: str
    room_id: str | None
    room_name: str | None
    kind: str
    status: str
    domains: tuple[str, ...]
    manufacturer: str | None
    model: str | None
    energy_eligible: bool = False
    climate_eligible: bool = False
    energy_selected: bool = False
    dashboard_visible: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveryArea:
    area_id: str
    name: str


class DeviceDiscoveryService:
    """Persist a baseline and only notify about devices first seen later."""

    def __init__(
        self,
        store: object,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._state: dict[str, object] | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if loaded is None:
            loaded = {
                "revision": 0,
                "updatedAt": self._timestamp(),
                "initialized": False,
                "knownDeviceIds": [],
                "notifications": [],
            }
        self._state = _validate_state(loaded)

    async def async_reconcile(
        self,
        devices: Iterable[DiscoveredDevice],
    ) -> bool:
        by_id = {device.device_id: device for device in devices}
        if len(by_id) > _MAX_KNOWN:
            raise DeviceDiscoveryViolation("device discovery inventory is too large")
        async with self._lock:
            state = self._require_state()
            known = set(state["knownDeviceIds"])
            pending = {
                item["deviceId"]: deepcopy(item) for item in state["notifications"]
            }
            changed = False
            if state["initialized"] is not True:
                state = deepcopy(state)
                state["initialized"] = True
                state["knownDeviceIds"] = sorted(by_id)
                changed = True
            else:
                state = deepcopy(state)
                for device_id in sorted(set(by_id) - known):
                    if len(pending) >= _MAX_PENDING:
                        break
                    device = by_id[device_id]
                    item = _stored_notification(device, self._timestamp())
                    pending[device_id] = item
                    known.add(device_id)
                    changed = True
                for device_id, item in tuple(pending.items()):
                    current = by_id.get(device_id)
                    if current is None:
                        if item.get("status") != "unavailable":
                            item["status"] = "unavailable"
                            changed = True
                        continue
                    refreshed = _stored_notification(
                        current,
                        str(item["firstSeenAt"]),
                        notice_id=str(item["id"]),
                    )
                    if refreshed != item:
                        pending[device_id] = refreshed
                        changed = True
                if set(state["knownDeviceIds"]) != known:
                    state["knownDeviceIds"] = sorted(known)[-_MAX_KNOWN:]
                    changed = True
                state["notifications"] = sorted(
                    pending.values(), key=lambda item: str(item["firstSeenAt"]), reverse=True
                )[:_MAX_PENDING]
            if not changed:
                return False
            state["revision"] = int(state["revision"]) + 1
            state["updatedAt"] = self._timestamp()
            validated = _validate_state(state)
            await self._store.async_save(validated)
            self._state = validated
            return True

    def document(self, areas: Iterable[DiscoveryArea]) -> dict[str, object]:
        state = self._require_state()
        area_values = tuple(sorted(areas, key=lambda item: item.name.casefold()))[:128]
        notifications = [
            _public_notification(item, area_values)
            for item in state["notifications"]
        ]
        return {
            "contract": {"name": DEVICE_DISCOVERY_CONTRACT, "version": 1},
            "revision": state["revision"],
            "updatedAt": state["updatedAt"],
            "initialized": state["initialized"],
            "pendingCount": len(notifications),
            "notifications": notifications,
        }

    def private_device_id(self, notification_id: object) -> str:
        item = self._notification(notification_id)
        return str(item["privateDeviceId"])

    def public_device_id(self, notification_id: object) -> str:
        item = self._notification(notification_id)
        return str(item["deviceId"])

    def action_supported(self, notification_id: object, action: str) -> bool:
        item = self._notification(notification_id)
        if action == "add_to_energy":
            return item.get("energyEligible") is True
        return action in {"acknowledge", "assign_area", "show_on_dashboard"}

    async def async_complete(
        self, expected_revision: object, notification_id: object
    ) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise DeviceDiscoveryViolation("expected discovery revision is invalid")
        async with self._lock:
            state = self._require_state()
            if expected_revision != state["revision"]:
                raise DeviceDiscoveryViolation(
                    "device discovery revision is stale", code="revision_conflict"
                )
            item = self._notification(notification_id)
            next_state = deepcopy(state)
            next_state["notifications"] = [
                candidate
                for candidate in next_state["notifications"]
                if candidate["id"] != item["id"]
            ]
            next_state["revision"] = int(state["revision"]) + 1
            next_state["updatedAt"] = self._timestamp()
            validated = _validate_state(next_state)
            await self._store.async_save(validated)
            self._state = validated

    def _notification(self, notification_id: object) -> dict[str, object]:
        if not isinstance(notification_id, str):
            raise DeviceDiscoveryViolation("device discovery notification id is invalid")
        for item in self._require_state()["notifications"]:
            if item["id"] == notification_id:
                return item
        raise DeviceDiscoveryViolation(
            "device discovery notification was not found", code="not_found"
        )

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _require_state(self) -> dict[str, object]:
        if self._state is None:
            raise DeviceDiscoveryViolation("device discovery service is not loaded")
        return self._state


def _stored_notification(
    device: DiscoveredDevice,
    first_seen_at: str,
    *,
    notice_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": notice_id or _notice_id(device.device_id),
        "deviceId": device.device_id,
        "privateDeviceId": device.private_device_id,
        "firstSeenAt": first_seen_at,
        "title": device.title[:160],
        "roomId": _bounded_optional(device.room_id, 128),
        "roomName": _bounded_optional(device.room_name, 160),
        "kind": device.kind,
        "status": device.status,
        "domains": list(device.domains[:32]),
        "manufacturer": _bounded_optional(device.manufacturer, 160),
        "model": _bounded_optional(device.model, 160),
        "energyEligible": device.energy_eligible,
        "climateEligible": device.climate_eligible,
        "energySelected": device.energy_selected,
        "dashboardVisible": device.dashboard_visible,
    }


def _public_notification(
    item: dict[str, object], areas: tuple[DiscoveryArea, ...]
) -> dict[str, object]:
    current_area = item.get("roomId")
    return {
        key: deepcopy(item[key])
        for key in (
            "id", "deviceId", "firstSeenAt", "title", "roomId", "roomName",
            "kind", "status", "domains", "manufacturer", "model",
        )
    } | {
        "correlationId": f"corr.notice.{str(item['id']).removeprefix('notice_')}",
        "suggestedPlacements": _placements(item),
        "areaOptions": [
            {
                "id": area.area_id,
                "name": area.name,
                "current": area.area_id == current_area,
                "recommended": False,
            }
            for area in areas
        ],
    }


def _placements(item: dict[str, object]) -> list[dict[str, object]]:
    domains = set(item["domains"])
    placements: list[dict[str, object]] = [
        _placement(
            "automatic_section", "devices", "Показать среди устройств",
            "Новое устройство автоматически доступно в общем списке устройств.",
            recommended=True, actionable=False,
        )
    ]
    if item.get("roomId") is None:
        placements.append(
            _placement(
                "assign_area", "rooms", "Выбрать комнату",
                "Устройство ещё не назначено комнате Home Assistant.",
                recommended=True, actionable=True,
            )
        )
    if item.get("climateEligible") is True:
        placements.append(
            _placement(
                "open_settings", "climate", "Использовать в климате",
                "Климатическое устройство или датчик можно выбрать в настройках комнаты.",
                recommended=True, actionable=False,
            )
        )
    if item.get("energyEligible") is True:
        placements.append(
            _placement(
                "add_to_energy", "energy", "Добавить в электроэнергию",
                "Устройство публикует мощность, ток или накопленную энергию.",
                recommended=item.get("energySelected") is not True,
                actionable=item.get("energySelected") is not True,
            )
        )
    section = None
    if domains & {"light"}:
        section = "lighting"
    elif domains & {"lock", "camera", "alarm_control_panel", "binary_sensor"}:
        section = "security"
    elif domains & {"media_player"}:
        section = "media"
    if section is not None:
        placements.append(
            _placement(
                "automatic_section", section, "Подходящий раздел",
                "Тип устройства соответствует этому разделу дома.",
                recommended=True, actionable=False,
            )
        )
    placements.append(
        _placement(
            "show_on_dashboard", "dashboard", "Показать на главной",
            "Устройство можно закрепить среди видимых устройств.",
            recommended=False,
            actionable=item.get("dashboardVisible") is not True,
        )
    )
    return placements[:12]


def _placement(
    kind: str,
    section: str,
    title: str,
    reason: str,
    *,
    recommended: bool,
    actionable: bool,
) -> dict[str, object]:
    return {
        "kind": kind,
        "section": section,
        "title": title,
        "reason": reason,
        "recommended": recommended,
        "actionable": actionable,
    }


def _notice_id(device_id: str) -> str:
    digest = hashlib.sha256(f"new-device:{device_id}".encode()).hexdigest()[:16]
    return f"notice_{digest}"


def _bounded_optional(value: str | None, maximum: int) -> str | None:
    return value[:maximum] if isinstance(value, str) and value else None


def _validate_state(value: object) -> dict[str, object]:
    required = {"revision", "updatedAt", "initialized", "knownDeviceIds", "notifications"}
    if not isinstance(value, dict) or set(value) != required:
        raise DeviceDiscoveryViolation("stored device discovery fields are invalid")
    if type(value["revision"]) is not int or value["revision"] < 0:
        raise DeviceDiscoveryViolation("stored discovery revision is invalid")
    if not isinstance(value["updatedAt"], str) or type(value["initialized"]) is not bool:
        raise DeviceDiscoveryViolation("stored discovery metadata is invalid")
    known = value["knownDeviceIds"]
    pending = value["notifications"]
    if not isinstance(known, list) or len(known) > _MAX_KNOWN or len(set(known)) != len(known):
        raise DeviceDiscoveryViolation("stored known device ids are invalid")
    if not all(isinstance(item, str) and item.startswith("device_") for item in known):
        raise DeviceDiscoveryViolation("stored known device id is invalid")
    if not isinstance(pending, list) or len(pending) > _MAX_PENDING:
        raise DeviceDiscoveryViolation("stored notifications are invalid")
    required_notice = {
        "id", "deviceId", "privateDeviceId", "firstSeenAt", "title", "roomId",
        "roomName", "kind", "status", "domains", "manufacturer", "model",
        "energyEligible", "climateEligible", "energySelected", "dashboardVisible",
    }
    for item in pending:
        if not isinstance(item, dict) or set(item) != required_notice:
            raise DeviceDiscoveryViolation("stored notification fields are invalid")
        if not all(isinstance(item[key], str) and item[key] for key in ("id", "deviceId", "privateDeviceId", "firstSeenAt", "title")):
            raise DeviceDiscoveryViolation("stored notification identity is invalid")
        if item["kind"] not in {"physical", "virtual", "entity_only"} or item["status"] not in {"available", "unavailable", "empty"}:
            raise DeviceDiscoveryViolation("stored notification state is invalid")
        if not isinstance(item["domains"], list) or len(item["domains"]) > 32:
            raise DeviceDiscoveryViolation("stored notification domains are invalid")
        for key in ("energyEligible", "climateEligible", "energySelected", "dashboardVisible"):
            if type(item[key]) is not bool:
                raise DeviceDiscoveryViolation("stored notification flags are invalid")
    return deepcopy(value)
