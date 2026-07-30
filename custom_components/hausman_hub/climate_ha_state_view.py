"""Home Assistant state view for the native climate observation adapter."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from typing import TYPE_CHECKING

from .application.climate_ha_observations import (
    MAX_STATE_LENGTH,
    ClimateHaEntityState,
)
from .application.climate_native_setup import (
    CLIMATE_HA_CATALOG_DOMAINS,
    CLIMATE_HA_SENSOR_DEVICE_CLASSES,
    ClimateHaCatalogEntry,
    ClimateHaCatalogRoom,
    ClimateHaEntityCatalog,
)
from .application.climate_signal_settings import (
    SIGNAL_DOMAINS_BY_KIND,
    SIGNAL_KINDS,
    signal_candidate_is_suitable,
)
from .ha_area_ids import stable_area_room_id
from .application.device_presentation import zigbee2mqtt_image_url

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_OBSERVED_ATTRIBUTES = frozenset(
    {
        "hvac_action",
        "temperature",
        "current_temperature",
        "fan_mode",
        "humidity",
    }
)
_MAX_DEVICE_DETAIL_LENGTH = 160


@dataclass(frozen=True, slots=True)
class _EntityDevicePresentation:
    """Bounded public presentation metadata for one HA registry device."""

    group_id: str
    name: str | None
    manufacturer: str | None
    model: str | None
    image_url: str | None


class HomeAssistantClimateStateView:
    """Expose bounded immutable entity states to the native adapter."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def entity_state(self, entity_id: str) -> ClimateHaEntityState | None:
        """Return one current bounded state, or None when it cannot be used."""

        state = self._hass.states.get(entity_id)
        if state is None or len(state.state) > MAX_STATE_LENGTH:
            return None
        attributes = {
            key: value
            for key, value in state.attributes.items()
            if key in _OBSERVED_ATTRIBUTES
            and type(value) in {str, int, float, bool}
        }
        return ClimateHaEntityState(
            entity_id=entity_id,
            state=state.state,
            attributes=attributes,
            last_updated_ms=int(state.last_updated.timestamp() * 1000),
        )

    def entity_catalog(self) -> ClimateHaEntityCatalog:
        """Enumerate climate-relevant entities for native setup discovery."""

        return self._climate_entity_catalog(CLIMATE_HA_CATALOG_DOMAINS)

    def binding_entity_catalog(self) -> ClimateHaEntityCatalog:
        """Include compatible switches only for explicit saved-device binding."""

        return self._climate_entity_catalog(
            CLIMATE_HA_CATALOG_DOMAINS | {"switch"}
        )

    def _climate_entity_catalog(
        self,
        domains: frozenset[str],
    ) -> ClimateHaEntityCatalog:
        """Build one bounded catalog for the requested climate domains."""

        states = []
        for state in self._hass.states.async_all():
            domain = state.entity_id.split(".", 1)[0]
            if domain not in domains:
                continue
            device_class = state.attributes.get("device_class")
            if (
                domain == "sensor"
                and device_class not in CLIMATE_HA_SENSOR_DEVICE_CLASSES
            ):
                continue
            if len(state.state) > MAX_STATE_LENGTH:
                continue
            states.append(state)
        rooms, entity_rooms, entity_devices, entity_categories = self._room_catalog(
            tuple(state.entity_id for state in states)
        )
        entries: list[ClimateHaCatalogEntry] = []
        for state in states:
            domain = state.entity_id.split(".", 1)[0]
            device_class = state.attributes.get("device_class")
            supported_features = state.attributes.get("supported_features")
            friendly_name = state.attributes.get("friendly_name")
            device = entity_devices.get(state.entity_id)
            entity_category = entity_categories.get(state.entity_id)
            if domain == "sensor" and entity_category == "diagnostic":
                continue
            entries.append(
                ClimateHaCatalogEntry(
                    entity_id=state.entity_id,
                    domain=domain,
                    state=state.state,
                    device_class=(
                        device_class if isinstance(device_class, str) else None
                    ),
                    supported_features=(
                        int(supported_features)
                        if isinstance(supported_features, int)
                        and supported_features >= 0
                        else 0
                    ),
                    friendly_name=(
                        friendly_name if isinstance(friendly_name, str) else None
                    ),
                    available=state.state not in {"", "unavailable", "unknown"},
                    last_updated_ms=int(state.last_updated.timestamp() * 1000),
                    room_id=entity_rooms.get(
                        state.entity_id,
                        "",
                    ),
                    entity_category=entity_category,
                    device_group_id=None if device is None else device.group_id,
                    device_name=None if device is None else device.name,
                    manufacturer=(
                        None if device is None else device.manufacturer
                    ),
                    model=None if device is None else device.model,
                    image_url=None if device is None else device.image_url,
                    hvac_modes=(
                        _bounded_hvac_modes(state.attributes.get("hvac_modes"))
                        if domain == "climate"
                        else ()
                    ),
                )
            )
        return ClimateHaEntityCatalog(
            entries=tuple(
                sorted(entries, key=lambda entry: entry.entity_id)
            ),
            rooms=rooms,
        )

    def _room_catalog(
        self,
        entity_ids: tuple[str, ...],
    ) -> tuple[
        tuple[ClimateHaCatalogRoom, ...],
        dict[str, str],
        dict[str, _EntityDevicePresentation],
        dict[str, str],
    ]:
        """Resolve HA areas and bounded device presentation metadata read-only."""

        try:
            area_module = importlib.import_module(
                "homeassistant.helpers.area_registry"
            )
            device_module = importlib.import_module(
                "homeassistant.helpers.device_registry"
            )
            entity_module = importlib.import_module(
                "homeassistant.helpers.entity_registry"
            )
        except ModuleNotFoundError:
            # The pure unit-test environment intentionally has no HA package.
            return (), {}, {}, {}

        area_registry = area_module.async_get(self._hass)
        device_registry = device_module.async_get(self._hass)
        entity_registry = entity_module.async_get(self._hass)
        list_areas = getattr(area_registry, "async_list_areas", None)
        raw_area_entries = (
            list_areas()
            if callable(list_areas)
            else tuple(getattr(area_registry, "areas", {}).values())
        )
        area_entries = sorted(
            (
                area
                for area in raw_area_entries
                if isinstance(getattr(area, "id", None), str)
            ),
            key=lambda area: area.id,
        )
        area_room_ids: dict[str, str] = {}
        rooms: list[ClimateHaCatalogRoom] = []
        used_room_ids: set[str] = set()
        for area in area_entries:
            source_area_id = str(area.id)
            room_id = stable_area_room_id(source_area_id, used_room_ids)
            used_room_ids.add(room_id)
            area_room_ids[source_area_id] = room_id
            rooms.append(
                ClimateHaCatalogRoom(
                    room_id=room_id,
                    name=_bounded_area_name(area.name, room_id),
                )
            )

        entity_rooms: dict[str, str] = {}
        entity_devices: dict[str, _EntityDevicePresentation] = {}
        entity_categories: dict[str, str] = {}
        for entity_id in entity_ids:
            registry_entry = _registry_entry(
                entity_registry,
                entity_id,
                collection_name="entities",
                match_entity_id=True,
            )
            if registry_entry is None:
                continue
            entity_category = _entity_category(
                getattr(registry_entry, "entity_category", None)
            )
            if entity_category is not None:
                entity_categories[entity_id] = entity_category
            source_area_id = registry_entry.area_id
            device_entry = None
            if registry_entry.device_id:
                device_entry = _registry_entry(
                    device_registry,
                    registry_entry.device_id,
                    collection_name="devices",
                )
                if device_entry is not None:
                    entity_devices[entity_id] = _device_presentation(
                        registry_entry.device_id,
                        device_entry,
                    )
            if not source_area_id and device_entry is not None:
                source_area_id = (
                    device_entry.area_id
                )
            room_id = area_room_ids.get(source_area_id)
            if room_id is not None:
                entity_rooms[entity_id] = room_id

        return tuple(rooms), entity_rooms, entity_devices, entity_categories

    def ir_remote_catalog(self) -> ClimateHaEntityCatalog:
        """Enumerate IR/RF remotes read-only for setup guidance hints.

        Remotes are never climate candidates; this bounded catalog only lets
        the wizard point a room at a missing SmartIR-style climate facade.
        """

        states = []
        for state in self._hass.states.async_all():
            if state.entity_id.split(".", 1)[0] != "remote":
                continue
            if len(state.state) > MAX_STATE_LENGTH:
                continue
            states.append(state)
        rooms, entity_rooms, entity_devices, _categories = self._room_catalog(
            tuple(state.entity_id for state in states)
        )
        entries: list[ClimateHaCatalogEntry] = []
        for state in states:
            friendly_name = state.attributes.get("friendly_name")
            device = entity_devices.get(state.entity_id)
            entries.append(
                ClimateHaCatalogEntry(
                    entity_id=state.entity_id,
                    domain="remote",
                    state=state.state,
                    device_class=None,
                    supported_features=0,
                    friendly_name=(
                        friendly_name if isinstance(friendly_name, str) else None
                    ),
                    available=state.state not in {"", "unavailable", "unknown"},
                    last_updated_ms=int(state.last_updated.timestamp() * 1000),
                    room_id=entity_rooms.get(state.entity_id, ""),
                    device_group_id=None if device is None else device.group_id,
                    device_name=None if device is None else device.name,
                    manufacturer=(
                        None if device is None else device.manufacturer
                    ),
                    model=None if device is None else device.model,
                    image_url=None,
                )
            )
        return ClimateHaEntityCatalog(
            entries=tuple(
                sorted(entries, key=lambda entry: entry.entity_id)
            ),
            rooms=rooms,
        )

    def signal_entity_catalog(
        self,
        signal_kind: str,
    ) -> ClimateHaEntityCatalog:
        """Enumerate only entities usable for one signal binding selection."""

        if signal_kind not in SIGNAL_KINDS:
            return ClimateHaEntityCatalog(entries=())
        states = []
        for state in self._hass.states.async_all():
            domain = state.entity_id.split(".", 1)[0]
            if domain not in SIGNAL_DOMAINS_BY_KIND[signal_kind]:
                continue
            if len(state.state) > MAX_STATE_LENGTH:
                continue
            states.append(state)
        rooms, entity_rooms, entity_devices, entity_categories = self._room_catalog(
            tuple(state.entity_id for state in states)
        )
        room_names = {room.room_id: room.name for room in rooms}
        entries: list[ClimateHaCatalogEntry] = []
        for state in states:
            domain = state.entity_id.split(".", 1)[0]
            friendly_name = state.attributes.get("friendly_name")
            device_class = state.attributes.get("device_class")
            entity_category = entity_categories.get(state.entity_id)
            room_id = entity_rooms.get(state.entity_id, "")
            if not signal_candidate_is_suitable(
                signal_kind,
                domain=domain,
                device_class=(
                    device_class if isinstance(device_class, str) else None
                ),
                entity_category=entity_category,
                attributes=state.attributes,
                entity_id=state.entity_id,
                friendly_name=(
                    friendly_name if isinstance(friendly_name, str) else None
                ),
                room_name=room_names.get(room_id),
            ):
                continue
            device = entity_devices.get(state.entity_id)
            entries.append(
                ClimateHaCatalogEntry(
                    entity_id=state.entity_id,
                    domain=domain,
                    state=state.state,
                    device_class=(
                        device_class if isinstance(device_class, str) else None
                    ),
                    supported_features=0,
                    friendly_name=(
                        friendly_name if isinstance(friendly_name, str) else None
                    ),
                    available=state.state not in {"", "unavailable", "unknown"},
                    last_updated_ms=int(state.last_updated.timestamp() * 1000),
                    room_id=room_id,
                    entity_category=entity_category,
                    device_group_id=None if device is None else device.group_id,
                    device_name=None if device is None else device.name,
                    manufacturer=(
                        None if device is None else device.manufacturer
                    ),
                    model=None if device is None else device.model,
                    image_url=None if device is None else device.image_url,
                )
            )
        return ClimateHaEntityCatalog(
            entries=tuple(
                sorted(entries, key=lambda entry: entry.entity_id)
            ),
            rooms=rooms,
        )


