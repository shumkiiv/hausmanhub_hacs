"""Execute HausmanHub scenario definitions and return confirmed receipts."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from datetime import time as datetime_time
from typing import TYPE_CHECKING, Any

from ..domain.device_power_dependencies import (
    AUTO_TURN_ON_POLICY,
    DevicePowerDependency,
    effective_device_state,
)
from ..domain.scenarios import (
    ScenarioCommandMode,
    ScenarioComparison,
    ScenarioCondition,
    ScenarioConditionType,
    ScenarioExecutionBackend,
)
from .scenario_light_priority import (
    MANUAL_LIGHT_PRIORITY_REASON,
    LightAutomationPriority,
    skipped_light_receipt,
)
from .scenario_node_red import NodeRedBackendError, NodeRedScenarioBackend
from .scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioCatalog,
    ScenarioDefinition,
    adaptive_brightness_minimum,
    night_light_percent,
    rgb_hex,
)
from .vendor_resilience import VendorCircuitBreaker, VendorServiceUnavailable

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_DEFAULT_DEVICE_READBACK_WINDOW_SECONDS = 8.0
_DEFAULT_DEVICE_READBACK_INTERVAL_SECONDS = 0.25
_CRITICAL_ACTION_DOMAINS = frozenset({"lock", "valve"})
_UNAVAILABLE_EVIDENCE = frozenset({"unknown", "unavailable"})
_VENDOR_SERVICE_DOMAINS = frozenset({"media_player", "remote"})


def _display_device_name(raw: str) -> str:
    """Свернуть задвоения имён из реестра HA для человеческих сообщений.

    Каталог собирает имя как «<устройство> · <сущность>», а friendly_name
    Zigbee2MQTT часто уже повторяет имя устройства: «Люстра тамбур · Люстра
    тамбур Люстра тамбур». Для квитанций и ленты оставляем один экземпляр.
    """

    text = " ".join(raw.split())
    if " · " in text:
        left, right = text.split(" · ", 1)
        if right.startswith(left):
            text = right
    words = text.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        half = len(words) // 2
        if words[:half] == words[half:]:
            text = " ".join(words[:half])
    return text or raw


def _value_parameter_name(action_id: str, domain: str, service: str) -> str | None:
    """Return the Home Assistant service-data key for an action value."""

    if (
        domain == "light"
        and service == "turn_on"
        and action_id in {"set_brightness", "set_adaptive_brightness", "set_night_light"}
    ):
        return "brightness"
    if (
        domain == "light"
        and service == "turn_on"
        and action_id == "set_brightness_percent"
    ):
        return "brightness"
    if (
        domain == "light"
        and service == "turn_on"
        and action_id == "set_color_temperature"
    ):
        return "color_temp_kelvin"
    if domain == "light" and service == "turn_on" and action_id == "set_rgb_color":
        return "rgb_color"
    if (
        domain == "cover"
        and service == "set_cover_position"
        and action_id == "set_position"
    ):
        return "position"
    if (
        domain == "valve"
        and service == "set_valve_position"
        and action_id == "set_position"
    ):
        return "position"
    if domain == "climate":
        if service == "set_temperature" and action_id == "set_temperature":
            return "temperature"
        if service == "set_hvac_mode" and action_id == "set_hvac_mode":
            return "hvac_mode"
        if service == "set_fan_mode" and action_id == "set_fan_mode":
            return "fan_mode"
    if (
        domain == "humidifier"
        and service == "set_humidity"
        and action_id == "set_humidity"
    ):
        return "humidity"
    if domain == "number" and service == "set_value" and action_id == "set_value":
        return "value"
    if domain == "water_heater":
        if service == "set_temperature" and action_id == "set_temperature":
            return "temperature"
        if service == "set_operation_mode" and action_id == "set_operation_mode":
            return "operation_mode"
    return None


def _normalize_light_action_value(action_id: str, param: str, value: object) -> object:
    """Scale tablet light controls (percent/kelvin) to HA-native values."""

    if action_id == "set_brightness_percent":
        percent = _normalize_action_value("position", value)
        return _normalize_action_value("brightness", round(percent * 255 / 100))
    if action_id == "set_night_light":
        percent = night_light_percent(value)
        return round(percent * 255 / 100)
    if action_id == "set_rgb_color":
        return list(rgb_hex(value))
    return _normalize_action_value(param, value)


def _normalize_action_value(param: str, value: object) -> object:
    """Convert incoming string/number values to HA-compatible types."""

    if value is None:
        return None
    if param in ("brightness", "position", "humidity"):
        if isinstance(value, str):
            value = value.strip()
            if value.endswith("%"):
                value = value[:-1].strip()
            try:
                numeric = int(value)
            except ValueError as error:
                raise ValueError(f"{param} must be an integer") from error
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = int(value)
        else:
            raise ValueError(f"{param} must be an integer")
        maximum = 255 if param == "brightness" else 100
        numeric = max(numeric, 0)
        numeric = min(numeric, maximum)
        return numeric
    if param == "color_temp_kelvin":
        if isinstance(value, str):
            value = value.strip()
            try:
                kelvin = int(value)
            except ValueError as error:
                raise ValueError(f"{param} must be an integer") from error
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            kelvin = int(value)
        else:
            raise ValueError(f"{param} must be an integer")
        return min(max(kelvin, 1000), 10000)
    if param in ("temperature", "value"):
        if isinstance(value, str):
            value = value.strip()
            try:
                return float(value)
            except ValueError as error:
                raise ValueError(f"{param} must be a number") from error
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ValueError(f"{param} must be a number")
    if param in ("hvac_mode", "fan_mode", "operation_mode"):
        if not isinstance(value, str):
            raise ValueError(f"{param} must be a string")
        return value.strip()
    return value


_RUSSIAN_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def _now_local(hass: HomeAssistant) -> Any:
    try:
        from homeassistant.util import dt as dt_util
    except ImportError:
        dt_util = None
    if dt_util is not None:
        return dt_util.now()
    import datetime

    return datetime.datetime.now(tz=datetime.timezone.utc)


def _parse_solar_time(value: object, now: datetime) -> datetime:
    if not isinstance(value, str):
        raise ValueError("solar transition time is unavailable")  # noqa: TRY004
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("solar transition time is invalid") from error
    if parsed.tzinfo is None or now.tzinfo is None:
        raise ValueError("solar transition time must include a timezone")
    return parsed.astimezone(now.tzinfo)


def _solar_curve_brightness(
    now: datetime,
    sun_state: str,
    next_rising: object,
    next_setting: object,
    minimum_percent: float,
) -> int:
    """Resolve a symmetric sunset-to-midnight-to-sunrise brightness curve."""

    if sun_state == "above_horizon":
        return 255
    if sun_state != "below_horizon":
        raise ValueError("sun state is unavailable")

    rising = _parse_solar_time(next_rising, now)
    setting = _parse_solar_time(next_setting, now)
    previous_setting = setting - timedelta(days=1)
    if previous_setting.date() == now.date() and previous_setting <= now:
        boundary = datetime.combine(
            now.date() + timedelta(days=1), datetime_time.min, tzinfo=now.tzinfo
        )
        duration = (boundary - previous_setting).total_seconds()
        elapsed = (now - previous_setting).total_seconds()
        progress = elapsed / duration if duration > 0 else 1.0
        percentage = 100 - (100 - minimum_percent) * progress
    else:
        boundary = datetime.combine(now.date(), datetime_time.min, tzinfo=now.tzinfo)
        duration = (rising - boundary).total_seconds()
        elapsed = (now - boundary).total_seconds()
        progress = elapsed / duration if duration > 0 else 0.0
        percentage = minimum_percent + (100 - minimum_percent) * progress
    percentage = min(100.0, max(minimum_percent, percentage))
    return round(255 * percentage / 100)


def _adaptive_brightness(hass: HomeAssistant, value: object) -> tuple[int, float]:
    minimum_percent = adaptive_brightness_minimum(value)
    states = getattr(hass, "states", None)
    sun = states.get("sun.sun") if states is not None else None
    if sun is None:
        raise ValueError("sun state is unavailable")
    attributes = getattr(sun, "attributes", {})
    if not isinstance(attributes, Mapping):
        attributes = {}
    brightness = _solar_curve_brightness(
        _now_local(hass),
        str(getattr(sun, "state", "unknown")),
        attributes.get("next_rising"),
        attributes.get("next_setting"),
        minimum_percent,
    )
    return brightness, minimum_percent


def _evaluate_condition(
    condition: ScenarioCondition,
    catalog: ScenarioCatalog,
    hass: HomeAssistant,
    power_dependencies: Mapping[str, DevicePowerDependency] | None = None,
    state_snapshot: Mapping[str, object | None] | None = None,
) -> tuple[bool, str | None]:
    if condition.type is ScenarioConditionType.DEVICE_STATE:
        device = catalog.device(condition.target_id) if condition.target_id else None
        if device is None:
            return (False, f"device {condition.target_id} is not available")
        live_states = getattr(hass, "states", None)
        if state_snapshot is None and live_states is None:
            return (False, "home assistant states are not available")
        get_state = (
            state_snapshot.get if state_snapshot is not None else live_states.get
        )
        state = get_state(device.entity_id)
        if state is None:
            return (False, f"entity {device.entity_id} is not available")
        dependencies = power_dependencies or {}
        effective_state, dependency_status = effective_device_state(
            device.entity_id,
            dependencies,
            lambda entity_id: (
                str(getattr(current, "state", "unknown"))
                if (current := get_state(entity_id)) is not None
                else None
            ),
        )
        if condition.property in {"state", "Состояние"}:
            actual = effective_state
        elif dependency_status is not None and dependency_status.blocks_commands:
            return (False, dependency_status.reason)
        else:
            actual = getattr(state, "attributes", {}).get(condition.property)
        expected = condition.value
        comparison = condition.comparison
        if comparison is ScenarioComparison.EQUALS:
            passed = str(actual) == str(expected)
        elif comparison is ScenarioComparison.NOT_EQUALS:
            passed = str(actual) != str(expected)
        elif comparison in (ScenarioComparison.ABOVE, ScenarioComparison.BELOW):
            try:
                actual_num = float(actual)
                expected_num = float(expected)
            except (TypeError, ValueError):
                return (False, "condition value is not numeric")
            passed = (
                actual_num > expected_num
                if comparison is ScenarioComparison.ABOVE
                else actual_num < expected_num
            )
        else:
            return (False, "unsupported condition comparison")
        return (passed, None)
    if condition.type is ScenarioConditionType.TIME_WINDOW:
        value = condition.value
        if not isinstance(value, str):
            return (False, "time_window value must be a string")
        parts = value.split("-")
        if len(parts) != 2:
            return (False, "time_window must use HH:mm-HH:mm")
        now = _now_local(hass)
        minutes = now.hour * 60 + now.minute
        try:
            start_h, start_m = map(int, parts[0].split(":"))
            end_h, end_m = map(int, parts[1].split(":"))
        except ValueError:
            return (False, "time_window boundaries must use HH:mm")
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        if start <= end:
            passed = start <= minutes <= end
        else:
            passed = minutes >= start or minutes <= end
        return (passed, None)
    if condition.type is ScenarioConditionType.WEEKDAY:
        value = condition.value
        if not isinstance(value, str):
            return (False, "weekday value must be a string")
        now = _now_local(hass)
        weekday = _RUSSIAN_WEEKDAYS[now.weekday()]
        passed = weekday in [item.strip() for item in value.split(",")]
        return (passed, None)
    if condition.type is ScenarioConditionType.PRESENCE:
        return (False, "presence source is not configured")
    return (False, "unsupported condition type")


def _condition_evidence_snapshot(
    definition: ScenarioDefinition,
    catalog: ScenarioCatalog,
    hass: HomeAssistant,
    power_dependencies: Mapping[str, DevicePowerDependency],
) -> tuple[dict[str, object | None], str | None, str | None]:
    """Capture every device condition without yielding to another HA event."""

    states = getattr(hass, "states", None)
    if states is None:
        return ({}, None, "home assistant states are not available")
    entity_ids: set[str] = set()
    condition_entities: list[tuple[ScenarioCondition, str]] = []
    for condition in definition.conditions:
        if condition.type is not ScenarioConditionType.DEVICE_STATE:
            continue
        device = catalog.device(condition.target_id) if condition.target_id else None
        if device is None:
            return ({}, None, f"device {condition.target_id} is not available")
        entity_ids.add(device.entity_id)
        condition_entities.append((condition, device.entity_id))
        dependency = power_dependencies.get(device.entity_id)
        if dependency:
            entity_ids.add(dependency.power_source_entity_id)
    snapshot = {entity_id: states.get(entity_id) for entity_id in entity_ids}
    for state in snapshot.values():
        raw_state = getattr(state, "state", None) if state is not None else None
        if raw_state is None or str(raw_state).lower() in _UNAVAILABLE_EVIDENCE:
            return (snapshot, None, "stale critical evidence")
    revision_parts: list[str] = []
    for condition, entity_id in condition_entities:
        state = snapshot.get(entity_id)
        if state is None:
            return (snapshot, None, f"entity {entity_id} is not available")
        attributes = getattr(state, "attributes", {})
        actual = (
            getattr(state, "state", None)
            if condition.property in {"state", "Состояние"}
            else attributes.get(condition.property)
            if isinstance(attributes, Mapping)
            else None
        )
        if actual is None or str(actual).lower() in _UNAVAILABLE_EVIDENCE:
            return (snapshot, None, "stale critical evidence")
        last_updated = getattr(state, "last_updated", None)
        revision_parts.extend(
            (
                condition.id,
                entity_id,
                str(condition.property),
                str(actual),
                last_updated.isoformat() if isinstance(last_updated, datetime) else "",
            )
        )
    for entity_id in sorted(entity_ids):
        state = snapshot.get(entity_id)
        revision_parts.extend((entity_id, str(getattr(state, "state", "missing"))))
    if not revision_parts:
        return (snapshot, None, None)
    revision = hashlib.sha256("\x1f".join(revision_parts).encode()).hexdigest()[:32]
    return (snapshot, revision, None)


class ScenarioExecutor:
    """Run a scenario definition and collect confirmed action receipts."""

    def __init__(
        self,
        hass: HomeAssistant,
        catalog: ScenarioCatalog,
        run_callback: Callable[..., Awaitable[dict[str, Any]]],
        *,
        notify_target: str = "",
        readback_window_seconds: float = _DEFAULT_DEVICE_READBACK_WINDOW_SECONDS,
        readback_interval_seconds: float = _DEFAULT_DEVICE_READBACK_INTERVAL_SECONDS,
        power_dependency_resolver: (
            Callable[[], Mapping[str, DevicePowerDependency]] | None
        ) = None,
        command_guard: Callable[[str, str, bool], str | None] | None = None,
        vendor_resilience: VendorCircuitBreaker | None = None,
        node_red_backend: NodeRedScenarioBackend | None = None,
    ):
        if not 0.01 <= readback_window_seconds <= 30.0:
            raise ValueError("readback window must be between 0.01 and 30 seconds")
        if not 0.01 <= readback_interval_seconds <= readback_window_seconds:
            raise ValueError("readback interval must fit inside the readback window")
        self._hass = hass
        self._catalog = catalog
        self._run_callback = run_callback
        self._notify_target = notify_target
        self._readback_window_seconds = readback_window_seconds
        self._readback_interval_seconds = readback_interval_seconds
        self._power_dependency_resolver = power_dependency_resolver
        self._power_source_locks: dict[str, asyncio.Lock] = {}
        self._command_guard = command_guard
        self._vendor_resilience = vendor_resilience
        self._node_red_backend = node_red_backend
        self._light_priority = LightAutomationPriority()

    def new_run_id(self) -> str:
        """Generate a unique execution trace id."""
        return uuid.uuid4().hex

    def replace_catalog(self, catalog: ScenarioCatalog) -> None:
        """Use the latest HA entities for following validations and commands."""

        self._catalog = catalog

    async def async_execute_device_action(
        self,
        target_id: str,
        action_id: str,
        value: object | None = None,
        *,
        correlation_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute one allowlisted device action and confirm its HA read-back."""

        request_id = self.new_run_id()
        correlation_id = correlation_id or request_id
        execution_action_id = f"action_{request_id[:16]}"
        action = ScenarioAction(
            id=execution_action_id,
            type=ScenarioActionType.DEVICE_ACTION,
            target_id=target_id,
            action_id=action_id,
            value=value,
        )
        try:
            receipt = await self._device_action_receipt(
                action,
                {
                    "action_id": execution_action_id,
                    "type": "device_action",
                    "status": "pending",
                },
                dry_run=dry_run,
            )
        except VendorServiceUnavailable as error:
            receipt = {"status": "failed", "error": error.code}
        if receipt.get("status") != "completed":
            return {
                "correlationId": correlation_id,
                "requestId": request_id,
                "accepted": False,
                "confirmed": False,
                "status": "failed",
                "statusName": "Не выполнено",
                "targetId": target_id,
                "actionId": action_id,
                "message": "Команда не была отправлена устройству.",
                "confirmationWindowMs": self._confirmation_window_ms,
                "readBack": {
                    "attempted": False,
                    "matched": False,
                    "observedAt": None,
                    "observedState": None,
                    "attempts": 0,
                },
                "error": receipt.get("error", "device_action_failed"),
            }

        device = self._catalog.device(target_id)
        # Решение владельца 2026-08-20: в ленте активности и квитанциях должно
        # быть видно, о каком устройстве речь, а не безликое «Устройство».
        device_name = (
            _display_device_name(device.name)
            if device is not None and device.name
            else "Устройство"
        )
        if dry_run:
            return {
                "correlationId": correlation_id,
                "requestId": request_id,
                "accepted": True,
                "confirmed": False,
                "status": "accepted",
                "statusName": "Проверяется",
                "targetId": target_id,
                "actionId": action_id,
                "observedState": None,
                "appliedAt": None,
                "message": f"{device_name}: dry-run прошёл без отправки команды.",
                "confirmationWindowMs": self._confirmation_window_ms,
                "readBack": {
                    "attempted": False,
                    "matched": False,
                    "observedAt": None,
                    "observedState": None,
                    "attempts": 0,
                },
                "reason": "dry_run",
                "dryRun": True,
            }
        read_back = receipt.get("read_back")
        if not isinstance(read_back, dict):
            confirmation_value = value
            allowed = device.action(action_id) if device is not None else None
            if value is not None and allowed is not None:
                param = _value_parameter_name(
                    action_id, allowed.domain, allowed.service
                )
                if param is not None:
                    confirmation_value = _normalize_light_action_value(
                        action_id, param, value
                    )
            read_back = await self._read_back_device(
                getattr(device, "entity_id", None), action_id, confirmation_value
            )
        confirmed = read_back["matched"] is True
        observed_state = read_back["observedState"]
        receipt["confirmed"] = confirmed
        receipt["read_back"] = read_back
        self._light_priority.note_direct_action(
            target_id,
            action_id,
            receipt,
            self._catalog,
            self._hass,
        )

        return {
            "correlationId": correlation_id,
            "requestId": request_id,
            "accepted": True,
            "confirmed": confirmed,
            "status": "confirmed" if confirmed else "accepted",
            "statusName": "Выполнено" if confirmed else "Проверяется",
            "targetId": target_id,
            "actionId": action_id,
            "observedState": observed_state,
            "appliedAt": int(time.time() * 1000),
            "message": (
                f"{device_name}: новое состояние подтверждено."
                if confirmed
                else f"{device_name}: команда принята, состояние ещё не подтверждено."
            ),
            "confirmationWindowMs": self._confirmation_window_ms,
            "readBack": read_back,
            "reason": None if confirmed else "state_not_confirmed",
        }

    @property
    def _confirmation_window_ms(self) -> int:
        return int(self._readback_window_seconds * 1000)

    async def _read_back_device(
        self,
        entity_id: object,
        action_id: str,
        value: object | None,
    ) -> dict[str, object]:
        """Poll one bounded HA state window and return explicit evidence."""

        if not isinstance(entity_id, str) or not entity_id:
            return {
                "attempted": False,
                "matched": False,
                "observedAt": None,
                "observedState": None,
                "attempts": 0,
            }
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._readback_window_seconds
        attempts = 0
        observed_at: int | None = None
        observed_state: str | None = None
        matched = False
        while True:
            attempts += 1
            state = self._hass.states.get(entity_id)
            if state is not None:
                observed_at = int(time.time() * 1000)
                dependencies = (
                    self._power_dependency_resolver()
                    if self._power_dependency_resolver is not None
                    else {}
                )
                effective_state, dependency_status = effective_device_state(
                    entity_id,
                    dependencies,
                    lambda requested_entity_id: (
                        str(getattr(current, "state", "unknown"))
                        if (current := self._hass.states.get(requested_entity_id))
                        is not None
                        else None
                    ),
                )
                observed_state = effective_state
                if (
                    dependency_status is None or not dependency_status.blocks_commands
                ) and _device_action_confirmed(state, action_id, value):
                    matched = True
                    break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self._readback_interval_seconds, remaining))
        return {
            "attempted": True,
            "matched": matched,
            "observedAt": observed_at,
            "observedState": observed_state,
            "attempts": attempts,
        }

    async def async_execute(
        self,
        definition: ScenarioDefinition,
        run_id: str,
        *,
        scenario_id: str = "",
        visited_scenarios: frozenset[str] | None = None,
        dry_run: bool = False,
        trigger_context: Mapping[str, object] | None = None,
        scenario_title: str = "",
        scenario_description: str = "",
        scenario_action_description: str = "",
        scenario_icon: str = "",
    ) -> dict[str, Any]:
        """Run every action sequentially and return confirmed receipts."""

        dry_run = dry_run or definition.command_mode is ScenarioCommandMode.SHADOW
        command_mode = "shadow" if dry_run else definition.command_mode.value
        if visited_scenarios is None:
            visited_scenarios = frozenset()
        if scenario_id and scenario_id in visited_scenarios:
            return {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "execution_mode": definition.execution_mode.value,
                "command_mode": command_mode,
                "status": "failed",
                "error": "recursive scenario call detected",
                "receipts": [],
            }
        if len(visited_scenarios) > definition.safety_policy.nested_depth_limit:
            return {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "execution_mode": definition.execution_mode.value,
                "command_mode": command_mode,
                "status": "failed",
                "error": "scenario call depth limit exceeded",
                "receipts": [],
            }
        next_visited = visited_scenarios | ({scenario_id} if scenario_id else set())

        power_dependencies = (
            self._power_dependency_resolver()
            if self._power_dependency_resolver is not None
            else {}
        )
        evidence_snapshot, evidence_revision, evidence_error = (
            _condition_evidence_snapshot(
                definition,
                self._catalog,
                self._hass,
                power_dependencies,
            )
        )
        evidence_captured_at_ms = int(time.time() * 1000)
        if (
            evidence_error is not None
            and definition.safety_policy.stop_on_stale_evidence
        ):
            return {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "execution_mode": definition.execution_mode.value,
                "command_mode": command_mode,
                "status": "failed",
                "reason": "stale_critical_evidence",
                "error": evidence_error,
                "evidence_revision": evidence_revision,
                "condition_results": [],
                "receipts": [],
                "accepted": False,
                "confirmed": False,
            }

        condition_results: list[dict[str, Any]] = []
        for condition in definition.conditions:
            passed, reason = _evaluate_condition(
                condition,
                self._catalog,
                self._hass,
                power_dependencies,
                evidence_snapshot,
            )
            condition_results.append(
                {
                    "condition_id": condition.id,
                    "passed": passed,
                    "outcome": "passed" if passed else "skipped",
                    "reason": reason,
                }
            )
            if not passed:
                return {
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "status": "skipped",
                    "execution_mode": definition.execution_mode.value,
                    "command_mode": command_mode,
                    "condition_results": condition_results,
                    "evidence_revision": evidence_revision,
                    "receipts": [],
                }

        actions = definition.actions
        node_red_result: dict[str, object] | None = None
        if definition.execution_backend is ScenarioExecutionBackend.NODE_RED:
            if self._node_red_backend is None:
                return {
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "execution_mode": definition.execution_mode.value,
                    "execution_backend": definition.execution_backend.value,
                    "command_mode": command_mode,
                    "status": "failed",
                    "error": "Node-RED backend is unavailable",
                    "condition_results": condition_results,
                    "evidence_revision": evidence_revision,
                    "receipts": [],
                    "accepted": False,
                    "confirmed": False,
                }
            try:
                actions, node_red_result = await self._node_red_backend.async_plan(
                    scenario_id,
                    definition,
                    run_id,
                    self._catalog,
                    dry_run=dry_run,
                )
            except NodeRedBackendError as error:
                return {
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "execution_mode": definition.execution_mode.value,
                    "execution_backend": definition.execution_backend.value,
                    "command_mode": command_mode,
                    "status": "failed",
                    "error": str(error),
                    "condition_results": condition_results,
                    "evidence_revision": evidence_revision,
                    "receipts": [],
                    "accepted": False,
                    "confirmed": False,
                }
            if node_red_result["status"] != "completed":
                return {
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "execution_mode": definition.execution_mode.value,
                    "execution_backend": definition.execution_backend.value,
                    "command_mode": command_mode,
                    "status": node_red_result["status"],
                    "condition_results": condition_results,
                    "evidence_revision": evidence_revision,
                    "node_red": node_red_result,
                    "receipts": [],
                    "accepted": node_red_result["status"] == "skipped",
                    "confirmed": False,
                }

        scenario_text = " ".join(
            (
                scenario_id,
                scenario_title,
                scenario_description,
                scenario_action_description,
                scenario_icon,
            )
        ).casefold()
        light_priority = self._light_priority.plan(
            actions,
            self._catalog,
            self._hass,
            scenario_text=scenario_text,
            trigger_context=trigger_context,
            power_dependencies=power_dependencies,
        )
        receipts: list[dict[str, Any]] = []
        powered_sources: dict[str, float] = {}
        powered_target_ids: set[str] = set()
        start_ms = int(time.time() * 1000)

        if light_priority.applied and light_priority.only_lighting_effects:
            receipts = [
                skipped_light_receipt(
                    action,
                    self._catalog,
                    correlation_id=run_id,
                    dry_run=dry_run,
                )
                for action in actions
                if action.id in light_priority.light_action_ids
            ]
            return {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "execution_mode": definition.execution_mode.value,
                "execution_backend": definition.execution_backend.value,
                "command_mode": command_mode,
                "started_at_ms": start_ms,
                "finished_at_ms": int(time.time() * 1000),
                "condition_results": condition_results,
                "evidence_revision": evidence_revision,
                "node_red": node_red_result,
                "manual_light_priority": {
                    "applied": True,
                    "reason": MANUAL_LIGHT_PRIORITY_REASON,
                    "manual_target_ids": sorted(light_priority.manual_target_ids),
                },
                "receipts": receipts,
                "accepted": True,
                "confirmed": not dry_run,
                "status": "completed",
            }

        for action_index, action in enumerate(actions):
            if not dry_run and action.type is ScenarioActionType.DELAY:
                await self._confirm_deferred_device_receipts(receipts)
            if (
                action.id in light_priority.light_action_ids
                and action.target_id in light_priority.guarded_target_ids
            ):
                receipt = skipped_light_receipt(
                    action,
                    self._catalog,
                    correlation_id=run_id,
                    dry_run=dry_run,
                )
            else:
                receipt = await self._execute_action(
                    action,
                    run_id,
                    next_visited,
                    dry_run=dry_run,
                    defer_device_readback=True,
                    powered_sources=dict(powered_sources),
                    idempotent_actions=definition.safety_policy.idempotent_actions,
                    evidence_age_seconds=(
                        int(time.time() * 1000) - evidence_captured_at_ms
                    )
                    / 1000,
                    max_evidence_age_seconds=(
                        definition.safety_policy.max_evidence_age_seconds
                    ),
                    stop_on_stale_evidence=(
                        definition.safety_policy.stop_on_stale_evidence
                    ),
                    trigger_context=trigger_context,
                )
            receipts.append(receipt)
            if receipt.get("status") == "failed":
                if not dry_run and powered_target_ids:
                    cleanup_receipts = await self._async_safety_cleanup_actions(
                        actions[action_index + 1 :],
                        run_id,
                        next_visited,
                        powered_target_ids=powered_target_ids,
                        powered_sources=powered_sources,
                        idempotent_actions=(
                            definition.safety_policy.idempotent_actions
                        ),
                        max_evidence_age_seconds=(
                            definition.safety_policy.max_evidence_age_seconds
                        ),
                    )
                    receipts.extend(cleanup_receipts)
                break
            if action.type is ScenarioActionType.DEVICE_ACTION:
                device = self._catalog.device(action.target_id or "")
                entity_id = getattr(device, "entity_id", None)
                if (
                    action.action_id == "turn_on"
                    and action.target_id
                    and receipt.get("skipped") is not True
                ):
                    powered_target_ids.add(action.target_id)
                elif action.action_id == "turn_off" and action.target_id:
                    powered_target_ids.discard(action.target_id)
                if isinstance(entity_id, str):
                    if action.action_id == "turn_on":
                        powered_sources[entity_id] = asyncio.get_running_loop().time()
                    elif action.action_id == "turn_off":
                        powered_sources.pop(entity_id, None)

        if not dry_run:
            await self._confirm_deferred_device_receipts(receipts)

        source = trigger_context.get("source") if trigger_context else "automatic"
        self._light_priority.note_results(
            actions,
            receipts,
            light_priority,
            self._catalog,
            self._hass,
            automatic=source != "manual",
            dry_run=dry_run,
        )

        completed = all(r.get("status") == "completed" for r in receipts)
        failed_after_progress = any(
            receipt.get("status") == "failed" for receipt in receipts
        ) and any(receipt.get("status") == "completed" for receipt in receipts)
        confirmed = (
            not dry_run
            and completed
            and all(
                r.get("type") != ScenarioActionType.DEVICE_ACTION
                or r.get("confirmed") is True
                for r in receipts
            )
        )
        return {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "execution_mode": definition.execution_mode.value,
            "execution_backend": definition.execution_backend.value,
            "command_mode": command_mode,
            "started_at_ms": start_ms,
            "finished_at_ms": int(time.time() * 1000),
            "condition_results": condition_results,
            "evidence_revision": evidence_revision,
            "node_red": node_red_result,
            "manual_light_priority": {
                "applied": light_priority.applied,
                "reason": (
                    MANUAL_LIGHT_PRIORITY_REASON if light_priority.applied else None
                ),
                "manual_target_ids": sorted(light_priority.manual_target_ids),
            },
            "receipts": receipts,
            "accepted": completed,
            "confirmed": confirmed,
            "status": "completed"
            if completed
            else "partial"
            if failed_after_progress
            else "failed",
        }

    async def _async_safety_cleanup_actions(
        self,
        remaining_actions: tuple[ScenarioAction, ...],
        run_id: str,
        visited: frozenset[str],
        *,
        powered_target_ids: set[str],
        powered_sources: dict[str, float],
        idempotent_actions: bool,
        max_evidence_age_seconds: int,
    ) -> list[dict[str, Any]]:
        """Run planned turn-off actions after a later action fails.

        A failed scenario must not leave a device powered solely because its
        matching planned turn-off action was still ahead in the sequence.
        Only targets switched on by this run are eligible, and every target is
        cleaned up at most once.
        """

        cleanup_receipts: list[dict[str, Any]] = []
        cleaned_target_ids: set[str] = set()
        for action in remaining_actions:
            if (
                action.type is not ScenarioActionType.DEVICE_ACTION
                or action.action_id != "turn_off"
                or not action.target_id
                or action.target_id not in powered_target_ids
                or action.target_id in cleaned_target_ids
            ):
                continue
            receipt = await self._execute_action(
                action,
                run_id,
                visited,
                defer_device_readback=False,
                powered_sources=dict(powered_sources),
                idempotent_actions=idempotent_actions,
                evidence_age_seconds=0.0,
                max_evidence_age_seconds=max_evidence_age_seconds,
                stop_on_stale_evidence=False,
            )
            receipt["safety_cleanup"] = True
            cleanup_receipts.append(receipt)
            cleaned_target_ids.add(action.target_id)
            if receipt.get("status") == "completed":
                powered_target_ids.discard(action.target_id)
                device = self._catalog.device(action.target_id)
                entity_id = getattr(device, "entity_id", None)
                if isinstance(entity_id, str):
                    powered_sources.pop(entity_id, None)
        return cleanup_receipts

    async def _execute_action(
        self,
        action: ScenarioAction,
        run_id: str,
        visited: frozenset[str],
        *,
        dry_run: bool = False,
        defer_device_readback: bool = False,
        powered_sources: Mapping[str, float] | None = None,
        idempotent_actions: bool = False,
        evidence_age_seconds: float = 0.0,
        max_evidence_age_seconds: int = 300,
        stop_on_stale_evidence: bool = True,
        trigger_context: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        base = {
            "action_id": action.id,
            "correlation_id": run_id,
            "type": action.type,
            "status": "pending",
        }

        try:
            if action.type == ScenarioActionType.DEVICE_ACTION:
                return await self._device_action_receipt(
                    action,
                    base,
                    dry_run=dry_run,
                    defer_readback=defer_device_readback,
                    powered_sources=powered_sources or {},
                    idempotent_actions=idempotent_actions,
                    evidence_age_seconds=evidence_age_seconds,
                    max_evidence_age_seconds=max_evidence_age_seconds,
                    stop_on_stale_evidence=stop_on_stale_evidence,
                    automatic=True,
                )
            if action.type == ScenarioActionType.DELAY:
                if not dry_run:
                    await asyncio.sleep(action.delay_seconds)
                return {
                    **base,
                    "status": "completed",
                    "delay_seconds": action.delay_seconds,
                }
            if action.type == ScenarioActionType.RUN_SCENARIO:
                return await self._run_scenario_receipt(
                    action,
                    base,
                    visited,
                    dry_run=dry_run,
                    trigger_context=trigger_context,
                )
            if action.type == ScenarioActionType.NOTIFICATION:
                return await self._notification_receipt(action, base, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            return {**base, "status": "failed", "error": str(exc)}

        return {**base, "status": "failed", "error": "unknown action type"}

    async def _device_action_receipt(
        self,
        action: ScenarioAction,
        base: dict[str, Any],
        *,
        dry_run: bool = False,
        defer_readback: bool = False,
        powered_sources: Mapping[str, float] | None = None,
        idempotent_actions: bool = False,
        evidence_age_seconds: float = 0.0,
        max_evidence_age_seconds: int = 300,
        stop_on_stale_evidence: bool = True,
        automatic: bool = False,
    ) -> dict[str, Any]:
        if action.target_id is None or action.action_id is None:
            return {
                **base,
                "status": "failed",
                "error": "device_action needs targetId and actionId",
            }
        device = self._catalog.device(action.target_id)
        if device is None:
            return {
                **base,
                "status": "failed",
                "error": f"device {action.target_id} is not available",
            }
        allowed = device.action(action.action_id)
        if allowed is None:
            return {
                **base,
                "status": "failed",
                "error": f"action {action.action_id} is not available for device {action.target_id}",
            }
        if self._command_guard is not None:
            guard_error = self._command_guard(
                device.entity_id,
                action.action_id,
                automatic,
            )
            if guard_error is not None:
                return {
                    **base,
                    "status": "failed",
                    "error": guard_error,
                }
        dependency_error = self._power_dependency_error(
            device.entity_id,
            powered_sources=powered_sources or {},
        )
        if dependency_error is not None:
            if (
                action.action_id == "turn_off"
                and dependency_error == "power_source_off"
            ):
                receipt = {
                    **base,
                    "status": "completed",
                    "target_id": action.target_id,
                    "domain": allowed.domain,
                    "service": allowed.service,
                    "entity_id": device.entity_id,
                    "skipped": True,
                    "reason": "already_effectively_off",
                }
                if dry_run:
                    receipt["planned"] = True
                    receipt["confirmed"] = None
                else:
                    receipt["confirmed"] = True
                    receipt["read_back"] = {
                        "attempted": False,
                        "matched": True,
                        "observedAt": int(time.time() * 1000),
                        "observedState": "off",
                        "attempts": 0,
                    }
                return receipt
            return {
                **base,
                "status": "failed",
                "error": dependency_error,
            }
        if (
            stop_on_stale_evidence
            and allowed.domain in _CRITICAL_ACTION_DOMAINS
            and evidence_age_seconds > max_evidence_age_seconds
        ):
            return {
                **base,
                "status": "failed",
                "error": "stale_critical_evidence",
            }
        service_data: dict[str, Any] = {"entity_id": device.entity_id}
        confirmation_value = action.value
        adaptive_minimum: float | None = None
        if allowed.domain == "number" and action.value is None:
            return {
                **base,
                "status": "failed",
                "error": "value is required for a numeric control",
            }
        if action.value is not None:
            param = _value_parameter_name(
                action.action_id, allowed.domain, allowed.service
            )
            if param is None:
                return {
                    **base,
                    "status": "failed",
                    "error": f"action {action.action_id} does not accept a value",
                }
            try:
                if action.action_id == "set_adaptive_brightness":
                    normalized, adaptive_minimum = _adaptive_brightness(
                        self._hass, action.value
                    )
                else:
                    normalized = _normalize_light_action_value(
                        action.action_id, param, action.value
                    )
            except ValueError as error:
                return {
                    **base,
                    "status": "failed",
                    "error": str(error),
                }
            if allowed.domain == "number":
                error = _number_range_error(device, normalized)
                if error is not None:
                    return {**base, "status": "failed", "error": error}
            service_data[param] = normalized
            confirmation_value = normalized
        power_error, power_precondition, _prepared_sources = (
            await self._prepare_power_dependency(
                device.entity_id,
                powered_sources=powered_sources or {},
                dry_run=dry_run,
            )
        )
        if power_error is not None:
            return {
                **base,
                "status": "failed",
                "error": power_error,
                **(
                    {"power_precondition": power_precondition}
                    if power_precondition is not None
                    else {}
                ),
            }
        source_was_turned_on = bool(
            power_precondition
            and power_precondition.get("sourceTurnedOn") is True
        )
        if idempotent_actions and not dry_run and not source_was_turned_on:
            current = self._hass.states.get(device.entity_id)
            if current is not None and _device_action_confirmed(
                current, action.action_id, confirmation_value
            ):
                observed_state = str(getattr(current, "state", "unknown"))
                return {
                    **base,
                    "status": "completed",
                    "target_id": action.target_id,
                    "domain": allowed.domain,
                    "service": allowed.service,
                    "entity_id": device.entity_id,
                    "confirmed": True,
                    "skipped": True,
                    "reason": "already_in_target_state",
                    **(
                        {"power_precondition": power_precondition}
                        if power_precondition is not None
                        else {}
                    ),
                    "read_back": {
                        "attempted": False,
                        "matched": True,
                        "observedAt": int(time.time() * 1000),
                        "observedState": observed_state,
                        "attempts": 0,
                    },
                }
        if not dry_run:
            await self._call_service(allowed.domain, allowed.service, service_data)
        receipt: dict[str, Any] = {
            **base,
            "status": "completed",
            "target_id": action.target_id,
            "domain": allowed.domain,
            "service": allowed.service,
            "entity_id": device.entity_id,
            **(
                {"power_precondition": power_precondition}
                if power_precondition is not None
                else {}
            ),
        }
        if dry_run:
            receipt["service_data"] = service_data
            receipt["planned"] = True
            receipt["confirmed"] = None
            receipt["reason"] = "shadow_plan"
        elif defer_readback:
            receipt["_readback_action_id"] = action.action_id
            receipt["_readback_value"] = confirmation_value
        else:
            read_back = await self._read_back_device(
                device.entity_id, action.action_id, confirmation_value
            )
            receipt["confirmed"] = read_back["matched"] is True
            receipt["read_back"] = read_back
            receipt["reason"] = (
                None if read_back["matched"] is True else "state_not_confirmed"
            )
        if adaptive_minimum is not None:
            receipt["adaptive_brightness"] = {
                "minimum_percent": adaptive_minimum,
                "resolved_percent": round(100 * confirmation_value / 255),
            }
        return receipt

    async def _prepare_power_dependency(
        self,
        entity_id: str,
        *,
        powered_sources: Mapping[str, float],
        dry_run: bool,
        visiting: frozenset[str] = frozenset(),
    ) -> tuple[str | None, dict[str, object] | None, frozenset[str]]:
        """Ensure an automatic upstream source is on before a device command."""

        if self._power_dependency_resolver is None:
            return None, None, frozenset()
        dependencies = self._power_dependency_resolver()
        dependency = dependencies.get(entity_id)
        if dependency is None:
            return None, None, frozenset()
        if entity_id in visiting:
            return "power_source_unavailable", None, frozenset()

        source_entity_id = dependency.power_source_entity_id
        upstream_error, _, upstream_sources = await self._prepare_power_dependency(
            source_entity_id,
            powered_sources=powered_sources,
            dry_run=dry_run,
            visiting=visiting | {entity_id},
        )
        precondition: dict[str, object] = {
            "sourceEntityId": source_entity_id,
            "policy": dependency.policy,
            "warmupSeconds": dependency.warmup_seconds,
            "sourceTurnedOn": False,
            "planned": dry_run,
        }
        if upstream_error is not None:
            return upstream_error, precondition, upstream_sources

        loop = asyncio.get_running_loop()
        activated_at = powered_sources.get(source_entity_id)
        if activated_at is not None:
            remaining = max(
                0.0,
                dependency.warmup_seconds - (loop.time() - activated_at),
            )
            if remaining > 0 and not dry_run:
                await asyncio.sleep(remaining)
            precondition["waitedSeconds"] = round(remaining, 3) if not dry_run else 0
            precondition["sourceTurnedOnByPreviousAction"] = True
            return None, precondition, upstream_sources | {source_entity_id}

        state = self._entity_state(source_entity_id)
        if state == "on":
            precondition["waitedSeconds"] = 0
            return None, precondition, upstream_sources
        if state in {None, "unknown", "unavailable"}:
            return "power_source_unavailable", precondition, upstream_sources
        if dependency.policy != AUTO_TURN_ON_POLICY:
            return "power_source_off", precondition, upstream_sources
        if dry_run:
            precondition["sourceTurnedOn"] = True
            precondition["waitedSeconds"] = 0
            return None, precondition, upstream_sources | {source_entity_id}

        lock = self._power_source_locks.setdefault(source_entity_id, asyncio.Lock())
        async with lock:
            state = self._entity_state(source_entity_id)
            source_turned_on = False
            if state != "on":
                if state in {None, "unknown", "unavailable"}:
                    return "power_source_unavailable", precondition, upstream_sources
                domain = source_entity_id.split(".", 1)[0]
                try:
                    await self._call_service(
                        domain,
                        "turn_on",
                        {"entity_id": source_entity_id},
                    )
                except Exception:  # source failure must not reach the target command
                    return "power_source_unavailable", precondition, upstream_sources
                source_turned_on = True
                if not await self._wait_for_entity_state(source_entity_id, "on"):
                    return "power_source_unavailable", precondition, upstream_sources
            wait_seconds = float(dependency.warmup_seconds) if source_turned_on else 0.0
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            precondition["sourceTurnedOn"] = source_turned_on
            precondition["waitedSeconds"] = wait_seconds
            return None, precondition, upstream_sources | {source_entity_id}

    def _entity_state(self, entity_id: str) -> str | None:
        states = getattr(self._hass, "states", None)
        state = states.get(entity_id) if states is not None else None
        return None if state is None else str(getattr(state, "state", "unknown"))

    async def _wait_for_entity_state(self, entity_id: str, expected: str) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._readback_window_seconds
        while True:
            if self._entity_state(entity_id) == expected:
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(self._readback_interval_seconds, remaining))

    def _power_dependency_error(
        self,
        entity_id: str,
        *,
        powered_sources: Mapping[str, float] | None = None,
    ) -> str | None:
        """Fail closed when an upstream source does not currently provide power."""

        if self._power_dependency_resolver is None:
            return None
        dependencies = self._power_dependency_resolver()
        if entity_id not in dependencies:
            return None

        def read_state(requested_entity_id: str) -> str | None:
            if requested_entity_id in (powered_sources or {}):
                return "on"
            states = getattr(self._hass, "states", None)
            state = states.get(requested_entity_id) if states is not None else None
            return None if state is None else str(getattr(state, "state", "unknown"))

        _, status = effective_device_state(entity_id, dependencies, read_state)
        if status is None or not status.blocks_commands:
            return None
        return status.reason

    async def _confirm_deferred_device_receipts(
        self, receipts: list[dict[str, Any]]
    ) -> None:
        """Confirm scenario device actions inside one shared wall-clock window."""

        pending: list[tuple[dict[str, Any], Awaitable[dict[str, object]]]] = []
        for receipt in receipts:
            action_id = receipt.pop("_readback_action_id", None)
            value = receipt.pop("_readback_value", None)
            if not isinstance(action_id, str):
                continue
            pending.append(
                (
                    receipt,
                    self._read_back_device(receipt.get("entity_id"), action_id, value),
                )
            )
        if not pending:
            return

        read_backs = await asyncio.gather(
            *(read_back for _, read_back in pending), return_exceptions=True
        )
        for (receipt, _), read_back in zip(pending, read_backs, strict=True):
            if isinstance(read_back, BaseException):
                receipt["confirmed"] = False
                receipt["read_back"] = {
                    "attempted": True,
                    "matched": False,
                    "observedAt": None,
                    "observedState": None,
                    "attempts": 0,
                }
                receipt["reason"] = "state_not_confirmed"
                continue
            confirmed = read_back["matched"] is True
            receipt["confirmed"] = confirmed
            receipt["read_back"] = read_back
            receipt["reason"] = None if confirmed else "state_not_confirmed"

    async def _run_scenario_receipt(
        self,
        action: ScenarioAction,
        base: dict[str, Any],
        visited: frozenset[str],
        *,
        dry_run: bool = False,
        trigger_context: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        if not action.scenario_id:
            return {
                **base,
                "status": "failed",
                "error": "run_scenario action needs scenarioId",
            }
        if dry_run:
            return {
                **base,
                "status": "completed",
                "planned": True,
                "scenario_id": action.scenario_id,
            }
        origin_target_id = (
            trigger_context.get("target_id")
            if isinstance(trigger_context, Mapping)
            else None
        )
        result = await self._run_callback(
            action.scenario_id,
            visited=visited,
            trigger_context={
                "source": "nested",
                "target_id": origin_target_id,
                "trigger_id": None,
                "recovery": False,
            },
        )
        nested_outcome = result.get("status", "failed")
        if nested_outcome == "completed":
            status = "completed"
            skipped = False
        elif nested_outcome == "skipped" or (
            nested_outcome == "cancelled"
            and result.get("reason") == "restarted_by_new_trigger"
        ):
            status = "completed"
            skipped = True
        else:
            status = "failed"
            skipped = False
        return {
            **base,
            "status": status,
            "scenario_id": action.scenario_id,
            "nested_run_id": result.get("run_id"),
            "nested_outcome": nested_outcome,
            "skipped": skipped,
            "reason": result.get("reason")
            or (
                f"nested_scenario_{nested_outcome}"
                if nested_outcome != "completed"
                else None
            ),
        }

    async def _notification_receipt(
        self, action: ScenarioAction, base: dict[str, Any], *, dry_run: bool = False
    ) -> dict[str, Any]:
        if not action.message:
            return {
                **base,
                "status": "failed",
                "error": "notification message is required",
            }
        if dry_run:
            return {
                **base,
                "status": "completed",
                "message": action.message,
                "planned": True,
                "confirmed": None,
                "reason": "shadow_plan",
            }
        if self._notify_target:
            domain, service = self._notify_target.split(".", 1)
            await self._call_service(
                domain,
                service,
                {
                    "message": action.message,
                    "data": {"correlation_id": base["correlation_id"]},
                },
            )
            return {**base, "status": "completed", "message": action.message}
        try:
            await self._call_service(
                "notify",
                "notify",
                {
                    "message": action.message,
                    "data": {"correlation_id": base["correlation_id"]},
                },
            )
        except Exception:  # noqa: BLE001 - bounded HA fallback, re-raised if it fails
            await self._call_service(
                "persistent_notification",
                "create",
                {
                    "title": "Hausman",
                    "message": action.message,
                    "notification_id": f"hausman-scenario-{base['correlation_id']}",
                },
            )
        return {**base, "status": "completed", "message": action.message}

    async def _call_service(
        self, domain: str, service: str, service_data: dict[str, Any]
    ) -> None:
        services = getattr(self._hass, "services", None)
        if services is None:
            raise RuntimeError("Home Assistant services are not available")
        call = getattr(services, "async_call", None)
        if call is None:
            raise RuntimeError("Home Assistant async_call is not available")
        operation = lambda: call(domain, service, service_data, blocking=True)
        if self._vendor_resilience is not None and domain in _VENDOR_SERVICE_DOMAINS:
            await self._vendor_resilience.async_execute(
                f"{domain}.{service}", operation
            )
            return
        await operation()

    async def async_release_intercom_switch(self, entity_id: str) -> bool:
        """Return the relay to off and confirm the physical HA read-back."""

        await self._call_service("switch", "turn_off", {"entity_id": entity_id})
        read_back = await self._read_back_device(entity_id, "turn_off", None)
        return read_back["matched"] is True


def _device_action_confirmed(
    state: object, action_id: str, value: object | None
) -> bool:
    """Compare one post-call state with the requested semantic action."""

    state_value = str(getattr(state, "state", "unknown"))
    attributes = getattr(state, "attributes", {})
    if action_id == "turn_on":
        return state_value not in {"off", "unknown", "unavailable"}
    if action_id == "turn_off":
        return state_value == "off"
    if action_id == "open_cover":
        return state_value in {"open", "opening"}
    if action_id == "close_cover":
        return state_value in {"closed", "closing"}
    if action_id == "lock":
        return state_value == "locked"
    if action_id == "unlock":
        return state_value == "unlocked"
    if action_id == "start":
        return state_value in {"cleaning", "on"}
    if action_id == "pause":
        return state_value in {"paused", "idle"}
    if action_id == "stop":
        return state_value in {"idle", "off", "docked"}
    if action_id == "return_home":
        return state_value in {"returning", "docked"}
    if action_id == "set_value":
        try:
            return abs(float(state_value) - float(value)) <= 1e-6
        except (TypeError, ValueError):
            return False
    if action_id == "open_valve":
        return state_value in {"open", "opening"}
    if action_id == "close_valve":
        return state_value in {"closed", "closing"}
    expected_attribute = {
        "set_brightness": "brightness",
        "set_adaptive_brightness": "brightness",
        "set_night_light": "brightness",
        "set_brightness_percent": "brightness",
        "set_color_temperature": "color_temp_kelvin",
        "set_rgb_color": "rgb_color",
        "set_position": "current_position",
        "set_temperature": "temperature",
        "set_hvac_mode": "hvac_mode",
        "set_fan_mode": "fan_mode",
        "set_humidity": "humidity",
        "set_operation_mode": "operation_mode",
    }.get(action_id)
    if expected_attribute is None:
        return False
    actual = attributes.get(expected_attribute)
    if action_id == "set_rgb_color":
        return isinstance(actual, (list, tuple)) and list(actual) == list(value or [])
    if isinstance(actual, (int, float)) and isinstance(value, (int, float)):
        # Кельвины гуляют на округление mireds (3000K -> 333 mired -> 3003K),
        # поэтому для температуры света допуск шире числового zero-tolerance.
        tolerance = 75.0 if action_id == "set_color_temperature" else 0.1
        return abs(float(actual) - float(value)) <= tolerance
    return str(actual) == str(value)


def _number_range_error(device: object, value: object) -> str | None:
    """Reject a number value outside the advertised HA bounds or step."""

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "value must be a number"
    minimum = getattr(device, "range_minimum", None)
    maximum = getattr(device, "range_maximum", None)
    step = getattr(device, "range_step", None)
    if not all(
        isinstance(item, (int, float)) and not isinstance(item, bool)
        for item in (minimum, maximum, step)
    ):
        return "number range is unavailable"
    numeric = float(value)
    if not all(
        math.isfinite(item)
        for item in (numeric, float(minimum), float(maximum), float(step))
    ):
        return "number range is unavailable"
    if float(minimum) >= float(maximum) or float(step) <= 0:
        return "number range is unavailable"
    if numeric < float(minimum) or numeric > float(maximum):
        return "value is outside the allowed range"
    steps = (numeric - float(minimum)) / float(step)
    if abs(steps - round(steps)) > 1e-6:
        return "value does not match the allowed step"
    return None
