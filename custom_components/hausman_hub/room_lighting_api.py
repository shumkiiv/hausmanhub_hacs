"""Authenticated tablet/admin HTTP views for room lighting.

Responses follow the room lighting contract schemas exactly. ``safe`` live
tests never call an executor; ``real`` tests require an explicitly injected
executor and otherwise fail with ``capability_unavailable``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import time as dt_time, timezone
from http import HTTPStatus
import inspect
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.room_lighting_live_test import (
    LIVE_TEST_DURATION_SECONDS,
    LIVE_TEST_MAX_RUNS,
    RoomLightingLiveTestRunner,
    cancelled_trace,
)
from .application.room_lighting_service import (
    DEFAULT_TEMPLATE_ID,
    ROOM_LIGHTING_TEMPLATES,
    RoomLightingService,
)
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_admin_request,
    _is_local_tablet_request,
    _not_found,
    _request_json,
)
from .domain.room_lighting import (
    RoomLightingConfig,
    RoomLightingViolation,
    SensorKind,
    config_from_payload,
)
from .domain.room_lighting_engine import (
    LightSnapshot,
    RoomLightingContext,
    SensorSnapshot,
    _active_schedule_entry,
    evaluate_room_lighting,
)
from .domain.room_lighting_ownership import (
    SensorState,
    has_proven_auto_ownership,
    resolve_manual_ownership,
)
from .error_taxonomy import api_error_payload, api_error_status

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

ROOM_LIGHTING_BASE = "/api/hausman_hub/v1/rooms/{room_id}/lighting"
ROOM_LIGHTING_CONFIG_PATH = f"{ROOM_LIGHTING_BASE}/config"
ROOM_LIGHTING_STATUS_PATH = f"{ROOM_LIGHTING_BASE}/status"
ROOM_LIGHTING_TEMPLATES_PATH = f"{ROOM_LIGHTING_BASE}/templates"
ROOM_LIGHTING_TEMPLATE_APPLY_PATH = f"{ROOM_LIGHTING_TEMPLATES_PATH}/apply"
ROOM_LIGHTING_LIVE_TESTS_PATH = f"{ROOM_LIGHTING_BASE}/live-tests"
ROOM_LIGHTING_LIVE_TEST_PATH = (
    f"{ROOM_LIGHTING_LIVE_TESTS_PATH}/{{correlation_id}}"
)

DATA_ROOM_LIGHTING_VIEWS = "room_lighting_views"
DATA_ROOM_LIGHTING_SERVICE = "room_lighting_service"
DATA_ROOM_LIGHTING_LIVE_TESTS = "room_lighting_live_tests"
DATA_ROOM_LIGHTING_EXECUTOR = "room_lighting_live_test_executor"
DATA_ROOM_LIGHTING_CONTEXT = "room_lighting_status_context_provider"
DATA_ROOM_LIGHTING_LIVE_CONTEXT = "room_lighting_live_test_context_provider"
DATA_ROOM_LIGHTING_RUNTIME = "room_lighting_runtime"

_ROOM_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_CONFIG_BODY_BYTES = 256 * 1024
_PUT_LOCK = asyncio.Lock()


def register_room_lighting_api(hass: HomeAssistant, entry_id: str) -> None:
    """Refresh the service on every setup and register fixed views once."""

    data = hass.data.setdefault(DOMAIN, {})
    if DATA_ROOM_LIGHTING_SERVICE not in data:
        from .application.room_lighting_storage import HomeAssistantRoomLightingStore

        data[DATA_ROOM_LIGHTING_SERVICE] = RoomLightingService(
            HomeAssistantRoomLightingStore(hass, entry_id)
        )
    data.setdefault(DATA_ROOM_LIGHTING_LIVE_TESTS, {})
    if DATA_ROOM_LIGHTING_VIEWS in data:
        return
    views = (
        RoomLightingConfigView(hass),
        RoomLightingStatusView(hass),
        RoomLightingTemplatesView(hass),
        RoomLightingTemplateApplyView(hass),
        RoomLightingLiveTestsView(hass),
        RoomLightingLiveTestView(hass),
    )
    for view in views:
        hass.http.register_view(view)
    data[DATA_ROOM_LIGHTING_VIEWS] = views


def clear_room_lighting_api(hass: HomeAssistant) -> None:
    """Drop the service so the next setup rebuilds it without re-registering views."""

    data = hass.data.get(DOMAIN)
    if data is None:
        return
    runtime = data.pop(DATA_ROOM_LIGHTING_RUNTIME, None)
    cancel = getattr(runtime, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:  # noqa: BLE001 - cleanup must be best effort
            _LOGGER.warning("room lighting runtime cleanup failed")
    data.pop(DATA_ROOM_LIGHTING_SERVICE, None)
    data.pop(DATA_ROOM_LIGHTING_LIVE_TESTS, None)


class _RoomLightingView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _authorized(self, request: Any) -> bool:
        return _is_local_admin_request(request) or _is_local_tablet_request(request)

    def _service(self) -> RoomLightingService | None:
        data = self._hass.data.get(DOMAIN)
        candidate = data.get(DATA_ROOM_LIGHTING_SERVICE) if data else None
        return candidate if isinstance(candidate, RoomLightingService) else None

    def _data(self) -> dict[str, object]:
        return self._hass.data.setdefault(DOMAIN, {})

    def _room_id(self, request: Any) -> str | None:
        room_id = getattr(request, "match_info", {}).get("room_id")
        if (
            not isinstance(room_id, str)
            or _ROOM_ID.fullmatch(room_id) is None
            or not _is_exact_request(request, self.url.format(room_id=room_id))
        ):
            return None
        return room_id

    def _error(
        self,
        code: str,
        *,
        details: Mapping[str, object] | None = None,
        message: str | None = None,
    ) -> Any:
        payload = api_error_payload(code, details=details)
        if message:
            payload["message"] = message[:500]
        return self.json(
            payload,
            status_code=api_error_status(code),
            headers=NO_STORE_HEADERS,
        )

    def _unavailable(self) -> Any:
        return self._error("unavailable")

    def _invalid(self, error: object) -> Any:
        return self._error(
            "invalid_request",
            message=f"Конфигурация освещения отклонена: {error}",
        )


class RoomLightingConfigView(_RoomLightingView):
    """Read and atomically replace one room lighting configuration."""

    url = ROOM_LIGHTING_CONFIG_PATH
    name = "api:hausman_hub:room_lighting_config"

    async def get(self, request: Any, room_id: str | None = None) -> Any:
        del room_id
        room_id = self._room_id(request)
        if room_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        config = await service.async_get_config(room_id)
        if config is None:
            return self._error("not_found")
        return self.json(config.to_dict(), headers=NO_STORE_HEADERS)

    async def put(self, request: Any, room_id: str | None = None) -> Any:
        del room_id
        room_id = self._room_id(request)
        if room_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=MAX_CONFIG_BODY_BYTES)
        except ValueError:
            return self._invalid("тело запроса должно быть JSON-объектом")
        if not isinstance(payload, Mapping):
            return self._invalid("тело запроса должно быть JSON-объектом")
        if payload.get("roomId") != room_id:
            return self._invalid("roomId не совпадает с путём")
        try:
            config = config_from_payload(payload)
        except RoomLightingViolation as error:
            return self._invalid(error)
        async with _PUT_LOCK:
            stored = await service.async_get_config(room_id)
            if stored is not None and payload.get("version") != stored.version:
                return self._error(
                    "revision_conflict",
                    details={
                        "expectedRevision": payload.get("version"),
                        "actualRevision": stored.version,
                    },
                )
            saved = await service.async_put_config(config)
        return self.json(saved.to_dict(), headers=NO_STORE_HEADERS)


class RoomLightingStatusView(_RoomLightingView):
    """Read the command-free shadow status of one room."""

    url = ROOM_LIGHTING_STATUS_PATH
    name = "api:hausman_hub:room_lighting_status"

    async def get(self, request: Any, room_id: str | None = None) -> Any:
        del room_id
        room_id = self._room_id(request)
        if room_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        config = await service.async_get_config(room_id)
        if config is None:
            return self._error("not_found")
        data = self._data()
        context = await _status_context(data, config)
        runtime = data.get(DATA_ROOM_LIGHTING_RUNTIME)
        fresh = runtime is not None and bool(getattr(runtime, "running", False))
        return self.json(
            _status_payload(
                config,
                context,
                fresh=fresh,
                phase=_status_phase(config, context) if fresh else "unknown",
            ),
            headers=NO_STORE_HEADERS,
        )


class RoomLightingTemplatesView(_RoomLightingView):
    """List the built-in room lighting templates as full documents."""

    url = ROOM_LIGHTING_TEMPLATES_PATH
    name = "api:hausman_hub:room_lighting_templates"

    async def get(self, request: Any, room_id: str | None = None) -> Any:
        del room_id
        room_id = self._room_id(request)
        if room_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        if self._service() is None:
            return self._unavailable()
        return self.json(
            {
                "contract": {
                    "name": "hausman-hub-room-lighting-template-catalog",
                    "version": 1,
                },
                "defaultTemplateId": DEFAULT_TEMPLATE_ID,
                "templates": [
                    ROOM_LIGHTING_TEMPLATES[template_id]
                    for template_id in sorted(ROOM_LIGHTING_TEMPLATES)
                ],
            },
            headers=NO_STORE_HEADERS,
        )


class RoomLightingTemplateApplyView(_RoomLightingView):
    """Fill a room lighting configuration from a ready template."""

    url = ROOM_LIGHTING_TEMPLATE_APPLY_PATH
    name = "api:hausman_hub:room_lighting_template_apply"

    async def post(self, request: Any, room_id: str | None = None) -> Any:
        del room_id
        room_id = self._room_id(request)
        if room_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=MAX_CONFIG_BODY_BYTES)
        except ValueError:
            return self._invalid("тело запроса должно быть JSON-объектом")
        if not isinstance(payload, Mapping):
            return self._invalid("тело запроса должно быть JSON-объектом")
        template_id = payload.get("templateId")
        if not isinstance(template_id, str):
            return self._invalid("templateId обязателен")
        keep_devices = payload.get("keepDevices", True)
        if type(keep_devices) is not bool:
            return self._invalid("keepDevices должен быть boolean")
        try:
            config = await service.async_apply_template(
                template_id,
                None,
                room_id=room_id,
                keep_devices=keep_devices,
            )
        except RoomLightingViolation as error:
            return self._invalid(error)
        return self.json(config.to_dict(), headers=NO_STORE_HEADERS)


class RoomLightingLiveTestsView(_RoomLightingView):
    """Start one bounded background live test for a room."""

    url = ROOM_LIGHTING_LIVE_TESTS_PATH
    name = "api:hausman_hub:room_lighting_live_tests"

    async def post(self, request: Any, room_id: str | None = None) -> Any:
        del room_id
        room_id = self._room_id(request)
        if room_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=MAX_CONFIG_BODY_BYTES)
        except ValueError:
            payload = {}
        if not isinstance(payload, Mapping):
            return self._invalid("тело запроса должно быть JSON-объектом")
        mode = payload.get("mode")
        if mode not in {"safe", "real"}:
            return self._invalid("mode обязателен и равен safe или real")
        correlation_id = payload.get("correlationId")
        if correlation_id is None:
            correlation_id = f"live.{int(time.time() * 1000)}"
        if not isinstance(correlation_id, str) or not _CORRELATION_ID.fullmatch(
            correlation_id
        ):
            return self._invalid("correlationId недопустим")
        config = await service.async_get_config(room_id)
        if config is None:
            return self._error("not_found")
        data = self._data()
        executor = data.get(DATA_ROOM_LIGHTING_EXECUTOR)
        if mode == "real" and executor is None:
            return self._error("capability_unavailable")
        context_factory = data.get(DATA_ROOM_LIGHTING_LIVE_CONTEXT)
        registry = data.setdefault(DATA_ROOM_LIGHTING_LIVE_TESTS, {})
        if not isinstance(registry, dict):
            return self._unavailable()
        cancel_event = asyncio.Event()
        started_at = int(time.time())
        entry: dict[str, object] = {
            "trace": None,
            "cancel_event": cancel_event,
            "mode": mode,
            "room_id": room_id,
            "started_at": started_at,
        }
        registry[correlation_id] = entry
        while len(registry) > LIVE_TEST_MAX_RUNS:
            registry.pop(next(iter(registry)), None)

        async def _run() -> None:
            runner = RoomLightingLiveTestRunner()
            try:
                trace = await runner.run(
                    config,
                    mode=mode,
                    correlation_id=correlation_id,
                    executor=executor,
                    cancel_event=cancel_event,
                    context_factory=(
                        context_factory if callable(context_factory) else None
                    ),
                )
            except Exception:  # noqa: BLE001 - background task must not crash the loop
                return
            entry["trace"] = trace

        entry["task"] = asyncio.ensure_future(_run())
        return self.json(
            _request_document(correlation_id, room_id, mode, started_at),
            status_code=HTTPStatus.ACCEPTED,
            headers=NO_STORE_HEADERS,
        )


class RoomLightingLiveTestView(_RoomLightingView):
    """Read or cancel one room lighting live test."""

    url = ROOM_LIGHTING_LIVE_TEST_PATH
    name = "api:hausman_hub:room_lighting_live_test"

    def _correlation_id(self, request: Any) -> str | None:
        match_info = getattr(request, "match_info", {})
        correlation_id = match_info.get("correlation_id")
        room_id = match_info.get("room_id")
        if (
            not isinstance(correlation_id, str)
            or not isinstance(room_id, str)
            or not _CORRELATION_ID.fullmatch(correlation_id)
            or not _is_exact_request(
                request,
                self.url.format(room_id=room_id, correlation_id=correlation_id),
            )
        ):
            return None
        return correlation_id

    def _entry(self, correlation_id: str) -> dict[str, object] | None:
        registry = self._data().get(DATA_ROOM_LIGHTING_LIVE_TESTS)
        if not isinstance(registry, dict):
            return None
        entry = registry.get(correlation_id)
        return entry if isinstance(entry, dict) else None

    async def get(self, request: Any, correlation_id: str | None = None) -> Any:
        del correlation_id
        correlation_id = self._correlation_id(request)
        if correlation_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        if self._service() is None:
            return self._unavailable()
        entry = self._entry(correlation_id)
        if entry is None:
            return self._error("not_found")
        trace = entry.get("trace")
        if trace is not None:
            return self.json(trace.to_payload(), headers=NO_STORE_HEADERS)  # type: ignore[attr-defined]
        return self.json(
            _request_document(
                correlation_id,
                str(entry.get("room_id", "")),
                str(entry.get("mode", "safe")),
                int(entry.get("started_at", 0)),
            ),
            headers=NO_STORE_HEADERS,
        )

    async def post(self, request: Any, correlation_id: str | None = None) -> Any:
        del correlation_id
        correlation_id = self._correlation_id(request)
        if correlation_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        if self._service() is None:
            return self._unavailable()
        entry = self._entry(correlation_id)
        if entry is None:
            return self._error("not_found")
        event = entry.get("cancel_event")
        if event is not None:
            event.set()  # type: ignore[attr-defined]
        trace = entry.get("trace")
        if trace is not None:
            return self.json(trace.to_payload(), headers=NO_STORE_HEADERS)  # type: ignore[attr-defined]
        cancelled = cancelled_trace(
            correlation_id=correlation_id,
            room_id=str(entry.get("room_id", "")),
            mode=str(entry.get("mode", "safe")),
            started_at=int(entry.get("started_at", 0)),
        )
        return self.json(cancelled.to_payload(), headers=NO_STORE_HEADERS)


def _request_document(
    correlation_id: str, room_id: str, mode: str, started_at: int
) -> dict[str, object]:
    return {
        "contract": {
            "name": "hausman-hub-room-lighting-live-test",
            "version": 1,
        },
        "kind": "request",
        "correlationId": correlation_id,
        "roomId": room_id,
        "mode": mode,
        "startedAt": started_at,
        "durationSeconds": LIVE_TEST_DURATION_SECONDS,
        "steps": [],
        "result": None,
    }


async def _status_context(
    data: Mapping[str, object], config: RoomLightingConfig
) -> RoomLightingContext:
    """Prefer the live runtime context, then a test provider, then a stub."""

    runtime = data.get(DATA_ROOM_LIGHTING_RUNTIME)
    builder = getattr(runtime, "async_context_for", None) if runtime is not None else None
    if callable(builder):
        try:
            context = await builder(config)
        except Exception:  # noqa: BLE001 - a broken read must not break status
            _LOGGER.warning("room lighting runtime context failed; using the stub")
        else:
            if isinstance(context, RoomLightingContext):
                return context
    provider = data.get(DATA_ROOM_LIGHTING_CONTEXT)
    if callable(provider):
        value = provider(config)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, RoomLightingContext):
            return value
    return _stub_context(config)


def _stub_context(config: RoomLightingConfig) -> RoomLightingContext:
    now_ms = int(time.time() * 1000)
    return RoomLightingContext(
        now=now_ms,
        timezone=timezone.utc,
        sunrise=dt_time(7, 0),
        sunset=dt_time(19, 0),
        sensors=tuple(
            SensorSnapshot(
                sensor_id=sensor.id,
                kind=sensor.kind,
                state=SensorState.UNKNOWN,
                last_changed=now_ms,
            )
            for sensor in config.devices.sensors
        ),
        lights=tuple(
            LightSnapshot(
                target_id=target.id,
                state=SensorState.UNKNOWN,
                last_changed=now_ms,
            )
            for target in config.devices.light_targets
        ),
    )


def _status_phase(
    config: RoomLightingConfig, context: RoomLightingContext
) -> str:
    if context.protection.active:
        return "manual_protection"
    if any(
        light.state is SensorState.ON for light in context.lights
    ):
        return "active"
    return "idle"


def _status_payload(
    config: RoomLightingConfig,
    context: RoomLightingContext,
    *,
    fresh: bool = False,
    phase: str = "unknown",
) -> dict[str, object]:
    evaluate_room_lighting(config, context)
    target = config.devices.light_targets[0] if config.devices.light_targets else None
    active = (
        _active_schedule_entry(config, context, target) if target is not None else None
    )
    sources = []
    for light_target in config.devices.light_targets:
        light = context.light(light_target.id)
        source_state = light.state.value if light is not None else "unknown"
        sources.append(
            {
                "source_id": light_target.id,
                "name": light_target.name,
                "kind": light_target.kind.value,
                "role": (
                    light_target.role.value if light_target.role is not None else None
                ),
                "groupId": light_target.group_id,
                "state": source_state,
                "ownership": (
                    "manual"
                    if resolve_manual_ownership(
                        context.ownership,
                        light_target.id,
                        light_on=light is not None and light.state is SensorState.ON,
                    )
                    else (
                        "auto"
                        if has_proven_auto_ownership(context.ownership, light_target.id)
                        else "none"
                    )
                ),
                "manual_protection_active": context.protection.active,
                "last_command": None,
            }
        )
    illuminance = next(
        (sensor for sensor in context.sensors if sensor.kind is SensorKind.ILLUMINANCE),
        None,
    )
    if illuminance is None:
        illumination = {
            "healthy": False,
            "lux": None,
            "sensorState": "missing",
            "failClosed": True,
        }
    else:
        if illuminance.state is SensorState.UNAVAILABLE:
            sensor_state = "unavailable"
        elif illuminance.state is SensorState.UNKNOWN:
            sensor_state = "unknown"
        elif not illuminance.lux_healthy:
            sensor_state = "stale"
        else:
            sensor_state = "ok"
        healthy = sensor_state == "ok" and illuminance.lux is not None
        illumination = {
            "healthy": healthy,
            "lux": illuminance.lux,
            "sensorState": sensor_state,
            "failClosed": not healthy,
        }
    if context.protection.active:
        manual_protection = {
            "active": True,
            "remaining_seconds": max(1, context.protection.minimum_interval_seconds),
            "reason": "manual_off",
            "since": (
                None
                if context.protection.started_at is None
                else int(context.protection.started_at) // 1000
            ),
            "minimum_interval_seconds": context.protection.minimum_interval_seconds,
        }
    else:
        manual_protection = {
            "active": False,
            "remaining_seconds": 0,
            "reason": "none",
            "since": None,
        }
    return {
        "contract": {"name": "hausman-hub-room-lighting-status", "version": 1},
        "generated_at": int(time.time()),
        "roomId": config.room_id,
        "fresh": bool(fresh),
        "phase": phase,
        "active_schedule": (
            None
            if active is None
            else {
                "id": active.id,
                "title": active.title or active.id,
                "when": _when_payload(active),
                "how": {
                    "brightness": active.how.brightness,
                    "colorTemperature": active.how.color_temperature,
                    "mode": active.how.mode.value,
                },
            }
        ),
        "sources": sources,
        "last_event": None,
        "last_action": None,
        "manual_protection": manual_protection,
        "illumination": illumination,
        "away": config.away_behavior.mode.value != "none",
    }


def _when_payload(entry: object) -> dict[str, object]:
    when = entry.when  # type: ignore[attr-defined]
    anchor = when.anchor
    payload: dict[str, object] = {
        "daysOfWeek": (
            when.days_of_week
            if isinstance(when.days_of_week, str)
            else list(when.days_of_week)
        ),
        "holiday": when.holiday,
        "anchor": {
            "kind": anchor.kind.value,
            "offsetMinutes": anchor.offset_minutes,
        },
    }
    if anchor.time is not None:
        payload["anchor"]["time"] = anchor.time  # type: ignore[index]
    return payload


def room_lighting_api_views(hass: HomeAssistant) -> tuple[HomeAssistantView, ...]:
    """Return the fixed view tuple for tests and feature checks."""

    return (
        RoomLightingConfigView(hass),
        RoomLightingStatusView(hass),
        RoomLightingTemplatesView(hass),
        RoomLightingTemplateApplyView(hass),
        RoomLightingLiveTestsView(hass),
        RoomLightingLiveTestView(hass),
    )


__all__ = [
    "ROOM_LIGHTING_CONFIG_PATH",
    "ROOM_LIGHTING_STATUS_PATH",
    "ROOM_LIGHTING_TEMPLATES_PATH",
    "ROOM_LIGHTING_TEMPLATE_APPLY_PATH",
    "ROOM_LIGHTING_LIVE_TESTS_PATH",
    "ROOM_LIGHTING_LIVE_TEST_PATH",
    "register_room_lighting_api",
    "clear_room_lighting_api",
]
