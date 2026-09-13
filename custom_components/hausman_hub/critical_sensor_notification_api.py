"""Read-only HTTP view for active critical sensor notifications.

The GET evaluates the current live states of the sensors that take part in a
light or climate decision, updates the in-memory dedup coordinator and returns
the active critical documents. It never writes configuration storage, never
changes ownership and never sends a physical command.
"""

from __future__ import annotations

import inspect
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.critical_sensor_notifications import (
    CriticalSensorNotificationService,
    climate_sensor_health_inputs,
    climate_sensor_participant_keys,
    light_sensor_health_inputs,
    light_sensor_participant_keys,
)
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_local_admin_request,
    _is_local_tablet_request,
)
from .domain.critical_sensor_notification import CriticalSensorHealth
from .domain.room_lighting_engine import RoomLightingContext
from .error_taxonomy import api_error_payload, api_error_status
from .room_lighting_api import (
    DATA_ROOM_LIGHTING_CONTEXT,
    DATA_ROOM_LIGHTING_RUNTIME,
    DATA_ROOM_LIGHTING_SERVICE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CRITICAL_SENSOR_NOTIFICATIONS_PATH = (
    "/api/hausman_hub/v1/critical-sensor-notifications"
)
CRITICAL_SENSOR_NOTIFICATIONS_CONTRACT_NAME = (
    "hausman-hub-critical-sensor-notifications"
)
CRITICAL_SENSOR_NOTIFICATIONS_CONTRACT_VERSION = 1

DATA_CRITICAL_SENSOR_NOTIFICATIONS = "critical_sensor_notifications"
DATA_CRITICAL_SENSOR_CLIMATE_RUNTIME = "critical_sensor_climate_runtime"
DATA_CRITICAL_SENSOR_VIEWS = "critical_sensor_views"


def register_critical_sensor_notification_api(
    hass: HomeAssistant,
    *,
    climate_runtime: object | None = None,
) -> None:
    """Create the in-memory coordinator once and register the view once."""

    data = hass.data.setdefault(DOMAIN, {})
    data.setdefault(
        DATA_CRITICAL_SENSOR_NOTIFICATIONS, CriticalSensorNotificationService()
    )
    if climate_runtime is not None:
        data[DATA_CRITICAL_SENSOR_CLIMATE_RUNTIME] = climate_runtime
    if DATA_CRITICAL_SENSOR_VIEWS in data:
        return
    view = CriticalSensorNotificationsView(hass)
    hass.http.register_view(view)
    data[DATA_CRITICAL_SENSOR_VIEWS] = (view,)


def clear_critical_sensor_notification_api(hass: HomeAssistant) -> None:
    """Drop the in-memory coordinator; the fixed read-only view stays."""

    data = hass.data.get(DOMAIN)
    if data is None:
        return
    data.pop(DATA_CRITICAL_SENSOR_NOTIFICATIONS, None)
    data.pop(DATA_CRITICAL_SENSOR_CLIMATE_RUNTIME, None)


class CriticalSensorNotificationsView(HomeAssistantView):
    """Return the active critical sensor faults for a local client."""

    url = CRITICAL_SENSOR_NOTIFICATIONS_PATH
    name = "api:hausman_hub:critical_sensor_notifications"
    requires_auth = True
    cors_allowed = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _authorized(self, request: Any) -> bool:
        return _is_local_admin_request(request) or _is_local_tablet_request(request)

    def _error(self, code: str) -> Any:
        return self.json(
            api_error_payload(code),
            status_code=api_error_status(code),
            headers=NO_STORE_HEADERS,
        )

    async def get(self, request: Any) -> Any:
        if not self._authorized(request):
            return _forbidden(self)
        data = self._hass.data.get(DOMAIN) or {}
        service = data.get(DATA_CRITICAL_SENSOR_NOTIFICATIONS)
        if not isinstance(service, CriticalSensorNotificationService):
            return self._error("unavailable")
        now = int(time.time())
        healths, participants = await self._health_inputs(data, now)
        if healths or participants is not None:
            service.evaluate(healths, now=now, participants=participants)
        return self.json(
            _notifications_document(service, now),
            headers=NO_STORE_HEADERS,
        )

    async def _health_inputs(
        self, data: dict[str, object], now: int
    ) -> tuple[
        tuple[CriticalSensorHealth, ...],
        tuple[tuple[str, str], ...] | None,
    ]:
        light_healths, light_keys = await self._light_inputs(data)
        climate_healths, climate_keys = self._climate_inputs(data, now)
        healths = light_healths + climate_healths
        if light_keys is None or climate_keys is None:
            # A configuration that cannot be read cannot prove a sensor was
            # removed, so nothing is dropped automatically this cycle.
            return healths, None
        return healths, light_keys + climate_keys

    async def _light_inputs(
        self, data: dict[str, object]
    ) -> tuple[
        tuple[CriticalSensorHealth, ...],
        tuple[tuple[str, str], ...] | None,
    ]:
        service = data.get(DATA_ROOM_LIGHTING_SERVICE)
        list_configs = getattr(service, "async_list_configs", None)
        if not callable(list_configs):
            return (), None
        try:
            configs = await list_configs()
        except Exception:  # noqa: BLE001 - a broken read must not break the view
            _LOGGER.warning("critical sensor notifications: config listing failed")
            return (), None
        runtime = data.get(DATA_ROOM_LIGHTING_RUNTIME)
        provider = data.get(DATA_ROOM_LIGHTING_CONTEXT)
        healths: list[CriticalSensorHealth] = []
        keys: list[tuple[str, str]] = []
        for config in configs:
            try:
                keys.extend(
                    light_sensor_participant_keys(
                        config, state_lookup=self._state_value
                    )
                )
            except Exception:  # noqa: BLE001 - an unread config hides the set
                _LOGGER.warning(
                    "critical sensor notifications: participant keys failed"
                )
                return tuple(healths), None
            context = await self._light_context(runtime, provider, config)
            if context is None:
                continue
            try:
                healths.extend(
                    light_sensor_health_inputs(
                        config, context, state_lookup=self._state_value
                    )
                )
            except Exception:  # noqa: BLE001 - isolate one broken room
                _LOGGER.warning(
                    "critical sensor notifications: room lighting evaluation "
                    "failed"
                )
        return tuple(healths), tuple(keys)

    async def _light_context(
        self, runtime: object | None, provider: object, config: object
    ) -> RoomLightingContext | None:
        builder = (
            getattr(runtime, "async_context_for", None)
            if runtime is not None
            else None
        )
        if callable(builder):
            try:
                context = await builder(config)
            except Exception:  # noqa: BLE001 - fall back to a test provider
                context = None
            if isinstance(context, RoomLightingContext):
                return context
        if callable(provider):
            value = provider(config)
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, RoomLightingContext):
                return value
        return None

    def _state_value(self, entity_id: str) -> str | None:
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        return str(getattr(state, "state", "")).lower()

    def _climate_inputs(
        self, data: dict[str, object], now: int
    ) -> tuple[
        tuple[CriticalSensorHealth, ...],
        tuple[tuple[str, str], ...] | None,
    ]:
        runtime = data.get(DATA_CRITICAL_SENSOR_CLIMATE_RUNTIME)
        registry = getattr(runtime, "registry", None)
        states = getattr(runtime, "ha_state_view", None)
        if registry is None:
            return (), None
        try:
            keys = climate_sensor_participant_keys(registry)
        except Exception:  # noqa: BLE001 - a broken registry is not a 500
            _LOGGER.warning(
                "critical sensor notifications: climate participant keys failed"
            )
            return (), None
        if states is None:
            # The participant set is known even without live observations.
            return (), keys
        try:
            return (
                climate_sensor_health_inputs(
                    registry, states, observed_at=now * 1000
                ),
                keys,
            )
        except Exception:  # noqa: BLE001 - a broken climate read is not a 500
            _LOGGER.warning(
                "critical sensor notifications: climate evaluation failed"
            )
            return (), keys


def _notifications_document(
    service: CriticalSensorNotificationService,
    now: int,
) -> dict[str, object]:
    return {
        "contract": {
            "name": CRITICAL_SENSOR_NOTIFICATIONS_CONTRACT_NAME,
            "version": CRITICAL_SENSOR_NOTIFICATIONS_CONTRACT_VERSION,
        },
        "generatedAt": now,
        "notifications": list(service.payloads()),
    }


__all__ = [
    "CRITICAL_SENSOR_NOTIFICATIONS_CONTRACT_NAME",
    "CRITICAL_SENSOR_NOTIFICATIONS_CONTRACT_VERSION",
    "CRITICAL_SENSOR_NOTIFICATIONS_PATH",
    "CriticalSensorNotificationsView",
    "clear_critical_sensor_notification_api",
    "register_critical_sensor_notification_api",
]
