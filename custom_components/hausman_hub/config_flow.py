"""Home Assistant form adapter for the safe HausMan Hub modes.

The forms intentionally offer no area, device, entity, token, route, proxy, or
direct-execution field. The initial selector chooses an approved mode. Later
settings may only keep or close the already-approved optional local page.
They may also keep the established five-minute count refresh or slow it to one
of two fixed choices.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .application.configuration import (
    ConfigurationViolation,
    LOCAL_SUMMARY_ENABLED_DEFAULT,
    LOCAL_SUMMARY_ENABLED_FIELD,
    MODE_FIELD,
    SUMMARY_UPDATE_INTERVAL_FIELD,
    create_initial_entry,
    create_options,
    effective_configuration,
)
from .const import DOMAIN, ENTRY_TITLE, ENTRY_UNIQUE_ID
from .domain.configuration import (
    APPROVED_MODES,
    APPROVED_SUMMARY_UPDATE_INTERVALS,
    READ_ONLY_MODE,
    SUMMARY_UPDATE_INTERVAL_DEFAULT,
)


MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[SelectOptionDict(value=mode, label=mode) for mode in APPROVED_MODES],
        translation_key="mode",
    )
)
SUMMARY_UPDATE_INTERVAL_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=interval, label=interval)
            for interval in APPROVED_SUMMARY_UPDATE_INTERVALS
        ],
        translation_key="summary_update_interval",
    )
)


class StrictBooleanSelector(BooleanSelector):
    """Keep the optional page choice exact instead of coercing a truth-like value."""

    selector_type = "boolean"

    def __call__(self, value: object) -> bool:
        """Accept only the two actual boolean values at the form boundary."""

        if type(value) is not bool:
            raise vol.Invalid("local summary setting must be true or false")
        return value


def _mode_schema(default: str) -> vol.Schema:
    return vol.Schema({vol.Required(MODE_FIELD, default=default): MODE_SELECTOR})


def _options_schema(
    mode_default: str,
    local_summary_enabled_default: bool,
    summary_update_interval_default: str,
) -> vol.Schema:
    """Show only the mode, optional page, and fixed safe refresh choices."""

    return vol.Schema(
        {
            vol.Required(MODE_FIELD, default=mode_default): MODE_SELECTOR,
            vol.Required(
                LOCAL_SUMMARY_ENABLED_FIELD,
                default=local_summary_enabled_default,
            ): StrictBooleanSelector(),
            vol.Required(
                SUMMARY_UPDATE_INTERVAL_FIELD,
                default=summary_update_interval_default,
            ): SUMMARY_UPDATE_INTERVAL_SELECTOR,
        }
    )


def _safe_mode_default(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> str:
    """Return a safe form default only when all saved settings are safe."""

    try:
        return effective_configuration(entry_data, options).mode
    except ConfigurationViolation:
        return READ_ONLY_MODE


def _safe_local_summary_default(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> bool:
    """Keep an invalid saved setting from changing the visible page choice."""

    try:
        return effective_configuration(entry_data, options).local_summary_enabled
    except ConfigurationViolation:
        return LOCAL_SUMMARY_ENABLED_DEFAULT


def _safe_summary_update_interval_default(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> str:
    """Keep unsafe saved settings from selecting a refresh interval."""

    try:
        return effective_configuration(entry_data, options).summary_update_interval
    except ConfigurationViolation:
        return SUMMARY_UPDATE_INTERVAL_DEFAULT


def _option_error_field(user_input: Mapping[str, Any]) -> str:
    """Point a rejected form value at the field that can safely explain it."""

    if user_input.get(MODE_FIELD) not in APPROVED_MODES:
        return MODE_FIELD
    if (
        LOCAL_SUMMARY_ENABLED_FIELD in user_input
        and type(user_input[LOCAL_SUMMARY_ENABLED_FIELD]) is not bool
    ):
        return LOCAL_SUMMARY_ENABLED_FIELD
    return SUMMARY_UPDATE_INTERVAL_FIELD


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
    """Edit only the safe mode, optional page, and count refresh interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Keep future options within the same read-only boundary."""

        mode_default = _safe_mode_default(self.config_entry.data, self.config_entry.options)
        local_summary_enabled_default = _safe_local_summary_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        summary_update_interval_default = _safe_summary_update_interval_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                options = create_options(
                    user_input.get(MODE_FIELD),
                    user_input.get(
                        LOCAL_SUMMARY_ENABLED_FIELD,
                        local_summary_enabled_default,
                    ),
                    user_input.get(
                        SUMMARY_UPDATE_INTERVAL_FIELD,
                        summary_update_interval_default,
                    ),
                )
            except ConfigurationViolation:
                error_field = _option_error_field(user_input)
                if error_field == MODE_FIELD:
                    errors[error_field] = "unsafe_mode"
                elif error_field == LOCAL_SUMMARY_ENABLED_FIELD:
                    errors[error_field] = "unsafe_local_summary_setting"
                else:
                    errors[error_field] = "unsafe_summary_update_interval"
            else:
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(
                mode_default,
                local_summary_enabled_default,
                summary_update_interval_default,
            ),
            errors=errors,
        )
