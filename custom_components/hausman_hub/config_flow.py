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
import secrets
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
    NATIVE_CLIMATE_MODE_DEFAULT,
    NATIVE_CLIMATE_MODE_FIELD,
    NATIVE_CLIMATE_ROOM_ID_FIELD,
    NATIVE_TARGET_HUMIDITY_FIELD,
    NATIVE_TARGET_TEMPERATURE_FIELD,
    SUMMARY_UPDATE_INTERVAL_FIELD,
    create_initial_entry,
    create_options,
    effective_configuration,
)
from .application.climate_import import (
    ClimateImportSnapshot,
    ImportedClimateDevice,
)
from .application.contours import (
    ContourRegistryViolation,
    build_climate_contour_setup,
    climate_room_parameters,
    contour_registry_from_payload,
    contour_registry_to_payload,
    contour_snapshot,
    validate_contour_bindings,
    with_climate_contour_mode,
)
from .application.contour_apply import ContourApplyViolation
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
from .domain.native_climate import (
    NATIVE_TARGET_HUMIDITY_DEFAULT,
    NATIVE_TARGET_TEMPERATURE_DEFAULT,
    NativeClimateMode,
    NativeClimatePolicy,
    NativeClimateViolation,
    native_climate_policy,
)
from .domain.contours import (
    CLIMATE_TARGET_HUMIDITY_DEFAULT,
    CLIMATE_TARGET_TEMPERATURE_DEFAULT,
    ClimateStrategy,
    ContourMode,
)


OPTIONS_SECTION_FIELD = "settings_section"
OPTIONS_SECTION_DEFAULT = "contours"
ADVANCED_SETTINGS_ACTION_FIELD = "advanced_settings_action"
CONTOUR_ACTION_FIELD = "contour_action"
CONTOUR_NAME_FIELD = "contour_name"
CONTOUR_MODE_FIELD = "contour_mode"
CONTOUR_ROOMS_FIELD = "contour_rooms"
CONTOUR_DEVICES_FIELD = "contour_devices"
CONTOUR_TARGET_TEMPERATURE_FIELD = "contour_target_temperature"
CONTOUR_TARGET_HUMIDITY_FIELD = "contour_target_humidity"
CONTOUR_STRATEGY_FIELD = "contour_strategy"
CONTOUR_CONFIRM_FIELD = "confirm_contour_save"
CONTOUR_STATUS_CLOSE_FIELD = "close_contour_status"
CONTOUR_APPLY_CONFIRM_FIELD = "confirm_contour_apply"
CONTOUR_APPLY_RESULT_CLOSE_FIELD = "close_contour_apply_result"
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
CLIMATE_PREFLIGHT_ROOM_FIELD = "climate_preflight_room"
CLIMATE_PREFLIGHT_CLOSE_FIELD = "close_canary_preflight"
NATIVE_CLIMATE_CONFIRM_FIELD = "confirm_native_climate_preview"
MAX_CLIMATE_REGISTRY_FORM_BYTES = 16 * 1024
MISSING_CLIMATE_DEVICE_LABEL = "Нет доступных устройств"
MISSING_CLIMATE_ROOM_LABEL = "Сначала добавьте комнату"

_RUSSIAN_STATUS_LABELS = {
    "ready": "готово",
    "collecting": "нужно больше наблюдений",
    "blocked": "заблокировано",
    "validated_offline": "проверено без подключения",
    "unavailable": "недоступно",
    "not_ready": "требуется проверка",
    "pending": "есть",
    "clear": "нет",
    "effective": "контур уже выключен",
}
_RUSSIAN_REASON_LABELS = {
    "bridge_disabled": "климатический контур выключен",
    "candidate_not_registered": "комната не добавлена в HASC",
    "climate_state_unavailable": "текущее состояние климата недоступно",
    "state_stale": "данные о климате устарели",
    "registry_mismatch": "список устройств не совпадает с текущим состоянием",
    "authority_not_ready": "климатический контур ещё не готов управлять комнатой",
    "required_actions_unsupported": "устройство не поддерживает нужные команды",
    "insufficient_matching_observations": "нужно больше успешных проверок состояния",
    "required_shadow_intents_missing": "ещё не проверены обязательные действия",
    "shadow_anomalies_observed": "при проверке обнаружены расхождения",
    "preflight_requires_shadow": "нужно включить режим «Проверка без команд»",
    "registry_not_reconciled": "список устройств ещё не сверен",
    "command_scope_not_qualified": "набор команд ещё не подтверждён",
    "preflight_state_not_fresh": "нужно обновить состояние климата",
    "pending_operation": "в комнате ещё выполняется команда",
    "rollback_not_ready": "безопасное отключение пока не готово",
    "registry_has_no_rooms": "в списке нет комнат",
    "registry_has_no_devices": "в списке нет устройств",
    "canary_registry_locked": "во время пробного управления список нельзя менять",
}
_RUSSIAN_ACTION_LABELS = {
    "set_room_target": "установка температуры",
    "turn_room_off": "выключение климата",
}
_RUSSIAN_NATIVE_STATUS_LABELS = {
    "disabled": "выключен",
    "ready": "расчёт выполнен",
    "unavailable": "нет данных для расчёта",
    "stale": "данные устарели",
    "room_missing": "комната не найдена",
}
_RUSSIAN_TEMPERATURE_DEMAND_LABELS = {
    "unavailable": "нет данных",
    "heating": "нужно нагревать",
    "hold": "температура в норме",
    "cooling": "нужно охлаждать",
}
_RUSSIAN_HUMIDITY_DEMAND_LABELS = {
    "unavailable": "нет данных",
    "humidifying": "нужно увлажнять",
    "hold": "влажность в норме",
    "high": "влажность выше цели",
}
_RUSSIAN_NATIVE_REASON_LABELS = {
    "controller_disabled": "встроенный расчёт выключен",
    "room_not_registered": "комната не добавлена в HASC",
    "climate_state_unavailable": "нет текущих данных из климатического контура",
    "room_state_unavailable": "для комнаты нет текущих показаний",
    "state_stale": "показания устарели",
    "temperature_unavailable": "нет температуры",
    "humidity_unavailable": "нет влажности",
    "temperature_device_unavailable": "нет доступного устройства для температуры",
    "humidity_device_unavailable": "нет доступного увлажнителя",
    "humidity_above_target": "понижение влажности пока не автоматизировано",
}
_RUSSIAN_CONTOUR_STATUS_LABELS = {
    "disabled": "выключен в HASC",
    "ready": "готов",
    "attention": "нужно проверить настройки",
    "unavailable": "двигатель недоступен",
    "stale": "данные устарели",
}
_RUSSIAN_CONTOUR_MODE_LABELS = {
    "disabled": "выключен",
    "observe": "наблюдение",
    "automatic": "автоматически",
}
_RUSSIAN_CONTOUR_STRATEGY_LABELS = {
    "soft": "мягко и тихо",
    "normal": "обычно",
    "aggressive": "быстрее достичь цели",
}
_RUSSIAN_CONTOUR_REASON_LABELS = {
    "room_state_unavailable": "нет состояния комнаты",
    "state_stale": "показания устарели",
    "device_unavailable": "одно из устройств недоступно",
    "engine_not_automatic": "существующий климатический двигатель не в автоматическом режиме",
    "authority_not_ready": "двигатель не подтвердил готовность управления",
    "target_temperature_differs": "температура в двигателе отличается от настройки HASC",
    "target_humidity_differs": "влажность в двигателе отличается от настройки HASC",
    "target_strategy_unavailable": "двигатель не сообщил текущую стратегию",
    "target_strategy_differs": "стратегия двигателя отличается от настройки HASC",
}
_RUSSIAN_CONTOUR_APPLY_STATUS_LABELS = {
    "pending": "команды приняты, подтверждение состояния ещё ожидается",
    "confirmed": "настройки подтверждены двигателем",
    "partial": "применена только часть настроек",
    "rejected": "двигатель отклонил настройку",
    "unavailable": "результат команды не удалось проверить",
}
_RUSSIAN_CONTOUR_APPLY_REASON_LABELS = {
    "already_in_sync": "настройки уже совпадали, команды не потребовались",
    "engine_rejected": "двигатель отклонил одну из команд",
    "command_result_unavailable": "ответ на одну из команд потерян; повторная отправка заблокирована",
    "verification_unavailable": "после команд не удалось перечитать состояние",
    "state_not_confirmed": "двигатель ещё не показал все новые значения",
}


def _russian_status(value: object) -> str:
    """Return a readable status without exposing an internal contract code."""

    if isinstance(value, str):
        return _RUSSIAN_STATUS_LABELS.get(value, "неизвестно")
    return "неизвестно"


def _russian_reasons(values: object) -> str:
    """Render known blocking reasons as plain Russian operator guidance."""

    if not isinstance(values, list) or not values:
        return "нет"
    labels = [
        _RUSSIAN_REASON_LABELS.get(value, "неизвестная причина")
        if isinstance(value, str)
        else "неизвестная причина"
        for value in values
    ]
    return "; ".join(dict.fromkeys(labels))


