"""Tests for the configurable away/return settings and runtime."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.away_runtime import AwayRuntime
from custom_components.hausman_hub.application.away_settings import (
    AwaySettingsService,
    AwaySettingsServiceViolation,
)
from custom_components.hausman_hub.domain.away_settings import (
    AwaySettingsViolation,
    validate_away_settings,
)

NOW = 1_800_000_000_000


class MemoryStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload
        self.saves = 0

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, value: dict[str, object]) -> None:
        self.payload = value
        self.saves += 1


def _settings_document() -> dict[str, object]:
    return {
        "triggers": [
            {"entityId": "lock.aqara_smart_lock_a100", "activeState": "locked"},
            {
                "entityId": "binary_sensor.a100_away_zaniatost",
                "activeState": "on",
                "forSeconds": 3,
            },
        ],
        "awayActions": [
            {"targetId": "entity_71859313239a14e4", "actionId": "turn_off"},
        ],
        "returnActions": [
            {"targetId": "entity_71859313239a14e4", "actionId": "turn_on"},
        ],
    }


def test_validate_away_settings_accepts_canonical_document() -> None:
    settings = validate_away_settings(_settings_document())
    assert len(settings.triggers) == 2
    assert settings.triggers[1].for_seconds == 3
    assert settings.away_actions[0].action_id == "turn_off"
    assert settings.return_actions[0].action_id == "turn_on"


@pytest.mark.parametrize(
    "settings",
    [
        {"triggers": [{"entityId": "Bad Entity", "activeState": "on"}], "awayActions": [], "returnActions": []},
        {"triggers": [{"entityId": "lock.a", "activeState": "nonsense"}], "awayActions": [], "returnActions": []},
        {"triggers": [], "awayActions": [{"targetId": "entity_a", "actionId": "turn_on", "value": 5}], "returnActions": []},
        {"triggers": [], "awayActions": [{"targetId": "entity_a", "actionId": "set_brightness_percent", "value": 200}], "returnActions": []},
        {"triggers": [], "awayActions": [{"targetId": "entity_a", "actionId": "turn_on"}, {"targetId": "entity_a", "actionId": "turn_off"}], "returnActions": []},
        {"triggers": [], "awayActions": [], "returnActions": [], "extra": 1},
    ],
)
def test_validate_away_settings_rejects_unsafe_documents(settings: object) -> None:
    with pytest.raises(AwaySettingsViolation):
        validate_away_settings(settings)


@pytest.mark.asyncio
async def test_service_round_trips_and_rejects_stale_revision() -> None:
    store = MemoryStore()
    service = AwaySettingsService(store, now=lambda: datetime(2026, 9, 11, tzinfo=timezone.utc))
    await service.async_load()
    assert service.settings.active is False

    document = await service.async_replace(0, _settings_document())
    assert document["revision"] == 1
    assert service.settings.active is True

    with pytest.raises(AwaySettingsServiceViolation) as error:
        await service.async_replace(0, _settings_document())
    assert error.value.stale is True

    reloaded = AwaySettingsService(store)
    await reloaded.async_load()
    assert reloaded.settings.triggers[0].entity_id == "lock.aqara_smart_lock_a100"
    assert reloaded.document["revision"] == 1

    reset = await reloaded.async_reset()
    assert reset["revision"] == 2
    assert reloaded.settings.active is False


@pytest.mark.asyncio
async def test_service_validates_trigger_entities_against_home() -> None:
    service = AwaySettingsService(
        MemoryStore(),
        entity_id_validator=lambda entity_id: entity_id == "lock.aqara_smart_lock_a100",
    )
    await service.async_load()
    with pytest.raises(AwaySettingsServiceViolation):
        await service.async_replace(0, _settings_document())


class RuntimeHarness:
    def __init__(self, active_state: str = "locked") -> None:
        self.states: dict[str, object] = {}
        self.calls: list[tuple[tuple[object, ...], str]] = []
        self.callbacks: list[object] = []
        self.subscriptions: list[tuple[str, ...]] = []
        self._active_state = active_state

    async def runner(self, actions, correlation):  # noqa: ANN001
        self.calls.append((tuple(actions), correlation))
        return []

    def track(self, entity_ids, callback):  # noqa: ANN001
        self.subscriptions.append(tuple(entity_ids))
        self.callbacks.append(callback)
        return lambda: None

    def runtime(self, settings_provider) -> AwayRuntime:  # noqa: ANN001
        return AwayRuntime(
            settings_provider=settings_provider,
            state_provider=lambda entity_id: self.states.get(entity_id),
            action_runner=self.runner,
            now_ms=lambda: NOW,
            track_state_changes=self.track,
        )


def _state(value: str, **attributes: object) -> object:
    return SimpleNamespace(state=value, attributes=dict(attributes))


async def _drain() -> None:
    for _ in range(6):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_runtime_observes_initial_state_without_commands() -> None:
    harness = RuntimeHarness()
    harness.states = {
        "lock.aqara_smart_lock_a100": _state("locked"),
        "binary_sensor.a100_away_zaniatost": _state("on"),
    }
    settings = validate_away_settings(_settings_document())
    runtime = harness.runtime(lambda: settings)
    await runtime.async_start()
    await _drain()
    assert runtime.status["awayActive"] is True
    assert harness.calls == []
    await runtime.async_stop()


@pytest.mark.asyncio
async def test_runtime_runs_away_and_return_on_real_transitions() -> None:
    harness = RuntimeHarness()
    harness.states = {
        "lock.aqara_smart_lock_a100": _state("unlocked"),
        "binary_sensor.a100_away_zaniatost": _state("off"),
    }
    document = _settings_document()
    for trigger in document["triggers"]:  # type: ignore[union-attr]
        trigger.pop("forSeconds", None)
    settings = validate_away_settings(document)
    runtime = harness.runtime(lambda: settings)
    await runtime.async_start()

    harness.states["lock.aqara_smart_lock_a100"] = _state("locked")
    harness.states["binary_sensor.a100_away_zaniatost"] = _state("on")
    for callback in harness.callbacks:
        callback(SimpleNamespace(data={"entity_id": "lock.aqara_smart_lock_a100"}))
        callback(SimpleNamespace(data={"entity_id": "binary_sensor.a100_away_zaniatost"}))
    await _drain()
    assert harness.calls and harness.calls[-1][1].startswith("away.away.")
    assert runtime.status["awayActive"] is True

    harness.states["lock.aqara_smart_lock_a100"] = _state("unlocked")
    for callback in harness.callbacks:
        callback(SimpleNamespace(data={"entity_id": "lock.aqara_smart_lock_a100"}))
    await _drain()
    assert harness.calls[-1][1].startswith("away.return.")
    assert runtime.status["awayActive"] is False
    await runtime.async_stop()


@pytest.mark.asyncio
async def test_runtime_never_treats_unknown_trigger_as_away() -> None:
    harness = RuntimeHarness()
    harness.states = {
        "lock.aqara_smart_lock_a100": _state("unavailable"),
        "binary_sensor.a100_away_zaniatost": _state("on"),
    }
    settings = validate_away_settings(_settings_document())
    runtime = harness.runtime(lambda: settings)
    await runtime.async_start()
    await _drain()
    assert runtime.status["awayActive"] is False
    assert harness.calls == []
    await runtime.async_stop()


@pytest.mark.asyncio
async def test_runtime_does_not_fabricate_return_on_unreliable_trigger() -> None:
    harness = RuntimeHarness()
    document = _settings_document()
    for trigger in document["triggers"]:  # type: ignore[union-attr]
        trigger.pop("forSeconds", None)
    settings = validate_away_settings(document)
    harness.states = {
        "lock.aqara_smart_lock_a100": _state("locked"),
        "binary_sensor.a100_away_zaniatost": _state("on"),
    }
    runtime = harness.runtime(lambda: settings)
    await runtime.async_start()
    assert runtime.status["awayActive"] is True

    harness.states["binary_sensor.a100_away_zaniatost"] = _state("unavailable")
    for callback in harness.callbacks:
        callback(SimpleNamespace(data={"entity_id": "binary_sensor.a100_away_zaniatost"}))
    await _drain()
    assert runtime.status["awayActive"] is True
    assert harness.calls == []
    await runtime.async_stop()


@pytest.mark.asyncio
async def test_runtime_refresh_rearms_from_the_settings_provider() -> None:
    harness = RuntimeHarness()
    state = {"settings": validate_away_settings({"triggers": [], "awayActions": [], "returnActions": []})}
    runtime = harness.runtime(lambda: state["settings"])
    await runtime.async_start()
    assert runtime.status["active"] is False

    state["settings"] = validate_away_settings(_settings_document())
    await runtime.async_refresh()
    assert runtime.status["active"] is True
    assert harness.subscriptions[-1] == (
        "binary_sensor.a100_away_zaniatost",
        "lock.aqara_smart_lock_a100",
    )
    await runtime.async_stop()
