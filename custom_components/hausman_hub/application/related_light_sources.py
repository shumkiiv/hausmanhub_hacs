"""Immutable, explicit room-level graph of physically managed light sources."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class RelatedLightSources:
    ready: bool
    components: dict[str, frozenset[str]]

    @classmethod
    def from_configs(cls, configs: Iterable[object]) -> "RelatedLightSources":
        components: dict[str, frozenset[str]] = {}
        for config in configs:
            entities: list[str] = []
            for target in config.devices.light_targets:
                entity_id = target.entity_id
                if not isinstance(entity_id, str) or not entity_id.startswith(("light.", "switch.")):
                    return cls(False, {})
                entities.append(entity_id)
            component = frozenset(entities)
            for entity_id in component:
                existing = components.get(entity_id)
                if existing is not None and existing != component:
                    return cls(False, {})
            for entity_id in component:
                components[entity_id] = component
        return cls(True, components)

    def related(self, entity_id: str) -> frozenset[str]:
        return self.components.get(entity_id, frozenset((entity_id,)))
