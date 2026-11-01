"""Immutable, explicit room-level graph of physically managed light sources."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from ..domain.device_power_dependencies import DevicePowerDependency


@dataclass(frozen=True, slots=True)
class RelatedLightSources:
    ready: bool
    components: Mapping[str, frozenset[str]]
    # Entities with complete physical scope AND separate single-device
    # (registry-backed) evidence; an HA group never qualifies on wiring alone.
    proven_entities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", MappingProxyType(dict(self.components)))
        proven = frozenset(self.proven_entities)
        if not self.ready:
            proven = frozenset()
        elif not proven <= frozenset(self.components):
            raise ValueError("proven entities must be a subset of components")
        object.__setattr__(self, "proven_entities", proven)

    @classmethod
    def from_room_projections(
        cls, configs: Iterable[object],
        projections: Mapping[str, Mapping[str, DevicePowerDependency] | None],
        *, fallback_dependencies: Mapping[str, DevicePowerDependency] | None = None,
        proven_single_entities: Iterable[str] = frozenset(),
    ) -> "RelatedLightSources":
        """Use individually proved room graphs; retain legacy grouping elsewhere.

        A mapping is accepted only when its caller proved the entire room's
        physical topology. None retains the previous conservative room scope.
        Connected components are merged across rooms for shared power sources.

        ``proven_single_entities`` carries SEPARATE evidence that an entity is
        one physical device (registry-backed), never a bare HA group. Physical
        completeness alone does not prove single-device-ness, so the published
        confirmation is the intersection of complete-proven components and
        this set.
        """
        groups: list[tuple[set[str], bool]] = []
        owners: dict[str, str] = {}
        seen_rooms: set[str] = set()
        for config in configs:
            room = config.room_id
            if not isinstance(room, str) or room in seen_rooms or room not in projections:
                return cls(False, {})
            seen_rooms.add(room)
            for target in config.devices.light_targets:
                entity = target.entity_id
                if not isinstance(entity, str):
                    return cls(False, {})
                if entity in owners and owners[entity] != room:
                    return cls(False, {})
                owners[entity] = room
            projection = projections[room]
            graph = (cls.from_configs((config,)) if projection is None else
                     cls.from_power_dependencies((config,), projection, complete=True))
            if not graph.ready:
                return cls(False, {})
            if projection is None:
                group = set(graph.components)
                power = getattr(config.devices, "power_switch", None)
                power_entity = getattr(power, "entity_id", None)
                if isinstance(power_entity, str) and power_entity.startswith(("light.", "switch.")):
                    group.add(power_entity)
                if fallback_dependencies is not None:
                    for entity in tuple(group):
                        seen = set()
                        while entity in fallback_dependencies:
                            dependency = fallback_dependencies[entity]
                            if (entity in seen or not isinstance(dependency, DevicePowerDependency)
                                    or dependency.dependent_entity_id != entity):
                                return cls(False, {})
                            seen.add(entity)
                            entity = dependency.power_source_entity_id
                            group.add(entity)
                component = frozenset(group)
                graph = cls(True, {entity: component for entity in group})
                room_components: tuple[tuple[frozenset[str], bool], ...] = ((component, False),)
            else:
                # A proved complete projection confirms only its own component's
                # scope; the fallback path confirms nothing.
                room_components = tuple(
                    (frozenset(members), True)
                    for members in set(graph.components.values())
                )
            for component, component_proven in room_components:
                merged = set(component)
                merged_proven = component_proven
                remaining: list[tuple[set[str], bool]] = []
                for group, group_proven in groups:
                    if group & merged:
                        merged.update(group)
                        # A component touching an unproved room keeps no provenance.
                        merged_proven = merged_proven and group_proven
                    else:
                        remaining.append((group, group_proven))
                groups = remaining
                groups.append((merged, merged_proven))
        result: dict[str, frozenset[str]] = {}
        proven: set[str] = set()
        for group, group_proven in groups:
            component = frozenset(group)
            result.update({entity: component for entity in group})
            if group_proven:
                proven.update(group)
        # Only entities with separate single-device evidence stay confirmed;
        # a complete physical map by itself does not exclude HA groups.
        proven &= frozenset(proven_single_entities)
        return cls(True, result, frozenset(proven))

    @classmethod
    def from_power_dependencies(
        cls, configs: Iterable[object], dependencies: Mapping[str, DevicePowerDependency] | None,
        *, complete: bool,
        proven_single_entities: Iterable[str] = frozenset(),
    ) -> "RelatedLightSources":
        """Group actual power links; never infer a link from room membership.

        Completeness must be established by the caller, not inferred from a
        nonempty map. An incomplete or inconsistent map leaves dispatch closed.

        ``complete=True`` proves only the physical wiring, not that each entity
        is a single device rather than an HA group; ``proven_single_entities``
        must carry that separate registry-backed evidence. The published
        confirmation is the intersection of both.
        """
        invalid = cls(False, {})
        if complete is not True or not isinstance(dependencies, Mapping):
            return invalid
        links: dict[str, str] = {}
        for key, dependency in dependencies.items():
            if (not isinstance(dependency, DevicePowerDependency)
                    or key != dependency.dependent_entity_id):
                return invalid
            links[key] = dependency.power_source_entity_id
        for entity in links:
            seen: set[str] = set()
            current = entity
            while current in links:
                if current in seen:
                    return invalid
                seen.add(current)
                current = links[current]

        components: dict[str, set[str]] = {}
        owners: dict[str, str] = {}
        for config in configs:
            for target in config.devices.light_targets:
                entity = target.entity_id
                if not isinstance(entity, str) or not entity.startswith(("light.", "switch.")):
                    return invalid
                owner = owners.setdefault(entity, config.room_id)
                if owner != config.room_id:
                    return invalid
                chain = {entity}
                current = entity
                while current in links:
                    current = links[current]
                    chain.add(current)
                overlapping = [key for key, group in components.items() if group & chain]
                for key in overlapping:
                    chain.update(components.pop(key))
                components[entity] = chain
        result: dict[str, frozenset[str]] = {}
        for group in components.values():
            frozen = frozenset(group)
            result.update({entity: frozen for entity in group})
        # Complete wiring alone never confirms single-device-ness; the
        # registry-backed evidence narrows the confirmation further.
        proven = frozenset(result) & frozenset(proven_single_entities)
        return cls(True, result, proven)

    @classmethod
    def from_configs(cls, configs: Iterable[object]) -> "RelatedLightSources":
        components: dict[str, frozenset[str]] = {}
        owners: dict[str, str] = {}
        for config in configs:
            entities: list[str] = []
            for target in config.devices.light_targets:
                entity_id = target.entity_id
                if not isinstance(entity_id, str) or not entity_id.startswith(("light.", "switch.")):
                    return cls(False, {})
                entities.append(entity_id)
            component = frozenset(entities)
            for entity_id in component:
                owner = owners.get(entity_id)
                if owner is not None and owner != config.room_id:
                    return cls(False, {})
                owners[entity_id] = config.room_id
                existing = components.get(entity_id)
                if existing is not None and existing != component:
                    return cls(False, {})
            for entity_id in component:
                components[entity_id] = component
        return cls(True, components)

    def related(self, entity_id: str) -> frozenset[str]:
        return self.components.get(entity_id, frozenset((entity_id,)))
