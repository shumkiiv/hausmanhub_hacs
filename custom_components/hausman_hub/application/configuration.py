"""Use cases for storing and reading safe configuration values.

Only an approved mode and the hard general direct-execution block are
represented in saved entry data. Options may also arm the deliberately narrow
input-boolean control canary. Unknown values are rejected instead of being
copied into a config entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.configuration import (
    APPROVED_SUMMARY_UPDATE_INTERVALS,
    DIRECT_EXECUTION_BLOCKED,
    SafeConfiguration,
    SUMMARY_UPDATE_INTERVAL_DEFAULT,
    UnsafeModeError,
    configuration_for_mode,
)
from ..domain.climate import ClimateModelViolation, ClimateRoom
from ..domain.climate_bridge import (
    ClimateBridgeMode,
    UnsafeClimateBridgeTarget,
    climate_bridge_target,
)
from ..domain.control import UnsafeCanaryTargetError, canary_control_target
from ..domain.native_climate import (
    NativeClimateMode,
    NativeClimateViolation,
    native_climate_policy,
)


MODE_FIELD = "mode"
DIRECT_EXECUTION_STATUS_FIELD = "direct_execution_status"
LOCAL_SUMMARY_ENABLED_FIELD = "local_summary_enabled"
LOCAL_SUMMARY_ENABLED_DEFAULT = True
SUMMARY_UPDATE_INTERVAL_FIELD = "summary_update_interval"
CANARY_CONTROL_ENABLED_FIELD = "canary_control_enabled"
CANARY_CONTROL_ENABLED_DEFAULT = False
CANARY_CONTROL_TARGET_FIELD = "canary_control_target"
CLIMATE_BRIDGE_MODE_FIELD = "climate_bridge_mode"
CLIMATE_BRIDGE_MODE_DEFAULT = ClimateBridgeMode.DISABLED.value
CLIMATE_BRIDGE_TARGET_FIELD = "climate_bridge_target"
CLIMATE_CANARY_ROOM_ID_FIELD = "climate_canary_room_id"
NATIVE_CLIMATE_MODE_FIELD = "native_climate_mode"
NATIVE_CLIMATE_MODE_DEFAULT = NativeClimateMode.DISABLED.value
NATIVE_CLIMATE_ROOM_ID_FIELD = "native_climate_room_id"
NATIVE_TARGET_TEMPERATURE_FIELD = "native_target_temperature"
NATIVE_TARGET_HUMIDITY_FIELD = "native_target_humidity"


class ConfigurationViolation(ValueError):
    """A config-entry value does not satisfy the safety contract."""


def create_initial_entry(mode_value: object) -> dict[str, str]:
    """Return the only data shape permitted when an entry is created."""

    configuration = _configuration_for(mode_value)
    return {
        MODE_FIELD: configuration.mode,
        DIRECT_EXECUTION_STATUS_FIELD: configuration.direct_execution_status,
    }


def create_options(
    mode_value: object,
    local_summary_enabled_value: object = LOCAL_SUMMARY_ENABLED_DEFAULT,
    summary_update_interval_value: object = SUMMARY_UPDATE_INTERVAL_DEFAULT,
    canary_control_enabled_value: object = CANARY_CONTROL_ENABLED_DEFAULT,
    canary_control_target_value: object = None,
    climate_bridge_mode_value: object = CLIMATE_BRIDGE_MODE_DEFAULT,
    climate_bridge_target_value: object = None,
    climate_canary_room_id_value: object = None,
    native_climate_mode_value: object = NATIVE_CLIMATE_MODE_DEFAULT,
    native_climate_room_id_value: object = None,
    native_target_temperature_value: object = None,
    native_target_humidity_value: object = None,
) -> dict[str, str | bool | float | int]:
    """Return only validated observation and canary-control choices."""

    if canary_control_enabled_value is False:
        # Turning the canary off is the rollback path. Never retain a previous
        # target merely because the frontend still submitted its visible value.
        canary_control_target_value = None
    if climate_bridge_mode_value == ClimateBridgeMode.DISABLED.value:
        # Disabled is the complete bridge rollback: private target and canary
        # selection must not survive in saved options.
        climate_bridge_target_value = None
        climate_canary_room_id_value = None
    elif climate_bridge_mode_value == ClimateBridgeMode.SHADOW.value:
        climate_canary_room_id_value = None
    if native_climate_mode_value == NativeClimateMode.DISABLED.value:
        # Preview rollback must also remove the selected room and its targets.
        native_climate_room_id_value = None
        native_target_temperature_value = None
        native_target_humidity_value = None
    configuration = _configuration_for(
        mode_value,
        local_summary_enabled_value,
        summary_update_interval_value,
        canary_control_enabled_value,
        canary_control_target_value,
        climate_bridge_mode_value,
        climate_bridge_target_value,
        climate_canary_room_id_value,
        native_climate_mode_value,
        native_climate_room_id_value,
        native_target_temperature_value,
        native_target_humidity_value,
    )
    options: dict[str, str | bool | float | int] = {
        MODE_FIELD: configuration.mode,
        LOCAL_SUMMARY_ENABLED_FIELD: configuration.local_summary_enabled,
        SUMMARY_UPDATE_INTERVAL_FIELD: configuration.summary_update_interval,
        CANARY_CONTROL_ENABLED_FIELD: configuration.canary_control_enabled,
        CLIMATE_BRIDGE_MODE_FIELD: configuration.climate_bridge_mode.value,
    }
    if configuration.canary_control_target is not None:
        options[CANARY_CONTROL_TARGET_FIELD] = (
            configuration.canary_control_target.entity_id
        )
    if configuration.climate_bridge_target is not None:
        options[CLIMATE_BRIDGE_TARGET_FIELD] = configuration.climate_bridge_target.origin
    if configuration.climate_canary_room_id is not None:
        options[CLIMATE_CANARY_ROOM_ID_FIELD] = configuration.climate_canary_room_id
    native_policy = configuration.native_climate_policy
    if native_policy.mode is NativeClimateMode.PREVIEW:
        if (
            native_policy.room_id is None
            or native_policy.target_temperature is None
            or native_policy.target_humidity is None
        ):
            raise ConfigurationViolation("native climate preview is incomplete")
        options[NATIVE_CLIMATE_MODE_FIELD] = native_policy.mode.value
        options[NATIVE_CLIMATE_ROOM_ID_FIELD] = native_policy.room_id
        options[NATIVE_TARGET_TEMPERATURE_FIELD] = native_policy.target_temperature
        options[NATIVE_TARGET_HUMIDITY_FIELD] = native_policy.target_humidity
    return options


def effective_configuration(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> SafeConfiguration:
    """Validate persisted values and return their safe effective meaning."""

    _require_exact_keys(
        entry_data,
        {MODE_FIELD, DIRECT_EXECUTION_STATUS_FIELD},
        "entry data",
    )
    _require_allowed_keys(
        options,
        {
            MODE_FIELD,
            LOCAL_SUMMARY_ENABLED_FIELD,
            SUMMARY_UPDATE_INTERVAL_FIELD,
            CANARY_CONTROL_ENABLED_FIELD,
            CANARY_CONTROL_TARGET_FIELD,
            CLIMATE_BRIDGE_MODE_FIELD,
            CLIMATE_BRIDGE_TARGET_FIELD,
            CLIMATE_CANARY_ROOM_ID_FIELD,
            NATIVE_CLIMATE_MODE_FIELD,
            NATIVE_CLIMATE_ROOM_ID_FIELD,
            NATIVE_TARGET_TEMPERATURE_FIELD,
            NATIVE_TARGET_HUMIDITY_FIELD,
        },
        "options",
    )

    if entry_data.get(DIRECT_EXECUTION_STATUS_FIELD) != DIRECT_EXECUTION_BLOCKED:
        raise ConfigurationViolation("direct execution must remain blocked")

    mode_value = options.get(MODE_FIELD, entry_data.get(MODE_FIELD))
    local_summary_enabled_value = options.get(
        LOCAL_SUMMARY_ENABLED_FIELD,
        LOCAL_SUMMARY_ENABLED_DEFAULT,
    )
    summary_update_interval_value = options.get(
        SUMMARY_UPDATE_INTERVAL_FIELD,
        SUMMARY_UPDATE_INTERVAL_DEFAULT,
    )
    canary_control_enabled_value = options.get(
        CANARY_CONTROL_ENABLED_FIELD,
        CANARY_CONTROL_ENABLED_DEFAULT,
    )
    canary_control_target_value = options.get(CANARY_CONTROL_TARGET_FIELD)
    climate_bridge_mode_value = options.get(
        CLIMATE_BRIDGE_MODE_FIELD,
        CLIMATE_BRIDGE_MODE_DEFAULT,
    )
    climate_bridge_target_value = options.get(CLIMATE_BRIDGE_TARGET_FIELD)
    climate_canary_room_id_value = options.get(CLIMATE_CANARY_ROOM_ID_FIELD)
    native_climate_mode_value = options.get(
        NATIVE_CLIMATE_MODE_FIELD,
        NATIVE_CLIMATE_MODE_DEFAULT,
    )
    native_climate_room_id_value = options.get(NATIVE_CLIMATE_ROOM_ID_FIELD)
    native_target_temperature_value = options.get(NATIVE_TARGET_TEMPERATURE_FIELD)
    native_target_humidity_value = options.get(NATIVE_TARGET_HUMIDITY_FIELD)
    return _configuration_for(
        mode_value,
        local_summary_enabled_value,
        summary_update_interval_value,
        canary_control_enabled_value,
        canary_control_target_value,
        climate_bridge_mode_value,
        climate_bridge_target_value,
        climate_canary_room_id_value,
        native_climate_mode_value,
        native_climate_room_id_value,
        native_target_temperature_value,
        native_target_humidity_value,
    )


def _configuration_for(
    mode_value: object,
    local_summary_enabled_value: object = LOCAL_SUMMARY_ENABLED_DEFAULT,
    summary_update_interval_value: object = SUMMARY_UPDATE_INTERVAL_DEFAULT,
    canary_control_enabled_value: object = CANARY_CONTROL_ENABLED_DEFAULT,
    canary_control_target_value: object = None,
    climate_bridge_mode_value: object = CLIMATE_BRIDGE_MODE_DEFAULT,
    climate_bridge_target_value: object = None,
    climate_canary_room_id_value: object = None,
    native_climate_mode_value: object = NATIVE_CLIMATE_MODE_DEFAULT,
    native_climate_room_id_value: object = None,
    native_target_temperature_value: object = None,
    native_target_humidity_value: object = None,
) -> SafeConfiguration:
    """Build the configuration after checking every optional choice."""

    if type(local_summary_enabled_value) is not bool:
        raise ConfigurationViolation("local summary setting must be true or false")
    if (
        not isinstance(summary_update_interval_value, str)
        or summary_update_interval_value not in APPROVED_SUMMARY_UPDATE_INTERVALS
    ):
        raise ConfigurationViolation(
            "summary update interval must be one of: "
            + ", ".join(APPROVED_SUMMARY_UPDATE_INTERVALS)
        )
    if type(canary_control_enabled_value) is not bool:
        raise ConfigurationViolation("canary control setting must be true or false")

    target = None
    if canary_control_enabled_value:
        try:
            target = canary_control_target(canary_control_target_value)
        except UnsafeCanaryTargetError as error:
            raise ConfigurationViolation(str(error)) from error
    elif canary_control_target_value is not None:
        raise ConfigurationViolation(
            "disabled canary control must not retain a target"
        )

    try:
        bridge_mode = ClimateBridgeMode(climate_bridge_mode_value)
    except (TypeError, ValueError) as error:
        raise ConfigurationViolation("climate bridge mode must be approved") from error
    bridge_target = None
    canary_room_id = None
    if bridge_mode is ClimateBridgeMode.DISABLED:
        if climate_bridge_target_value is not None or climate_canary_room_id_value is not None:
            raise ConfigurationViolation(
                "disabled climate bridge must not retain a target or canary room"
            )
    else:
        try:
            bridge_target = climate_bridge_target(climate_bridge_target_value)
        except UnsafeClimateBridgeTarget as error:
            raise ConfigurationViolation(str(error)) from error
        if bridge_mode is ClimateBridgeMode.CANARY:
            try:
                canary_room_id = ClimateRoom(
                    climate_canary_room_id_value,  # type: ignore[arg-type]
                    "Temporary",
                ).room_id
            except ClimateModelViolation as error:
                raise ConfigurationViolation(
                    "climate canary room must be a stable lowercase id"
                ) from error
        elif climate_canary_room_id_value is not None:
            raise ConfigurationViolation("shadow climate bridge must not retain a canary room")

    try:
        native_policy = native_climate_policy(
            native_climate_mode_value,
            native_climate_room_id_value,
            native_target_temperature_value,
            native_target_humidity_value,
        )
    except NativeClimateViolation as error:
        raise ConfigurationViolation(str(error)) from error

    try:
        mode_configuration = configuration_for_mode(mode_value)
    except UnsafeModeError as error:
        raise ConfigurationViolation(str(error)) from error
    return SafeConfiguration(
        mode=mode_configuration.mode,
        direct_execution_status=mode_configuration.direct_execution_status,
        local_summary_enabled=local_summary_enabled_value,
        summary_update_interval=summary_update_interval_value,
        canary_control_enabled=canary_control_enabled_value,
        canary_control_target=target,
        climate_bridge_mode=bridge_mode,
        climate_bridge_target=bridge_target,
        climate_canary_room_id=canary_room_id,
        native_climate_policy=native_policy,
    )


def _require_exact_keys(
    values: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    """Require every fixed saved field and reject any hidden channel."""

    missing = allowed - set(values)
    if missing:
        names = ", ".join(sorted(missing))
        raise ConfigurationViolation(f"{label} is missing required fields: {names}")

    _require_allowed_keys(values, allowed, label)


def _require_allowed_keys(
    values: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    """Reject unmodelled optional fields so they cannot become a hidden channel."""

    unexpected = set(values) - allowed
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ConfigurationViolation(f"{label} contains unsupported fields: {names}")
