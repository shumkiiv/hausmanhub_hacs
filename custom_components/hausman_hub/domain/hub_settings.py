"""Persistent user-owned settings that have moved out of legacy Node-RED."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any


HUB_SETTINGS_VERSION = 2
MAX_LIGHT_PRESET_ENTITIES = 40
MAX_TV_OFF_ENTITIES = 12
MAX_CURTAIN_HOLIDAYS = 64
MAX_ENERGY_DEVICES = 128
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
_PUBLIC_DEVICE_ID = re.compile(r"^device_[0-9a-f]{16}$")
ENERGY_DISPLAY_UNITS = frozenset({"watts", "amps", "both"})
ENERGY_AGGREGATIONS = frozenset({"combined", "separate"})


class HausmanHubSettingsViolation(ValueError):
    """Saved HausmanHub settings are malformed or outside safe bounds."""


@dataclass(frozen=True, slots=True)
class HausmanHubSettings:
    """Complete native settings document for legacy user preferences."""

    light_on_entities: tuple[str, ...] = ()
    light_off_entities: tuple[str, ...] = ()
    tv_off_entities: tuple[str, ...] = ()
    climate_reports_enabled: bool = True
    curtain_holidays: tuple[str, ...] = ()
    energy_display_units: str = "watts"
    energy_show_voltage: bool = True
    energy_aggregation: str = "combined"
    energy_use_all_devices: bool = False
    energy_selected_device_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _entities(
            self.light_on_entities,
            domains=frozenset({"light", "switch"}),
            maximum=MAX_LIGHT_PRESET_ENTITIES,
            label="light on preset",
        )
        _entities(
            self.light_off_entities,
            domains=frozenset({"light", "switch"}),
            maximum=MAX_LIGHT_PRESET_ENTITIES,
            label="light off preset",
        )
        _entities(
            self.tv_off_entities,
            domains=frozenset({"media_player"}),
            maximum=MAX_TV_OFF_ENTITIES,
            label="TV off preset",
        )
        if type(self.climate_reports_enabled) is not bool:
            raise HausmanHubSettingsViolation("climate reports flag must be boolean")
        _holidays(self.curtain_holidays)
        if self.energy_display_units not in ENERGY_DISPLAY_UNITS:
            raise HausmanHubSettingsViolation("energy display units are invalid")
        if type(self.energy_show_voltage) is not bool:
            raise HausmanHubSettingsViolation("energy voltage flag must be boolean")
        if self.energy_aggregation not in ENERGY_AGGREGATIONS:
            raise HausmanHubSettingsViolation("energy aggregation is invalid")
        if type(self.energy_use_all_devices) is not bool:
            raise HausmanHubSettingsViolation("energy all-devices flag must be boolean")
        _public_device_ids(self.energy_selected_device_ids)


def hub_settings_to_payload(settings: HausmanHubSettings) -> dict[str, object]:
    """Encode the exact versioned storage document."""

    if not isinstance(settings, HausmanHubSettings):
        raise HausmanHubSettingsViolation("HausmanHub settings are required")
    return {
        "version": HUB_SETTINGS_VERSION,
        "light_on_entities": list(settings.light_on_entities),
        "light_off_entities": list(settings.light_off_entities),
        "tv_off_entities": list(settings.tv_off_entities),
        "climate_reports_enabled": settings.climate_reports_enabled,
        "curtain_holidays": list(settings.curtain_holidays),
        "energy_display_units": settings.energy_display_units,
        "energy_show_voltage": settings.energy_show_voltage,
        "energy_aggregation": settings.energy_aggregation,
        "energy_use_all_devices": settings.energy_use_all_devices,
        "energy_selected_device_ids": list(settings.energy_selected_device_ids),
    }


def hub_settings_from_payload(payload: object) -> HausmanHubSettings:
    """Decode only the current complete storage document."""

    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise HausmanHubSettingsViolation("stored HausmanHub settings must be an object")
    required = {
        "version",
        "light_on_entities",
        "light_off_entities",
        "tv_off_entities",
        "climate_reports_enabled",
        "curtain_holidays",
        "energy_display_units",
        "energy_show_voltage",
        "energy_aggregation",
        "energy_use_all_devices",
        "energy_selected_device_ids",
    }
    if set(payload) != required:
        raise HausmanHubSettingsViolation("stored HausmanHub settings fields are invalid")
    if type(payload.get("version")) is not int or payload["version"] != HUB_SETTINGS_VERSION:
        raise HausmanHubSettingsViolation("stored HausmanHub settings version is unsupported")
    return HausmanHubSettings(
        light_on_entities=_string_tuple(payload["light_on_entities"], "light on preset"),
        light_off_entities=_string_tuple(payload["light_off_entities"], "light off preset"),
        tv_off_entities=_string_tuple(payload["tv_off_entities"], "TV off preset"),
        climate_reports_enabled=payload["climate_reports_enabled"],  # type: ignore[arg-type]
        curtain_holidays=_string_tuple(payload["curtain_holidays"], "curtain holidays"),
        energy_display_units=payload["energy_display_units"],  # type: ignore[arg-type]
        energy_show_voltage=payload["energy_show_voltage"],  # type: ignore[arg-type]
        energy_aggregation=payload["energy_aggregation"],  # type: ignore[arg-type]
        energy_use_all_devices=payload["energy_use_all_devices"],  # type: ignore[arg-type]
        energy_selected_device_ids=_string_tuple(
            payload["energy_selected_device_ids"], "energy selected devices"
        ),
    )


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HausmanHubSettingsViolation(f"{label} must be a string list")
    return tuple(value)


def _entities(
    values: tuple[str, ...],
    *,
    domains: frozenset[str],
    maximum: int,
    label: str,
) -> None:
    if type(values) is not tuple or len(values) > maximum or len(values) != len(set(values)):
        raise HausmanHubSettingsViolation(f"{label} is invalid")
    for entity_id in values:
        if (
            not isinstance(entity_id, str)
            or not _ENTITY_ID.fullmatch(entity_id)
            or entity_id.split(".", 1)[0] not in domains
        ):
            raise HausmanHubSettingsViolation(f"{label} contains an invalid entity id")


def _holidays(values: tuple[str, ...]) -> None:
    if (
        type(values) is not tuple
        or len(values) > MAX_CURTAIN_HOLIDAYS
        or len(values) != len(set(values))
    ):
        raise HausmanHubSettingsViolation("curtain holidays are invalid")
    for value in values:
        if not isinstance(value, str):
            raise HausmanHubSettingsViolation("curtain holiday must be a date")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise HausmanHubSettingsViolation("curtain holiday must be an ISO date") from error
        if parsed.isoformat() != value:
            raise HausmanHubSettingsViolation("curtain holiday must be canonical")


def _public_device_ids(values: tuple[str, ...]) -> None:
    if (
        type(values) is not tuple
        or len(values) > MAX_ENERGY_DEVICES
        or len(values) != len(set(values))
        or any(
            not isinstance(value, str) or not _PUBLIC_DEVICE_ID.fullmatch(value)
            for value in values
        )
    ):
        raise HausmanHubSettingsViolation("energy selected devices are invalid")
