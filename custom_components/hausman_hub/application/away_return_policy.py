"""Admission filter for general return actions, never a second light writer."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from ..domain.away_settings import AwayAction
from ..domain.device_power_dependencies import DevicePowerDependency
from ..domain.room_lighting import RoomLightingConfig
from .scenarios import ScenarioCatalog


class AwayReturnPolicyBlocked(RuntimeError):
    """The general return batch must not dispatch this action."""


@dataclass(frozen=True)
class _PolicySnapshot:
    generation: int
    dependency_revision: int
    catalog_generation: int
    entities: frozenset[str]
    target_ids: frozenset[str]
    dependencies: Mapping[str, DevicePowerDependency]
    catalog: ScenarioCatalog


def _upstream_entities(
    entities: frozenset[str], dependencies: Mapping[str, DevicePowerDependency],
) -> frozenset[str]:
    found = set(entities)
    pending = list(entities)
    while pending:
        dependency = dependencies.get(pending.pop())
        if dependency is not None and dependency.power_source_entity_id not in found:
            found.add(dependency.power_source_entity_id)
            pending.append(dependency.power_source_entity_id)
    return frozenset(found)


class AwayReturnActionFilter:
    """Prepare return actions for one serialized AwayRuntime batch.

Read committed room settings under their mutation lock. Capture a generation
and create synchronous pre-send guards, so any later setting/binding change
refuses dispatch instead of sending with stale permission. No retry or write.
"""

    def __init__(
        self,
        *,
        configs: Callable[[], Awaitable[tuple[int, tuple[RoomLightingConfig, ...]]]],
        generation: Callable[[], int],
        catalog: Callable[[], ScenarioCatalog],
        dependencies: Callable[[], Mapping[str, DevicePowerDependency]],
        dependency_revision: Callable[[], int],
        catalog_generation: Callable[[], int],
    ) -> None:
        self._configs = configs
        self._generation = generation
        self._catalog = catalog
        self._dependencies = dependencies
        self._dependency_revision = dependency_revision
        self._catalog_generation = catalog_generation
        self._prepared: _PolicySnapshot | None = None

    async def __call__(self, actions: tuple[AwayAction, ...]) -> tuple[AwayAction, ...]:
        self._prepared = None
        generation, configs = await self._configs()
        if generation != self._generation():
            raise AwayReturnPolicyBlocked("return policy changed during preparation")
        entities: set[str] = set()
        target_ids: set[str] = set()
        for config in configs:
            returned = config.away_behavior.return_
            if returned is None or returned.initial_action is None:
                continue
            targets = list(config.devices.light_targets)
            if config.devices.power_switch is not None:
                targets.append(config.devices.power_switch)
            for target in targets:
                target_ids.add(target.id)
                if target.entity_id is not None:
                    entities.add(target.entity_id)
        dependencies = dict(self._dependencies())
        snapshot = _PolicySnapshot(
            generation, self._dependency_revision(), self._catalog_generation(),
            _upstream_entities(frozenset(entities), dependencies),
            frozenset(target_ids), dependencies, self._catalog(),
        )
        filtered = tuple(
            action for action in actions if not self._blocked(action.target_id, snapshot)
        )
        self._prepared = snapshot
        return filtered

    @staticmethod
    def _blocked(target_id: str, snapshot: _PolicySnapshot) -> bool:
        if target_id in snapshot.target_ids or target_id in snapshot.entities:
            return True
        if not (snapshot.entities or snapshot.target_ids):
            return False
        device = snapshot.catalog.device(target_id)
        # An unresolved alias cannot prove disjointness from opted-in rooms.
        return device is None or bool(
            _upstream_entities(frozenset({device.entity_id}), snapshot.dependencies)
            & snapshot.entities
        )

    def dispatch_guard(self, target_id: str) -> Callable[[], None]:
        snapshot = self._prepared
        if snapshot is None or self._blocked(target_id, snapshot):
            raise AwayReturnPolicyBlocked("return controlled by room lighting policy")
        expected = snapshot.catalog.device(target_id)

        def guard() -> None:
            if (
                self._generation() != snapshot.generation
                or self._dependency_revision() != snapshot.dependency_revision
                or self._catalog_generation() != snapshot.catalog_generation
                or self._dependencies() != snapshot.dependencies
                or self._catalog().device(target_id) != expected
            ):
                raise AwayReturnPolicyBlocked("return policy or binding changed before dispatch")

        return guard
