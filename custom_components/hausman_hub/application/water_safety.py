"""Durable fail-safe leak detection and water shutoff policy."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

WATER_SAFETY_CONTRACT_NAME = "hausman-hub-water-safety"
WATER_SAFETY_CONTRACT_VERSION = 1
_UNAVAILABLE = frozenset({"", "unknown", "unavailable"})
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
_NOTIFY_SERVICE = re.compile(r"^(notify|persistent_notification)\.[a-z0-9_]+$")
_LOGGER = logging.getLogger(__name__)


class WaterSafetyStore(Protocol):
    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


class WaterSafetyCommandGateway(Protocol):
    def has_service(self, target: str) -> bool: ...

    async def async_close(self, entity_id: str, close_action: str) -> None: ...

    async def async_notify(self, target: str, message: str) -> None: ...


def default_water_safety_configuration() -> dict[str, object]:
    """Return a disabled policy that cannot send a physical command."""

    return {
        "enabled": False,
        "sensorEntityIds": [],
        "actuators": [],
        "requiredActiveSensors": 1,
        "activationDebounceSeconds": 3,
        "clearDebounceSeconds": 30,
        "recipientServices": [],
        "directionVerified": False,
        "autoCloseEnabled": False,
    }


def validate_water_safety_configuration(value: object) -> dict[str, object]:
    """Normalize one strict policy and fail closed on unsafe combinations."""

    if not isinstance(value, Mapping):
        raise TypeError("water safety configuration must be an object")
    expected = {
        "enabled",
        "sensorEntityIds",
        "actuators",
        "requiredActiveSensors",
        "activationDebounceSeconds",
        "clearDebounceSeconds",
        "recipientServices",
        "directionVerified",
        "autoCloseEnabled",
    }
    if set(value) != expected:
        raise ValueError("water safety configuration fields are invalid")
    booleans = ("enabled", "directionVerified", "autoCloseEnabled")
    if any(type(value.get(field)) is not bool for field in booleans):
        raise ValueError("water safety flags must be boolean")

    sensors = _unique_strings(value.get("sensorEntityIds"), 32, "sensorEntityIds")
    if any(_ENTITY_ID.fullmatch(item) is None for item in sensors):
        raise ValueError("water safety sensor entityId is invalid")
    recipients = _unique_strings(
        value.get("recipientServices"), 16, "recipientServices"
    )
    if any(_NOTIFY_SERVICE.fullmatch(item) is None for item in recipients):
        raise ValueError("water safety recipient service is invalid")
    actuators_raw = value.get("actuators")
    if not isinstance(actuators_raw, list) or len(actuators_raw) > 8:
        raise ValueError("water safety actuators are invalid")
    actuators: list[dict[str, object]] = []
    seen_actuators: set[str] = set()
    for item in actuators_raw:
        actuator = _validate_actuator(item)
        entity_id = str(actuator["entityId"])
        if entity_id in seen_actuators:
            raise ValueError("water safety actuator ids must be unique")
        seen_actuators.add(entity_id)
        actuators.append(actuator)

    required = _bounded_int(value.get("requiredActiveSensors"), 1, 32)
    activation = _bounded_int(value.get("activationDebounceSeconds"), 1, 60)
    clear = _bounded_int(value.get("clearDebounceSeconds"), 1, 300)
    enabled = bool(value["enabled"])
    verified = bool(value["directionVerified"])
    auto_close = bool(value["autoCloseEnabled"])
    if required > max(1, len(sensors)):
        raise ValueError("requiredActiveSensors exceeds configured sensors")
    if auto_close and (
        not enabled or not sensors or not actuators or not recipients or not verified
    ):
        raise ValueError(
            "auto close requires enabled sensors, actuators, recipients and verified direction"
        )
    return {
        "enabled": enabled,
        "sensorEntityIds": sensors,
        "actuators": actuators,
        "requiredActiveSensors": required,
        "activationDebounceSeconds": activation,
        "clearDebounceSeconds": clear,
        "recipientServices": recipients,
        "directionVerified": verified,
        "autoCloseEnabled": auto_close,
    }


def _validate_actuator(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "entityId",
        "closeAction",
        "openStates",
        "closedStates",
    }:
        raise ValueError("water safety actuator is invalid")
    entity_id = value.get("entityId")
    close_action = value.get("closeAction")
    if not isinstance(entity_id, str) or _ENTITY_ID.fullmatch(entity_id) is None:
        raise ValueError("water safety actuator entityId is invalid")
    if close_action not in {"turn_off", "turn_on", "close_valve"}:
        raise ValueError("water safety closeAction is invalid")
    open_states = _unique_strings(
        value.get("openStates"), 8, "openStates", item_maximum=64
    )
    closed_states = _unique_strings(
        value.get("closedStates"), 8, "closedStates", item_maximum=64
    )
    if not open_states or not closed_states or set(open_states) & set(closed_states):
        raise ValueError("water safety actuator states are invalid")
    return {
        "entityId": entity_id,
        "closeAction": close_action,
        "openStates": open_states,
        "closedStates": closed_states,
    }


def _unique_strings(
    value: object,
    maximum: int,
    field: str,
    *,
    item_maximum: int = 255,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} is invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > item_maximum:
            raise ValueError(f"{field} is invalid")
        if item in result:
            raise ValueError(f"{field} must be unique")
        result.append(item)
    return result


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("water safety integer is outside bounds")
    return value


class WaterSafetyService:
    """Observe configured sensors and issue only a verified close command."""

    def __init__(
        self,
        hass: Any,
        store: WaterSafetyStore,
        *,
        command_gateway: WaterSafetyCommandGateway | None = None,
        operation_journal: object | None = None,
        now_ms: Callable[[], int] | None = None,
        readback_window_seconds: float = 8.0,
        readback_interval_seconds: float = 0.25,
    ) -> None:
        self._hass = hass
        self._store = store
        self._command_gateway = command_gateway
        self._operation_journal = operation_journal
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._readback_window_seconds = readback_window_seconds
        self._readback_interval_seconds = readback_interval_seconds
        self._revision = 0
        self._updated_at = datetime.now(UTC).isoformat()
        self._configuration = default_water_safety_configuration()
        self._latched = False
        self._leak_detected_at: int | None = None
        self._qualifying = False
        self._command_status = "idle"
        self._remove_listener: Callable[[], None] | None = None
        self._activation_handle: asyncio.TimerHandle | None = None
        self._clear_handle: asyncio.TimerHandle | None = None
        self._lock = asyncio.Lock()

    @property
    def configuration(self) -> dict[str, object]:
        return _copy_configuration(self._configuration)

    async def async_load(self) -> None:
        payload = await self._store.async_load()
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            return
        try:
            revision = _bounded_int(payload.get("revision"), 0, 2**31 - 1)
            configuration = validate_water_safety_configuration(
                payload.get("configuration")
            )
        except (TypeError, ValueError):
            return
        self._revision = revision
        self._configuration = configuration
        updated_at = payload.get("updated_at")
        if isinstance(updated_at, str) and updated_at:
            self._updated_at = updated_at
        self._latched = payload.get("latched") is True
        self._qualifying = payload.get("qualifying") is True
        detected = payload.get("leak_detected_at")
        self._leak_detected_at = detected if type(detected) is int and detected >= 0 else None
        status = payload.get("command_status")
        if status in {"idle", "accepted", "confirmed", "failed", "blocked"}:
            self._command_status = str(status)

    def start(self) -> Callable[[], None]:
        bus = getattr(self._hass, "bus", None)
        if bus is not None and self._remove_listener is None:
            self._remove_listener = bus.async_listen("state_changed", self._state_changed)
        self._schedule_activation_if_needed()
        if self._latched and self._configuration["autoCloseEnabled"]:
            state = self.snapshot()["state"]
            assert isinstance(state, Mapping)
            if (
                state["valveState"] != "closed"
                or self._command_status != "confirmed"
            ):
                self._start_recovery_close()
        return self.close

    def close(self) -> None:
        for handle_name in ("_activation_handle", "_clear_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                handle.cancel()
                setattr(self, handle_name, None)
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    async def async_update(
        self, expected_revision: int, configuration: object
    ) -> dict[str, object]:
        normalized = validate_water_safety_configuration(configuration)
        self._validate_live_bindings(normalized)
        async with self._lock:
            if expected_revision != self._revision:
                raise RuntimeError("revision_conflict")
            self._configuration = normalized
            if not normalized["enabled"]:
                self._qualifying = False
            self._revision += 1
            self._updated_at = datetime.now(UTC).isoformat()
            await self._async_save()
        self._schedule_activation_if_needed()
        return self.snapshot()

    async def async_clear_latch(
        self, expected_revision: int, *, confirmation: bool
    ) -> dict[str, object]:
        if confirmation is not True:
            raise ValueError("explicit latch confirmation is required")
        async with self._lock:
            if not self._all_sensors_dry_and_available() or self._qualifying:
                raise ValueError("active leak sensors prevent latch reset")
            if expected_revision != self._revision:
                raise RuntimeError("revision_conflict")
            self._latched = False
            self._leak_detected_at = None
            self._command_status = "idle"
            self._revision += 1
            self._updated_at = datetime.now(UTC).isoformat()
            await self._async_save()
        await self._async_journal_operation(
            "water_safety_latch_clear",
            confirmed=True,
        )
        return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        active_count = self._active_sensor_count()
        actuators = [self._actuator_projection(item) for item in self._actuators()]
        readbacks = [str(item["readBack"]) for item in actuators]
        return {
            "contract": {
                "name": WATER_SAFETY_CONTRACT_NAME,
                "version": WATER_SAFETY_CONTRACT_VERSION,
            },
            "revision": self._revision,
            "updatedAt": self._updated_at,
            "configuration": self.configuration,
            "state": {
                "latched": self._latched,
                "leakDetectedAt": self._leak_detected_at,
                "activeSensorCount": active_count,
                "qualifying": self._qualifying,
                "automaticOpenAllowed": False,
                "valveState": _combined_readback(readbacks),
                "commandStatus": self._command_status,
                "actuators": actuators,
            },
        }

    def direction_test(self, entity_id: str) -> dict[str, object]:
        actuator = next(
            (item for item in self._actuators() if item["entityId"] == entity_id),
            None,
        )
        if actuator is None:
            raise KeyError(entity_id)
        projection = self._actuator_projection(actuator)
        return {
            "contract": {"name": "hausman-hub-water-direction-test", "version": 1},
            "entityId": entity_id,
            "commandSent": False,
            "readBack": projection["readBack"],
            "observedState": projection["observedState"],
            "observedAt": self._now_ms(),
            "safeToConfirm": projection["readBack"] in {"open", "closed"},
        }

    def command_guard(
        self, entity_id: str, action_id: str, automatic: bool
    ) -> str | None:
        actuator = next(
            (item for item in self._actuators() if item["entityId"] == entity_id),
            None,
        )
        if actuator is None:
            return None
        opening_action = {
            "turn_off": "turn_on",
            "turn_on": "turn_off",
            "close_valve": "open_valve",
        }[str(actuator["closeAction"])]
        if action_id != opening_action:
            return None
        if automatic:
            return "automatic_water_open_forbidden"
        if self._latched:
            return "water_leak_latched"
        if not self._safety_state_verified():
            return "water_safety_state_unverified"
        return None

    def alarm_projection(self, entity_id: str) -> dict[str, object] | None:
        if entity_id not in self._configuration["sensorEntityIds"]:
            return None
        state = self.snapshot()["state"]
        assert isinstance(state, Mapping)
        return {
            "occurredAt": self._leak_detected_at,
            "waterValveState": state["valveState"],
            "waterCommandStatus": state["commandStatus"],
        }

    def _state_changed(self, event: Any) -> None:
        data = getattr(event, "data", {})
        entity_id = data.get("entity_id") if isinstance(data, Mapping) else None
        if entity_id not in self._configuration["sensorEntityIds"]:
            return
        self._schedule_activation_if_needed()

    def _schedule_activation_if_needed(self) -> None:
        if not self._configuration["enabled"]:
            return
        active_count = self._active_sensor_count()
        required = int(self._configuration["requiredActiveSensors"])
        loop = getattr(self._hass, "loop", None) or asyncio.get_running_loop()
        if active_count >= required:
            if self._clear_handle is not None:
                self._clear_handle.cancel()
                self._clear_handle = None
            if not self._latched and self._activation_handle is None:
                self._activation_handle = loop.call_later(
                    int(self._configuration["activationDebounceSeconds"]),
                    self._start_activation,
                )
        elif self._all_sensors_dry_and_available():
            if self._activation_handle is not None:
                self._activation_handle.cancel()
                self._activation_handle = None
            if self._clear_handle is None:
                self._clear_handle = loop.call_later(
                    int(self._configuration["clearDebounceSeconds"]),
                    self._finish_clear_debounce,
                )

    def _start_activation(self) -> None:
        self._activation_handle = None
        create_task = getattr(self._hass, "async_create_task", None)
        coroutine = self._async_activate()
        if callable(create_task):
            create_task(coroutine)
        else:
            asyncio.create_task(coroutine)

    def _start_recovery_close(self) -> None:
        create_task = getattr(self._hass, "async_create_task", None)
        coroutine = self._async_recover_latched_close()
        if callable(create_task):
            create_task(coroutine)
        else:
            asyncio.create_task(coroutine)

    def _finish_clear_debounce(self) -> None:
        self._clear_handle = None
        create_task = getattr(self._hass, "async_create_task", None)
        coroutine = self._async_finish_clear_debounce()
        if callable(create_task):
            create_task(coroutine)
        else:
            asyncio.create_task(coroutine)

    async def _async_finish_clear_debounce(self) -> None:
        if not self._all_sensors_dry_and_available():
            return
        if self._qualifying:
            self._qualifying = False
            await self._async_save()

    async def _async_activate(self) -> None:
        if not self._configuration["enabled"]:
            return
        active_count = self._active_sensor_count()
        if active_count < int(self._configuration["requiredActiveSensors"]):
            return
        self._qualifying = True
        if self._latched:
            await self._async_save()
            return
        self._latched = True
        self._leak_detected_at = self._now_ms()
        self._command_status = (
            "accepted" if self._configuration["autoCloseEnabled"] else "blocked"
        )
        self._revision += 1
        self._updated_at = datetime.now(UTC).isoformat()
        await self._async_save()
        if not self._configuration["autoCloseEnabled"]:
            await self._async_notify("Обнаружена протечка. Автоперекрытие отключено.")
            return
        result = await self._async_close_actuators()
        self._command_status = "confirmed" if result else "failed"
        await self._async_save()
        await self._async_notify(
            "Обнаружена протечка. Вода перекрыта и подтверждена."
            if result
            else "Обнаружена протечка. Не удалось подтвердить перекрытие воды."
        )
        await self._async_journal_close(result)

    async def _async_recover_latched_close(self) -> None:
        """Reconfirm a latched close after restart without ever opening water."""

        if not self._latched or not self._configuration["autoCloseEnabled"]:
            return
        self._command_status = "accepted"
        await self._async_save()
        result = await self._async_close_actuators()
        self._command_status = "confirmed" if result else "failed"
        await self._async_save()
        await self._async_notify(
            "После перезапуска закрытие воды повторно подтверждено."
            if result
            else "После перезапуска не удалось подтвердить закрытие воды."
        )
        await self._async_journal_close(result)

    async def _async_close_actuators(self) -> bool:
        all_confirmed = True
        for actuator in self._actuators():
            projection = self._actuator_projection(actuator)
            if projection["readBack"] == "closed":
                continue
            entity_id = str(actuator["entityId"])
            close_action = str(actuator["closeAction"])
            try:
                if self._command_gateway is None:
                    raise RuntimeError("water safety command gateway is unavailable")
                await self._command_gateway.async_close(entity_id, close_action)
                if not await self._async_wait_closed(actuator):
                    all_confirmed = False
            except Exception:  # noqa: BLE001 - each actuator remains isolated
                all_confirmed = False
        return all_confirmed and bool(self._actuators())

    async def _async_wait_closed(self, actuator: Mapping[str, object]) -> bool:
        deadline = time.monotonic() + self._readback_window_seconds
        while time.monotonic() <= deadline:
            if self._actuator_projection(actuator)["readBack"] == "closed":
                return True
            await asyncio.sleep(self._readback_interval_seconds)
        return False

    async def _async_notify(self, message: str) -> None:
        for target in self._configuration["recipientServices"]:
            try:
                if self._command_gateway is None:
                    raise RuntimeError("water safety command gateway is unavailable")
                await self._command_gateway.async_notify(str(target), message)
            except Exception:
                _LOGGER.warning("water safety recipient call failed", exc_info=True)
                continue

    async def _async_journal_close(self, confirmed: bool) -> None:
        await self._async_journal_operation(
            "water_safety_close",
            confirmed=confirmed,
            error_code=None if confirmed else "water_close_unconfirmed",
        )

    async def _async_journal_operation(
        self,
        operation: str,
        *,
        confirmed: bool,
        error_code: str | None = None,
    ) -> None:
        append = getattr(self._operation_journal, "async_append", None)
        if not callable(append):
            return
        correlation_id = f"water.{uuid.uuid4().hex}"
        try:
            await append(
                {
                    "correlation_id": correlation_id,
                    "operation": operation,
                    "accepted": True,
                    "confirmed": confirmed,
                    "status": "confirmed" if confirmed else "failed",
                    "reason": error_code,
                    "error_code": error_code,
                }
            )
        except Exception:
            _LOGGER.warning("water safety journal append failed", exc_info=True)

    def _active_sensor_count(self) -> int:
        return sum(state == "on" for state in self._sensor_states())

    def _sensor_states(self) -> list[str]:
        states = getattr(self._hass, "states", None)
        if states is None:
            return ["unavailable" for _ in self._configuration["sensorEntityIds"]]
        return [
            str(getattr(states.get(entity_id), "state", "unavailable")).lower()
            for entity_id in self._configuration["sensorEntityIds"]
        ]

    def _all_sensors_dry_and_available(self) -> bool:
        sensor_states = self._sensor_states()
        return bool(sensor_states) and all(state == "off" for state in sensor_states)

    def _safety_state_verified(self) -> bool:
        if not self._all_sensors_dry_and_available():
            return False
        return all(
            self._actuator_projection(actuator)["readBack"] in {"open", "closed"}
            for actuator in self._actuators()
        )

    def _actuators(self) -> list[Mapping[str, object]]:
        raw = self._configuration["actuators"]
        return list(raw) if isinstance(raw, list) else []

    def _actuator_projection(self, actuator: Mapping[str, object]) -> dict[str, object]:
        entity_id = str(actuator["entityId"])
        state = getattr(getattr(self._hass, "states", None), "get", lambda _: None)(
            entity_id
        )
        raw = str(getattr(state, "state", "")) if state is not None else ""
        normalized = raw.lower()
        if state is None or normalized in _UNAVAILABLE:
            read_back = "unavailable"
        elif normalized in {str(item).lower() for item in actuator["openStates"]}:
            read_back = "open"
        elif normalized in {str(item).lower() for item in actuator["closedStates"]}:
            read_back = "closed"
        else:
            read_back = "unknown"
        last_updated = getattr(state, "last_updated", None)
        return {
            "entityId": entity_id,
            "readBack": read_back,
            "observedState": raw or None,
            "observedAt": (
                int(last_updated.timestamp() * 1000)
                if isinstance(last_updated, datetime)
                else self._now_ms()
                if state is not None
                else None
            ),
        }

    def _validate_live_bindings(self, configuration: Mapping[str, object]) -> None:
        if not configuration["enabled"]:
            return
        states = getattr(self._hass, "states", None)
        for entity_id in configuration["sensorEntityIds"]:
            state = states.get(entity_id) if states is not None else None
            if (
                state is None
                or str(getattr(state, "state", "")).lower() in _UNAVAILABLE
            ):
                raise ValueError("configured water safety sensor is unavailable")
        for actuator in configuration["actuators"]:
            if states is None or states.get(actuator["entityId"]) is None:
                raise ValueError("configured water safety actuator is unavailable")
            if self._actuator_projection(actuator)["readBack"] not in {"open", "closed"}:
                raise ValueError("configured water safety actuator state is unknown")
        if configuration["recipientServices"] and self._command_gateway is None:
            raise ValueError("water safety command gateway is unavailable")
        if self._command_gateway is not None:
            for target in configuration["recipientServices"]:
                if not self._command_gateway.has_service(str(target)):
                    raise ValueError("configured water safety recipient is unavailable")

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "version": 1,
                "revision": self._revision,
                "updated_at": self._updated_at,
                "configuration": self.configuration,
                "latched": self._latched,
                "qualifying": self._qualifying,
                "leak_detected_at": self._leak_detected_at,
                "command_status": self._command_status,
            }
        )


def _copy_configuration(value: Mapping[str, object]) -> dict[str, object]:
    return {
        **value,
        "sensorEntityIds": list(value["sensorEntityIds"]),
        "recipientServices": list(value["recipientServices"]),
        "actuators": [
            {
                **item,
                "openStates": list(item["openStates"]),
                "closedStates": list(item["closedStates"]),
            }
            for item in value["actuators"]
        ],
    }


def _combined_readback(values: list[str]) -> str:
    if not values:
        return "unknown"
    if all(value == "closed" for value in values):
        return "closed"
    if all(value == "open" for value in values):
        return "open"
    if all(value == "unavailable" for value in values):
        return "unavailable"
    if any(value in {"open", "closed"} for value in values):
        return "mixed"
    return "unknown"
