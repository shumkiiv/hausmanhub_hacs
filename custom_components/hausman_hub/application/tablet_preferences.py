"""Canonical Home Assistant-owned tablet and energy preferences."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable

from ..domain.hub_settings import HausmanHubSettings

_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_DEVICE_ID = re.compile(r"^device_[0-9a-f]{16}$")
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_START_MODES = frozenset({"dashboard", "kiosk"})
_SIGNAL_MODES = frozenset({"any", "all"})
_UNAVAILABLE_POLICIES = frozenset({"keep_awake", "use_timeouts", "sleep"})
_ENERGY_UNITS = frozenset({"watts", "amps", "both"})
_ENERGY_AGGREGATIONS = frozenset({"combined", "separate"})
ROOM_TYPE_ICONS = {
    "rooms": "mdi:door-open",
    "living": "mdi:sofa",
    "kitchen": "mdi:fridge-outline",
    "bedroom": "mdi:bed",
    "child": "mdi:human-child",
    "bathroom": "mdi:bathtub",
    "toilet": "mdi:human-male-female",
    "hallway": "mdi:door",
    "office": "mdi:briefcase",
    "terrace": "mdi:balcony",
    "spa": "mdi:hot-tub",
    "storage": "mdi:archive",
    "category": "mdi:shape",
}
ROOM_TYPE_BY_ICON = {icon: room_type for room_type, icon in ROOM_TYPE_ICONS.items()}


class TabletPreferencesViolation(ValueError):
    """A settings document is malformed, stale, or outside safe bounds."""

    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


def default_tablet_settings() -> dict[str, object]:
    return {
        "startScreen": {"mode": "dashboard", "heroTargetId": None},
        "kiosk": {
            "enabled": False,
            "enterAfterIdleSeconds": 300,
            "doubleTapToExit": True,
        },
        "displayAutomation": {
            "enabled": False,
            "sensorEntityIds": [],
            "signalMode": "any",
            "wakeBrightnessPercent": 100,
            "dimBrightnessPercent": 50,
            "dimAfterSeconds": 60,
            "sleepAfterSeconds": 300,
            "unavailablePolicy": "keep_awake",
        },
        "dayNight": {
            "enabled": False,
            "dayStartsAt": "07:00",
            "nightStartsAt": "22:00",
            "deepNightStartsAt": "00:30",
            "dayVolumePercent": 60,
            "nightVolumePercent": 30,
            "deepNightVolumePercent": 15,
            "appExitBrightnessPercent": 50,
        },
        "alerts": {
            "lowBatteryThresholdPercent": 8,
            "lowBatterySnoozeMinutes": 60,
            "criticalSoundEnabled": True,
            "criticalVolumePercent": 100,
        },
        "dashboard": {"favoriteScenarioIds": [], "visibleDeviceIds": []},
        "intercom": {"showQuickAccess": False, "deviceId": None},
    }


def energy_settings_from_legacy(settings: HausmanHubSettings) -> dict[str, object]:
    return {
        "displayUnits": settings.energy_display_units,
        "showVoltage": settings.energy_show_voltage,
        "aggregation": settings.energy_aggregation,
        "useAllDevices": settings.energy_use_all_devices,
        "selectedDeviceIds": list(settings.energy_selected_device_ids),
    }


def energy_as_hub_settings(settings: dict[str, object]) -> HausmanHubSettings:
    validated = validate_energy_settings(settings)
    return HausmanHubSettings(
        energy_display_units=validated["displayUnits"],  # type: ignore[arg-type]
        energy_show_voltage=validated["showVoltage"],  # type: ignore[arg-type]
        energy_aggregation=validated["aggregation"],  # type: ignore[arg-type]
        energy_use_all_devices=validated["useAllDevices"],  # type: ignore[arg-type]
        energy_selected_device_ids=tuple(validated["selectedDeviceIds"]),
    )


def validate_tablet_settings(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "startScreen", "kiosk", "displayAutomation", "dayNight",
        "alerts", "dashboard", "intercom",
    }:
        raise TabletPreferencesViolation("tablet settings fields are invalid")
    result = deepcopy(value)
    start = _object(result["startScreen"], {"mode", "heroTargetId"}, "start screen")
    if start["mode"] not in _START_MODES:
        raise TabletPreferencesViolation("start screen mode is invalid")
    _nullable_text(start["heroTargetId"], 128, "hero target")
    kiosk = _object(
        result["kiosk"], {"enabled", "enterAfterIdleSeconds", "doubleTapToExit"}, "kiosk"
    )
    _boolean(kiosk["enabled"], "kiosk enabled")
    _integer(kiosk["enterAfterIdleSeconds"], 0, 86400, "kiosk idle timeout")
    _boolean(kiosk["doubleTapToExit"], "kiosk double tap")
    display = _object(
        result["displayAutomation"],
        {
            "enabled", "sensorEntityIds", "signalMode", "wakeBrightnessPercent",
            "dimBrightnessPercent", "dimAfterSeconds", "sleepAfterSeconds",
            "unavailablePolicy",
        },
        "display automation",
    )
    _boolean(display["enabled"], "display automation enabled")
    _string_list(display["sensorEntityIds"], 16, _ENTITY_ID, "display sensors")
    if display["signalMode"] not in _SIGNAL_MODES:
        raise TabletPreferencesViolation("display signal mode is invalid")
    _integer(display["wakeBrightnessPercent"], 10, 100, "wake brightness")
    _integer(display["dimBrightnessPercent"], 1, 100, "dim brightness")
    _integer(display["dimAfterSeconds"], 0, 3600, "dim timeout")
    _integer(display["sleepAfterSeconds"], 30, 86400, "sleep timeout")
    if display["unavailablePolicy"] not in _UNAVAILABLE_POLICIES:
        raise TabletPreferencesViolation("display unavailable policy is invalid")
    day_night = _object(
        result["dayNight"],
        {
            "enabled", "dayStartsAt", "nightStartsAt", "deepNightStartsAt",
            "dayVolumePercent", "nightVolumePercent", "deepNightVolumePercent",
            "appExitBrightnessPercent",
        },
        "day and night",
    )
    _boolean(day_night["enabled"], "day and night enabled")
    for key in ("dayStartsAt", "nightStartsAt", "deepNightStartsAt"):
        if not isinstance(day_night[key], str) or not _TIME.fullmatch(day_night[key]):
            raise TabletPreferencesViolation(f"{key} is invalid")
    for key in ("dayVolumePercent", "nightVolumePercent", "deepNightVolumePercent"):
        _integer(day_night[key], 0, 100, key)
    _integer(day_night["appExitBrightnessPercent"], 1, 100, "exit brightness")
    alerts = _object(
        result["alerts"],
        {
            "lowBatteryThresholdPercent", "lowBatterySnoozeMinutes",
            "criticalSoundEnabled", "criticalVolumePercent",
        },
        "alerts",
    )
    _integer(alerts["lowBatteryThresholdPercent"], 1, 50, "battery threshold")
    _integer(alerts["lowBatterySnoozeMinutes"], 1, 1440, "battery snooze")
    _boolean(alerts["criticalSoundEnabled"], "critical sound")
    _integer(alerts["criticalVolumePercent"], 1, 100, "critical volume")
    dashboard = _object(
        result["dashboard"], {"favoriteScenarioIds", "visibleDeviceIds"}, "dashboard"
    )
    _plain_ids(dashboard["favoriteScenarioIds"], 64, "favorite scenarios")
    _plain_ids(dashboard["visibleDeviceIds"], 128, "visible devices")
    intercom = _object(result["intercom"], {"showQuickAccess", "deviceId"}, "intercom")
    _boolean(intercom["showQuickAccess"], "intercom quick access")
    _nullable_text(intercom["deviceId"], 128, "intercom device")
    return result


def validate_energy_settings(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "displayUnits", "showVoltage", "aggregation", "useAllDevices",
        "selectedDeviceIds",
    }:
        raise TabletPreferencesViolation("energy settings fields are invalid")
    result = deepcopy(value)
    if result["displayUnits"] not in _ENERGY_UNITS:
        raise TabletPreferencesViolation("energy display units are invalid")
    _boolean(result["showVoltage"], "energy voltage")
    if result["aggregation"] not in _ENERGY_AGGREGATIONS:
        raise TabletPreferencesViolation("energy aggregation is invalid")
    _boolean(result["useAllDevices"], "energy all devices")
    _string_list(result["selectedDeviceIds"], 128, _DEVICE_ID, "energy devices")
    return result


def validate_room_settings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > 128:
        raise TabletPreferencesViolation("room settings are invalid")
    result = deepcopy(value)
    room_ids: set[str] = set()
    orders: set[int] = set()
    for room in result:
        item = _object(room, {"roomId", "type", "icon", "order", "visible"}, "room")
        room_id = item["roomId"]
        room_type = item["type"]
        order = item["order"]
        if (
            not isinstance(room_id, str)
            or not room_id
            or len(room_id) > 128
            or room_id in room_ids
        ):
            raise TabletPreferencesViolation("room id is invalid")
        if (
            room_type not in ROOM_TYPE_ICONS
            or item["icon"] != ROOM_TYPE_ICONS[room_type]
        ):
            raise TabletPreferencesViolation("room purpose is invalid")
        _integer(order, 0, 1023, "room order")
        if order in orders:
            raise TabletPreferencesViolation("room order is duplicated")
        _boolean(item["visible"], "room visibility")
        room_ids.add(room_id)
        orders.add(order)
    return result


def room_type_from_icon(icon: object) -> str:
    return ROOM_TYPE_BY_ICON.get(icon, "rooms") if isinstance(icon, str) else "rooms"


class TabletPreferencesService:
    """Atomically persist both public preference documents with revisions."""

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

    async def async_load(self, legacy_energy: HausmanHubSettings) -> None:
        loaded = await self._store.async_load()
        if loaded is None:
            timestamp = self._timestamp()
            loaded = {
                "tablet": _document(0, timestamp, default_tablet_settings()),
                "energy": _document(
                    0, timestamp, energy_settings_from_legacy(legacy_energy)
                ),
                "rooms": _room_document(0, timestamp, []),
            }
        elif isinstance(loaded, dict) and "rooms" not in loaded:
            loaded = deepcopy(loaded)
            tablet = loaded.get("tablet")
            timestamp = (
                tablet.get("updatedAt")
                if isinstance(tablet, dict) and isinstance(tablet.get("updatedAt"), str)
                else self._timestamp()
            )
            loaded["rooms"] = _room_document(0, timestamp, [])
        self._state = _validate_state(loaded)

    @property
    def tablet(self) -> dict[str, object]:
        return deepcopy(self._document("tablet"))

    @property
    def energy(self) -> dict[str, object]:
        return deepcopy(self._document("energy"))

    @property
    def rooms(self) -> dict[str, object]:
        return deepcopy(self._document("rooms"))

    @property
    def room_presentations(self) -> dict[str, dict[str, object]]:
        return {
            str(room["roomId"]): deepcopy(room)
            for room in self._document("rooms")["rooms"]
        }

    @property
    def energy_for_dashboard(self) -> HausmanHubSettings:
        return energy_as_hub_settings(self._document("energy")["settings"])

    @property
    def energy_selected_device_ids(self) -> frozenset[str]:
        settings = self._document("energy")["settings"]
        return frozenset(settings["selectedDeviceIds"])

    @property
    def dashboard_visible_device_ids(self) -> frozenset[str]:
        settings = self._document("tablet")["settings"]
        return frozenset(settings["dashboard"]["visibleDeviceIds"])

    @property
    def tablet_pinned_entity_ids(self) -> frozenset[str]:
        """Entity ids explicitly chosen in the tablet profile.

        Devices selected by the user (currently the intercom) must stay
        visible in the dashboard projection even when they would otherwise
        be hidden as virtual duplicate projections.
        """

        settings = self._document("tablet")["settings"]
        intercom = settings.get("intercom") if isinstance(settings, dict) else None
        device_id = intercom.get("deviceId") if isinstance(intercom, dict) else None
        if isinstance(device_id, str) and device_id.strip():
            return frozenset({device_id.strip()})
        return frozenset()

    async def async_replace_tablet(
        self, expected_revision: object, settings: object
    ) -> dict[str, object]:
        return await self._replace(
            "tablet", expected_revision, validate_tablet_settings(settings)
        )

    async def async_replace_energy(
        self, expected_revision: object, settings: object
    ) -> dict[str, object]:
        return await self._replace(
            "energy", expected_revision, validate_energy_settings(settings)
        )

    async def async_replace_rooms(
        self,
        expected_revision: object,
        rooms: object,
        *,
        apply: Callable[[list[dict[str, object]]], Awaitable[object]] | None = None,
        rollback: Callable[[], Awaitable[object]] | None = None,
    ) -> dict[str, object]:
        validated = validate_room_settings(rooms)
        async with self._lock:
            current = self._document("rooms")
            self._validate_expected_revision(expected_revision, current)
            if apply is not None:
                await apply(validated)
            updated = _room_document(
                int(expected_revision) + 1, self._timestamp(), validated
            )
            state = deepcopy(self._required_state())
            state["rooms"] = updated
            try:
                await self._store.async_save(state)
            except Exception:
                if rollback is not None:
                    await rollback()
                raise
            self._state = state
            return deepcopy(updated)

    async def async_include_energy_device(self, device_id: str) -> None:
        if not _DEVICE_ID.fullmatch(device_id):
            raise TabletPreferencesViolation("energy device id is invalid")
        async with self._lock:
            state = deepcopy(self._required_state())
            current = deepcopy(self._document("energy"))
            settings = current["settings"]
            selected = list(settings["selectedDeviceIds"])
            if device_id in selected:
                return
            if len(selected) >= 128:
                raise TabletPreferencesViolation("energy device limit reached")
            selected.append(device_id)
            settings["selectedDeviceIds"] = selected
            settings["useAllDevices"] = False
            state["energy"] = _document(
                int(current["revision"]) + 1,
                self._timestamp(),
                validate_energy_settings(settings),
            )
            await self._store.async_save(state)
            self._state = state

    async def async_include_dashboard_device(self, device_id: str) -> None:
        if not _DEVICE_ID.fullmatch(device_id):
            raise TabletPreferencesViolation("dashboard device id is invalid")
        async with self._lock:
            state = deepcopy(self._required_state())
            current = deepcopy(self._document("tablet"))
            settings = current["settings"]
            visible = list(settings["dashboard"]["visibleDeviceIds"])
            if device_id in visible:
                return
            if len(visible) >= 128:
                raise TabletPreferencesViolation("dashboard device limit reached")
            visible.append(device_id)
            settings["dashboard"]["visibleDeviceIds"] = visible
            state["tablet"] = _document(
                int(current["revision"]) + 1,
                self._timestamp(),
                validate_tablet_settings(settings),
            )
            await self._store.async_save(state)
            self._state = state

    async def async_reset(self) -> None:
        timestamp = self._timestamp()
        state = {
            "tablet": _document(0, timestamp, default_tablet_settings()),
            "energy": _document(
                0, timestamp, energy_settings_from_legacy(HausmanHubSettings())
            ),
            "rooms": _room_document(0, timestamp, []),
        }
        async with self._lock:
            await self._store.async_save(state)
            self._state = state

    async def _replace(
        self, key: str, expected_revision: object, settings: dict[str, object]
    ) -> dict[str, object]:
        async with self._lock:
            current = self._document(key)
            self._validate_expected_revision(expected_revision, current)
            updated = _document(expected_revision + 1, self._timestamp(), settings)
            state = deepcopy(self._required_state())
            state[key] = updated
            await self._store.async_save(state)
            self._state = state
            return deepcopy(updated)

    @staticmethod
    def _validate_expected_revision(
        expected_revision: object, current: dict[str, object]
    ) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise TabletPreferencesViolation("expected revision is invalid")
        if expected_revision != current["revision"]:
            raise TabletPreferencesViolation("settings revision changed", stale=True)

    def _timestamp(self) -> str:
        value = self._now().astimezone(timezone.utc).isoformat(timespec="seconds")
        return value.replace("+00:00", "Z")

    def _required_state(self) -> dict[str, object]:
        if self._state is None:
            raise RuntimeError("tablet preferences service is not loaded")
        return self._state

    def _document(self, key: str) -> dict[str, object]:
        document = self._required_state().get(key)
        if not isinstance(document, dict):
            raise RuntimeError("tablet preferences document is unavailable")
        return document


def _document(revision: int, updated_at: str, settings: dict[str, object]) -> dict[str, object]:
    return {
        "contract": {
            "name": "hausman-hub-tablet-profile"
            if "startScreen" in settings
            else "hausman-hub-energy-settings",
            "version": 1,
        },
        "revision": revision,
        "updatedAt": updated_at,
        "settings": deepcopy(settings),
    }


def _room_document(
    revision: int, updated_at: str, rooms: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "contract": {"name": "hausman-hub-room-settings", "version": 1},
        "revision": revision,
        "updatedAt": updated_at,
        "rooms": deepcopy(rooms),
    }


def _validate_state(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"tablet", "energy", "rooms"}:
        raise TabletPreferencesViolation("stored tablet preferences are invalid")
    result = deepcopy(value)
    for key, validator, name in (
        ("tablet", validate_tablet_settings, "hausman-hub-tablet-profile"),
        ("energy", validate_energy_settings, "hausman-hub-energy-settings"),
    ):
        document = result[key]
        if not isinstance(document, dict) or set(document) != {
            "contract", "revision", "updatedAt", "settings"
        }:
            raise TabletPreferencesViolation(f"stored {key} document is invalid")
        contract = document["contract"]
        if contract != {"name": name, "version": 1}:
            raise TabletPreferencesViolation(f"stored {key} contract is invalid")
        _integer(document["revision"], 0, 9_007_199_254_740_991, f"{key} revision")
        if not isinstance(document["updatedAt"], str) or not document["updatedAt"]:
            raise TabletPreferencesViolation(f"stored {key} timestamp is invalid")
        document["settings"] = validator(document["settings"])
    rooms = result["rooms"]
    if not isinstance(rooms, dict) or set(rooms) != {
        "contract", "revision", "updatedAt", "rooms"
    }:
        raise TabletPreferencesViolation("stored room document is invalid")
    if rooms["contract"] != {"name": "hausman-hub-room-settings", "version": 1}:
        raise TabletPreferencesViolation("stored room contract is invalid")
    _integer(rooms["revision"], 0, 9_007_199_254_740_991, "rooms revision")
    if not isinstance(rooms["updatedAt"], str) or not rooms["updatedAt"]:
        raise TabletPreferencesViolation("stored room timestamp is invalid")
    rooms["rooms"] = validate_room_settings(rooms["rooms"])
    return result


def _object(value: object, fields: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise TabletPreferencesViolation(f"{label} fields are invalid")
    return value


def _boolean(value: object, label: str) -> None:
    if type(value) is not bool:
        raise TabletPreferencesViolation(f"{label} must be boolean")


def _integer(value: object, minimum: int, maximum: int, label: str) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TabletPreferencesViolation(f"{label} is out of range")


def _nullable_text(value: object, maximum: int, label: str) -> None:
    if value is not None and (
        not isinstance(value, str) or not value or len(value) > maximum
    ):
        raise TabletPreferencesViolation(f"{label} is invalid")


def _string_list(value: object, maximum: int, pattern: re.Pattern[str], label: str) -> None:
    if (
        not isinstance(value, list)
        or len(value) > maximum
        or len(value) != len(set(value))
        or any(not isinstance(item, str) or not pattern.fullmatch(item) for item in value)
    ):
        raise TabletPreferencesViolation(f"{label} are invalid")


def _plain_ids(value: object, maximum: int, label: str) -> None:
    if (
        not isinstance(value, list)
        or len(value) > maximum
        or len(value) != len(set(value))
        or any(not isinstance(item, str) or not item or len(item) > 128 for item in value)
    ):
        raise TabletPreferencesViolation(f"{label} are invalid")
