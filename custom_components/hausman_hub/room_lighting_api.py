"""Authenticated tablet/admin HTTP views for room lighting.

The API exposes configuration, a command-free shadow status, templates and a
bounded live test. ``safe`` live tests never call an executor; ``real`` tests
require an explicitly injected executor and otherwise fail with
``capability_unavailable``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from http import HTTPStatus
import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.room_lighting_live_test import RoomLightingLiveTestRunner
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
from .domain.room_lighting import SensorKind
from .error_taxonomy import api_error_payload, api_error_status

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


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

_ROOM_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_CONFIG_BODY_BYTES = 256 * 1024


def register_room_lighting_api(hass: HomeAssistant, entry_id: str) -> None:
    """Register fixed room lighting routes once per loaded entry."""

    data = hass.data.setdefault(DOMAIN, {})
    if DATA_ROOM_LIGHTING_VIEWS in data:
        return
    from .application.room_lighting_storage import HomeAssistantRoomLightingStore

    data[DATA_ROOM_LIGHTING_SERVICE] = RoomLightingService(
        HomeAssistantRoomLightingStore(hass, entry_id)
    )
    data.setdefault(DATA_ROOM_LIGHTING_LIVE_TESTS, {})
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
    """Drop room lighting services while leaving registered views harmless."""

    data = hass.data.get(DOMAIN)
    if data is None:
        return
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
    ) -> Any:
        return self.json(
            api_error_payload(code, details=details),
            status_code=api_error_status(code),
            headers=NO_STORE_HEADERS,
        )

    def _unavailable(self) -> Any:
        return self._error("unavailable")

    def _invalid(self, violations: tuple[str, ...]) -> Any:
        payload = api_error_payload("invalid_request")
        payload["details"] = {"violations": list(violations)}
        return self.json(payload, status_code=HTTPStatus.BAD_REQUEST, headers=NO_STORE_HEADERS)


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
            return self._error("invalid_request")
        if not isinstance(payload, Mapping):
            return self._error("invalid_request")
        if payload.get("roomId") != room_id:
            return self._error("invalid_request")
        try:
            config = config_from_payload(payload)
        except RoomLightingViolation as error:
            return self._invalid((str(error),))
        stored = await service.async_get_config(room_id)
        expected = payload.get("expectedRevision", payload.get("version"))
        if stored is not None and expected != stored.version:
            return self._error(
                "revision_conflict",
                details={
                    "expectedRevision": expected,
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
        context = _status_context(self._data(), config)
        return self.json(
            _status_payload(config, context),
            headers=NO_STORE_HEADERS,
        )


class RoomLightingTemplatesView(_RoomLightingView):
    """List the built-in room lighting templates."""

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
                    {
                        "id": template_id,
                        "title": "Суточный профиль освещения",
                        "category": "day_profile",
                    }
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
            return self._error("invalid_request")
        if not isinstance(payload, Mapping):
            return self._error("invalid_request")
        template_id = payload.get("templateId")
        overrides = payload.get("overrides")
        if not isinstance(template_id, str):
            return self._error("invalid_request")
        try:
            config = await service.async_apply_template(
                template_id,
                overrides if isinstance(overrides, Mapping) else None,
                room_id=room_id,
            )
        except RoomLightingViolation as error:
            return self._invalid((str(error),))
        return self.json(config.to_dict(), headers=NO_STORE_HEADERS)


class RoomLightingLiveTestsView(_RoomLightingView):
    """Start one bounded live test for a room."""

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
            return self._error("invalid_request")
        mode = payload.get("mode", "safe")
        if mode not in {"safe", "real"}:
            return self._error("invalid_request")
        correlation_id = payload.get("correlationId")
        if not isinstance(correlation_id, str) or not _CORRELATION_ID.fullmatch(correlation_id):
            return self._error("invalid_request")
        config = await service.async_get_config(room_id)
        if config is None:
            return self._error("not_found")
        data = self._data()
        executor = data.get(DATA_ROOM_LIGHTING_EXECUTOR)
        if mode == "real" and executor is None:
            return self._error("capability_unavailable")
        context_factory = data.get(DATA_ROOM_LIGHTING_LIVE_CONTEXT)
        runner = RoomLightingLiveTestRunner()
        try:
            trace = await runner.run(
                config,
                mode=mode,
                correlation_id=correlation_id,
                executor=executor,
                context_factory=context_factory if callable(context_factory) else None,
            )
        except RoomLightingViolation:
            return self._error("invalid_request")
        registry = data.setdefault(DATA_ROOM_LIGHTING_LIVE_TESTS, {})
        if isinstance(registry, dict):
            registry[correlation_id] = trace
        return self.json(
            trace.to_payload(),
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

    def _trace(self, correlation_id: str) -> object | None:
        registry = self._data().get(DATA_ROOM_LIGHTING_LIVE_TESTS)
        if not isinstance(registry, dict):
            return None
        return registry.get(correlation_id)

    async def get(self, request: Any, correlation_id: str | None = None) -> Any:
        del correlation_id
        correlation_id = self._correlation_id(request)
        if correlation_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        if self._service() is None:
            return self._unavailable()
        trace = self._trace(correlation_id)
        if trace is None:
            return self._error("not_found")
        return self.json(
            trace.to_payload(),  # type: ignore[attr-defined]
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
        registry = self._data().get(DATA_ROOM_LIGHTING_LIVE_TESTS)
        if not isinstance(registry, dict):
            return self._error("not_found")
        event = registry.get(f"{correlation_id}.cancel")
        if correlation_id not in registry and event is None:
            return self._error("not_found")
        if event is not None:
            event.set()  # type: ignore[attr-defined]
        return self.json(
            {"correlationId": correlation_id, "status": "cancelled"},
            headers=NO_STORE_HEADERS,
        )


def _status_context(
    data: Mapping[str, object], config: RoomLightingConfig
) -> RoomLightingContext:
    provider = data.get(DATA_ROOM_LIGHTING_CONTEXT)
    if callable(provider):
        return provider(config)  # type: ignore[no-any-return]
    from datetime import time as dt_time, timezone

    now = 0
    return RoomLightingContext(
        now=now,
        timezone=timezone.utc,
        sunrise=dt_time(7, 0),
        sunset=dt_time(19, 0),
        sensors=tuple(
            SensorSnapshot(
                sensor_id=sensor.id,
                kind=sensor.kind,
                state=SensorState.UNKNOWN,
                last_changed=now,
            )
            for sensor in config.devices.sensors
        ),
        lights=tuple(
            LightSnapshot(
                target_id=target.id,
                state=SensorState.UNKNOWN,
                last_changed=now,
            )
            for target in config.devices.light_targets
        ),
    )


def _status_payload(
    config: RoomLightingConfig, context: RoomLightingContext
) -> dict[str, object]:
    decision = evaluate_room_lighting(config, context)
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
                "role": light_target.role.value if light_target.role is not None else None,
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
    return {
        "contract": {"name": "hausman-hub-room-lighting-status", "version": 1},
        "generated_at": context.now,
        "roomId": config.room_id,
        "fresh": False,
        "phase": "unknown",
        "active_schedule": (
            None
            if active is None
            else {
                "id": active.id,
                "title": active.title,
                "mode": active.how.mode.value,
                "minOnSeconds": active.how.min_on_seconds,
            }
        ),
        "sources": sources,
        "last_event": None,
        "last_action": None,
        "manual_protection": {
            "active": context.protection.active,
            "remaining_seconds": 0,
            "reason": "none",
            "since": None,
        },
        "illumination": {
            "healthy": False,
            "lux": None,
            "sensorState": "unknown",
            "failClosed": True,
        },
        "away": config.away_behavior.mode.value != "none",
        "commandsEnabled": False,
        "shadowDecision": decision.to_payload(),
    }


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
