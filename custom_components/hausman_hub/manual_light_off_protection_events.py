"""State-event adapter for durable manual light-off protection."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Any

from .domain.manual_light_off_protection import ScenarioCommandAttribution

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.manual_light_off_protection import (
        ManualLightOffProtectionCoordinator,
    )
    from .application.scenario_command_context import ScenarioCommandContextRegistry


def _async_track_state_change_event(
    hass: HomeAssistant, entity_ids: Collection[str], listener: Any
) -> Any:
    """Load the Home Assistant subscriber only in the runtime path."""

    from homeassistant.helpers.event import (  # noqa: PLC0415
        async_track_state_change_event,
    )

    return async_track_state_change_event(hass, entity_ids, listener)


class ManualLightOffProtectionEventListener:
    """Forward only protected-light and frozen-profile sensor transitions."""

    def __init__(
        self,
        coordinator: ManualLightOffProtectionCoordinator,
        command_contexts: ScenarioCommandContextRegistry | None,
        entity_ids: Collection[str],
    ) -> None:
        self._coordinator = coordinator
        self._command_contexts = command_contexts
        self._entity_ids = frozenset(entity_ids)

    async def async_handle(self, event: Any) -> None:
        """Persist a relevant transition without issuing physical commands."""

        data = getattr(event, "data", {})
        if not isinstance(data, Mapping):
            return
        entity_id = data.get("entity_id")
        old_state = data.get("old_state")
        new_state = data.get("new_state")
        if (
            not isinstance(entity_id, str)
            or entity_id not in self._entity_ids
            or old_state is None
            or new_state is None
            or getattr(old_state, "state", None)
            == getattr(new_state, "state", None)
        ):
            return
        attribution = None
        if self._command_contexts is not None:
            matched = self._command_contexts.match(
                getattr(event, "context", None),
                entity_id,
                getattr(new_state, "state", None),
            )
            if matched is not None:
                attribution = ScenarioCommandAttribution(
                    source=matched.origin,
                    attribution_id=matched.request_id or entity_id,
                )
        await self._coordinator.async_note_state_transition(
            entity_id, old_state, new_state, attribution
        )


def configured_manual_light_off_protection_entities(
    coordinator: ManualLightOffProtectionCoordinator,
) -> frozenset[str]:
    """Return the exact configured protected lights and presence sensors."""

    settings = coordinator.snapshot().get("settings", {})
    if not isinstance(settings, Mapping):
        return frozenset()
    profiles = settings.get("profiles", [])
    if not isinstance(profiles, list):
        return frozenset()
    entity_ids: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, Mapping):
            continue
        for key in ("lightIds", "presenceSensorIds"):
            values = profile.get(key, [])
            if isinstance(values, list):
                entity_ids.update(value for value in values if isinstance(value, str))
    return frozenset(entity_ids)


async def async_start_manual_light_off_protection_events(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ManualLightOffProtectionCoordinator,
    command_contexts: ScenarioCommandContextRegistry | None,
) -> None:
    """Subscribe exactly configured entities to the state-event adapter."""

    entity_ids = configured_manual_light_off_protection_entities(coordinator)
    if not entity_ids:
        return
    listener = ManualLightOffProtectionEventListener(
        coordinator, command_contexts, entity_ids
    )
    unsubscribe = _async_track_state_change_event(
        hass, entity_ids, listener.async_handle
    )
    entry.async_on_unload(unsubscribe)
