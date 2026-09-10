"""Validated local bindings for fixed smart-switch profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


_PROFILE_BINDINGS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "shower": (("shower-cabinet", ("toggle_b2_down", "on_b2_down", "toggle_b2_up")),),
    "passthrough": (("tambur-light-group", ("on_down", "toggle_down", "off_up")),),
    "marmitek": (
        ("tambur-mirror-left", ("1_single", "1_double")),
        ("tambur-master-off", ("2_single", "2_double")),
    ),
}
_PROFILES = frozenset(_PROFILE_BINDINGS)
_BINDINGS = frozenset(
    binding
    for entries in _PROFILE_BINDINGS.values()
    for binding, _subtypes in entries
)
_PAYLOAD_FIELDS = frozenset({"version", "revision", "devices"})


@dataclass(frozen=True, slots=True)
class SmartSwitchBindings:
    """One strictly validated local profile-to-device mapping."""

    version: int
    revision: int
    devices: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ResolvedSmartSwitchTrigger:
    """One fixed trigger paired with its local device identity."""

    binding: str
    config: Mapping[str, str]


def bindings_from_payload(value: object) -> SmartSwitchBindings | None:
    """Return local bindings only when their persisted shape is exact."""

    if not isinstance(value, Mapping) or set(value) != _PAYLOAD_FIELDS:
        return None
    version = value.get("version")
    revision = value.get("revision")
    devices = value.get("devices")
    if (
        type(version) is not int
        or version != 1
        or type(revision) is not int
        or revision < 1
        or not isinstance(devices, Mapping)
        or not set(devices) <= _PROFILES
    ):
        return None

    normalized: dict[str, str] = {}
    assigned_devices: set[str] = set()
    for profile, device_id in devices.items():
        if (
            not isinstance(profile, str)
            or not isinstance(device_id, str)
            or not device_id.strip()
            or device_id in assigned_devices
        ):
            return None
        normalized[profile] = device_id
        assigned_devices.add(device_id)
    return SmartSwitchBindings(
        version=version,
        revision=revision,
        devices=MappingProxyType(normalized),
    )


def resolve_trigger_bindings(
    bindings: SmartSwitchBindings, included_bindings: frozenset[str]
) -> tuple[ResolvedSmartSwitchTrigger, ...]:
    """Resolve only fixed profile triggers belonging to the adapter scope."""

    if not isinstance(included_bindings, frozenset) or not included_bindings <= _BINDINGS:
        return ()

    resolved: list[ResolvedSmartSwitchTrigger] = []
    for profile, entries in _PROFILE_BINDINGS.items():
        device_id = bindings.devices.get(profile)
        if device_id is None:
            continue
        for binding, subtypes in entries:
            if binding not in included_bindings:
                continue
            for subtype in subtypes:
                resolved.append(
                    ResolvedSmartSwitchTrigger(
                        binding=binding,
                        config=MappingProxyType(
                            {
                                "platform": "device",
                                "domain": "mqtt",
                                "type": "action",
                                "device_id": device_id,
                                "subtype": subtype,
                            }
                        ),
                    )
                )
    return tuple(resolved)


def valid_smart_switch_bindings_payload(value: object) -> bool:
    """Validate an unmodified store payload before trusting it."""

    return bindings_from_payload(value) is not None


class HomeAssistantSmartSwitchBindingsStore:
    """Verified HA Store wrapper for local smart-switch bindings."""

    def __init__(self, hass: object, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store  # type: ignore[import-not-found]
        from ..verified_safety_storage import VerifiedSafetyStore

        backend = Store(
            hass,
            1,
            f"hausman_hub.smart_switch_bindings.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=valid_smart_switch_bindings_payload,
        )

    async def async_load(self) -> object:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous
