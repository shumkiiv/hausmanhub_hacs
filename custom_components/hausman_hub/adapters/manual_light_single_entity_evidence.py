"""Registry-backed evidence that an entity is one physical device.

A complete physical wiring map proves room scope, but it never proves that an
entity is not a bare Home Assistant group (a group can be unknown/unavailable
without a composition attribute). This adapter provides the SEPARATE positive
evidence required before an unavailable entity may bypass the group check:
the entity must be explicitly bound to a physical Zigbee device, its registry
entry must exactly match that binding, and it must be enabled.

Read-only and synchronous: no subscriptions, no network calls, no commands.
Missing evidence of any kind yields an empty result; nothing is inferred from
entity names or platform deny-lists.
"""

from __future__ import annotations

from typing import Iterable

from .lighting_mqtt_observations import MQTT_ENTRY, ROLLOUT_BINDINGS

_BINDING_UNIQUE_IDS = {
    binding.entity_id: binding.unique_id for binding in ROLLOUT_BINDINGS
}


def proven_single_entity_evidence(hass: object, candidates: Iterable[str]) -> frozenset[str]:
    """Return the subset of ``candidates`` proven to be single devices.

    Every check must pass for each entity:
    the entity id is an explicit rollout binding; a registry entry exists;
    its platform is the device bridge; it belongs to the expected config
    entry; its unique_id equals the bound physical device; it is enabled.
    Any missing registry, field, mismatch or read failure yields no evidence.
    """
    wanted = frozenset(candidates) & frozenset(_BINDING_UNIQUE_IDS)
    if not wanted:
        return frozenset()
    try:
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        proven: set[str] = set()
        for entity_id in wanted:
            entry = registry.async_get(entity_id)
            if (entry is not None
                    and entry.platform == "mqtt"
                    and entry.config_entry_id == MQTT_ENTRY
                    and entry.unique_id == _BINDING_UNIQUE_IDS[entity_id]
                    and entry.disabled_by is None):
                proven.add(entity_id)
        return frozenset(proven)
    except Exception:  # No registry access means no evidence, never a bypass.
        return frozenset()
