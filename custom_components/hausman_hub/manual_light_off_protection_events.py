"""State-event adapter for durable manual light-off protection."""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .application.manual_light_off_protection import (
    trusted_presence_sensor_evidence,
)
from .domain.manual_light_off_protection import ScenarioCommandAttribution

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.manual_light_off_protection import (
        ManualLightOffProtectionCoordinator,
    )
    from .application.scenario_command_context import ScenarioCommandContextRegistry
    from .application.scenario_light_priority import LightAutomationPriority
    from .application.light_safety_obligations import LightSafetyObligations
    from .application.scenarios import ScenarioCatalog


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
        *,
        hass: HomeAssistant | None = None,
        catalog: ScenarioCatalog | None = None,
        light_priority: LightAutomationPriority | None = None,
        light_safety_obligations: LightSafetyObligations | None = None,
        authority_lock: asyncio.Lock | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._command_contexts = command_contexts
        self._entity_ids = frozenset(entity_ids)
        self._hass = hass
        self._catalog = catalog
        self._light_priority = light_priority
        self._light_safety_obligations = light_safety_obligations
        self._authority_lock = authority_lock

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
        ):
            return
        is_light = not entity_id.startswith(("binary_sensor.", "sensor."))
        state_unchanged = getattr(old_state, "state", None) == getattr(
            new_state, "state", None
        )
        if is_light:
            if state_unchanged or not _is_fresh_light_transition(
                old_state, new_state
            ):
                return
        elif state_unchanged and trusted_presence_sensor_evidence(
            new_state, datetime.now(timezone.utc)
        ) is not None:
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
        is_manual = is_light and attribution is None
        state = getattr(new_state, "state", None)
        if is_manual and state == "off":
            await self._coordinator.async_note_state_transition(
                entity_id, old_state, new_state, None
            )
            try:
                await self._async_note_manual_off(entity_id)
            except Exception:
                self._coordinator.mark_unhealthy()
                raise
            return
        if is_manual and state == "on":
            try:
                await self._async_note_manual_on(entity_id)
            except Exception:
                self._coordinator.mark_unhealthy()
                raise
            await self._coordinator.async_note_state_transition(
                entity_id, old_state, new_state, None
            )
            return
        await self._coordinator.async_note_state_transition(
            entity_id, old_state, new_state, attribution
        )

    async def _async_note_manual_off(self, entity_id: str) -> None:
        if self._light_priority is None and self._light_safety_obligations is None:
            return
        target_id = self._target_id(entity_id)
        async with self._required_authority_lock():
            if self._light_priority is not None:
                await self._light_priority._async_clear_ownership_unlocked(entity_id)
            if target_id is not None and self._light_safety_obligations is not None:
                await self._light_safety_obligations.async_cancel(target_id)

    async def _async_note_manual_on(self, entity_id: str) -> None:
        if self._light_priority is None and self._light_safety_obligations is None:
            return
        target_id = self._target_id(entity_id)
        async with self._required_authority_lock():
            if (
                self._light_priority is not None
                and (
                    target_id is None
                    or self._catalog is None
                    or self._hass is None
                )
            ):
                raise RuntimeError("manual light ownership target is unavailable")
            if (
                target_id is not None
                and self._light_priority is not None
                and self._catalog is not None
                and self._hass is not None
            ):
                await self._light_priority._async_begin_direct_action_unlocked(
                    target_id, "turn_on", self._catalog, self._hass
                )
            if target_id is not None and self._light_safety_obligations is not None:
                await self._light_safety_obligations.async_cancel(target_id)

    def _required_authority_lock(self) -> asyncio.Lock:
        if self._authority_lock is None:
            raise RuntimeError("light authority lock is unavailable")
        return self._authority_lock

    def _target_id(self, entity_id: str) -> str | None:
        devices = getattr(self._catalog, "devices", {})
        if not isinstance(devices, Mapping):
            return None
        return next(
            (
                target_id
                for target_id, device in devices.items()
                if getattr(device, "entity_id", None) == entity_id
            ),
            None,
        )