def _russian_actions(values: object) -> str:
    """Render only the fixed first climate actions as readable Russian text."""

    if not isinstance(values, list) or not values:
        return "нет"
    labels = [
        _RUSSIAN_ACTION_LABELS.get(value, "неизвестное действие")
        if isinstance(value, str)
        else "неизвестное действие"
        for value in values
    ]
    return ", ".join(dict.fromkeys(labels))


def _russian_yes_no(value: object) -> str:
    """Render strict booleans for the Russian operator screen."""

    return "да" if value is True else "нет"


def _display_measurement(value: object, unit: str) -> str:
    """Render one optional numeric reading without accepting arbitrary text."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "нет данных"
    return f"{value:g}{unit}"


def _russian_native_value(value: object, labels: Mapping[str, str]) -> str:
    """Render one fixed native-controller code for the local operator."""

    return labels.get(value, "неизвестно") if isinstance(value, str) else "неизвестно"


def _russian_native_reasons(values: object) -> str:
    """Render bounded preview reasons without exposing internal identifiers."""

    if not isinstance(values, list) or not values:
        return "нет"
    return "; ".join(
        dict.fromkeys(
            _RUSSIAN_NATIVE_REASON_LABELS.get(value, "неизвестная причина")
            if isinstance(value, str)
            else "неизвестная причина"
            for value in values
        )
    )


MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=list(APPROVED_MODES),
        translation_key="mode",
    )
)
SUMMARY_UPDATE_INTERVAL_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=list(APPROVED_SUMMARY_UPDATE_INTERVALS),
        translation_key="summary_update_interval",
    )
)
CANARY_CONTROL_TARGET_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain=INPUT_BOOLEAN_DOMAIN, multiple=False)
)
CLIMATE_BRIDGE_MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[mode.value for mode in ClimateBridgeMode],
        translation_key="climate_bridge_mode",
    )
)
NATIVE_CLIMATE_MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[mode.value for mode in NativeClimateMode],
        translation_key="native_climate_mode",
    )
)
OPTIONS_SECTION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            "contours",
            "general_settings",
            "advanced_settings",
        ],
        translation_key="settings_section",
    )
)
ADVANCED_SETTINGS_ACTION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            "climate_registry",
            "climate_connection",
            "native_climate",
            "test_switch",
        ],
        translation_key="advanced_settings_action",
    )
)
CONTOUR_ACTION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            "configure_climate",
            "apply_climate",
            "view_status",
            "disable_climate",
        ],
        translation_key="contour_action",
    )
)
CONTOUR_MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[ContourMode.OBSERVE.value, ContourMode.AUTOMATIC.value],
        translation_key="contour_mode",
    )
)
CONTOUR_STRATEGY_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[value.value for value in ClimateStrategy],
        translation_key="contour_strategy",
    )
)
NATIVE_TARGET_TEMPERATURE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[f"{value / 2:.1f}" for value in range(36, 57)],
    )
)
NATIVE_TARGET_HUMIDITY_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[str(value) for value in range(30, 71, 5)],
    )
)
CLIMATE_REGISTRY_JSON_SELECTOR = TextSelector(
    TextSelectorConfig(multiline=True, type=TextSelectorType.TEXT)
)
CLIMATE_REGISTRY_ACTION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            "import_candidate",
            "add_room",
            "add_device",
            "review_canary_preflight",
            "review_shadow_evidence",
            "review_registry",
            "advanced_json",
            "reset_registry",
        ],
        translation_key="climate_registry_action",
    )
)
CLIMATE_DEVICE_KIND_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            "air_conditioner",
            "radiator_thermostat",
            "humidifier",
            "floor_heating",
            "temperature_sensor",
            "humidity_sensor",
        ],
        translation_key="climate_device_kind",
    )
)
CLIMATE_DEVICE_SCOPE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=["observed", "canary", "managed"],
        translation_key="climate_device_control_scope",
    )
)
CLIMATE_DEVICE_OWNER_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=["climate_core", "manual", "observed"],
        translation_key="climate_device_control_owner",
    )
)
CLIMATE_DEVICE_CAPABILITIES_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            "power",
            "target_temperature",
            "target_humidity",
            "hvac_mode",
            "fan_mode",
            "auto_manual",
            "target_strategy",
            "cooldown",
            "physical_feedback",
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


def _options_section_schema() -> vol.Schema:
    """Keep ordinary contour setup separate from optional advanced tools."""

    return vol.Schema(
        {
            vol.Required(
                OPTIONS_SECTION_FIELD,
                default=OPTIONS_SECTION_DEFAULT,
            ): OPTIONS_SECTION_SELECTOR
        }
    )


def _advanced_settings_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                ADVANCED_SETTINGS_ACTION_FIELD,
                default="climate_connection",
            ): ADVANCED_SETTINGS_ACTION_SELECTOR
        }
    )


def _contour_action_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONTOUR_ACTION_FIELD,
                default="configure_climate",
            ): CONTOUR_ACTION_SELECTOR
        }
    )


def _climate_contour_source_schema(default: str | None) -> vol.Schema:
    field = (
        vol.Required(CLIMATE_BRIDGE_TARGET_FIELD, default=default)
        if default is not None
        else vol.Required(CLIMATE_BRIDGE_TARGET_FIELD)
    )
    return vol.Schema({field: str})


def _climate_contour_setup_schema(
    *,
    rooms: list[dict[str, str]],
    devices: list[dict[str, str]],
    name_default: str,
    room_defaults: list[str],
    device_defaults: list[str],
    mode_default: str,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONTOUR_NAME_FIELD, default=name_default): str,
            vol.Required(
                CONTOUR_MODE_FIELD,
                default=mode_default,
            ): CONTOUR_MODE_SELECTOR,
            vol.Required(
                CONTOUR_ROOMS_FIELD,
                default=room_defaults,
            ): SelectSelector(
                SelectSelectorConfig(options=rooms, multiple=True)
            ),
            vol.Required(
                CONTOUR_DEVICES_FIELD,
                default=device_defaults,
            ): SelectSelector(
                SelectSelectorConfig(options=devices, multiple=True)
            ),
        }
    )


def _climate_contour_room_schema(
    *,
    temperature_default: float,
    humidity_default: int,
    strategy_default: str,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONTOUR_TARGET_TEMPERATURE_FIELD,
                default=f"{temperature_default:.1f}",
            ): NATIVE_TARGET_TEMPERATURE_SELECTOR,
            vol.Required(
                CONTOUR_TARGET_HUMIDITY_FIELD,
                default=str(humidity_default),
            ): NATIVE_TARGET_HUMIDITY_SELECTOR,
            vol.Required(
                CONTOUR_STRATEGY_FIELD,
                default=strategy_default,
            ): CONTOUR_STRATEGY_SELECTOR,
        }
    )


def _contour_confirm_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONTOUR_CONFIRM_FIELD, default=False): StrictBooleanSelector()
        }
    )


def _contour_status_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONTOUR_STATUS_CLOSE_FIELD,
                default=False,
            ): StrictBooleanSelector()
        }
    )


def _contour_apply_confirm_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONTOUR_APPLY_CONFIRM_FIELD,
                default=False,
            ): StrictBooleanSelector()
        }
    )


def _contour_apply_result_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONTOUR_APPLY_RESULT_CLOSE_FIELD,
                default=False,
            ): StrictBooleanSelector()
        }
    )


def _general_settings_schema(
    mode_default: str,
    local_summary_enabled_default: bool,
    summary_update_interval_default: str,
) -> vol.Schema:
    """Show only settings for HASC's aggregate informational display."""

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


def _test_switch_schema(
    canary_control_enabled_default: bool,
    canary_control_target_default: str | None,
) -> vol.Schema:
    """Keep the unrelated input-boolean test on its own clearly named screen."""

    target_field = (
        vol.Optional(
            CANARY_CONTROL_TARGET_FIELD,
            default=canary_control_target_default,
        )
        if canary_control_target_default is not None
        else vol.Optional(CANARY_CONTROL_TARGET_FIELD)
    )
    return vol.Schema(
        {
            vol.Required(
                CANARY_CONTROL_ENABLED_FIELD,
                default=canary_control_enabled_default,
            ): StrictBooleanSelector(),
            target_field: CANARY_CONTROL_TARGET_SELECTOR,
        }
    )


def _climate_connection_schema(climate_bridge_mode_default: str) -> vol.Schema:
    """Choose the bridge stage before asking for stage-specific values."""

    return vol.Schema(
        {
            vol.Required(
                CLIMATE_BRIDGE_MODE_FIELD,
                default=climate_bridge_mode_default,
            ): CLIMATE_BRIDGE_MODE_SELECTOR
        }
    )


