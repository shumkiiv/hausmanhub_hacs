"""Authenticated local-network adapter for the fixed HASC home summary.

This is intentionally a single GET-only Home Assistant view. It has no
outgoing connection, token storage, service call, entity creation, or command
handling. It serves only the already-approved nine aggregate counts.
"""

from __future__ import annotations

from http import HTTPStatus
from ipaddress import ip_address
from typing import TYPE_CHECKING, Any

from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.components.http import HomeAssistantView

from .application.configuration import ConfigurationViolation
from .application.local_summary import local_summary_snapshot
from .home_observation import collect_home_summary

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


DATA_ACTIVE_ENTRY = "local_summary_active_entry"
DATA_VIEW = "local_summary_view"
DOMAIN = "hausman_hub"
LOCAL_SUMMARY_PATH = "/api/hausman_hub/local-summary"


def register_local_summary_access(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Make one safe entry available to the fixed local summary view."""

    runtime = hass.data.setdefault(DOMAIN, {})
    runtime[DATA_ACTIVE_ENTRY] = entry
    if DATA_VIEW not in runtime:
        view = LocalSummaryView(hass)
        hass.http.register_view(view)
        runtime[DATA_VIEW] = view


def clear_local_summary_access(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Stop serving a summary when its only safe config entry unloads."""

    runtime = hass.data.get(DOMAIN)
    if runtime is not None and runtime.get(DATA_ACTIVE_ENTRY) is entry:
        runtime.pop(DATA_ACTIVE_ENTRY, None)


class LocalSummaryView(HomeAssistantView):
    """Serve the fixed aggregate summary to a dedicated local read-only user."""

    url = LOCAL_SUMMARY_PATH
    name = "api:hausman_hub:local_summary"
    requires_auth = True
    cors_allowed = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Keep only the local Home Assistant runtime reference."""

        self._hass = hass

    async def get(self, request: Any) -> Any:
        """Return nine aggregate counts, or fail closed without home details."""

        if not _is_local_read_only_request(request):
            return self.json_message(
                "Local read-only access is required.",
                HTTPStatus.FORBIDDEN,
            )

        entry = self._active_entry()
        if entry is None:
            return self.json_message(
                "The local summary is unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

        try:
            summary = local_summary_snapshot(
                entry.data,
                entry.options,
                lambda: collect_home_summary(self._hass, entry.entry_id),
            )
        except ConfigurationViolation:
            return self.json_message(
                "The local summary is unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        return self.json(summary)

    def _active_entry(self) -> ConfigEntry | None:
        """Return the one saved entry only while it is currently loaded."""

        runtime = self._hass.data.get(DOMAIN)
        if runtime is None:
            return None
        entry = runtime.get(DATA_ACTIVE_ENTRY)
        if entry is None:
            return None
        configured_entries = self._hass.config_entries.async_entries(DOMAIN)
        if len(configured_entries) != 1:
            return None
        configured_entry = configured_entries[0]
        if configured_entry.entry_id != entry.entry_id:
            return None
        if not any(
            loaded_entry.entry_id == configured_entry.entry_id
            for loaded_entry in self._hass.config_entries.async_loaded_entries(DOMAIN)
        ):
            return None
        return configured_entry


def _is_local_read_only_request(request: Any) -> bool:
    """Accept only a local address and the exact Home Assistant read-only role."""

    try:
        user = request["hass_user"]
    except (KeyError, TypeError):
        return False
    return _is_local_address(getattr(request, "remote", None)) and _is_read_only_user(user)


def _is_local_address(remote: object) -> bool:
    """Reject absent, named, public, and malformed request origins."""

    if not isinstance(remote, str):
        return False
    try:
        address = ip_address(remote)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _is_read_only_user(user: object) -> bool:
    """Require exactly Home Assistant's built-in read-only group.

    A non-administrator in the ordinary user group can still have control
    permissions. Requiring the single system read-only group rejects a mixed
    group policy and therefore fails closed if the account is configured too
    broadly.
    """

    if user is None or getattr(user, "is_admin", True) or getattr(user, "system_generated", True):
        return False
    groups = getattr(user, "groups", None)
    if not isinstance(groups, (frozenset, list, set, tuple)):
        return False
    group_ids = {getattr(group, "id", None) for group in groups}
    return group_ids == {GROUP_ID_READ_ONLY}
