"""Validation, persistence and effective-state tests for power dependencies."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from custom_components.hausman_hub.application.device_power_dependencies import (
    DevicePowerDependencyService,
    DevicePowerDependencyServiceViolation,
)
from custom_components.hausman_hub.domain.device_power_dependencies import (
    DevicePowerDependency,
    DevicePowerDependencyViolation,
    effective_device_state,
    validate_device_power_dependencies,
)


class _Store:
    def __init__(self, loaded: dict[str, object] | None = None) -> None:
        self.loaded = loaded
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> dict[str, object] | None:
        return self.loaded

    async def async_save(self, value: dict[str, object]) -> None:
        self.saved.append(value)


def _dependency(
    dependent: str = "light.ceiling",
    source: str = "switch.wall",
    *,
    policy: str = "requires_on",
    warmup_seconds: int | None = None,
) -> dict[str, object]:
    dependency: dict[str, object] = {
        "dependentEntityId": dependent,
        "powerSourceEntityId": source,
        "policy": policy,
    }
    if warmup_seconds is not None:
        dependency["warmupSeconds"] = warmup_seconds
    return dependency


def _link(
    dependent: str = "light.ceiling",
    source: str = "switch.wall",
    *,
    policy: str = "requires_on",
    warmup_seconds: int = 0,
) -> DevicePowerDependency:
    return DevicePowerDependency(dependent, source, policy, warmup_seconds)


class DevicePowerDependencyDomainTest(unittest.TestCase):
    def test_off_source_forces_stale_on_child_to_effective_off(self) -> None:
        states = {"switch.wall": "off", "light.ceiling": "on"}
        effective, status = effective_device_state(
            "light.ceiling",
            {"light.ceiling": _link()},
            states.get,
        )
        self.assertEqual("off", effective)
        self.assertIsNotNone(status)
        self.assertEqual("unpowered", status.state)
        self.assertEqual("power_source_off", status.reason)
        self.assertTrue(status.blocks_commands)

    def test_dependency_chain_uses_effective_upstream_state(self) -> None:
        states = {
            "switch.main": "off",
            "switch.branch": "on",
            "light.ceiling": "on",
        }
        effective, status = effective_device_state(
            "light.ceiling",
            {
                "switch.branch": _link("switch.branch", "switch.main"),
                "light.ceiling": _link("light.ceiling", "switch.branch"),
            },
            states.get,
        )
        self.assertEqual("off", effective)
        self.assertEqual("unpowered", status.state)

    def test_auto_source_off_keeps_command_available_for_preparation(self) -> None:
        effective, status = effective_device_state(
            "light.ceiling",
            {
                "light.ceiling": _link(
                    policy="auto_turn_on", warmup_seconds=5
                )
            },
            {"switch.wall": "off", "light.ceiling": "on"}.get,
        )

        self.assertEqual("off", effective)
        self.assertIsNotNone(status)
        self.assertFalse(status.blocks_commands)
        self.assertTrue(status.auto_power_on)
        self.assertEqual(5, status.warmup_seconds)

    def test_auto_policy_requires_explicit_bounded_warmup(self) -> None:
        with self.assertRaises(DevicePowerDependencyViolation):
            validate_device_power_dependencies(
                [_dependency(policy="auto_turn_on")]
            )
        with self.assertRaises(DevicePowerDependencyViolation):
            validate_device_power_dependencies(
                [
                    _dependency(
                        policy="auto_turn_on", warmup_seconds=31
                    )
                ]
            )

    def test_cycles_and_duplicate_dependents_are_rejected(self) -> None:
        with self.assertRaises(DevicePowerDependencyViolation):
            validate_device_power_dependencies(
                [_dependency(), _dependency(source="switch.other")]
            )
        with self.assertRaises(DevicePowerDependencyViolation):
            validate_device_power_dependencies(
                [
                    _dependency("switch.first", "switch.second"),
                    _dependency("switch.second", "switch.first"),
                ]
            )


class DevicePowerDependencyServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_exact_source_migration_is_cas_safe_and_survives_restart(self) -> None:
        old_source = "switch.0x603d61fffe75c334_1"
        new_source = "switch.0x603d61fffe759363_1"
        dependent = "light.0xa4c138d69d102803"
        untouched = _dependency(
            "light.user_configured",
            "switch.user_configured",
            policy="auto_turn_on",
            warmup_seconds=7,
        )
        store = _Store(
            {
                "revision": 4,
                "updatedAt": "2026-09-06T06:00:00Z",
                "dependencies": [
                    _dependency(dependent, old_source),
                    untouched,
                ],
            }
        )
        validated_pairs: list[tuple[str, str]] = []

        def validate_pair(current_dependent: str, current_source: str) -> bool:
            validated_pairs.append((current_dependent, current_source))
            return (current_dependent, current_source) == (dependent, new_source)

        service = DevicePowerDependencyService(
            store,
            entity_pair_validator=validate_pair,
            now=lambda: datetime(2026, 9, 6, 6, 5, tzinfo=timezone.utc),
        )
        await service.async_load()

        migrated = await service.async_migrate_exact_source(
            4,
            dependent_entity_id=dependent,
            old_source_entity_id=old_source,
            new_source_entity_id=new_source,
        )

        self.assertTrue(migrated)
        self.assertEqual([(dependent, new_source)], validated_pairs)
        self.assertEqual(5, service.document["revision"])
        self.assertEqual(
            [
                _dependency(dependent, new_source),
                untouched,
            ],
            service.document["dependencies"],
        )
        self.assertEqual(
            [
                {
                    "revision": 5,
                    "updatedAt": "2026-09-06T06:05:00Z",
                    "dependencies": [
                        _dependency(dependent, new_source),
                        untouched,
                    ],
                }
            ],
            store.saved,
        )

        restarted_store = _Store(store.saved[-1])
        restarted = DevicePowerDependencyService(
            restarted_store,
            entity_pair_validator=validate_pair,
        )
        await restarted.async_load()
        migrated_again = await restarted.async_migrate_exact_source(
            5,
            dependent_entity_id=dependent,
            old_source_entity_id=old_source,
            new_source_entity_id=new_source,
        )

        self.assertFalse(migrated_again)
        self.assertEqual(5, restarted.document["revision"])
        self.assertEqual([], restarted_store.saved)
        self.assertEqual(service.document, restarted.document)

    async def test_exact_source_migration_never_overwrites_a_different_mapping(self) -> None:
        dependent = "light.0xa4c138d69d102803"
        configured_source = "switch.owner_selected"
        store = _Store(
            {
                "revision": 9,
                "updatedAt": "2026-09-06T06:00:00Z",
                "dependencies": [_dependency(dependent, configured_source)],
            }
        )
        service = DevicePowerDependencyService(
            store,
            entity_pair_validator=lambda *_pair: True,
        )
        await service.async_load()

        migrated = await service.async_migrate_exact_source(
            9,
            dependent_entity_id=dependent,
            old_source_entity_id="switch.0x603d61fffe75c334_1",
            new_source_entity_id="switch.0x603d61fffe759363_1",
        )

        self.assertFalse(migrated)
        self.assertEqual(9, service.document["revision"])
        self.assertEqual(
            [_dependency(dependent, configured_source)],
            service.document["dependencies"],
        )
        self.assertEqual([], store.saved)

    async def test_exact_source_migration_rejects_a_stale_revision(self) -> None:
        service = DevicePowerDependencyService(
            _Store(
                {
                    "revision": 4,
                    "updatedAt": "2026-09-06T06:00:00Z",
                    "dependencies": [
                        _dependency(
                            "light.0xa4c138d69d102803",
                            "switch.0x603d61fffe75c334_1",
                        )
                    ],
                }
            ),
            entity_pair_validator=lambda *_pair: True,
        )
        await service.async_load()

        with self.assertRaises(DevicePowerDependencyServiceViolation) as raised:
            await service.async_migrate_exact_source(
                3,
                dependent_entity_id="light.0xa4c138d69d102803",
                old_source_entity_id="switch.0x603d61fffe75c334_1",
                new_source_entity_id="switch.0x603d61fffe759363_1",
            )

        self.assertTrue(raised.exception.stale)

    async def test_replace_is_durable_and_optimistically_locked(self) -> None:
        store = _Store()
        service = DevicePowerDependencyService(
            store,
            entity_pair_validator=lambda dependent, source: {
                dependent,
                source,
            }
            == {"light.ceiling", "switch.wall"},
            now=lambda: datetime(2026, 8, 10, 19, 0, tzinfo=timezone.utc),
        )
        await service.async_load()
        document = await service.async_replace(0, [_dependency()])
        self.assertEqual(1, document["revision"])
        self.assertEqual(
            {"light.ceiling": _link()},
            service.mapping,
        )
        self.assertEqual(1, len(store.saved))
        with self.assertRaises(DevicePowerDependencyServiceViolation) as raised:
            await service.async_replace(0, [])
        self.assertTrue(raised.exception.stale)

    async def test_replace_rejects_missing_live_entities(self) -> None:
        service = DevicePowerDependencyService(
            _Store(), entity_pair_validator=lambda _dependent, _source: False
        )
        await service.async_load()
        with self.assertRaises(DevicePowerDependencyServiceViolation):
            await service.async_replace(0, [_dependency()])

    async def test_restart_loads_the_same_dependency_mapping(self) -> None:
        service = DevicePowerDependencyService(
            _Store(
                {
                    "revision": 4,
                    "updatedAt": "2026-08-10T19:00:00Z",
                    "dependencies": [_dependency()],
                }
            )
        )
        await service.async_load()
        self.assertEqual(4, service.document["revision"])
        self.assertEqual({"light.ceiling": _link()}, service.mapping)

    async def test_auto_policy_and_warmup_survive_restart(self) -> None:
        stored = {
            "revision": 2,
            "updatedAt": "2026-08-26T09:45:00Z",
            "dependencies": [
                _dependency(policy="auto_turn_on", warmup_seconds=5)
            ],
        }
        service = DevicePowerDependencyService(_Store(stored))

        await service.async_load()

        self.assertEqual(
            {
                "light.ceiling": _link(
                    policy="auto_turn_on", warmup_seconds=5
                )
            },
            service.mapping,
        )
        self.assertEqual(stored["dependencies"], service.document["dependencies"])
