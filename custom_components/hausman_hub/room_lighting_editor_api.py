"""Authenticated read-only HTTP views for the room lighting editor.

The catalog lists the devices of one room. The preview validates an unsaved
draft and explains the decision without persisting it, changing ownership or
sending a physical command. Both routes are registered once per entry setup.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .application.room_lighting_editor import (
    EditorDevice,
    RoomLightingEditorService,
)
from .climate_api import DOMAIN, NO_STORE_HEADERS, _forbidden, _not_found
from .domain.room_lighting import RoomLightingConfig, RoomLightingViolation
from .room_lighting_api import (
    DATA_ROOM_LIGHTING_CONTEXT,
    DATA_ROOM_LIGHTING_SERVICE,
    MAX_CONFIG_BODY_BYTES,
    _RoomLightingView,
    _request_json,
    _status_context,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

ROOM_LIGHTING_BASE = "/api/hausman_hub/v1/rooms/{room_id}/lighting"
ROOM_LIGHTING_EDITOR_CATALOG_PATH = f"{ROOM_LIGHTING_BASE}/editor-catalog"
ROOM_LIGHTING_PREVIEW_PATH = f"{ROOM_LIGHTING_BASE}/preview"

DATA_ROOM_LIGHTING_EDITOR_VIEWS = "room_lighting_editor_views"
DATA_ROOM_LIGHTING_EDITOR_DEVICES = "room_lighting_editor_devices"


def register_room_lighting_editor_api(hass: HomeAssistant) -> None:
    """Register the two editor views once for the loaded entry."""

    data = hass.data.setdefault(DOMAIN, {})
    if DATA_ROOM_LIGHTING_EDITOR_VIEWS in data:
        return
    views = (
        RoomLightingEditorCatalogView(hass),
        RoomLightingPreviewView(hass),
    )
    for view in views:
        hass.http.register_view(view)
    data[DATA_ROOM_LIGHTING_EDITOR_VIEWS] = views


def clear_room_lighting_editor_api(hass: HomeAssistant) -> None:
    """Drop the device provider; the fixed views stay registered."""

    data = hass.data.get(DOMAIN)
    if data is None:
        return
    data.pop(DATA_ROOM_LIGHTING_EDITOR_DEVICES, None)


class _RoomLightingEditorView(_RoomLightingView):
    """Shared setup for the catalog and the safe preview."""

    async def _editor(
        self, room_id: str
    ) -> tuple[RoomLightingEditorService, RoomLightingConfig] | Any:
        service = self._service()
        if service is None:
            return self._unavailable()
        config = await service.async_get_config(room_id)
        if config is None:
            return self._error("not_found")
        context = await _status_context(self._data(), config)
        return (
            RoomLightingEditorService(
                configs={room_id: config},
                devices=self._devices_provider(config),
                context=context,
            ),
            config,
        )

    def _devices_provider(self, config: RoomLightingConfig):
        data = self._data()
        provider = data.get(DATA_ROOM_LIGHTING_EDITOR_DEVICES)
        if callable(provider):
            return lambda _room_id: tuple(_as_devices(provider(config)))
        from .room_lighting_editor_ha import editor_devices

        return lambda _room_id: editor_devices(self._hass, config)


class RoomLightingEditorCatalogView(_RoomLightingEditorView):
    """List the devices of one room with human labels."""

    url = ROOM_LIGHTING_EDITOR_CATALOG_PATH
    name = "api:hausman_hub:room_lighting_editor_catalog"

    async def get(self, request: Any, room_id: str | None = None) -> Any:
        del room_id
        room_id = self._room_id(request)
        if room_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        editor = await self._editor(room_id)
        if not isinstance(editor, tuple):
            return editor
        service, _config = editor
        return self.json(service.catalog(room_id), headers=NO_STORE_HEADERS)


class RoomLightingPreviewView(_RoomLightingEditorView):
    """Validate one unsaved draft and explain the decision."""

    url = ROOM_LIGHTING_PREVIEW_PATH
    name = "api:hausman_hub:room_lighting_preview"

    async def post(self, request: Any, room_id: str | None = None) -> Any:
        del room_id
        room_id = self._room_id(request)
        if room_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        try:
            payload = await _request_json(
                request, maximum_bytes=MAX_CONFIG_BODY_BYTES
            )
        except ValueError:
            return self._invalid("тело запроса должно быть JSON-объектом")
        if not isinstance(payload, Mapping):
            return self._invalid("тело запроса должно быть JSON-объектом")
        editor = await self._editor(room_id)
        if not isinstance(editor, tuple):
            return editor
        service, _config = editor
        try:
            result = service.preview(room_id, dict(payload))
        except RoomLightingViolation:
            return self._error("not_found")
        return self.json(result, headers=NO_STORE_HEADERS)


def _as_devices(value: object) -> Sequence[EditorDevice]:
    if isinstance(value, Sequence):
        return tuple(item for item in value if isinstance(item, EditorDevice))
    return ()
