"""Home Assistant switch for the opt-in input-boolean control canary."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .application.configuration import ConfigurationViolation, effective_configuration
from .application.control import CanaryControlViolation, canary_control_command
from .const import DOMAIN
from .domain.configuration import SafeConfiguration
from .domain.control import INPUT_BOOLEAN_DOMAIN


CANARY_SWITCH_TRANSLATION_KEY: Final = "canary_control"
CANARY_SWITCH_ENTITY_ID: Final = f"switch.{DOMAIN}_hasc_canary_control"


def _canary_unique_id(entry_id: str) -> str:
    """Return the one stable registry key owned by a HASC entry."""

    return f"{entry_id}_{CANARY_SWITCH_TRANSLATION_KEY}"


def _remove_disabled_canary_record(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove only HASC's stale canary row after the owner disarms it."""

    registry = entity_registry.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "switch",
        DOMAIN,
        _canary_unique_id(entry.entry_id),
    )
    if entity_id is not None:
        hass.states.async_remove(entity_id)
        registry.async_remove(entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one canary switch only after exact opt-in configuration."""

    configuration = effective_configuration(entry.data, entry.options)
    target = configuration.canary_control_target
    if not configuration.canary_control_enabled or target is None:
        _remove_disabled_canary_record(hass, entry)
        return
    async_add_entities((CanaryInputBooleanSwitch(hass, entry, target.entity_id),))


class CanaryInputBooleanSwitch(SwitchEntity):
    """Mirror and control one explicitly selected Home Assistant helper."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = CANARY_SWITCH_TRANSLATION_KEY
    _attr_icon = "mdi:test-tube"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        target_entity_id: str,
    ) -> None:
        """Keep only the validated target needed by this loaded switch."""

        self.hass = hass
        self._entry = entry
        self._target_entity_id = target_entity_id
        self._attr_unique_id = _canary_unique_id(entry.entry_id)
        self.entity_id = CANARY_SWITCH_ENTITY_ID

    @property
    def available(self) -> bool:
        """Stay available only while config and target state remain exact."""

        try:
            self._current_configuration()
        except CanaryControlViolation:
            return False
        state = self.hass.states.get(self._target_entity_id)
        return state is not None and state.state in (STATE_ON, STATE_OFF)

    @property
    def is_on(self) -> bool:
        """Mirror only the selected helper's current boolean state."""

        state = self.hass.states.get(self._target_entity_id)
        return state is not None and state.state == STATE_ON

    async def async_added_to_hass(self) -> None:
        """Refresh the canary switch whenever its selected helper changes."""

        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                self._target_entity_id,
                self._async_target_state_changed,
            )
        )

    @callback
    def _async_target_state_changed(self, _: Event) -> None:
        """Publish only the mirrored on/off availability state."""

        self.async_write_ha_state()

    async def async_turn_on(self, **_: Any) -> None:
        """Request the fixed helper's standard turn-on service."""

        await self._async_set_target(True)

    async def async_turn_off(self, **_: Any) -> None:
        """Request the fixed helper's standard turn-off service."""

        await self._async_set_target(False)

    def _current_configuration(self) -> SafeConfiguration:
        """Revalidate the one saved HASC entry before every action or display."""

        entries = self.hass.config_entries.async_entries(self._entry.domain)
        if len(entries) != 1 or entries[0].entry_id != self._entry.entry_id:
            raise CanaryControlViolation("canary requires one saved HASC entry")
        try:
            configuration = effective_configuration(
                self._entry.data,
                self._entry.options,
            )
        except ConfigurationViolation as error:
            raise CanaryControlViolation("HASC configuration is unsafe") from error
        canary_control_command(
            configuration,
            self._target_entity_id,
            False,
        )
        return configuration

    async def _async_set_target(self, turn_on: bool) -> None:
        """Fail closed, then invoke only ``input_boolean.turn_on/off``."""

        try:
            configuration = self._current_configuration()
            command = canary_control_command(
                configuration,
                self._target_entity_id,
                turn_on,
            )
        except CanaryControlViolation as error:
            raise HomeAssistantError(
                "HASC canary control is no longer authorized"
            ) from error

        state = self.hass.states.get(command.target_entity_id)
        if state is None or state.state not in (STATE_ON, STATE_OFF):
            raise HomeAssistantError("HASC canary helper is unavailable")

        service = SERVICE_TURN_ON if command.turn_on else SERVICE_TURN_OFF
        if not self.hass.services.has_service(INPUT_BOOLEAN_DOMAIN, service):
            raise HomeAssistantError("HASC canary helper service is unavailable")
        await self.hass.services.async_call(
            INPUT_BOOLEAN_DOMAIN,
            service,
            {ATTR_ENTITY_ID: command.target_entity_id},
            blocking=True,
            context=self._context,
        )
        self.async_write_ha_state()
