"""Verified HA storage and registry identity for office curtain scale authority."""

from __future__ import annotations

from homeassistant.helpers.storage import Store

from .application.curtain_scale_confirmation import (
    CurtainScaleIdentity,
    valid_curtain_scale_confirmation_payload,
)
from .verified_safety_storage import VerifiedSafetyStore


class HomeAssistantCurtainScaleConfirmationStore:
    """Keep scale authority separate from editable options and policy."""

    def __init__(self, hass: object, entry_id: str) -> None:
        backend = Store(
            hass,
            1,
            f"hausman_hub.curtain_scale_confirmation.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=valid_curtain_scale_confirmation_payload,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous


def resolve_office_curtain_identity(
    hass: object,
    catalog_resolver: object,
    target_id: str,
) -> CurtainScaleIdentity | None:
    """Resolve the exact catalog target through both HA registries."""

    from homeassistant.helpers import device_registry, entity_registry

    if not callable(catalog_resolver):
        return None
    device = catalog_resolver(target_id)
    entity_id = getattr(device, "entity_id", None)
    if not isinstance(entity_id, str) or not entity_id:
        return None
    try:
        entities = entity_registry.async_get(hass)
        entity = entities.async_get(entity_id)
        unique_id = getattr(entity, "unique_id", None)
        device_id = getattr(entity, "device_id", None)
        if not isinstance(device_id, str) or not device_id:
            return None
        devices = device_registry.async_get(hass)
        registry_device = devices.async_get(device_id)
        if (
            registry_device is None
            or getattr(registry_device, "id", device_id) != device_id
        ):
            return None
        return CurtainScaleIdentity.create(
            target_id=target_id,
            entity_id=entity_id,
            entity_unique_id=unique_id,
            device_id=device_id,
            device_identifiers=getattr(registry_device, "identifiers", None),
            office_target_id=target_id,
        )
    except (AttributeError, TypeError, ValueError):
        return None
