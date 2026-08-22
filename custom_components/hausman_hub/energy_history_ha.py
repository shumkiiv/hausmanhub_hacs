"""Home Assistant Recorder adapter for bounded tablet energy history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import logging
from typing import TYPE_CHECKING

from .application.energy_history import EnergySeriesDescriptor, build_energy_history

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_LOGGER = logging.getLogger(__name__)

_MEASUREMENTS: dict[str, tuple[str, str, str, float]] = {
    "power": ("Мощность", "W", "mean", 1.0),
    "current": ("Ток", "A", "mean", 1.0),
    "voltage": ("Напряжение", "V", "mean", 1.0),
    "energy": ("Энергия", "kWh", "sum", 1.0),
}


def _enum_string(value: object) -> str | None:
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) and raw else None


def _scale(device_class: str, unit: object) -> float | None:
    units = {
        "power": {"W": 1.0, "kW": 1000.0},
        "current": {"A": 1.0, "mA": 0.001},
        "voltage": {"V": 1.0, "mV": 0.001},
        "energy": {"kWh": 1.0, "Wh": 0.001},
    }
    return units.get(device_class, {}).get(unit) if isinstance(unit, str) else None


def _descriptors(
    hass: HomeAssistant,
    dashboard: Mapping[str, object],
    requested_device_ids: frozenset[str],
) -> tuple[EnergySeriesDescriptor, ...]:
    sources = dashboard.get("energy", {})
    source_values = sources.get("sources", []) if isinstance(sources, Mapping) else []
    if not isinstance(source_values, Sequence):
        return ()
    source_by_id = {
        source.get("deviceId"): source
        for source in source_values
        if isinstance(source, Mapping) and isinstance(source.get("deviceId"), str)
    }
    devices = dashboard.get("devices", [])
    if not isinstance(devices, Sequence):
        return ()
    result: list[EnergySeriesDescriptor] = []
    for device in devices:
        if not isinstance(device, Mapping):
            continue
        device_id = device.get("id")
        if not isinstance(device_id, str) or device_id not in source_by_id:
            continue
        if requested_device_ids and device_id not in requested_device_ids:
            continue
        details = device.get("details", [])
        if not isinstance(details, Sequence):
            continue
        for detail in details:
            if not isinstance(detail, Mapping):
                continue
            entity_id = detail.get("entityId")
            if not isinstance(entity_id, str):
                continue
            state = hass.states.get(entity_id)
            attributes = getattr(state, "attributes", {})
            device_class = _enum_string(
                attributes.get("device_class") if isinstance(attributes, Mapping) else None
            )
            if device_class not in _MEASUREMENTS:
                continue
            scale = _scale(
                device_class,
                attributes.get("unit_of_measurement") if isinstance(attributes, Mapping) else None,
            )
            if scale is None:
                continue
            label, unit, value_key, base_scale = _MEASUREMENTS[device_class]
            result.append(
                EnergySeriesDescriptor(
                    entity_id=entity_id,
                    source_id=f"{device_id}:{device_class}",
                    device_id=device_id,
                    name=f"{device.get('name') or 'Устройство'} · {label}",
                    room_id=(
                        device.get("roomId")
                        if isinstance(device.get("roomId"), str)
                        else None
                    ),
                    unit=unit,
                    value_key=value_key,
                    scale=scale * base_scale,
                    metric=device_class,
                )
            )
    # Reserve four contract slots for server-owned selection aggregates.
    return tuple(result[:124])


async def async_energy_history(
    hass: HomeAssistant,
    *,
    dashboard: Mapping[str, object],
    start: datetime,
    end: datetime,
    interval: str,
    requested_device_ids: frozenset[str],
    window: str = "explicit",
    timezone_name: str | None = None,
) -> dict[str, object]:
    """Read statistics through Recorder's public Python boundary."""

    descriptors = _descriptors(hass, dashboard, requested_device_ids)
    rows: Mapping[str, object] = {}
    if descriptors:
        try:
            from homeassistant.components.recorder import get_instance  # noqa: PLC0415
            from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
                statistics_during_period,
            )

            period = {"5m": "5minute", "15m": "5minute", "1h": "hour", "1d": "day"}[interval]
            rows = await get_instance(hass).async_add_executor_job(
                statistics_during_period,
                hass,
                start,
                end,
                {item.entity_id for item in descriptors},
                period,
                None,
                {"mean", "state", "sum"},
            )
        except Exception:
            _LOGGER.exception(
                "Не удалось прочитать статистику энергии Recorder для %s рядов",
                len(descriptors),
            )
            rows = {}
    return build_energy_history(
        start=start,
        end=end,
        interval=interval,
        descriptors=descriptors,
        rows_by_entity=rows,
        window=window,
        timezone_name=timezone_name,
    )