def _bounded_area_name(value: object, room_id: str) -> str:
    """Return a non-empty name accepted by the climate domain."""

    if isinstance(value, str):
        normalized = value.strip()[:120].rstrip()
        if normalized:
            return normalized
    return f"Комната {room_id}"[:120]


def _entity_category(value: object) -> str | None:
    """Normalize an HA EntityCategory enum without importing its concrete type."""

    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) and raw else None


def _registry_entry(
    registry: object,
    key: str,
    *,
    collection_name: str,
    match_entity_id: bool = False,
) -> object | None:
    """Read one registry entry across the real HA and minimal test shapes."""

    getter = getattr(registry, "async_get", None)
    if callable(getter):
        return getter(key)
    collection = getattr(registry, collection_name, None)
    if not isinstance(collection, dict):
        return None
    direct = collection.get(key)
    if direct is not None or not match_entity_id:
        return direct
    return next(
        (
            entry
            for entry in collection.values()
            if getattr(entry, "entity_id", None) == key
        ),
        None,
    )


def _device_presentation(
    registry_device_id: str,
    device_entry: object,
) -> _EntityDevicePresentation:
    """Project only display-safe facts and an opaque physical-device group."""

    group_digest = hashlib.sha256(
        registry_device_id.encode("utf-8")
    ).hexdigest()[:16]
    name = _bounded_device_detail(
        getattr(device_entry, "name_by_user", None)
        or getattr(device_entry, "name", None)
    )
    manufacturer = _bounded_device_detail(
        getattr(device_entry, "manufacturer", None)
    )
    model = _bounded_device_detail(getattr(device_entry, "model", None))
    model_id = _bounded_device_detail(getattr(device_entry, "model_id", None))
    identifiers = getattr(device_entry, "identifiers", ()) or ()
    image_url = zigbee2mqtt_image_url(model_id, identifiers)
    return _EntityDevicePresentation(
        group_id=f"device_{group_digest}",
        name=name,
        manufacturer=manufacturer,
        model=model,
        image_url=image_url,
    )


def _bounded_device_detail(value: object) -> str | None:
    """Return one compact single-line registry label or no public value."""

    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())[:_MAX_DEVICE_DETAIL_LENGTH].rstrip()
    return normalized or None


def _bounded_hvac_modes(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 16:
        return ()
    if not all(isinstance(mode, str) and len(mode) <= 32 for mode in value):
        return ()
    return tuple(value)