def _climate_endpoint_schema(
    *,
    bridge_mode: str,
    climate_bridge_target_default: str | None,
    climate_canary_room_id_default: str | None,
) -> vol.Schema:
    """Ask for the address and only the room needed by one-room control."""

    bridge_target_field = (
        vol.Required(
            CLIMATE_BRIDGE_TARGET_FIELD,
            default=climate_bridge_target_default,
        )
        if climate_bridge_target_default is not None
        else vol.Required(CLIMATE_BRIDGE_TARGET_FIELD)
    )
    fields: dict[vol.Marker, object] = {bridge_target_field: str}
    if bridge_mode == ClimateBridgeMode.CANARY.value:
        canary_room_field = (
            vol.Required(
                CLIMATE_CANARY_ROOM_ID_FIELD,
                default=climate_canary_room_id_default,
            )
            if climate_canary_room_id_default is not None
            else vol.Required(CLIMATE_CANARY_ROOM_ID_FIELD)
        )
        fields[canary_room_field] = str
    return vol.Schema(fields)


def _native_climate_mode_schema(mode_default: str) -> vol.Schema:
    """Choose whether HASC should calculate its own room decision."""

    return vol.Schema(
        {
            vol.Required(
                NATIVE_CLIMATE_MODE_FIELD,
                default=mode_default,
            ): NATIVE_CLIMATE_MODE_SELECTOR
        }
    )


def _native_climate_policy_schema(
    rooms: list[dict[str, str]],
    *,
    room_default: str,
    temperature_default: float,
    humidity_default: int,
) -> vol.Schema:
    """Ask only for one room and its two comfort targets."""

    return vol.Schema(
        {
            vol.Required(
                NATIVE_CLIMATE_ROOM_ID_FIELD,
                default=room_default,
            ): SelectSelector(SelectSelectorConfig(options=rooms)),
            vol.Required(
                NATIVE_TARGET_TEMPERATURE_FIELD,
                default=f"{temperature_default:.1f}",
            ): NATIVE_TARGET_TEMPERATURE_SELECTOR,
            vol.Required(
                NATIVE_TARGET_HUMIDITY_FIELD,
                default=str(humidity_default),
            ): NATIVE_TARGET_HUMIDITY_SELECTOR,
        }
    )


def _native_climate_confirm_schema() -> vol.Schema:
    """Require a separate save after showing HASC's read-only decision."""

    return vol.Schema(
        {
            vol.Required(
                NATIVE_CLIMATE_CONFIRM_FIELD,
                default=False,
            ): StrictBooleanSelector()
        }
    )


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
                options=kinds,
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


def _climate_preflight_candidate_schema(
    rooms: list[dict[str, str]],
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CLIMATE_PREFLIGHT_ROOM_FIELD): SelectSelector(
                SelectSelectorConfig(options=rooms)
            )
        }
    )


def _climate_canary_preflight_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CLIMATE_PREFLIGHT_CLOSE_FIELD,
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


def _safe_native_climate_defaults(
    entry_data: Mapping[str, Any],
    options: Mapping[str, Any],
) -> tuple[str, str | None, float, int]:
    """Return safe stored preview values or conservative first-run defaults."""

    try:
        policy = effective_configuration(entry_data, options).native_climate_policy
    except ConfigurationViolation:
        return (
            NATIVE_CLIMATE_MODE_DEFAULT,
            None,
            NATIVE_TARGET_TEMPERATURE_DEFAULT,
            NATIVE_TARGET_HUMIDITY_DEFAULT,
        )
    return (
        policy.mode.value,
        policy.room_id,
        policy.target_temperature or NATIVE_TARGET_TEMPERATURE_DEFAULT,
        policy.target_humidity or NATIVE_TARGET_HUMIDITY_DEFAULT,
    )