class _ManualLightOffProtectionSubscription:
    """Keep one exact Home Assistant state subscription current."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ManualLightOffProtectionCoordinator,
        command_contexts: ScenarioCommandContextRegistry | None,
        catalog: ScenarioCatalog | None,
        light_priority: LightAutomationPriority | None,
        light_safety_obligations: LightSafetyObligations | None,
        authority_lock: asyncio.Lock | None,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._command_contexts = command_contexts
        self._catalog = catalog
        self._light_priority = light_priority
        self._light_safety_obligations = light_safety_obligations
        self._authority_lock = authority_lock
        self._entity_ids = frozenset()
        self._unsubscribe: Any = None

    async def async_refresh(self) -> None:
        entity_ids = configured_manual_light_off_protection_entities(
            self._coordinator
        )
        if entity_ids == self._entity_ids:
            return
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._entity_ids = entity_ids
        if not entity_ids:
            return
        listener = ManualLightOffProtectionEventListener(
            self._coordinator,
            self._command_contexts,
            entity_ids,
            hass=self._hass,
            catalog=self._catalog,
            light_priority=self._light_priority,
            light_safety_obligations=self._light_safety_obligations,
            authority_lock=self._authority_lock,
        )
        self._unsubscribe = _async_track_state_change_event(
            self._hass, entity_ids, listener.async_handle
        )

    def schedule_refresh(self) -> None:
        create_task = getattr(self._hass, "async_create_task", None)
        coroutine = self.async_refresh()
        if callable(create_task):
            create_task(coroutine)
        else:
            import asyncio  # noqa: PLC0415

            asyncio.create_task(coroutine)

    def cancel(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


def configured_manual_light_off_protection_entities(
    coordinator: ManualLightOffProtectionCoordinator,
) -> frozenset[str]:
    """Return the exact configured protected lights and presence sensors."""

    event_entity_ids = getattr(coordinator, "event_entity_ids", None)
    if callable(event_entity_ids):
        return frozenset(event_entity_ids())
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
    catalog: ScenarioCatalog | None = None,
    light_priority: LightAutomationPriority | None = None,
    light_safety_obligations: LightSafetyObligations | None = None,
    authority_lock: asyncio.Lock | None = None,
) -> None:
    """Subscribe exactly configured entities to the state-event adapter."""

    subscription = _ManualLightOffProtectionSubscription(
        hass,
        coordinator,
        command_contexts,
        catalog,
        light_priority,
        light_safety_obligations,
        authority_lock,
    )
    await subscription.async_refresh()
    add_listener = getattr(coordinator, "add_event_entity_listener", None)
    if callable(add_listener):
        entry.async_on_unload(add_listener(subscription.schedule_refresh))
    entry.async_on_unload(subscription.cancel)


def _is_fresh_light_transition(old_state: object, new_state: object) -> bool:
    """Reject restored, cached, unavailable and stale light evidence."""

    if (
        getattr(old_state, "state", None) not in {"on", "off"}
        or getattr(new_state, "state", None) not in {"on", "off"}
    ):
        return False
    attributes = getattr(new_state, "attributes", {})
    if (
        getattr(new_state, "assumed_state", False) is True
        or getattr(new_state, "is_assumed_state", False) is True
        or isinstance(attributes, Mapping)
        and (
            attributes.get("assumed_state") is True
            or attributes.get("restored") is True
            or attributes.get("cached") is True
            or attributes.get("cache") is True
            or attributes.get("evidence_source") in {"restore", "cache"}
        )
    ):
        return False
    observed = getattr(new_state, "last_changed", None) or getattr(
        new_state, "last_updated", None
    )
    if not isinstance(observed, datetime):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - observed).total_seconds()
    return 0 <= age_seconds <= 300
