"""Authenticated SSE adapter for immediate tablet refresh and alarms."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import callback

from .application.api_capabilities import EVENT_STREAM_PATH
from .application.event_stream import (
    EVENT_STREAM_HEARTBEAT_SECONDS,
    EventStreamBroker,
    heartbeat_event,
    hello_event,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


DOMAIN: Final = "hausman_hub"
DATA_EVENT_STREAM_RUNTIME: Final = "event_stream_runtime"
DATA_EVENT_STREAM_VIEW: Final = "event_stream_view"
_INVALIDATION_DEBOUNCE_SECONDS: Final = 0.35
_CRITICAL_DEVICE_CLASSES: Final = {
    "moisture": ("leak", "Обнаружена протечка", "Протечка устранена"),
    "smoke": ("smoke", "Обнаружен дым", "Дым больше не обнаружен"),
    "gas": ("gas", "Обнаружен газ", "Газ больше не обнаружен"),
}


class EventStreamRuntime:
    """Translate HA changes to a bounded, privacy-safe live stream."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.broker = EventStreamBroker()
        self._pending_invalidation: asyncio.TimerHandle | None = None
        self._remove_listener: Any = None

    def start(self) -> None:
        bus = getattr(self.hass, "bus", None)
        if bus is not None:
            self._remove_listener = bus.async_listen(
                "state_changed", self._state_changed
            )

    def close(self) -> None:
        if self._pending_invalidation is not None:
            self._pending_invalidation.cancel()
            self._pending_invalidation = None
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        self.broker.close()

    @callback
    def _state_changed(self, event: Any) -> None:
        data = getattr(event, "data", {})
        if not isinstance(data, dict):
            return
        new_state = data.get("new_state")
        old_state = data.get("old_state")
        if new_state is None and old_state is None:
            return
        self._publish_critical_alert(data.get("entity_id"), old_state, new_state)
        if self._pending_invalidation is None:
            self._pending_invalidation = self.hass.loop.call_later(
                _INVALIDATION_DEBOUNCE_SECONDS,
                self._publish_invalidation,
            )

    @callback
    def _publish_invalidation(self) -> None:
        self._pending_invalidation = None
        self.broker.publish("snapshot_invalidated", {"reason": "state_changed"})

    def _publish_critical_alert(
        self, entity_id: object, old_state: Any, new_state: Any
    ) -> None:
        state = getattr(new_state, "state", None)
        old_value = getattr(old_state, "state", None)
        attributes = getattr(new_state, "attributes", {})
        if not isinstance(entity_id, str) or not isinstance(attributes, dict):
            return
        device_class = attributes.get("device_class")
        alert_definition = _CRITICAL_DEVICE_CLASSES.get(device_class)
        if alert_definition is None or state not in {"on", "off"} or state == old_value:
            return
        kind, active_title, cleared_title = alert_definition
        active = state == "on"
        title = active_title if active else cleared_title
        device = attributes.get("friendly_name")
        room = attributes.get("area_name")
        self.broker.publish(
            "critical_alert",
            {
                "alert_id": sha256(entity_id.encode("utf-8")).hexdigest()[:20],
                "kind": kind,
                "severity": "critical" if active else "warning",
                "title": title,
                "message": (
                    f"{title}: {device}" if isinstance(device, str) and device else title
                ),
                "room": room if isinstance(room, str) and room else None,
                "device": device if isinstance(device, str) and device else None,
                "active": active,
            },
        )


class EventStreamView(HomeAssistantView):
    """Keep one authenticated local SSE response open per tablet client."""

    url = EVENT_STREAM_PATH
    name = "api:hausman_hub:events"
    requires_auth = True
    cors_allowed = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: Any) -> Any:
        from aiohttp import web

        from .climate_api import _is_exact_request, _is_local_tablet_request

        if not _is_exact_request(request, EVENT_STREAM_PATH):
            raise web.HTTPNotFound()
        if not _is_local_tablet_request(request):
            raise web.HTTPForbidden()
        runtime = _current_runtime(self._hass)
        if runtime is None:
            raise web.HTTPServiceUnavailable()

        response = web.StreamResponse(
            status=200,
            headers={
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        response.content_type = "text/event-stream"
        response.charset = "utf-8"
        await response.prepare(request)
        queue = runtime.broker.subscribe()
        heartbeat_sequence = 0
        try:
            await response.write(b"retry: 5000\n\n")
            await response.write(_encode_sse(hello_event(runtime.broker)))
            while True:
                try:
                    message = await asyncio.wait_for(
                        queue.get(), timeout=EVENT_STREAM_HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    heartbeat_sequence += 1
                    message = heartbeat_event(runtime.broker, heartbeat_sequence)
                if message is None:
                    break
                await response.write(_encode_sse(message))
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, RuntimeError):
            pass
        finally:
            runtime.broker.unsubscribe(queue)
            try:
                await response.write_eof()
            except (ConnectionResetError, RuntimeError):
                pass
        return response


def register_event_stream(hass: HomeAssistant, entry_id: str) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    previous = data.pop(DATA_EVENT_STREAM_RUNTIME, None)
    if isinstance(previous, EventStreamRuntime):
        previous.close()
    runtime = EventStreamRuntime(hass, entry_id)
    runtime.start()
    data[DATA_EVENT_STREAM_RUNTIME] = runtime
    if DATA_EVENT_STREAM_VIEW not in data:
        view = EventStreamView(hass)
        hass.http.register_view(view)
        data[DATA_EVENT_STREAM_VIEW] = view


def clear_event_stream(hass: HomeAssistant, entry_id: str) -> None:
    data = hass.data.get(DOMAIN)
    if not isinstance(data, dict):
        return
    runtime = data.get(DATA_EVENT_STREAM_RUNTIME)
    if isinstance(runtime, EventStreamRuntime) and runtime.entry_id == entry_id:
        runtime.close()
        data.pop(DATA_EVENT_STREAM_RUNTIME, None)


def _current_runtime(hass: HomeAssistant) -> EventStreamRuntime | None:
    data = hass.data.get(DOMAIN, {})
    runtime = data.get(DATA_EVENT_STREAM_RUNTIME)
    if not isinstance(runtime, EventStreamRuntime):
        return None
    loaded = hass.config_entries.async_loaded_entries(DOMAIN)
    if not any(entry.entry_id == runtime.entry_id for entry in loaded):
        return None
    return runtime


def _encode_sse(message: dict[str, object]) -> bytes:
    return (
        f"id: {message['id']}\n"
        f"event: {message['type']}\n"
        f"data: {json.dumps(message, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode("utf-8")
