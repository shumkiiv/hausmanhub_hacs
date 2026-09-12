"""Admin toggle for the legacy Tambur runtime gate.

The endpoint stores an operator flag and reloads the HausmanHub config entry so
the startup coordinator picks it up. It never touches the Node-RED graph or the
room migration records.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_admin_request,
    _request_json,
)
from .application.tambur_runtime_gate import (
    HomeAssistantTamburLegacyRuntimeStore,
    TamburLegacyRuntimeGate,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

TAMBUR_LEGACY_RUNTIME_PATH = "/api/hausman_hub/v1/admin/tambur-legacy-runtime"
DATA_TAMBUR_LEGACY_RUNTIME = "tambur_legacy_runtime_gate"
DATA_TAMBUR_LEGACY_RUNTIME_ENTRY = "tambur_legacy_runtime_entry_id"
DATA_TAMBUR_LEGACY_RUNTIME_VIEWS = "tambur_legacy_runtime_views"
_MAX_BODY_BYTES = 4096


def register_tambur_runtime_api(hass: HomeAssistant, entry_id: str) -> None:
    """Store the gate and register the fixed admin view once."""

    data = hass.data.setdefault(DOMAIN, {})
    data[DATA_TAMBUR_LEGACY_RUNTIME_ENTRY] = entry_id
    if DATA_TAMBUR_LEGACY_RUNTIME_VIEWS in data:
        return
    hass.http.register_view(TamburLegacyRuntimeView(hass))
    data[DATA_TAMBUR_LEGACY_RUNTIME_VIEWS] = True


def clear_tambur_runtime_api(hass: HomeAssistant) -> None:
    """Drop the per-entry gate so a stale flag cannot survive a reload."""

    data = hass.data.get(DOMAIN)
    if data is None:
        return
    data.pop(DATA_TAMBUR_LEGACY_RUNTIME, None)
    data.pop(DATA_TAMBUR_LEGACY_RUNTIME_ENTRY, None)


async def async_ensure_tambur_runtime_gate(
    hass: HomeAssistant, entry_id: str
) -> TamburLegacyRuntimeGate:
    """Load the persisted gate for one entry and publish it in ``hass.data``."""

    data = hass.data.setdefault(DOMAIN, {})
    gate = data.get(DATA_TAMBUR_LEGACY_RUNTIME)
    if isinstance(gate, TamburLegacyRuntimeGate):
        return gate
    gate = TamburLegacyRuntimeGate(
        HomeAssistantTamburLegacyRuntimeStore(hass, entry_id)
    )
    await gate.async_load()
    data[DATA_TAMBUR_LEGACY_RUNTIME] = gate
    return gate


class TamburLegacyRuntimeView(HomeAssistantView):
    """Read and change the legacy Tambur runtime gate for local admins."""

    requires_auth = True
    cors_allowed = False
    url = TAMBUR_LEGACY_RUNTIME_PATH
    name = "api:hausman_hub:tambur_legacy_runtime"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _data(self) -> dict[str, object]:
        return self._hass.data.setdefault(DOMAIN, {})

    def _gate(self) -> TamburLegacyRuntimeGate | None:
        gate = self._data().get(DATA_TAMBUR_LEGACY_RUNTIME)
        return gate if isinstance(gate, TamburLegacyRuntimeGate) else None

    def _authorized(self, request: Any) -> bool:
        return _is_exact_request(request, self.url) and _is_local_admin_request(request)

    def _unavailable(self) -> Any:
        return self.json(
            {"message": "Legacy Tambur runtime gate is unavailable."},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            headers=NO_STORE_HEADERS,
        )

    def _invalid(self, message: str) -> Any:
        return self.json(
            {"message": message},
            status_code=HTTPStatus.BAD_REQUEST,
            headers=NO_STORE_HEADERS,
        )

    async def get(self, request: Any) -> Any:
        if not self._authorized(request):
            return _forbidden(self)
        gate = self._gate()
        if gate is None:
            return self._unavailable()
        return self.json(
            {"enabled": gate.enabled, "reason": gate.reason},
            headers=NO_STORE_HEADERS,
        )

    async def put(self, request: Any) -> Any:
        if not self._authorized(request):
            return _forbidden(self)
        gate = self._gate()
        if gate is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=_MAX_BODY_BYTES)
        except ValueError:
            return self._invalid("The Tambur gate body must be a JSON object.")
        if not isinstance(payload, Mapping):
            return self._invalid("The Tambur gate body must be a JSON object.")
        enabled = payload.get("enabled")
        if type(enabled) is not bool:
            return self._invalid("enabled must be a boolean.")
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            return self._invalid("reason must be a string or omitted.")
        await gate.async_set_enabled(enabled, reason)
        entry_id = self._data().get(DATA_TAMBUR_LEGACY_RUNTIME_ENTRY)
        if isinstance(entry_id, str):
            await self._hass.config_entries.async_reload(entry_id)
        return self.json(
            {"enabled": gate.enabled, "reason": gate.reason},
            headers=NO_STORE_HEADERS,
        )


__all__ = [
    "TAMBUR_LEGACY_RUNTIME_PATH",
    "register_tambur_runtime_api",
    "clear_tambur_runtime_api",
    "async_ensure_tambur_runtime_gate",
]