def _merged_safe_options(
    entry_data: Mapping[str, Any],
    saved_options: Mapping[str, Any],
    updates: Mapping[str, object],
) -> dict[str, str | bool | float | int]:
    """Apply one small settings screen while preserving other validated areas."""

    try:
        current = effective_configuration(entry_data, saved_options)
    except ConfigurationViolation:
        values: dict[str, object] = {
            MODE_FIELD: READ_ONLY_MODE,
            LOCAL_SUMMARY_ENABLED_FIELD: LOCAL_SUMMARY_ENABLED_DEFAULT,
            SUMMARY_UPDATE_INTERVAL_FIELD: SUMMARY_UPDATE_INTERVAL_DEFAULT,
            CANARY_CONTROL_ENABLED_FIELD: CANARY_CONTROL_ENABLED_DEFAULT,
            CANARY_CONTROL_TARGET_FIELD: None,
            CLIMATE_BRIDGE_MODE_FIELD: CLIMATE_BRIDGE_MODE_DEFAULT,
            CLIMATE_BRIDGE_TARGET_FIELD: None,
            CLIMATE_CANARY_ROOM_ID_FIELD: None,
            NATIVE_CLIMATE_MODE_FIELD: NATIVE_CLIMATE_MODE_DEFAULT,
            NATIVE_CLIMATE_ROOM_ID_FIELD: None,
            NATIVE_TARGET_TEMPERATURE_FIELD: None,
            NATIVE_TARGET_HUMIDITY_FIELD: None,
        }
    else:
        values = {
            MODE_FIELD: current.mode,
            LOCAL_SUMMARY_ENABLED_FIELD: current.local_summary_enabled,
            SUMMARY_UPDATE_INTERVAL_FIELD: current.summary_update_interval,
            CANARY_CONTROL_ENABLED_FIELD: current.canary_control_enabled,
            CANARY_CONTROL_TARGET_FIELD: (
                None
                if current.canary_control_target is None
                else current.canary_control_target.entity_id
            ),
            CLIMATE_BRIDGE_MODE_FIELD: current.climate_bridge_mode.value,
            CLIMATE_BRIDGE_TARGET_FIELD: (
                None
                if current.climate_bridge_target is None
                else current.climate_bridge_target.origin
            ),
            CLIMATE_CANARY_ROOM_ID_FIELD: current.climate_canary_room_id,
            NATIVE_CLIMATE_MODE_FIELD: current.native_climate_policy.mode.value,
            NATIVE_CLIMATE_ROOM_ID_FIELD: current.native_climate_policy.room_id,
            NATIVE_TARGET_TEMPERATURE_FIELD: (
                current.native_climate_policy.target_temperature
            ),
            NATIVE_TARGET_HUMIDITY_FIELD: current.native_climate_policy.target_humidity,
        }
    values.update(updates)
    return create_options(
        mode_value=values[MODE_FIELD],
        local_summary_enabled_value=values[LOCAL_SUMMARY_ENABLED_FIELD],
        summary_update_interval_value=values[SUMMARY_UPDATE_INTERVAL_FIELD],
        canary_control_enabled_value=values[CANARY_CONTROL_ENABLED_FIELD],
        canary_control_target_value=values[CANARY_CONTROL_TARGET_FIELD],
        climate_bridge_mode_value=values[CLIMATE_BRIDGE_MODE_FIELD],
        climate_bridge_target_value=values[CLIMATE_BRIDGE_TARGET_FIELD],
        climate_canary_room_id_value=values[CLIMATE_CANARY_ROOM_ID_FIELD],
        native_climate_mode_value=values[NATIVE_CLIMATE_MODE_FIELD],
        native_climate_room_id_value=values[NATIVE_CLIMATE_ROOM_ID_FIELD],
        native_target_temperature_value=values[NATIVE_TARGET_TEMPERATURE_FIELD],
        native_target_humidity_value=values[NATIVE_TARGET_HUMIDITY_FIELD],
    )


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

    _climate_bridge_mode_draft: str | None = None
    _registry_draft: object | None = None
    _registry_preview: Mapping[str, Any] | None = None
    _shadow_evidence: Mapping[str, Any] | None = None
    _canary_preflight: Mapping[str, Any] | None = None
    _import_snapshot: ClimateImportSnapshot | None = None
    _import_candidates: dict[str, ImportedClimateDevice] | None = None
    _selected_import_source_id: str | None = None
    _native_climate_mode_draft: str | None = None
    _native_climate_rooms: list[dict[str, str]] | None = None
    _native_climate_policy_draft: NativeClimatePolicy | None = None
    _native_climate_preview: Mapping[str, Any] | None = None
    _contour_source_target: str | None = None
    _contour_source_snapshot: ClimateImportSnapshot | None = None
    _contour_device_tokens: dict[str, ImportedClimateDevice] | None = None
    _contour_saved_name: str | None = None
    _contour_saved_mode: str | None = None
    _contour_saved_room_parameters: dict[str, dict[str, object]] | None = None
    _contour_saved_source_ids: tuple[str, ...] = ()
    _contour_name_draft: str | None = None
    _contour_mode_draft: str | None = None
    _contour_room_ids_draft: tuple[str, ...] = ()
    _contour_source_ids_draft: tuple[str, ...] = ()
    _contour_room_index: int = 0
    _contour_room_parameters_draft: dict[str, dict[str, object]] | None = None
    _contour_registry_draft: Mapping[str, object] | None = None
    _contour_definition_draft: Mapping[str, object] | None = None
    _contour_preview: Mapping[str, Any] | None = None
    _contour_apply_preview: Mapping[str, Any] | None = None
    _contour_apply_request_id: str | None = None
    _contour_apply_receipt: Mapping[str, Any] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show one short choice instead of mixing unrelated settings."""

        errors: dict[str, str] = {}
        if user_input is not None:
            section = user_input.get(OPTIONS_SECTION_FIELD)
            if section == "contours":
                return await self.async_step_contours()
            if section == "general_settings":
                return await self.async_step_general_settings()
            if section == "advanced_settings":
                return await self.async_step_advanced_settings()
            errors[OPTIONS_SECTION_FIELD] = "unsafe_settings_section"

        return self.async_show_form(
            step_id="init",
            data_schema=_options_section_schema(),
            errors=errors,
        )

    async def async_step_advanced_settings(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Keep migration, diagnostics, and service tests out of normal setup."""

        errors: dict[str, str] = {}
        if user_input is not None:
            action = user_input.get(ADVANCED_SETTINGS_ACTION_FIELD)
            if action == "climate_registry":
                return await self.async_step_climate_registry()
            if action == "climate_connection":
                return await self.async_step_climate_connection()
            if action == "native_climate":
                return await self.async_step_native_climate()
            if action == "test_switch":
                return await self.async_step_test_switch()
            errors[ADVANCED_SETTINGS_ACTION_FIELD] = "unsafe_advanced_action"
        return self.async_show_form(
            step_id="advanced_settings",
            data_schema=_advanced_settings_schema(),
            errors=errors,
        )

    async def async_step_contours(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Open the ordinary automatic-contour workflow."""

        runtime = self._climate_runtime()
        errors: dict[str, str] = {}
        if runtime is None:
            errors["base"] = "climate_runtime_unavailable"
        elif user_input is not None:
            action = user_input.get(CONTOUR_ACTION_FIELD)
            if action == "configure_climate":
                return await self._async_begin_climate_contour(runtime)
            if action == "apply_climate":
                try:
                    preview = await runtime.async_contour_apply_preview()
                except ContourApplyViolation:
                    errors["base"] = "contour_apply_not_ready"
                except Exception:
                    errors["base"] = "contour_apply_unavailable"
                else:
                    self._contour_apply_preview = preview
                    self._contour_apply_request_id = (
                        f"ha-options-{secrets.token_hex(16)}"
                    )
                    self._contour_apply_receipt = None
                    return await self.async_step_climate_contour_apply_confirm()
            elif action == "view_status":
                try:
                    payload = await runtime.async_contours_snapshot()
                except Exception:
                    errors["base"] = "contour_status_unavailable"
                else:
                    if not payload.get("contours"):
                        errors["base"] = "contour_not_configured"
                    else:
                        self._contour_preview = payload
                        return await self.async_step_contour_status()
            elif action == "disable_climate":
                try:
                    payload = await runtime.async_contour_registry_payload()
                    contours = contour_registry_from_payload(payload)
                    disabled = with_climate_contour_mode(
                        contours,
                        ContourMode.DISABLED.value,
                    )
                    await runtime.async_replace_contours(
                        contour_registry_to_payload(disabled)
                    )
                    options = _merged_safe_options(
                        self.config_entry.data,
                        self.config_entry.options,
                        {
                            CLIMATE_BRIDGE_MODE_FIELD: (
                                ClimateBridgeMode.DISABLED.value
                            ),
                            CLIMATE_BRIDGE_TARGET_FIELD: None,
                            CLIMATE_CANARY_ROOM_ID_FIELD: None,
                        },
                    )
                except ContourRegistryViolation:
                    errors["base"] = "contour_not_configured"
                except Exception:
                    errors["base"] = "contour_save_failed"
                else:
                    return self.async_create_entry(title="", data=options)
            else:
                errors[CONTOUR_ACTION_FIELD] = "invalid_contour_action"
        return self.async_show_form(
            step_id="contours",
            data_schema=_contour_action_schema(),
            errors=errors,
        )

    async def async_step_climate_contour_apply_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Require explicit consent before changing existing-engine settings."""

        runtime = self._climate_runtime()
        if (
            runtime is None
            or self._contour_apply_preview is None
            or self._contour_apply_request_id is None
        ):
            return await self.async_step_contours()
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONTOUR_APPLY_CONFIRM_FIELD) is not True:
                errors[CONTOUR_APPLY_CONFIRM_FIELD] = (
                    "contour_apply_confirmation_required"
                )
            else:
                try:
                    receipt = await runtime.async_apply_contour(
                        {
                            "request_id": self._contour_apply_request_id,
                            "contour_id": "climate",
                            "confirm": True,
                        }
                    )
                except ContourApplyViolation:
                    errors["base"] = "contour_apply_not_ready"
                except Exception:
                    errors["base"] = "contour_apply_unavailable"
                else:
                    self._contour_apply_receipt = receipt.as_payload()
                    return await self.async_step_climate_contour_apply_result()
        return self.async_show_form(
            step_id="climate_contour_apply_confirm",
            data_schema=_contour_apply_confirm_schema(),
            errors=errors,
            description_placeholders=self._contour_apply_preview_placeholders(),
        )

    async def async_step_climate_contour_apply_result(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Show an honest accepted/confirmed/partial result before closing."""

        if self._contour_apply_receipt is None:
            return await self.async_step_contours()
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONTOUR_APPLY_RESULT_CLOSE_FIELD) is not True:
                errors[CONTOUR_APPLY_RESULT_CLOSE_FIELD] = (
                    "contour_apply_result_close_required"
                )
            else:
                options = _merged_safe_options(
                    self.config_entry.data,
                    self.config_entry.options,
                    {},
                )
                return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="climate_contour_apply_result",
            data_schema=_contour_apply_result_schema(),
            errors=errors,
            description_placeholders=self._contour_apply_result_placeholders(),
        )

    async def _async_begin_climate_contour(self, runtime: Any) -> FlowResult:
        """Reuse a configured engine or ask for its one-time local address."""

        try:
            configuration = effective_configuration(
                self.config_entry.data,
                self.config_entry.options,
            )
        except ConfigurationViolation:
            return await self.async_step_climate_contour_source()
        target = configuration.climate_bridge_target
        if (
            target is None
            or configuration.climate_bridge_mode is ClimateBridgeMode.DISABLED
        ):
            return await self.async_step_climate_contour_source()
        try:
            snapshot = await runtime.async_registry_import_snapshot()
        except Exception:
            return await self.async_step_climate_contour_source()
        self._contour_source_target = target.origin
        self._set_contour_source_snapshot(snapshot)
        await self._async_load_saved_contour(runtime)
        return await self.async_step_climate_contour_setup()

    async def async_step_climate_contour_source(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Connect the existing climate engine once, using a read-only GET."""

        _, saved_target, _ = _safe_climate_bridge_defaults(
            self.config_entry.data,
            self.config_entry.options,
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            raw_target = user_input.get(CLIMATE_BRIDGE_TARGET_FIELD)
            try:
                target = climate_bridge_target(raw_target)
                from .climate_bridge import ClimateApiClient

                snapshot = await ClimateApiClient(
                    self.hass,
                    target,
                ).async_fetch_state()
                if not snapshot.runtime_fresh:
                    raise ValueError("climate source is stale")
            except UnsafeClimateBridgeTarget:
                errors[CLIMATE_BRIDGE_TARGET_FIELD] = "unsafe_climate_bridge_target"
            except Exception:
                errors["base"] = "climate_contour_source_unavailable"
            else:
                self._contour_source_target = target.origin
                self._set_contour_source_snapshot(snapshot)
                runtime = self._climate_runtime()
                if runtime is not None:
                    await self._async_load_saved_contour(runtime)
                return await self.async_step_climate_contour_setup()
        return self.async_show_form(
            step_id="climate_contour_source",
            data_schema=_climate_contour_source_schema(saved_target),
            errors=errors,
        )

    async def async_step_climate_contour_setup(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Choose the contour, rooms, and devices before room parameters."""

        snapshot = self._contour_source_snapshot
        tokens = self._contour_device_tokens or {}
        if snapshot is None or not snapshot.runtime_fresh or not tokens:
            return await self.async_step_climate_contour_source()
        room_options = [
            SelectOptionDict(value=room.room_id, label=room.name)
            for room in snapshot.rooms
        ]
        device_options: list[dict[str, str]] = []
        for token, device in tokens.items():
            device_room = snapshot.room(device.room_id)
            if device_room is None:
                continue
            device_options.append(
                SelectOptionDict(
                    value=token,
                    label=f"{device_room.name} — {device.name}",
                )
            )
        saved_parameters = self._contour_saved_room_parameters or {}
        room_defaults = [
            room.room_id
            for room in snapshot.rooms
            if room.room_id in saved_parameters
        ]
        saved_sources = set(self._contour_saved_source_ids)
        device_defaults = [
            token
            for token, device in tokens.items()
            if device.source_id in saved_sources
        ]
        engine_mode_default = (
            ContourMode.AUTOMATIC.value
            if all(
                room.mode in {"auto", "forced_auto_only"}
                for room in snapshot.rooms
            )
            else ContourMode.OBSERVE.value
        )
        mode_default = (
            self._contour_saved_mode
            if self._contour_saved_mode
            in {ContourMode.OBSERVE.value, ContourMode.AUTOMATIC.value}
            else engine_mode_default
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            expected_fields = {
                CONTOUR_NAME_FIELD,
                CONTOUR_MODE_FIELD,
                CONTOUR_ROOMS_FIELD,
                CONTOUR_DEVICES_FIELD,
            }
            if set(user_input) != expected_fields:
                errors["base"] = "invalid_contour_setup"
            name = user_input.get(CONTOUR_NAME_FIELD)
            mode = user_input.get(CONTOUR_MODE_FIELD)
            room_ids = user_input.get(CONTOUR_ROOMS_FIELD)
            device_tokens = user_input.get(CONTOUR_DEVICES_FIELD)
            if (
                not isinstance(name, str)
                or name != name.strip()
                or not name
                or len(name) > 120
            ):
                errors[CONTOUR_NAME_FIELD] = "invalid_contour_name"
            if mode not in {
                ContourMode.OBSERVE.value,
                ContourMode.AUTOMATIC.value,
            }:
                errors[CONTOUR_MODE_FIELD] = "invalid_contour_mode"
            if (
                not isinstance(room_ids, list)
                or not room_ids
                or any(not isinstance(value, str) for value in room_ids)
                or len(room_ids) != len(set(room_ids))
            ):
                errors[CONTOUR_ROOMS_FIELD] = "contour_rooms_required"
            elif any(snapshot.room(value) is None for value in room_ids):
                errors[CONTOUR_ROOMS_FIELD] = "invalid_contour_rooms"
            if (
                not isinstance(device_tokens, list)
                or not device_tokens
                or any(not isinstance(value, str) for value in device_tokens)
                or len(device_tokens) != len(set(device_tokens))
            ):
                errors[CONTOUR_DEVICES_FIELD] = "contour_devices_required"
                selected: list[ImportedClimateDevice | None] = []
            else:
                selected = [tokens.get(value) for value in device_tokens]
                if any(value is None for value in selected):
                    errors[CONTOUR_DEVICES_FIELD] = "invalid_contour_devices"
            if (
                isinstance(room_ids, list)
                and room_ids
                and selected
                and not any(value is None for value in selected)
            ):
                selected_rooms = set(room_ids)
                if any(
                    value is not None and value.room_id not in selected_rooms
                    for value in selected
                ):
                    errors[CONTOUR_DEVICES_FIELD] = "device_outside_contour_rooms"
                elif any(
                    not any(
                        device is not None and device.room_id == room_id
                        for device in selected
                    )
                    for room_id in selected_rooms
                ):
                    errors[CONTOUR_DEVICES_FIELD] = "contour_room_device_required"
            if not errors:
                selected_room_ids = set(room_ids)
                selected_token_ids = set(device_tokens)
                self._contour_name_draft = name
                self._contour_mode_draft = mode
                self._contour_room_ids_draft = tuple(
                    room.room_id
                    for room in snapshot.rooms
                    if room.room_id in selected_room_ids
                )
                self._contour_source_ids_draft = tuple(
                    device.source_id
                    for token, device in tokens.items()
                    if token in selected_token_ids
                )
                self._contour_room_index = 0
                self._contour_room_parameters_draft = {}
                return await self.async_step_climate_contour_room()
        return self.async_show_form(
            step_id="climate_contour_setup",
            data_schema=_climate_contour_setup_schema(
                rooms=room_options,
                devices=device_options,
                name_default=self._contour_saved_name or "Климат",
                room_defaults=room_defaults,
                device_defaults=device_defaults,
                mode_default=mode_default,
            ),
            errors=errors,
        )

    async def async_step_climate_contour_room(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Collect comfort parameters separately for each selected room."""

        snapshot = self._contour_source_snapshot
        parameters = self._contour_room_parameters_draft
        if (
            snapshot is None
            or not snapshot.runtime_fresh
            or parameters is None
            or self._contour_name_draft is None
            or self._contour_mode_draft is None
            or not self._contour_room_ids_draft
            or not self._contour_source_ids_draft
            or not 0 <= self._contour_room_index < len(self._contour_room_ids_draft)
        ):
            return await self.async_step_climate_contour_setup()
        room_id = self._contour_room_ids_draft[self._contour_room_index]
        room = snapshot.room(room_id)
        if room is None:
            return await self.async_step_climate_contour_setup()
        temperature_default, humidity_default, strategy_default = (
            self._contour_room_defaults(snapshot, room_id)
        )
        saved = (self._contour_saved_room_parameters or {}).get(room_id)
        if saved is not None:
            temperature_default = float(saved["target_temperature"])
            humidity_default = int(saved["target_humidity"])
            strategy_default = str(saved["strategy"])
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized = climate_room_parameters(
                    {
                        "target_temperature": user_input.get(
                            CONTOUR_TARGET_TEMPERATURE_FIELD
                        ),
                        "target_humidity": user_input.get(
                            CONTOUR_TARGET_HUMIDITY_FIELD
                        ),
                        "strategy": user_input.get(CONTOUR_STRATEGY_FIELD),
                    }
                )
                if set(user_input) != {
                    CONTOUR_TARGET_TEMPERATURE_FIELD,
                    CONTOUR_TARGET_HUMIDITY_FIELD,
                    CONTOUR_STRATEGY_FIELD,
                }:
                    raise ContourRegistryViolation(
                        "room parameter form has unsupported fields"
                    )
            except ContourRegistryViolation:
                errors["base"] = "invalid_contour_room_parameters"
            else:
                parameters[room_id] = normalized
                if self._contour_room_index + 1 < len(
                    self._contour_room_ids_draft
                ):
                    self._contour_room_index += 1
                    return await self.async_step_climate_contour_room()
                try:
                    registry, contours = build_climate_contour_setup(
                        snapshot,
                        room_ids=list(self._contour_room_ids_draft),
                        source_ids=list(self._contour_source_ids_draft),
                        name=self._contour_name_draft,
                        mode=self._contour_mode_draft,
                        room_parameters=parameters,
                    )
                    preview = contour_snapshot(contours, registry, snapshot)
                except ContourRegistryViolation:
                    errors["base"] = "invalid_contour_setup"
                else:
                    from .application.climate_registry import registry_to_payload

                    self._contour_registry_draft = registry_to_payload(registry)
                    self._contour_definition_draft = contour_registry_to_payload(
                        contours
                    )
                    self._contour_preview = preview
                    return await self.async_step_climate_contour_confirm()
        return self.async_show_form(
            step_id="climate_contour_room",
            data_schema=_climate_contour_room_schema(
                temperature_default=temperature_default,
                humidity_default=humidity_default,
                strategy_default=strategy_default,
            ),
            errors=errors,
            description_placeholders={
                "room_name": room.name,
                "room_number": str(self._contour_room_index + 1),
                "room_count": str(len(self._contour_room_ids_draft)),
            },
        )

    async def async_step_climate_contour_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Show one plain summary and save both selected devices and contour."""

        runtime = self._climate_runtime()
        if (
            runtime is None
            or self._contour_registry_draft is None
            or self._contour_definition_draft is None
            or self._contour_preview is None
            or self._contour_source_target is None
        ):
            return await self.async_step_contours()
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONTOUR_CONFIRM_FIELD) is not True:
                errors[CONTOUR_CONFIRM_FIELD] = "contour_confirmation_required"
            else:
                try:
                    await runtime.async_replace_contour_setup(
                        self._contour_registry_draft,
                        self._contour_definition_draft,
                    )
                    options = _merged_safe_options(
                        self.config_entry.data,
                        self.config_entry.options,
                        {
                            CLIMATE_BRIDGE_MODE_FIELD: ClimateBridgeMode.MANAGED.value,
                            CLIMATE_BRIDGE_TARGET_FIELD: self._contour_source_target,
                            CLIMATE_CANARY_ROOM_ID_FIELD: None,
                            NATIVE_CLIMATE_MODE_FIELD: NativeClimateMode.DISABLED.value,
                            NATIVE_CLIMATE_ROOM_ID_FIELD: None,
                            NATIVE_TARGET_TEMPERATURE_FIELD: None,
                            NATIVE_TARGET_HUMIDITY_FIELD: None,
                        },
                    )
                except Exception:
                    errors["base"] = "contour_save_failed"
                else:
                    return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="climate_contour_confirm",
            data_schema=_contour_confirm_schema(),
            errors=errors,
            description_placeholders=self._contour_preview_placeholders(),
        )

    async def async_step_contour_status(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Show current contour/engine state without changing anything."""

        if self._contour_preview is None:
            return await self.async_step_contours()
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONTOUR_STATUS_CLOSE_FIELD) is not True:
                errors[CONTOUR_STATUS_CLOSE_FIELD] = "contour_status_close_required"
            else:
                options = _merged_safe_options(
                    self.config_entry.data,
                    self.config_entry.options,
                    {},
                )
                return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="contour_status",
            data_schema=_contour_status_schema(),
            errors=errors,
            description_placeholders=self._contour_preview_placeholders(),
        )

    async def async_step_general_settings(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Configure only aggregate informational reads and their display."""

        mode_default = _safe_mode_default(self.config_entry.data, self.config_entry.options)
        local_page_default = _safe_local_summary_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        interval_default = _safe_summary_update_interval_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            mode = user_input.get(MODE_FIELD, mode_default)
            local_page = user_input.get(LOCAL_SUMMARY_ENABLED_FIELD, local_page_default)
            interval = user_input.get(SUMMARY_UPDATE_INTERVAL_FIELD, interval_default)
            if mode not in APPROVED_MODES:
                errors[MODE_FIELD] = "unsafe_mode"
            elif type(local_page) is not bool:
                errors[LOCAL_SUMMARY_ENABLED_FIELD] = "unsafe_local_summary_setting"
            elif interval not in APPROVED_SUMMARY_UPDATE_INTERVALS:
                errors[SUMMARY_UPDATE_INTERVAL_FIELD] = "unsafe_summary_update_interval"
            else:
                options = _merged_safe_options(
                    self.config_entry.data,
                    self.config_entry.options,
                    {
                        MODE_FIELD: mode,
                        LOCAL_SUMMARY_ENABLED_FIELD: local_page,
                        SUMMARY_UPDATE_INTERVAL_FIELD: interval,
                    },
                )
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="general_settings",
            data_schema=_general_settings_schema(
                mode_default,
                local_page_default,
                interval_default,
            ),
            errors=errors,
        )

    async def async_step_test_switch(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Keep the legacy input-boolean test separate from climate settings."""

        enabled_default = _safe_canary_control_enabled_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        target_default = _safe_canary_control_target_default(
            self.config_entry.data,
            self.config_entry.options,
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            enabled = user_input.get(CANARY_CONTROL_ENABLED_FIELD, enabled_default)
            target = user_input.get(CANARY_CONTROL_TARGET_FIELD, target_default)
            if type(enabled) is not bool:
                errors[CANARY_CONTROL_ENABLED_FIELD] = "unsafe_canary_control_setting"
            elif enabled:
                try:
                    canary_control_target(target)
                except UnsafeCanaryTargetError:
                    errors[CANARY_CONTROL_TARGET_FIELD] = "unsafe_canary_control_target"
            if not errors:
                options = _merged_safe_options(
                    self.config_entry.data,
                    self.config_entry.options,
                    {
                        CANARY_CONTROL_ENABLED_FIELD: enabled,
                        CANARY_CONTROL_TARGET_FIELD: target,
                    },
                )
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="test_switch",
            data_schema=_test_switch_schema(enabled_default, target_default),
            errors=errors,
        )

    async def async_step_climate_connection(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Select a safe bridge stage before showing its required details."""

        mode_default, _, _ = _safe_climate_bridge_defaults(
            self.config_entry.data,
            self.config_entry.options,
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            mode = user_input.get(CLIMATE_BRIDGE_MODE_FIELD)
            if mode not in {value.value for value in ClimateBridgeMode}:
                errors[CLIMATE_BRIDGE_MODE_FIELD] = "unsafe_climate_bridge_mode"
            elif mode == ClimateBridgeMode.DISABLED.value:
                options = _merged_safe_options(
                    self.config_entry.data,
                    self.config_entry.options,
                    {
                        CLIMATE_BRIDGE_MODE_FIELD: mode,
                        CLIMATE_BRIDGE_TARGET_FIELD: None,
                        CLIMATE_CANARY_ROOM_ID_FIELD: None,
                    },
                )
                return self.async_create_entry(title="", data=options)
            else:
                self._climate_bridge_mode_draft = mode
                return await self.async_step_climate_endpoint()

        return self.async_show_form(
            step_id="climate_connection",
            data_schema=_climate_connection_schema(mode_default),
            errors=errors,
        )

    async def async_step_climate_endpoint(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Request only the address and optional one-room control ID."""

        mode = self._climate_bridge_mode_draft
        if mode not in {
            ClimateBridgeMode.SHADOW.value,
            ClimateBridgeMode.CANARY.value,
            ClimateBridgeMode.MANAGED.value,
        }:
            return await self.async_step_init()
        _, target_default, room_default = _safe_climate_bridge_defaults(
            self.config_entry.data,
            self.config_entry.options,
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            target = user_input.get(CLIMATE_BRIDGE_TARGET_FIELD)
            room_id = (
                user_input.get(CLIMATE_CANARY_ROOM_ID_FIELD)
                if mode == ClimateBridgeMode.CANARY.value
                else None
            )
            try:
                climate_bridge_target(target)
            except UnsafeClimateBridgeTarget:
                errors[CLIMATE_BRIDGE_TARGET_FIELD] = "unsafe_climate_bridge_target"
            if mode == ClimateBridgeMode.CANARY.value:
                try:
                    ClimateRoom(room_id, "Temporary")  # type: ignore[arg-type]
                except ClimateModelViolation:
                    errors[CLIMATE_CANARY_ROOM_ID_FIELD] = "unsafe_climate_canary_room"
            if not errors:
                options = _merged_safe_options(
                    self.config_entry.data,
                    self.config_entry.options,
                    {
                        CLIMATE_BRIDGE_MODE_FIELD: mode,
                        CLIMATE_BRIDGE_TARGET_FIELD: target,
                        CLIMATE_CANARY_ROOM_ID_FIELD: room_id,
                    },
                )
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="climate_endpoint",
            data_schema=_climate_endpoint_schema(
                bridge_mode=mode,
                climate_bridge_target_default=target_default,
                climate_canary_room_id_default=room_default,
            ),
            errors=errors,
        )

    async def async_step_native_climate(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Enable only HASC's non-executing decision preview."""

        mode_default, _, _, _ = _safe_native_climate_defaults(
            self.config_entry.data,
            self.config_entry.options,
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            mode = user_input.get(NATIVE_CLIMATE_MODE_FIELD)
            if mode not in {value.value for value in NativeClimateMode}:
                errors[NATIVE_CLIMATE_MODE_FIELD] = "unsafe_native_climate_mode"
            elif mode == NativeClimateMode.DISABLED.value:
                options = _merged_safe_options(
                    self.config_entry.data,
                    self.config_entry.options,
                    {
                        NATIVE_CLIMATE_MODE_FIELD: mode,
                        NATIVE_CLIMATE_ROOM_ID_FIELD: None,
                        NATIVE_TARGET_TEMPERATURE_FIELD: None,
                        NATIVE_TARGET_HUMIDITY_FIELD: None,
                    },
                )
                return self.async_create_entry(title="", data=options)
            else:
                runtime = self._climate_runtime()
                if runtime is None:
                    errors["base"] = "climate_runtime_unavailable"
                else:
                    try:
                        payload = await runtime.async_registry_payload()
                    except Exception:
                        errors["base"] = "climate_runtime_unavailable"
                    else:
                        rooms = self._rooms_from_registry_payload(payload)
                        if not rooms:
                            errors["base"] = "native_climate_needs_room"
                        else:
                            self._native_climate_mode_draft = mode
                            self._native_climate_rooms = rooms
                            return await self.async_step_native_climate_policy()

        return self.async_show_form(
            step_id="native_climate",
            data_schema=_native_climate_mode_schema(mode_default),
            errors=errors,
        )

    async def async_step_native_climate_policy(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Configure one room's targets and calculate a safe preview."""

        rooms = self._native_climate_rooms or []
        runtime = self._climate_runtime()
        if (
            self._native_climate_mode_draft != NativeClimateMode.PREVIEW.value
            or not rooms
            or runtime is None
        ):
            return await self.async_step_init()
        _, saved_room, temperature_default, humidity_default = (
            _safe_native_climate_defaults(
                self.config_entry.data,
                self.config_entry.options,
            )
        )
        room_values = {
            room["value"]
            for room in rooms
            if isinstance(room.get("value"), str)
        }
        room_default = saved_room if saved_room in room_values else rooms[0]["value"]
        errors: dict[str, str] = {}
        if user_input is not None:
            room_id = user_input.get(NATIVE_CLIMATE_ROOM_ID_FIELD)
            temperature = user_input.get(NATIVE_TARGET_TEMPERATURE_FIELD)
            humidity_raw = user_input.get(NATIVE_TARGET_HUMIDITY_FIELD)
            try:
                humidity = (
                    int(humidity_raw)
                    if isinstance(humidity_raw, str) and humidity_raw.isdigit()
                    else humidity_raw
                )
                policy = native_climate_policy(
                    NativeClimateMode.PREVIEW.value,
                    room_id,
                    temperature,
                    humidity,
                )
            except NativeClimateViolation:
                errors["base"] = "invalid_native_climate_policy"
            else:
                if policy.room_id not in room_values:
                    errors[NATIVE_CLIMATE_ROOM_ID_FIELD] = "invalid_native_climate_room"
                else:
                    try:
                        preview = await runtime.async_native_climate_preview(policy)
                    except Exception:
                        errors["base"] = "native_climate_preview_unavailable"
                    else:
                        self._native_climate_policy_draft = policy
                        self._native_climate_preview = preview
                        return await self.async_step_native_climate_confirm()

        return self.async_show_form(
            step_id="native_climate_policy",
            data_schema=_native_climate_policy_schema(
                rooms,
                room_default=room_default,
                temperature_default=temperature_default,
                humidity_default=humidity_default,
            ),
            errors=errors,
        )

    async def async_step_native_climate_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Show the calculated result and separately confirm saving targets."""

        policy = self._native_climate_policy_draft
        if policy is None or self._native_climate_preview is None:
            return await self.async_step_init()
        errors: dict[str, str] = {}
        if user_input is not None:
            confirmed = user_input.get(NATIVE_CLIMATE_CONFIRM_FIELD)
            if confirmed is not True:
                errors[NATIVE_CLIMATE_CONFIRM_FIELD] = (
                    "native_climate_confirmation_required"
                )
            else:
                options = _merged_safe_options(
                    self.config_entry.data,
                    self.config_entry.options,
                    {
                        NATIVE_CLIMATE_MODE_FIELD: policy.mode.value,
                        NATIVE_CLIMATE_ROOM_ID_FIELD: policy.room_id,
                        NATIVE_TARGET_TEMPERATURE_FIELD: policy.target_temperature,
                        NATIVE_TARGET_HUMIDITY_FIELD: policy.target_humidity,
                    },
                )
                return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="native_climate_confirm",
            data_schema=_native_climate_confirm_schema(),
            errors=errors,
            description_placeholders=self._native_climate_preview_placeholders(),
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
            if action == "review_canary_preflight":
                return await self.async_step_climate_preflight_candidate()
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
            candidates = [
                SelectOptionDict(
                    value="missing",
                    label=MISSING_CLIMATE_DEVICE_LABEL,
                )
            ]
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
                    [
                        SelectOptionDict(
                            value="missing",
                            label=MISSING_CLIMATE_ROOM_LABEL,
                        )
                    ]
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

    async def async_step_climate_preflight_candidate(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Select one saved public room for a non-activating rollout preflight."""

        runtime = self._climate_runtime()
        errors: dict[str, str] = {}
        rooms: list[dict[str, str]] = []
        if runtime is None:
            errors["base"] = "climate_runtime_unavailable"
        else:
            try:
                saved_registry = await runtime.async_registry_payload()
                rooms = self._rooms_from_registry_payload(saved_registry)
            except Exception:
                errors["base"] = "climate_preflight_unavailable"
            else:
                if not rooms:
                    errors["base"] = "climate_registry_needs_room"
        if user_input is not None and not errors and runtime is not None:
            try:
                preflight = await runtime.async_canary_preflight(
                    {"room_id": user_input.get(CLIMATE_PREFLIGHT_ROOM_FIELD)}
                )
            except Exception:
                errors["base"] = "climate_preflight_unavailable"
            else:
                self._canary_preflight = preflight
                return await self.async_step_climate_canary_preflight()
        if not rooms:
            rooms = [
                SelectOptionDict(
                    value="missing",
                    label=MISSING_CLIMATE_ROOM_LABEL,
                )
            ]
        return self.async_show_form(
            step_id="climate_preflight_candidate",
            data_schema=_climate_preflight_candidate_schema(rooms),
            errors=errors,
        )

    async def async_step_climate_canary_preflight(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Show one complete redacted result without saving or activating."""

        if self._canary_preflight is None:
            return await self.async_step_climate_preflight_candidate()
        if (
            user_input is not None
            and user_input.get(CLIMATE_PREFLIGHT_CLOSE_FIELD) is True
        ):
            self._canary_preflight = None
            return await self.async_step_climate_registry()
        return self.async_show_form(
            step_id="climate_canary_preflight",
            data_schema=_climate_canary_preflight_schema(),
            errors={},
            description_placeholders=self._canary_preflight_placeholders(),
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
                    [
                        SelectOptionDict(
                            value="missing",
                            label=MISSING_CLIMATE_ROOM_LABEL,
                        )
                    ]
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

    def _rooms_from_registry_payload(
        self,
        payload: object,
    ) -> list[dict[str, str]]:
        if not isinstance(payload, Mapping):
            return []
        rooms = payload.get("rooms")
        if not isinstance(rooms, list):
            return []
        return [
            SelectOptionDict(value=room["id"], label=room["name"])
            for room in rooms
            if isinstance(room, Mapping)
            and isinstance(room.get("id"), str)
            and isinstance(room.get("name"), str)
        ]

    def _room_display_name(self, room_id: object) -> str:
        """Return the configured room name instead of its technical identifier."""

        if isinstance(room_id, str):
            for room in self._draft_rooms():
                if room.get("value") == room_id:
                    return room["label"]
        return "неизвестная комната"

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

    def _set_contour_source_snapshot(
        self,
        snapshot: ClimateImportSnapshot,
    ) -> None:
        """Keep only ephemeral device tokens for the ordinary contour wizard."""

        candidates: dict[str, ImportedClimateDevice] = {}
        index = 0
        for candidate in snapshot.devices:
            if not candidate.suggested_kinds:
                continue
            index += 1
            candidates[f"device_{index:03d}"] = candidate
        self._contour_source_snapshot = snapshot
        self._contour_device_tokens = candidates
        self._contour_saved_name = None
        self._contour_saved_mode = None
        self._contour_saved_room_parameters = None
        self._contour_saved_source_ids = ()
        self._contour_name_draft = None
        self._contour_mode_draft = None
        self._contour_room_ids_draft = ()
        self._contour_source_ids_draft = ()
        self._contour_room_index = 0
        self._contour_room_parameters_draft = None
        self._contour_registry_draft = None
        self._contour_definition_draft = None
        self._contour_preview = None

    async def _async_load_saved_contour(self, runtime: Any) -> None:
        """Use only a fully valid saved contour as edit-form defaults."""

        try:
            from .application.climate_registry import registry_from_payload

            climate_registry = registry_from_payload(
                await runtime.async_registry_payload()
            )
            contours = contour_registry_from_payload(
                await runtime.async_contour_registry_payload()
            )
            validate_contour_bindings(contours, climate_registry)
            contour = contours.contour("climate")
            if contour is None:
                return
            parameters = {
                room.room_id: {
                    "target_temperature": room.target_temperature,
                    "target_humidity": room.target_humidity,
                    "strategy": room.strategy.value,
                }
                for room in contour.rooms
            }
            source_ids: list[str] = []
            for assignment in contour.rooms:
                for device_id in assignment.device_ids:
                    device = climate_registry.device(device_id)
                    if device is None:
                        raise ContourRegistryViolation(
                            "saved contour device is unavailable"
                        )
                    source_ids.append(device.source_id)
        except Exception:
            return
        self._contour_saved_name = contour.name
        self._contour_saved_mode = contour.mode.value
        self._contour_saved_room_parameters = parameters
        self._contour_saved_source_ids = tuple(source_ids)

    def _contour_room_defaults(
        self,
        snapshot: ClimateImportSnapshot,
        room_id: str,
    ) -> tuple[float, int, str]:
        """Use this room's current valid values, never another room's values."""

        room = snapshot.room(room_id)
        temperature = (
            room.target_temperature
            if room is not None
            and isinstance(room.target_temperature, (int, float))
            and not isinstance(room.target_temperature, bool)
            and 18 <= room.target_temperature <= 28
            and room.target_temperature * 2
            == int(room.target_temperature * 2)
            else CLIMATE_TARGET_TEMPERATURE_DEFAULT
        )
        humidity = (
            int(room.target_humidity)
            if room is not None
            and isinstance(room.target_humidity, (int, float))
            and not isinstance(room.target_humidity, bool)
            and room.target_humidity == int(room.target_humidity)
            and 30 <= room.target_humidity <= 70
            and int(room.target_humidity) % 5 == 0
            else CLIMATE_TARGET_HUMIDITY_DEFAULT
        )
        strategy = (
            room.target_strategy
            if room is not None
            and room.target_strategy in {
                ClimateStrategy.SOFT.value,
                ClimateStrategy.NORMAL.value,
                ClimateStrategy.AGGRESSIVE.value,
            }
            else ClimateStrategy.NORMAL.value
        )
        return float(temperature), humidity, strategy

    def _contour_preview_placeholders(self) -> dict[str, str]:
        """Render one contour summary without private device identifiers."""

        payload = self._contour_preview or {}
        raw_contours = payload.get("contours")
        contours = raw_contours if isinstance(raw_contours, list) else []
        contour = contours[0] if contours and isinstance(contours[0], Mapping) else {}
        raw_rooms = contour.get("rooms") if isinstance(contour, Mapping) else None
        rooms = raw_rooms if isinstance(raw_rooms, list) else []
        execution = contour.get("execution") if isinstance(contour, Mapping) else None
        execution_values = execution if isinstance(execution, Mapping) else {}
        raw_reasons = contour.get("reasons") if isinstance(contour, Mapping) else None
        reasons = raw_reasons if isinstance(raw_reasons, list) else []
        reason_text = "; ".join(
            dict.fromkeys(
                _RUSSIAN_CONTOUR_REASON_LABELS.get(value, "неизвестная причина")
                for value in reasons
                if isinstance(value, str)
            )
        )
        room_settings: list[str] = []
        for room in rooms:
            if not isinstance(room, Mapping):
                continue
            raw_targets = room.get("targets")
            targets = raw_targets if isinstance(raw_targets, Mapping) else {}
            room_settings.append(
                "{name}: {temperature} °C, {humidity} %, {strategy}".format(
                    name=str(room.get("name") or "Комната"),
                    temperature=_display_measurement(
                        targets.get("temperature"),
                        "",
                    ),
                    humidity=_display_measurement(
                        targets.get("humidity"),
                        "",
                    ),
                    strategy=_RUSSIAN_CONTOUR_STRATEGY_LABELS.get(
                        targets.get("strategy"),
                        "неизвестно",
                    ),
                )
            )
        return {
            "name": str(contour.get("name") or "Климат"),
            "mode": _RUSSIAN_CONTOUR_MODE_LABELS.get(
                contour.get("mode"),
                "неизвестно",
            ),
            "status": _RUSSIAN_CONTOUR_STATUS_LABELS.get(
                contour.get("status"),
                "неизвестно",
            ),
            "room_count": str(len(rooms)),
            "device_count": str(
                sum(
                    room.get("device_count", 0)
                    for room in rooms
                    if isinstance(room, Mapping)
                    and type(room.get("device_count")) is int
                )
            ),
            "automatic": _russian_yes_no(
                execution_values.get("automatic_active")
            ),
            "algorithm": "существующий климатический контур",
            "reasons": reason_text or "нет",
            "room_settings": "; ".join(room_settings) or "нет",
        }

    def _contour_apply_preview_placeholders(self) -> dict[str, str]:
        """Explain exact command counts without exposing engine identifiers."""

        payload = self._contour_apply_preview or {}
        raw_changes = payload.get("changes")
        changes = raw_changes if isinstance(raw_changes, Mapping) else {}
        return {
            "room_count": str(payload.get("room_count", 0)),
            "command_count": str(payload.get("command_count", 0)),
            "temperature_count": str(changes.get("temperature", 0)),
            "strategy_count": str(changes.get("strategy", 0)),
            "automatic_count": str(changes.get("automatic_mode", 0)),
        }

    def _contour_apply_result_placeholders(self) -> dict[str, str]:
        """Render one bounded application receipt in plain Russian."""

        payload = self._contour_apply_receipt or {}
        raw_reasons = payload.get("reasons")
        reasons = raw_reasons if isinstance(raw_reasons, list) else []
        reason_text = "; ".join(
            dict.fromkeys(
                _RUSSIAN_CONTOUR_APPLY_REASON_LABELS.get(
                    value,
                    "неизвестная причина",
                )
                for value in reasons
                if isinstance(value, str)
            )
        )
        return {
            "status": _RUSSIAN_CONTOUR_APPLY_STATUS_LABELS.get(
                payload.get("status"),
                "неизвестно",
            ),
            "command_count": str(payload.get("command_count", 0)),
            "accepted_count": str(payload.get("accepted_count", 0)),
            "room_count": str(payload.get("room_count", 0)),
            "confirmed_room_count": str(payload.get("confirmed_room_count", 0)),
            "reasons": reason_text or "нет",
        }

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
        return {
            "status": _russian_status(preview.get("status")),
            "room_count": str(counts.get("room_count", 0)),
            "device_count": str(counts.get("device_count", 0)),
            "reasons": _russian_reasons(preview.get("reasons")),
        }

    def _native_climate_preview_placeholders(self) -> dict[str, str]:
        """Render the HASC-owned decision in plain Russian for confirmation."""

        preview = self._native_climate_preview or {}
        current = preview.get("current")
        current_values = current if isinstance(current, Mapping) else {}
        targets = preview.get("targets")
        target_values = targets if isinstance(targets, Mapping) else {}
        decision = preview.get("decision")
        decision_values = decision if isinstance(decision, Mapping) else {}
        equipment = preview.get("equipment")
        equipment_values = equipment if isinstance(equipment, Mapping) else {}
        execution = preview.get("execution")
        execution_values = execution if isinstance(execution, Mapping) else {}
        return {
            "room": str(preview.get("room_name") or "неизвестная комната"),
            "status": _russian_native_value(
                preview.get("status"),
                _RUSSIAN_NATIVE_STATUS_LABELS,
            ),
            "current_temperature": _display_measurement(
                current_values.get("temperature"),
                " °C",
            ),
            "target_temperature": _display_measurement(
                target_values.get("temperature"),
                " °C",
            ),
            "temperature_decision": _russian_native_value(
                decision_values.get("temperature"),
                _RUSSIAN_TEMPERATURE_DEMAND_LABELS,
            ),
            "temperature_device": _russian_yes_no(
                equipment_values.get("temperature_ready")
            ),
            "current_humidity": _display_measurement(
                current_values.get("humidity"),
                " %",
            ),
            "target_humidity": _display_measurement(
                target_values.get("humidity"),
                " %",
            ),
            "humidity_decision": _russian_native_value(
                decision_values.get("humidity"),
                _RUSSIAN_HUMIDITY_DEMAND_LABELS,
            ),
            "humidity_device": _russian_yes_no(
                equipment_values.get("humidity_ready")
            ),
            "commands": _russian_yes_no(execution_values.get("commands_enabled")),
            "reasons": _russian_native_reasons(preview.get("reasons")),
        }

    def _shadow_evidence_placeholders(self) -> dict[str, str]:
        evidence = self._shadow_evidence or {}
        candidate = evidence.get("candidate")
        candidate_values = candidate if isinstance(candidate, Mapping) else {}
        counts = evidence.get("counts")
        count_values = counts if isinstance(counts, Mapping) else {}
        return {
            "room_id": self._room_display_name(candidate_values.get("room_id")),
            "status": _russian_status(candidate_values.get("status")),
            "matched": str(candidate_values.get("matched_observation_count", 0)),
            "required_matched": str(
                candidate_values.get("required_matched_observation_count", 3)
            ),
            "translated": str(candidate_values.get("translated_action_count", 0)),
            "anomalies": str(candidate_values.get("anomaly_count", 0)),
            "global_rejected": str(count_values.get("rejected", 0)),
            "reasons": _russian_reasons(candidate_values.get("reasons")),
        }

    def _canary_preflight_placeholders(self) -> dict[str, str]:
        preflight = self._canary_preflight or {}
        registry = preflight.get("registry")
        registry_values = registry if isinstance(registry, Mapping) else {}
        reconciliation = registry_values.get("reconciliation")
        reconciliation_values = (
            reconciliation if isinstance(reconciliation, Mapping) else {}
        )
        shadow = preflight.get("shadow")
        shadow_values = shadow if isinstance(shadow, Mapping) else {}
        scope = preflight.get("command_scope")
        scope_values = scope if isinstance(scope, Mapping) else {}
        actions = scope_values.get("actions")
        action_values = actions if isinstance(actions, list) else []
        operation = preflight.get("operation")
        operation_values = operation if isinstance(operation, Mapping) else {}
        rollback = preflight.get("rollback")
        rollback_values = rollback if isinstance(rollback, Mapping) else {}
        return {
            "room_id": self._room_display_name(preflight.get("room_id")),
            "status": _russian_status(preflight.get("status")),
            "registry_matches": _russian_yes_no(
                reconciliation_values.get("matches")
            ),
            "matched_devices": str(
                reconciliation_values.get("matched_device_count", 0)
            ),
            "missing_devices": str(
                reconciliation_values.get("missing_device_count", 0)
            ),
            "moved_devices": str(
                reconciliation_values.get("room_mismatch_device_count", 0)
            ),
            "unregistered_devices": str(
                reconciliation_values.get("unregistered_source_count", 0)
            ),
            "shadow_status": _russian_status(shadow_values.get("status")),
            "matched": str(shadow_values.get("matched_observation_count", 0)),
            "required_matched": str(
                shadow_values.get("required_matched_observation_count", 3)
            ),
            "translated": str(shadow_values.get("translated_action_count", 0)),
            "required_actions": str(
                shadow_values.get("required_action_count", 2)
            ),
            "anomalies": str(shadow_values.get("anomaly_count", 0)),
            "scope": _russian_actions(action_values),
            "scope_qualified": _russian_yes_no(scope_values.get("qualified")),
            "operation": _russian_status(operation_values.get("status")),
            "rollback": _russian_status(rollback_values.get("status")),
            "reasons": _russian_reasons(preflight.get("reasons")),
        }
