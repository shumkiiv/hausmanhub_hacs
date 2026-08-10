"""Execute HausmanHub scenario definitions and return confirmed receipts."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioCatalog,
    ScenarioDefinition,
)
from ..domain.scenarios import (
    ScenarioComparison,
    ScenarioCondition,
    ScenarioConditionType,
    ScenarioExecutionMode,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_RUN_SCENARIO_DEPTH_LIMIT = 8
_DEFAULT_DEVICE_READBACK_WINDOW_SECONDS = 8.0
_DEFAULT_DEVICE_READBACK_INTERVAL_SECONDS = 0.25


def _value_parameter_name(action_id: str, domain: str, service: str) -> str | None:
    """Return the Home Assistant service-data key for an action value."""

    if domain == "light" and service == "turn_on" and action_id == "set_brightness":
        return "brightness"
    if domain == "cover" and service == "set_cover_position" and action_id == "set_position":
        return "position"
    if domain == "valve" and service == "set_valve_position" and action_id == "set_position":
        return "position"
    if domain == "climate":
        if service == "set_temperature" and action_id == "set_temperature":
            return "temperature"
        if service == "set_hvac_mode" and action_id == "set_hvac_mode":
            return "hvac_mode"
        if service == "set_fan_mode" and action_id == "set_fan_mode":
            return "fan_mode"
    if domain == "humidifier" and service == "set_humidity" and action_id == "set_humidity":
        return "humidity"
    if domain == "water_heater":
        if service == "set_temperature" and action_id == "set_temperature":
            return "temperature"
        if service == "set_operation_mode" and action_id == "set_operation_mode":
            return "operation_mode"
    return None


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
        if numeric < 0:
            numeric = 0
        if numeric > maximum:
            numeric = maximum
        return numeric
    if param == "temperature":
        if isinstance(value, str):
            value = value.strip()
            try:
                return float(value)
            except ValueError as error:
                raise ValueError("temperature must be a number") from error
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ValueError("temperature must be a number")
    if param in ("hvac_mode", "fan_mode", "operation_mode"):
        if not isinstance(value, str):
            raise ValueError(f"{param} must be a string")
        return value.strip()
    return value


_RUSSIAN_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def _now_local(hass: "HomeAssistant") -> Any:
    try:
        from homeassistant.util import dt as dt_util
    except ImportError:
        dt_util = None
    if dt_util is not None:
        return dt_util.now()
    import datetime
    return datetime.datetime.now(tz=datetime.timezone.utc)


def _evaluate_condition(
    condition: ScenarioCondition,
    catalog: ScenarioCatalog,
    hass: "HomeAssistant",
) -> tuple[bool, str | None]:
    if condition.type is ScenarioConditionType.DEVICE_STATE:
        device = catalog.device(condition.target_id) if condition.target_id else None
        if device is None:
            return (False, f"device {condition.target_id} is not available")
        states = getattr(hass, "states", None)
        if states is None:
            return (False, "home assistant states are not available")
        state = states.get(device.entity_id)
        if state is None:
            return (False, f"entity {device.entity_id} is not available")
        if condition.property == "state":
            actual = state.state
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


class ScenarioExecutor:
    """Run a scenario definition and collect confirmed action receipts."""

    def __init__(
        self,
        hass: HomeAssistant,
        catalog: ScenarioCatalog,
        run_callback: Callable[[str, frozenset[str] | None], Awaitable[dict[str, Any]]],
        *,
        notify_target: str = "",
        readback_window_seconds: float = _DEFAULT_DEVICE_READBACK_WINDOW_SECONDS,
        readback_interval_seconds: float = _DEFAULT_DEVICE_READBACK_INTERVAL_SECONDS,
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
    ) -> dict[str, Any]:
        """Execute one allowlisted device action and confirm its HA read-back."""

        request_id = self.new_run_id()
        execution_action_id = f"action_{request_id[:16]}"
        action = ScenarioAction(
            id=execution_action_id,
            type=ScenarioActionType.DEVICE_ACTION,
            target_id=target_id,
            action_id=action_id,
            value=value,
        )
        receipt = await self._device_action_receipt(
            action,
            {
                "action_id": execution_action_id,
                "type": "device_action",
                "status": "pending",
            },
        )
        if receipt.get("status") != "completed":
            return {
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
        read_back = receipt.get("read_back")
        if not isinstance(read_back, dict):
            confirmation_value = value
            allowed = device.action(action_id) if device is not None else None
            if value is not None and allowed is not None:
                param = _value_parameter_name(action_id, allowed.domain, allowed.service)
                if param is not None:
                    confirmation_value = _normalize_action_value(param, value)
            read_back = await self._read_back_device(
                getattr(device, "entity_id", None), action_id, confirmation_value
            )
        confirmed = read_back["matched"] is True
        observed_state = read_back["observedState"]

        return {
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
                "Устройство подтвердило новое состояние."
                if confirmed
                else "Команда принята; устройство ещё не подтвердило новое состояние."
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
                observed_state = str(getattr(state, "state", "unknown"))
                if _device_action_confirmed(state, action_id, value):
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
    ) -> dict[str, Any]:
        """Run every action sequentially and return confirmed receipts."""

        if visited_scenarios is None:
            visited_scenarios = frozenset()
        if scenario_id and scenario_id in visited_scenarios:
            return {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "status": "failed",
                "error": "recursive scenario call detected",
                "receipts": [],
            }
        if len(visited_scenarios) > _RUN_SCENARIO_DEPTH_LIMIT:
            return {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "status": "failed",
                "error": "scenario call depth limit exceeded",
                "receipts": [],
            }
        next_visited = visited_scenarios | ({scenario_id} if scenario_id else set())

        condition_results: list[dict[str, Any]] = []
        for condition in definition.conditions:
            passed, reason = _evaluate_condition(condition, self._catalog, self._hass)
            condition_results.append(
                {
                    "condition_id": condition.id,
                    "passed": passed,
                    "reason": reason,
                }
            )
            if not passed:
                return {
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "status": "skipped",
                    "execution_mode": definition.execution_mode.value,
                    "condition_results": condition_results,
                    "receipts": [],
                }

        receipts: list[dict[str, Any]] = []
        start_ms = int(time.time() * 1000)

        for action in definition.actions:
            receipt = await self._execute_action(
                action,
                run_id,
                next_visited,
                dry_run=dry_run,
                defer_device_readback=True,
            )
            receipts.append(receipt)
            if receipt.get("status") == "failed":
                break

        if not dry_run:
            await self._confirm_deferred_device_receipts(receipts)

        completed = all(r.get("status") == "completed" for r in receipts)
        confirmed = completed and all(
            r.get("type") != ScenarioActionType.DEVICE_ACTION
            or r.get("confirmed") is True
            for r in receipts
        )
        return {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "execution_mode": definition.execution_mode.value,
            "started_at_ms": start_ms,
            "finished_at_ms": int(time.time() * 1000),
            "condition_results": condition_results,
            "receipts": receipts,
            "accepted": completed,
            "confirmed": confirmed,
            "status": "completed" if completed else "failed",
        }

    async def _execute_action(
        self,
        action: ScenarioAction,
        run_id: str,
        visited: frozenset[str],
        *,
        dry_run: bool = False,
        defer_device_readback: bool = False,
    ) -> dict[str, Any]:
        base = {
            "action_id": action.id,
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
                )
            if action.type == ScenarioActionType.DELAY:
                if not dry_run:
                    await asyncio.sleep(action.delay_seconds)
                return {**base, "status": "completed", "delay_seconds": action.delay_seconds}
            if action.type == ScenarioActionType.RUN_SCENARIO:
                return await self._run_scenario_receipt(action, base, visited, dry_run=dry_run)
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
        service_data: dict[str, Any] = {"entity_id": device.entity_id}
        confirmation_value = action.value
        if action.value is not None:
            param = _value_parameter_name(action.action_id, allowed.domain, allowed.service)
            if param is None:
                return {
                    **base,
                    "status": "failed",
                    "error": f"action {action.action_id} does not accept a value",
                }
            try:
                normalized = _normalize_action_value(param, action.value)
            except ValueError as error:
                return {
                    **base,
                    "status": "failed",
                    "error": str(error),
                }
            service_data[param] = normalized
            confirmation_value = normalized
        if not dry_run:
            await self._call_service(allowed.domain, allowed.service, service_data)
        receipt: dict[str, Any] = {
            **base,
            "status": "completed",
            "target_id": action.target_id,
            "domain": allowed.domain,
            "service": allowed.service,
            "entity_id": device.entity_id,
        }
        if dry_run:
            receipt["service_data"] = service_data
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
        return receipt

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
                "status": "planned",
                "scenario_id": action.scenario_id,
            }
        result = await self._run_callback(action.scenario_id, visited=visited)
        return {
            **base,
            "status": result.get("status", "completed"),
            "scenario_id": action.scenario_id,
            "nested_run_id": result.get("run_id"),
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
        if not self._notify_target:
            return {
                **base,
                "status": "failed",
                "error": "notification target is not configured",
            }
        domain, service = self._notify_target.split(".", 1)
        if not dry_run:
            await self._call_service(domain, service, {"message": action.message})
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
        await call(domain, service, service_data, blocking=True)

    async def async_release_intercom_switch(self, entity_id: str) -> None:
        """Return the intercom relay to off after the hold window."""

        await self._call_service("switch", "turn_off", {"entity_id": entity_id})


def _device_action_confirmed(state: object, action_id: str, value: object | None) -> bool:
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
    if action_id == "open_valve":
        return state_value in {"open", "opening"}
    if action_id == "close_valve":
        return state_value in {"closed", "closing"}
    expected_attribute = {
        "set_brightness": "brightness",
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
    if isinstance(actual, (int, float)) and isinstance(value, (int, float)):
        return abs(float(actual) - float(value)) <= 0.1
    return str(actual) == str(value)
