"""Home Assistant form adapter for the safe HausMan Hub modes.

The form intentionally offers no area, device, entity, token, route, proxy, or
direct-execution field. Its sole selector chooses a mode already approved by
the framework-independent domain policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .application.configuration import (
    ConfigurationViolation,
    MODE_FIELD,
    create_initial_entry,
    create_options,
    effective_configuration,
)
from .const import DOMAIN, ENTRY_TITLE, ENTRY_UNIQUE_ID
from .domain.configuration import APPROVED_MODES, READ_ONLY_MODE


MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[SelectOptionDict(value=mode, label=mode) for mode in APPROVED_MODES],
        translation_key="mode",
    )
)


def _mode_schema(default: str) -> vol.Schema:
    return vol.Schema({vol.Required(MODE_FIELD, default=default): MODE_SELECTOR})


def _safe_mode_default(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> str:
    """Return a safe form default only when all saved settings are safe."""

    try:
        return effective_configuration(entry_data, options).mode
    except ConfigurationViolation:
        return READ_ONLY_MODE


class HausmanHubConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single safe HausMan Hub configuration entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HausmanHubOptionsFlow:
        """Return the options flow without retaining mutable entry state."""

        return HausmanHubOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Accept only an approved non-executing mode."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = create_initial_entry(user_input.get(MODE_FIELD))
            except ConfigurationViolation:
                errors[MODE_FIELD] = "unsafe_mode"
            else:
                await self.async_set_unique_id(ENTRY_UNIQUE_ID)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=ENTRY_TITLE, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_mode_schema(READ_ONLY_MODE),
            errors=errors,
        )


class HausmanHubOptionsFlow(config_entries.OptionsFlow):
    """Edit only the safe mode of an existing entry."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Keep future options within the same read-only boundary."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                options = create_options(user_input.get(MODE_FIELD))
            except ConfigurationViolation:
                errors[MODE_FIELD] = "unsafe_mode"
            else:
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=_mode_schema(
                _safe_mode_default(self.config_entry.data, self.config_entry.options)
            ),
            errors=errors,
        )
