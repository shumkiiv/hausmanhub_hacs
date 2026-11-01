"""Runtime graph builder must ask the evidence provider and apply it.

The runtime is the only place that ADDS single-device proof; events may only
remove it. These checks pin the wiring: the provider is called with the full
component set, its intersection lands in ``proven_entities``, a provider
failure stays fail-closed, and the same method backs both publication and
freshness equality.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.hausman_hub.application.device_power_dependencies import (
    DevicePowerDependency,
)
from custom_components.hausman_hub.application.room_lighting_runtime import (
    RoomLightingRuntime,
)
from custom_components.hausman_hub.application.room_power_migration import (
    room_target_claim_key,
)


def _runtime(configs, provider):
    runtime = object.__new__(RoomLightingRuntime)
    runtime._configs = configs
    runtime._power_migration_manifest = {}
    runtime._single_entity_evidence = (
        provider if provider is not None else (lambda candidates: frozenset())
    )
    return runtime


def _room(room_id, targets):
    return SimpleNamespace(room_id=room_id, devices=SimpleNamespace(light_targets=tuple(
        SimpleNamespace(id=target_id, entity_id=entity_id)
        for target_id, entity_id in targets
    )))


def _dependencies(pairs):
    return {
        entity: DevicePowerDependency(entity, power)
        for entity, power in pairs
    }


def test_provider_is_called_and_proof_is_applied():
    config = _room("room", (("lamp", "switch.lamp"), ("power", "switch.power")))
    dependencies = _dependencies((("switch.lamp", "switch.power"),))
    runtime = _runtime({"room": config}, lambda candidates: frozenset(candidates))
    runtime._power_migration_manifest = {
        room_target_claim_key("room", "lamp"): "switch.power",
    }
    graph = runtime._related_sources_for_configs(dependencies)
    assert graph.ready
    assert graph.proven_entities == frozenset({"switch.lamp", "switch.power"})


def test_provider_failure_stays_fail_closed():
    config = _room("room", (("lamp", "switch.lamp"),))
    dependencies = _dependencies((("switch.lamp", "switch.power"),))
    runtime = _runtime({"room": config}, lambda candidates: 1 / 0)
    runtime._power_migration_manifest = {
        room_target_claim_key("room", "lamp"): "switch.power",
    }
    graph = runtime._related_sources_for_configs(dependencies)
    assert graph.ready
    assert graph.proven_entities == frozenset()


def test_default_provider_confirms_nothing():
    config = _room("room", (("lamp", "switch.lamp"),))
    dependencies = _dependencies((("switch.lamp", "switch.power"),))
    runtime = _runtime({"room": config}, None)
    runtime._power_migration_manifest = {
        room_target_claim_key("room", "lamp"): "switch.power",
    }
    graph = runtime._related_sources_for_configs(dependencies)
    assert graph.ready
    assert graph.proven_entities == frozenset()
