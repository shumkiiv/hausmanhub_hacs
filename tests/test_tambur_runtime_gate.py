"""Tests for the persisted legacy Tambur runtime deactivation gate."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.tambur_room_migration import (
    TAMBUR_INPUT_TARGET_IDS,
    TamburRoomStartupCoordinator,
)
from custom_components.hausman_hub.application.tambur_runtime_gate import (
    TamburLegacyRuntimeGate,
)


class _MemoryStore:
    def __init__(self, value: object | None = None) -> None:
        self.value = value
        self.saves = 0

    async def async_load(self) -> object | None:
        return self.value

    async def async_save(self, payload: dict[str, object]) -> None:
        self.value = payload
        self.saves += 1


class _Catalog:
    def __init__(self, target_ids: tuple[str, ...]) -> None:
        self._devices = {
            target_id: SimpleNamespace(target_id=target_id)
            for target_id in target_ids
        }

    def device(self, target_id: str) -> object | None:
        return self._devices.get(target_id)


class _Service:
    def current_catalog(self) -> _Catalog:
        return _Catalog(TAMBUR_INPUT_TARGET_IDS)

    def add_catalog_warmup_observer(self, _observer: object):
        return lambda: None


class _Migration:
    def __init__(self) -> None:
        self.applies = 0

    async def async_apply(self, *, operation_run: object = None) -> str:
        del operation_run
        self.applies += 1
        return "completed"

    def cancel(self, *, operation_run: object = None) -> None:
        del operation_run


def _activation(activations: list[object], commits: list[int]):
    async def activate(scope: object) -> object:
        activations.append(scope)
        return SimpleNamespace(
            commit=lambda: commits.append(1),
            cleanup=lambda: None,
            revoke=lambda: None,
        )

    return activate


def test_gate_defaults_to_enabled() -> None:
    assert TamburLegacyRuntimeGate().enabled is True


def test_gate_persists_and_reverts() -> None:
    store = _MemoryStore()

    async def exercise() -> None:
        first = TamburLegacyRuntimeGate(store)
        assert await first.async_load() is True
        assert await first.async_set_enabled(False, "room lighting takeover") is False

        assert store.value == {
            "version": 1,
            "enabled": False,
            "reason": "room lighting takeover",
        }

        reloaded = TamburLegacyRuntimeGate(store)
        await reloaded.async_load()
        assert reloaded.enabled is False
        assert reloaded.reason == "room lighting takeover"

        # Rollback restores the legacy runtime.
        assert await reloaded.async_set_enabled(True, "rollback") is True
        assert TamburLegacyRuntimeGate(store).restore(store.value) is True

    asyncio.run(exercise())


def test_gate_damaged_payload_fails_safe_enabled() -> None:
    gate = TamburLegacyRuntimeGate()
    gate.restore("broken")
    assert gate.enabled is True
    gate.restore({"enabled": "yes"})
    assert gate.enabled is True
    gate.restore({"enabled": False, "reason": "operator"})
    assert gate.enabled is False
    with pytest.raises(ValueError):
        gate.set_enabled("no")


def test_coordinator_does_not_start_when_deactivated() -> None:
    migration = _Migration()
    activations: list[object] = []
    commits: list[int] = []
    statuses: list[dict[str, str]] = []
    gate = SimpleNamespace(enabled=False, reason="operator deactivated")
    coordinator = TamburRoomStartupCoordinator(
        _Service(),
        migration,
        _activation(activations, commits),
        status_publisher=statuses.append,
        runtime_gate=gate,
    )

    asyncio.run(coordinator.async_start())

    assert migration.applies == 0
    assert activations == []
    assert commits == []
    assert coordinator.ready is False
    assert statuses == [{"state": "deactivated", "stage": "runtime"}]


def test_coordinator_starts_when_enabled_and_can_be_reactivated() -> None:
    migration = _Migration()
    activations: list[object] = []
    commits: list[int] = []
    statuses: list[dict[str, str]] = []
    gate = SimpleNamespace(enabled=True, reason="")

    async def exercise() -> None:
        coordinator = TamburRoomStartupCoordinator(
            _Service(),
            migration,
            _activation(activations, commits),
            status_publisher=statuses.append,
            runtime_gate=gate,
        )
        await coordinator.async_start()
        assert coordinator.ready is True
        assert migration.applies == 1
        assert len(activations) == 1
        assert commits == [1]

        # Deactivation then a fresh coordinator for the same entry: rollback.
        gate.enabled = False
        disabled = TamburRoomStartupCoordinator(
            _Service(),
            migration,
            _activation(activations, commits),
            status_publisher=statuses.append,
            runtime_gate=gate,
        )
        await disabled.async_start()
        assert disabled.ready is False
        assert migration.applies == 1
        assert statuses[-1] == {"state": "deactivated", "stage": "runtime"}

        gate.enabled = True
        restored = TamburRoomStartupCoordinator(
            _Service(),
            migration,
            _activation(activations, commits),
            status_publisher=statuses.append,
            runtime_gate=gate,
        )
        await restored.async_start()
        assert restored.ready is True
        assert migration.applies == 2

    asyncio.run(exercise())
