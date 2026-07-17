"""Home Assistant form adapter for observation and typed rollout options.

The initial selector still chooses only an approved observation mode. Later
settings may explicitly arm one canary switch for one ``input_boolean`` helper
or configure the separate private Climate API bridge as disabled, shadow, or a
one-room canary. No generic service, token, route, proxy, or execution field is
accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .application.configuration import (
    CANARY_CONTROL_ENABLED_DEFAULT,
    CANARY_CONTROL_ENABLED_FIELD,
    CANARY_CONTROL_TARGET_FIELD,
    CLIMATE_BRIDGE_MODE_DEFAULT,
    CLIMATE_BRIDGE_MODE_FIELD,
    CLIMATE_BRIDGE_TARGET_FIELD,
    CLIMATE_CANARY_ROOM_ID_FIELD,
    ConfigurationViolation,
    LOCAL_SUMMARY_ENABLED_DEFAULT,
    LOCAL_SUMMARY_ENABLED_FIELD,
    MODE_FIELD,
    SUMMARY_UPDATE_INTERVAL_FIELD,
    create_initial_entry,
    create_options,
    effective_configuration,
)
from .application.climate_import import (
    ClimateImportSnapshot,
    ImportedClimateDevice,
)
from .const import DOMAIN, ENTRY_TITLE, ENTRY_UNIQUE_ID
from .domain.configuration import (
    APPROVED_MODES,
    APPROVED_SUMMARY_UPDATE_INTERVALS,
    READ_ONLY_MODE,
    SUMMARY_UPDATE_INTERVAL_DEFAULT,
)
from .domain.climate import ClimateModelViolation, ClimateRoom
from .domain.control import (
    INPUT_BOOLEAN_DOMAIN,
    UnsafeCanaryTargetError,
    canary_control_target,
)
from .domain.climate_bridge import (
    ClimateBridgeMode,
    UnsafeClimateBridgeTarget,
    climate_bridge_target,
)


OPTIONS_NEXT_STEP_FIELD = "next_step"
OPTIONS_NEXT_STEP_DEFAULT = "save_settings"
CLIMATE_REGISTRY_JSON_FIELD = "climate_registry_json"
CLIMATE_REGISTRY_CONFIRM_FIELD = "confirm_registry_save"
CLIMATE_REGISTRY_ACTION_FIELD = "climate_registry_action"
CLIMATE_ROOM_ID_FIELD = "climate_room_id"
CLIMATE_ROOM_NAME_FIELD = "climate_room_name"
CLIMATE_DEVICE_ID_FIELD = "climate_device_id"
CLIMATE_DEVICE_NAME_FIELD = "climate_device_name"
CLIMATE_DEVICE_ROOM_FIELD = "climate_device_room"
CLIMATE_DEVICE_KIND_FIELD = "climate_device_kind"
CLIMATE_DEVICE_SOURCE_FIELD = "climate_device_source_id"
CLIMATE_DEVICE_SCOPE_FIELD = "climate_device_control_scope"
CLIMATE_DEVICE_OWNER_FIELD = "climate_device_control_owner"
CLIMATE_DEVICE_CAPABILITIES_FIELD = "climate_device_capabilities"
CLIMATE_DEVICE_CONTROL_ENTITY_FIELD = "climate_device_control_entity"
CLIMATE_SHADOW_CANDIDATE_ROOM_FIELD = "climate_shadow_candidate_room"
CLIMATE_SHADOW_EVIDENCE_CLOSE_FIELD = "close_shadow_evidence"
CLIMATE_IMPORT_CANDIDATE_FIELD = "climate_import_candidate"
MAX_CLIMATE_REGISTRY_FORM_BYTES = 16 * 1024


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
CANARY_CONTROL_TARGET_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain=INPUT_BOOLEAN_DOMAIN, multiple=False)
)
CLIMATE_BRIDGE_MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=mode.value, label=mode.value)
            for mode in ClimateBridgeMode
        ],
        translation_key="climate_bridge_mode",
    )
)
OPTIONS_NEXT_STEP_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value="save_settings", label="save_settings"),
            SelectOptionDict(
                value="manage_climate_registry",
                label="manage_climate_registry",
            ),
        ],
        translation_key="next_step",
    )
)
CLIMATE_REGISTRY_JSON_SELECTOR = TextSelector(
    TextSelectorConfig(multiline=True, type=TextSelectorType.TEXT)
)
CLIMATE_REGISTRY_ACTION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=value, label=value)
            for value in (
                "import_candidate",
                "add_room",
                "add_device",
                "review_shadow_evidence",
                "review_registry",
                "advanced_json",
                "reset_registry",
            )
        ],
        translation_key="climate_registry_action",
    )
)
CLIMATE_DEVICE_KIND_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=value, label=value)
            for value in (
                "air_conditioner",
                "radiator_thermostat",
                "humidifier",
                "floor_heating",
                "temperature_sensor",
                "humidity_sensor",
            )
        ],
        translation_key="climate_device_kind",
    )
)
CLIMATE_DEVICE_SCOPE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=value, label=value)
            for value in ("observed", "canary", "managed")
        ],
        translation_key="climate_device_control_scope",
    )
)
CLIMATE_DEVICE_OWNER_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=value, label=value)
            for value in ("climate_core", "manual", "observed")
        ],
        translation_key="climate_device_control_owner",
    )
)
CLIMATE_DEVICE_CAPABILITIES_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=value, label=value)
            for value in (
                "power",
                "target_temperature",
                "target_humidity",
                "hvac_mode",
                "fan_mode",
                "auto_manual",
                "target_strategy",
                "cooldown",
                "physical_feedback",
            )
        ],
        multiple=True,
        translation_key="climate_device_capabilities",
    )
)


class StrictBooleanSelector(BooleanSelector):
    """Keep boolean choices exact instead of coercing truth-like values."""

    selector_type = "boolean"

    def __call__(self, value: object) -> bool:
        """Accept only the two actual boolean values at the form boundary."""

        if type(value) is not bool:
            raise vol.Invalid("setting must be true or false")
        return value


def _mode_schema(default: str) -> vol.Schema:
    return vol.Schema({vol.Required(MODE_FIELD, default=default): MODE_SELECTOR})


def _options_schema(
    mode_default: str,
    local_summary_enabled_default: bool,
    summary_update_interval_default: str,
    canary_control_enabled_default: bool,
    canary_control_target_default: str | None,
    climate_bridge_mode_default: str,
    climate_bridge_target_default: str | None,
    climate_canary_room_id_default: str | None,
) -> vol.Schema:
    """Show observation choices and the one narrow control-canary target."""

    fields: dict[vol.Marker, object] = {
        vol.Required(MODE_FIELD, default=mode_default): MODE_SELECTOR,
        vol.Required(
            LOCAL_SUMMARY_ENABLED_FIELD,
            default=local_summary_enabled_default,
        ): StrictBooleanSelector(),
        vol.Required(
            SUMMARY_UPDATE_INTERVAL_FIELD,
            default=summary_update_interval_default,
        ): SUMMARY_UPDATE_INTERVAL_SELECTOR,
        vol.Required(
            CANARY_CONTROL_ENABLED_FIELD,
            default=canary_control_enabled_default,
        ): StrictBooleanSelector(),
        vol.Required(
            CLIMATE_BRIDGE_MODE_FIELD,
            default=climate_bridge_mode_default,
        ): CLIMATE_BRIDGE_MODE_SELECTOR,
    }
    target_field = (
        vol.Optional(
            CANARY_CONTROL_TARGET_FIELD,
            default=canary_control_target_default,
        )
        if canary_control_target_default is not None
        else vol.Optional(CANARY_CONTROL_TARGET_FIELD)
    )
    fields[target_field] = CANARY_CONTROL_TARGET_SELECTOR
    bridge_target_field = (
        vol.Optional(
            CLIMATE_BRIDGE_TARGET_FIELD,
            default=climate_bridge_target_default,
        )
        if climate_bridge_target_default is not None
        else vol.Optional(CLIMATE_BRIDGE_TARGET_FIELD)
    )
    fields[bridge_target_field] = str
    canary_room_field = (
        vol.Optional(
            CLIMATE_CANARY_ROOM_ID_FIELD,
            default=climate_canary_room_id_default,
        )
        if climate_canary_room_id_default is not None
        else vol.Optional(CLIMATE_CANARY_ROOM_ID_FIELD)
    )
    fields[canary_room_field] = str
    fields[
        vol.Required(
            OPTIONS_NEXT_STEP_FIELD,
            default=OPTIONS_NEXT_STEP_DEFAULT,
        )
    ] = OPTIONS_NEXT_STEP_SELECTOR
    return vol.Schema(fields)


def _climate_registry_json_schema(default: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CLIMATE_REGISTRY_JSON_FIELD,
                default=default,
            ): CLIMATE_REGISTRY_JSON_SELECTOR
        }
    )


def _climate_registry_menu_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CLIMATE_REGISTRY_ACTION_FIELD,
                default="import_candidate",
            ): CLIMATE_REGISTRY_ACTION_SELECTOR
        }
    )


def _climate_import_candidate_schema(
    candidates: list[dict[str, str]],
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CLIMATE_IMPORT_CANDIDATE_FIELD): SelectSelector(
                SelectSelectorConfig(options=candidates)
            )
        }
    )


def _climate_import_device_schema(candidate: ImportedClimateDevice) -> vol.Schema:
    from .application.climate_registry_import import candidate_control_domain

    kinds = [value.value for value in candidate.suggested_kinds]
    fields: dict[vol.Marker, object] = {
        vol.Required(CLIMATE_DEVICE_ID_FIELD): str,
        vol.Required(CLIMATE_DEVICE_NAME_FIELD, default=candidate.name): str,
        vol.Required(CLIMATE_DEVICE_KIND_FIELD, default=kinds[0]): SelectSelector(
            SelectSelectorConfig(
                options=[SelectOptionDict(value=value, label=value) for value in kinds],
                translation_key="climate_device_kind",
            )
        ),
        vol.Required(
            CLIMATE_DEVICE_SCOPE_FIELD,
            default="observed",
        ): CLIMATE_DEVICE_SCOPE_SELECTOR,
        vol.Required(
            CLIMATE_DEVICE_OWNER_FIELD,
            default="observed",
        ): CLIMATE_DEVICE_OWNER_SELECTOR,
    }
    domains: set[str] = set()
    for value in kinds:
        domain = candidate_control_domain(value)
        if isinstance(domain, str):
            domains.add(domain)
        elif isinstance(domain, tuple):
            domains.update(domain)
    if domains:
        if candidate.domain in domains:
            selector_domain: str | list[str] = candidate.domain
        else:
            selector_domain = (
                next(iter(domains)) if len(domains) == 1 else sorted(domains)
            )
        fields[vol.Required(CLIMATE_DEVICE_CONTROL_ENTITY_FIELD)] = EntitySelector(
            EntitySelectorConfig(domain=selector_domain, multiple=False)
        )
    return vol.Schema(fields)


def _climate_room_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CLIMATE_ROOM_ID_FIELD): str,
            vol.Required(CLIMATE_ROOM_NAME_FIELD): str,
        }
    )


def _climate_device_schema(rooms: list[dict[str, str]]) -> vol.Schema:
    room_selector = SelectSelector(SelectSelectorConfig(options=rooms))
    return vol.Schema(
        {
            vol.Required(CLIMATE_DEVICE_ID_FIELD): str,
            vol.Required(CLIMATE_DEVICE_NAME_FIELD): str,
            vol.Required(CLIMATE_DEVICE_ROOM_FIELD): room_selector,
            vol.Required(CLIMATE_DEVICE_KIND_FIELD): CLIMATE_DEVICE_KIND_SELECTOR,
            vol.Required(CLIMATE_DEVICE_SOURCE_FIELD): str,
            vol.Required(
                CLIMATE_DEVICE_SCOPE_FIELD,
                default="observed",
            ): CLIMATE_DEVICE_SCOPE_SELECTOR,
            vol.Required(
                CLIMATE_DEVICE_OWNER_FIELD,
                default="observed",
            ): CLIMATE_DEVICE_OWNER_SELECTOR,
            vol.Required(
                CLIMATE_DEVICE_CAPABILITIES_FIELD,
                default=[],
            ): CLIMATE_DEVICE_CAPABILITIES_SELECTOR,
            vol.Optional(CLIMATE_DEVICE_CONTROL_ENTITY_FIELD): str,
        }
    )


def _climate_registry_confirm_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CLIMATE_REGISTRY_CONFIRM_FIELD,
                default=False,
            ): StrictBooleanSelector()
        }
    )


def _climate_shadow_candidate_schema(rooms: list[dict[str, str]]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CLIMATE_SHADOW_CANDIDATE_ROOM_FIELD): SelectSelector(
                SelectSelectorConfig(options=rooms)
            )
        }
    )


def _climate_shadow_evidence_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CLIMATE_SHADOW_EVIDENCE_CLOSE_FIELD,
                default=False,
            ): StrictBooleanSelector()
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


def _safe_canary_control_enabled_default(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> bool:
    """Keep damaged saved settings from visibly arming the canary."""

    try:
        return effective_configuration(entry_data, options).canary_control_enabled
    except ConfigurationViolation:
        return CANARY_CONTROL_ENABLED_DEFAULT


def _safe_canary_control_target_default(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> str | None:
    """Expose a target default only for one completely valid armed canary."""

    try:
        target = effective_configuration(entry_data, options).canary_control_target
    except ConfigurationViolation:
        return None
    return None if target is None else target.entity_id


def _safe_climate_bridge_defaults(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> tuple[str, str | None, str | None]:
    """Return only completely validated bridge defaults to the options form."""

    try:
        configuration = effective_configuration(entry_data, options)
    except ConfigurationViolation:
        return CLIMATE_BRIDGE_MODE_DEFAULT, None, None
    target = configuration.climate_bridge_target
    return (
        configuration.climate_bridge_mode.value,
        None if target is None else target.origin,
        configuration.climate_canary_room_id,
    )


def _option_error_field(user_input: Mapping[str, Any]) -> str:
    """Point a rejected form value at the field that can safely explain it."""

    if user_input.get(MODE_FIELD) not in APPROVED_MODES:
        return MODE_FIELD
    if (
        LOCAL_SUMMARY_ENABLED_FIELD in user_input
        and type(user_input[LOCAL_SUMMARY_ENABLED_FIELD]) is not bool
    ):
        return LOCAL_SUMMARY_ENABLED_FIELD
    if (
        SUMMARY_UPDATE_INTERVAL_FIELD in user_input
        and user_input[SUMMARY_UPDATE_INTERVAL_FIELD]
        not in APPROVED_SUMMARY_UPDATE_INTERVALS
    ):
        return SUMMARY_UPDATE_INTERVAL_FIELD
    if (
        CANARY_CONTROL_ENABLED_FIELD in user_input
        and type(user_input[CANARY_CONTROL_ENABLED_FIELD]) is not bool
    ):
        return CANARY_CONTROL_ENABLED_FIELD
    if user_input.get(CANARY_CONTROL_ENABLED_FIELD) is True:
        try:
            canary_control_target(user_input.get(CANARY_CONTROL_TARGET_FIELD))
        except UnsafeCanaryTargetError:
            return CANARY_CONTROL_TARGET_FIELD
    bridge_mode = user_input.get(CLIMATE_BRIDGE_MODE_FIELD, CLIMATE_BRIDGE_MODE_DEFAULT)
    if bridge_mode not in {mode.value for mode in ClimateBridgeMode}:
        return CLIMATE_BRIDGE_MODE_FIELD
    if bridge_mode != ClimateBridgeMode.DISABLED.value:
        try:
            climate_bridge_target(user_input.get(CLIMATE_BRIDGE_TARGET_FIELD))
        except UnsafeClimateBridgeTarget:
            return CLIMATE_BRIDGE_TARGET_FIELD
    if bridge_mode == ClimateBridgeMode.CANARY.value:
        try:
            ClimateRoom(
                user_input.get(CLIMATE_CANARY_ROOM_ID_FIELD),  # type: ignore[arg-type]
                "Temporary",
            )
        except ClimateModelViolation:
            return CLIMATE_CANARY_ROOM_ID_FIELD
    return CANARY_CONTROL_TARGET_FIELD


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
    """Edit observation settings and the opt-in input-boolean canary."""

    _registry_draft: object | None = None
    _registry_preview: Mapping[str, Any] | None = None
    _shadow_evidence: Mapping[str, Any] | None = None
    _import_snapshot: ClimateImportSnapshot | None = None
    _import_candidates: dict[str, ImportedClimateDevice] | None = None
    _selected_import_source_id: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Keep future options within the validated canary boundary."""

        mode_default = _safe_mode_default(self.config_entry.data, self.config_entry.options)
        local_summary_enabled_default = _safe_local_summary_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        summary_update_interval_default = _safe_summary_update_interval_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        canary_control_enabled_default = _safe_canary_control_enabled_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        canary_control_target_default = _safe_canary_control_target_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        (
            climate_bridge_mode_default,
            climate_bridge_target_default,
            climate_canary_room_id_default,
        ) = _safe_climate_bridge_defaults(
            self.config_entry.data,
            self.config_entry.options,
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(OPTIONS_NEXT_STEP_FIELD) == "manage_climate_registry":
                return await self.async_step_climate_registry()
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
                    user_input.get(
                        CANARY_CONTROL_ENABLED_FIELD,
                        canary_control_enabled_default,
                    ),
                    user_input.get(
                        CANARY_CONTROL_TARGET_FIELD,
                        canary_control_target_default,
                    ),
                    user_input.get(
                        CLIMATE_BRIDGE_MODE_FIELD,
                        climate_bridge_mode_default,
                    ),
                    user_input.get(
                        CLIMATE_BRIDGE_TARGET_FIELD,
                        climate_bridge_target_default,
                    ),
                    user_input.get(
                        CLIMATE_CANARY_ROOM_ID_FIELD,
                        climate_canary_room_id_default,
                    ),
                )
            except ConfigurationViolation:
                error_field = _option_error_field(user_input)
                if error_field == MODE_FIELD:
                    errors[error_field] = "unsafe_mode"
                elif error_field == LOCAL_SUMMARY_ENABLED_FIELD:
                    errors[error_field] = "unsafe_local_summary_setting"
                elif error_field == SUMMARY_UPDATE_INTERVAL_FIELD:
                    errors[error_field] = "unsafe_summary_update_interval"
                elif error_field == CANARY_CONTROL_ENABLED_FIELD:
                    errors[error_field] = "unsafe_canary_control_setting"
                elif error_field == CLIMATE_BRIDGE_MODE_FIELD:
                    errors[error_field] = "unsafe_climate_bridge_mode"
                elif error_field == CLIMATE_BRIDGE_TARGET_FIELD:
                    errors[error_field] = "unsafe_climate_bridge_target"
                elif error_field == CLIMATE_CANARY_ROOM_ID_FIELD:
                    errors[error_field] = "unsafe_climate_canary_room"
                else:
                    errors[error_field] = "unsafe_canary_control_target"
            else:
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(
                mode_default,
                local_summary_enabled_default,
                summary_update_interval_default,
                canary_control_enabled_default,
                canary_control_target_default,
                climate_bridge_mode_default,
                climate_bridge_target_default,
                climate_canary_room_id_default,
            ),
            errors=errors,
        )

    async def async_step_climate_registry(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Build a private registry through explicit room/device steps."""

        runtime = self._climate_runtime()
        errors: dict[str, str] = {}
        if runtime is None:
            errors["base"] = "climate_runtime_unavailable"
        elif self._registry_draft is None:
            try:
                self._registry_draft = await runtime.async_registry_payload()
            except Exception:
                errors["base"] = "climate_runtime_unavailable"

        if user_input is not None and runtime is not None:
            action = user_input.get(CLIMATE_REGISTRY_ACTION_FIELD)
            if action == "import_candidate":
                return await self.async_step_climate_import_candidate()
            if action == "add_room":
                return await self.async_step_climate_registry_room()
            if action == "add_device":
                return await self.async_step_climate_registry_device()
            if action == "review_shadow_evidence":
                return await self.async_step_climate_shadow_candidate()
            if action == "advanced_json":
                return await self.async_step_climate_registry_json()
            if action == "reset_registry":
                self._registry_draft = {"version": 1, "rooms": [], "devices": []}
                return await self._async_preview_registry_draft(runtime)
            if action == "review_registry":
                return await self._async_preview_registry_draft(runtime)
            errors[CLIMATE_REGISTRY_ACTION_FIELD] = "invalid_climate_registry_action"

        return self.async_show_form(
            step_id="climate_registry",
            data_schema=_climate_registry_menu_schema(),
            errors=errors,
        )

    async def async_step_climate_import_candidate(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Select one fresh candidate by an ephemeral non-private form token."""

        runtime = self._climate_runtime()
        errors: dict[str, str] = {}
        candidates: list[dict[str, str]] = []
        if runtime is None:
            errors["base"] = "climate_runtime_unavailable"
        elif user_input is not None:
            candidates = self._import_candidate_options()
            token = user_input.get(CLIMATE_IMPORT_CANDIDATE_FIELD)
            selected = (self._import_candidates or {}).get(token)
            if selected is None:
                errors[CLIMATE_IMPORT_CANDIDATE_FIELD] = "invalid_climate_candidate"
            else:
                self._selected_import_source_id = selected.source_id
                return await self.async_step_climate_import_device()
        else:
            try:
                snapshot = await runtime.async_registry_import_snapshot()
                if snapshot.runtime_fresh is not True:
                    raise ValueError("stale import snapshot")
                candidates = self._set_import_candidates(snapshot)
            except Exception:
                errors["base"] = "climate_import_unavailable"
            else:
                if not candidates:
                    errors["base"] = "climate_import_no_candidates"
        if not candidates:
            candidates = [SelectOptionDict(value="missing", label="missing")]
        return self.async_show_form(
            step_id="climate_import_candidate",
            data_schema=_climate_import_candidate_schema(candidates),
            errors=errors,
        )

    async def async_step_climate_import_device(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Configure public fields while HASC supplies the selected private binding."""

        source_id = self._selected_import_source_id
        snapshot = self._import_snapshot
        selected = snapshot.device(source_id) if snapshot is not None else None
        if selected is None or not selected.suggested_kinds:
            return await self.async_step_climate_import_candidate()
        errors: dict[str, str] = {}
        if user_input is not None:
            runtime = self._climate_runtime()
            if runtime is None:
                errors["base"] = "climate_runtime_unavailable"
            else:
                try:
                    current = await runtime.async_registry_import_snapshot()
                    current_selected = current.device(source_id)
                    from .application.climate_registry import (
                        registry_from_payload,
                        registry_to_payload,
                    )
                    from .application.climate_registry_import import (
                        add_import_candidate_to_registry,
                        import_candidate_is_unchanged,
                    )

                    if (
                        current_selected is None
                        or not import_candidate_is_unchanged(
                            snapshot,
                            current,
                            source_id,
                        )
                    ):
                        raise ValueError("import candidate changed")
                    registry = registry_from_payload(self._copy_registry_draft())
                    imported = add_import_candidate_to_registry(
                        registry,
                        current,
                        source_id=source_id,
                        device_id=user_input.get(CLIMATE_DEVICE_ID_FIELD),
                        device_name=user_input.get(CLIMATE_DEVICE_NAME_FIELD),
                        kind=user_input.get(CLIMATE_DEVICE_KIND_FIELD),
                        control_scope=user_input.get(CLIMATE_DEVICE_SCOPE_FIELD),
                        control_owner=user_input.get(CLIMATE_DEVICE_OWNER_FIELD),
                        control_entity_id=user_input.get(
                            CLIMATE_DEVICE_CONTROL_ENTITY_FIELD
                        ),
                    )
                except ValueError:
                    errors["base"] = "invalid_climate_candidate"
                except Exception:
                    errors["base"] = "climate_import_unavailable"
                else:
                    self._registry_draft = registry_to_payload(imported)
                    self._clear_import_selection()
                    return await self.async_step_climate_registry()
        return self.async_show_form(
            step_id="climate_import_device",
            data_schema=_climate_import_device_schema(selected),
            errors=errors,
        )

    async def async_step_climate_shadow_candidate(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Select one public HASC room for a read-only evidence result."""

        rooms = self._draft_rooms()
        if not rooms:
            return self.async_show_form(
                step_id="climate_shadow_candidate",
                data_schema=_climate_shadow_candidate_schema(
                    [SelectOptionDict(value="missing", label="missing")]
                ),
                errors={"base": "climate_registry_needs_room"},
            )
        errors: dict[str, str] = {}
        if user_input is not None:
            runtime = self._climate_runtime()
            if runtime is None:
                errors["base"] = "climate_runtime_unavailable"
            else:
                try:
                    evidence = await runtime.async_shadow_evidence(
                        {
                            "room_id": user_input.get(
                                CLIMATE_SHADOW_CANDIDATE_ROOM_FIELD
                            )
                        }
                    )
                except Exception:
                    errors["base"] = "climate_shadow_evidence_unavailable"
                else:
                    self._shadow_evidence = evidence
                    return await self.async_step_climate_shadow_evidence()
        return self.async_show_form(
            step_id="climate_shadow_candidate",
            data_schema=_climate_shadow_candidate_schema(rooms),
            errors=errors,
        )

    async def async_step_climate_shadow_evidence(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Show the redacted evidence result without changing settings."""

        if self._shadow_evidence is None:
            return await self.async_step_climate_shadow_candidate()
        if (
            user_input is not None
            and user_input.get(CLIMATE_SHADOW_EVIDENCE_CLOSE_FIELD) is True
        ):
            self._shadow_evidence = None
            return await self.async_step_climate_registry()
        return self.async_show_form(
            step_id="climate_shadow_evidence",
            data_schema=_climate_shadow_evidence_schema(),
            errors={},
            description_placeholders=self._shadow_evidence_placeholders(),
        )

    async def async_step_climate_registry_room(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Add or replace one logical room without requiring JSON editing."""

        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = self._copy_registry_draft()
            rooms = candidate.get("rooms")
            if not isinstance(rooms, list):
                errors["base"] = "invalid_climate_registry"
            else:
                room = {
                    "id": user_input.get(CLIMATE_ROOM_ID_FIELD),
                    "name": user_input.get(CLIMATE_ROOM_NAME_FIELD),
                }
                candidate["rooms"] = [
                    value
                    for value in rooms
                    if not isinstance(value, Mapping) or value.get("id") != room["id"]
                ] + [room]
                try:
                    self._validate_registry_draft(candidate)
                except Exception:
                    errors["base"] = "invalid_climate_room"
                else:
                    self._registry_draft = candidate
                    return await self.async_step_climate_registry()
        return self.async_show_form(
            step_id="climate_registry_room",
            data_schema=_climate_room_schema(),
            errors=errors,
        )

    async def async_step_climate_registry_device(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Add or replace one typed logical device through native fields."""

        rooms = self._draft_rooms()
        if not rooms:
            return self.async_show_form(
                step_id="climate_registry_device",
                data_schema=_climate_device_schema(
                    [SelectOptionDict(value="missing", label="missing")]
                ),
                errors={"base": "climate_registry_needs_room"},
            )
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = self._copy_registry_draft()
            devices = candidate.get("devices")
            capabilities = user_input.get(CLIMATE_DEVICE_CAPABILITIES_FIELD)
            control_entity = user_input.get(CLIMATE_DEVICE_CONTROL_ENTITY_FIELD)
            if not isinstance(devices, list) or not isinstance(capabilities, list):
                errors["base"] = "invalid_climate_device"
            else:
                endpoints = (
                    [{"role": "control", "entity_id": control_entity}]
                    if isinstance(control_entity, str) and control_entity
                    else []
                )
                device = {
                    "id": user_input.get(CLIMATE_DEVICE_ID_FIELD),
                    "name": user_input.get(CLIMATE_DEVICE_NAME_FIELD),
                    "room_id": user_input.get(CLIMATE_DEVICE_ROOM_FIELD),
                    "kind": user_input.get(CLIMATE_DEVICE_KIND_FIELD),
                    "source_id": user_input.get(CLIMATE_DEVICE_SOURCE_FIELD),
                    "control_scope": user_input.get(CLIMATE_DEVICE_SCOPE_FIELD),
                    "control_owner": user_input.get(CLIMATE_DEVICE_OWNER_FIELD),
                    "capabilities": capabilities,
                    "endpoints": endpoints,
                }
                candidate["devices"] = [
                    value
                    for value in devices
                    if not isinstance(value, Mapping) or value.get("id") != device["id"]
                ] + [device]
                try:
                    self._validate_registry_draft(candidate)
                except Exception:
                    errors["base"] = "invalid_climate_device"
                else:
                    self._registry_draft = candidate
                    return await self.async_step_climate_registry()
        return self.async_show_form(
            step_id="climate_registry_device",
            data_schema=_climate_device_schema(rooms),
            errors=errors,
        )

    async def async_step_climate_registry_json(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Offer the complete JSON contract only as an advanced fallback."""

        runtime = self._climate_runtime()
        errors: dict[str, str] = {}
        default = json.dumps(
            self._copy_registry_draft(),
            ensure_ascii=False,
            indent=2,
        )
        if runtime is None:
            errors["base"] = "climate_runtime_unavailable"
        if user_input is not None and runtime is not None:
            raw = user_input.get(CLIMATE_REGISTRY_JSON_FIELD)
            if (
                not isinstance(raw, str)
                or not raw
                or len(raw.encode("utf-8")) > MAX_CLIMATE_REGISTRY_FORM_BYTES
            ):
                errors[CLIMATE_REGISTRY_JSON_FIELD] = "invalid_climate_registry"
            else:
                try:
                    draft = json.loads(raw)
                    self._validate_registry_draft(draft)
                except Exception:
                    errors[CLIMATE_REGISTRY_JSON_FIELD] = "invalid_climate_registry"
                else:
                    self._registry_draft = draft
                    return await self._async_preview_registry_draft(runtime)

        return self.async_show_form(
            step_id="climate_registry_json",
            data_schema=_climate_registry_json_schema(default),
            errors=errors,
        )

    async def _async_preview_registry_draft(self, runtime: Any) -> FlowResult:
        try:
            preview = await runtime.async_preview_registry(self._registry_draft)
        except Exception:
            return self.async_show_form(
                step_id="climate_registry",
                data_schema=_climate_registry_menu_schema(),
                errors={"base": "invalid_climate_registry"},
            )
        if preview.get("save_allowed") is not True:
            return self.async_show_form(
                step_id="climate_registry",
                data_schema=_climate_registry_menu_schema(),
                errors={"base": "climate_registry_locked"},
            )
        self._registry_preview = preview
        return await self.async_step_climate_registry_confirm()

    async def async_step_climate_registry_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Require a separate explicit confirmation before atomic replacement."""

        if self._registry_draft is None or self._registry_preview is None:
            return await self.async_step_climate_registry()
        if user_input is not None:
            if user_input.get(CLIMATE_REGISTRY_CONFIRM_FIELD) is not True:
                return await self.async_step_climate_registry()
            runtime = self._climate_runtime()
            if runtime is None:
                return self.async_show_form(
                    step_id="climate_registry_confirm",
                    data_schema=_climate_registry_confirm_schema(),
                    errors={"base": "climate_runtime_unavailable"},
                    description_placeholders=self._preview_placeholders(),
                )
            try:
                await runtime.async_replace_registry(self._registry_draft)
            except Exception:
                return self.async_show_form(
                    step_id="climate_registry_confirm",
                    data_schema=_climate_registry_confirm_schema(),
                    errors={"base": "invalid_climate_registry"},
                    description_placeholders=self._preview_placeholders(),
                )
            return self.async_create_entry(
                title="",
                data=dict(self.config_entry.options),
            )

        return self.async_show_form(
            step_id="climate_registry_confirm",
            data_schema=_climate_registry_confirm_schema(),
            errors={},
            description_placeholders=self._preview_placeholders(),
        )

    def _copy_registry_draft(self) -> dict[str, Any]:
        draft = self._registry_draft
        if not isinstance(draft, Mapping):
            return {"version": 1, "rooms": [], "devices": []}
        return json.loads(json.dumps(draft, ensure_ascii=False))

    def _validate_registry_draft(self, draft: object) -> None:
        from .application.climate_registry import registry_from_payload

        registry_from_payload(draft)

    def _draft_rooms(self) -> list[dict[str, str]]:
        draft = self._copy_registry_draft()
        rooms = draft.get("rooms")
        if not isinstance(rooms, list):
            return []
        return [
            SelectOptionDict(value=room["id"], label=room["name"])
            for room in rooms
            if isinstance(room, Mapping)
            and isinstance(room.get("id"), str)
            and isinstance(room.get("name"), str)
        ]

    def _set_import_candidates(
        self,
        snapshot: ClimateImportSnapshot,
    ) -> list[dict[str, str]]:
        from .application.climate_registry import registry_from_payload

        registry = registry_from_payload(self._copy_registry_draft())
        registered_sources = {device.source_id for device in registry.devices}
        candidates: dict[str, ImportedClimateDevice] = {}
        options: list[dict[str, str]] = []
        for index, candidate in enumerate(snapshot.devices, start=1):
            if (
                candidate.source_id in registered_sources
                or not candidate.suggested_kinds
            ):
                continue
            room = snapshot.room(candidate.room_id)
            if room is None:
                continue
            token = f"candidate_{index:03d}"
            candidates[token] = candidate
            options.append(
                SelectOptionDict(
                    value=token,
                    label=f"{room.name} — {candidate.name}",
                )
            )
        self._import_snapshot = snapshot
        self._import_candidates = candidates
        self._selected_import_source_id = None
        return options

    def _import_candidate_options(self) -> list[dict[str, str]]:
        snapshot = self._import_snapshot
        if snapshot is None:
            return []
        options: list[dict[str, str]] = []
        for token, candidate in (self._import_candidates or {}).items():
            room = snapshot.room(candidate.room_id)
            if room is not None:
                options.append(
                    SelectOptionDict(
                        value=token,
                        label=f"{room.name} — {candidate.name}",
                    )
                )
        return options

    def _clear_import_selection(self) -> None:
        self._import_snapshot = None
        self._import_candidates = None
        self._selected_import_source_id = None

    def _climate_runtime(self) -> Any | None:
        data = getattr(self, "hass", None)
        domain_data = getattr(data, "data", {}).get(DOMAIN)
        if not isinstance(domain_data, Mapping):
            return None
        from .application.climate_runtime import ClimateRuntime

        runtime = domain_data.get("climate_runtime")
        return runtime if isinstance(runtime, ClimateRuntime) else None

    def _preview_placeholders(self) -> dict[str, str]:
        preview = self._registry_preview or {}
        registry = preview.get("registry")
        counts = registry if isinstance(registry, Mapping) else {}
        reasons = preview.get("reasons")
        reason_values = reasons if isinstance(reasons, list) else []
        return {
            "status": str(preview.get("status", "unavailable")),
            "room_count": str(counts.get("room_count", 0)),
            "device_count": str(counts.get("device_count", 0)),
            "reasons": ", ".join(str(value) for value in reason_values) or "none",
        }

    def _shadow_evidence_placeholders(self) -> dict[str, str]:
        evidence = self._shadow_evidence or {}
        candidate = evidence.get("candidate")
        candidate_values = candidate if isinstance(candidate, Mapping) else {}
        counts = evidence.get("counts")
        count_values = counts if isinstance(counts, Mapping) else {}
        reasons = candidate_values.get("reasons")
        reason_values = reasons if isinstance(reasons, list) else []
        return {
            "room_id": str(candidate_values.get("room_id", "unknown")),
            "status": str(candidate_values.get("status", "blocked")),
            "matched": str(candidate_values.get("matched_observation_count", 0)),
            "required_matched": str(
                candidate_values.get("required_matched_observation_count", 3)
            ),
            "translated": str(candidate_values.get("translated_action_count", 0)),
            "anomalies": str(candidate_values.get("anomaly_count", 0)),
            "global_rejected": str(count_values.get("rejected", 0)),
            "reasons": ", ".join(str(value) for value in reason_values) or "none",
        }
