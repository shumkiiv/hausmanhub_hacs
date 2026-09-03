"""Event adapter tests for durable manual light-off protection."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.hausman_hub.application.scenario_command_context import (
    ScenarioCommandContextRegistry,
)
from custom_components.hausman_hub.application.manual_light_off_protection import (
    ManualLightOffProtectionCoordinator,
)
from custom_components.hausman_hub.manual_light_off_protection_events import (
    ManualLightOffProtectionEventListener,
    async_start_manual_light_off_protection_events,
    configured_manual_light_off_protection_entities,
)


class _MemoryStore:
    def __init__(self) -> None:
        self.payload: object | None = None

    async def async_load(self) -> object | None:
        return copy.deepcopy(self.payload)

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = copy.deepcopy(payload)


def _settings() -> dict[str, object]:
    return {
        "globalPolicy": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_only",
            "stableAbsenceSeconds": 30,
            "extendOnRepeatedManualOff": True,
            "noSensorFallback": "timer_only",
            "protectedScope": "profile",
            "allowManualRelease": True,
        },
        "roomOverrides": {},
        "profileOverrides": {},
        "profiles": [
            {
                "roomId": "tambur",
                "profileId": "tambur_lights",
                "lightIds": ["light.tambur"],
                "presenceSensorIds": ["binary_sensor.tambur_presence"],
            }
        ],
    }


async def _coordinator() -> ManualLightOffProtectionCoordinator:
    coordinator = ManualLightOffProtectionCoordinator(_MemoryStore())
    await coordinator.async_load()
    await coordinator.async_replace_settings("settings.1", 0, _settings())
    return coordinator


def _state(value: str) -> SimpleNamespace:
    return SimpleNamespace(state=value)


def _event(
    entity_id: str,
    old: str,
    new: str,
    context: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        data={"entity_id": entity_id, "old_state": _state(old), "new_state": _state(new)},
        context=context,
    )


def test_automatic_off_is_attributed_and_never_activates_manual_protection() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        contexts = ScenarioCommandContextRegistry(
            context_factory=lambda: SimpleNamespace(id="automatic.off", parent_id=None)
        )
        context = contexts.create("light.tambur", "off")
        listener = ManualLightOffProtectionEventListener(
            coordinator, contexts, {"light.tambur", "binary_sensor.tambur_presence"}
        )

        await listener.async_handle(_event("light.tambur", "on", "off", context))

        assert coordinator.snapshot()["protections"] == []

    asyncio.run(exercise())


def test_fresh_external_off_is_forwarded_once_but_repeated_state_is_ignored() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        listener = ManualLightOffProtectionEventListener(
            coordinator, None, {"light.tambur", "binary_sensor.tambur_presence"}
        )

        await listener.async_handle(_event("light.tambur", "on", "off"))
        await listener.async_handle(_event("light.tambur", "off", "off"))

        assert len(coordinator.snapshot()["protections"]) == 1

    asyncio.run(exercise())


def test_manual_on_cancels_protection_and_unrelated_entities_are_not_forwarded() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        listener = ManualLightOffProtectionEventListener(
            coordinator, None, {"light.tambur", "binary_sensor.tambur_presence"}
        )

        await listener.async_handle(_event("light.other", "on", "off"))
        await listener.async_handle(_event("light.tambur", "on", "off"))
        await listener.async_handle(_event("light.tambur", "off", "on"))

        assert coordinator.snapshot()["protections"] == []

    asyncio.run(exercise())


def test_restart_and_stale_presence_do_not_create_release_evidence() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        store = _MemoryStore()
        coordinator = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings.1", 0, _settings())
        listener = ManualLightOffProtectionEventListener(
            coordinator, None, {"light.tambur", "binary_sensor.tambur_presence"}
        )

        await listener.async_handle(_event("light.tambur", "on", "off"))
        stale_presence = SimpleNamespace(
            state="off", last_updated=now - timedelta(minutes=6)
        )
        await listener.async_handle(
            SimpleNamespace(
                data={
                    "entity_id": "binary_sensor.tambur_presence",
                    "old_state": _state("on"),
                    "new_state": stale_presence,
                },
                context=None,
            )
        )
        assert coordinator.snapshot()["protections"][0]["absenceSince"] is None

        restarted = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await restarted.async_load()
        now += timedelta(minutes=11)
        decision = await restarted.async_decide_entity(
            "light.tambur", automatic=True, dry_run=False
        )
        assert not decision.allowed

    asyncio.run(exercise())


def test_configured_entities_include_only_profile_lights_and_presence_sensors() -> None:
    coordinator = SimpleNamespace(
        snapshot=lambda: {
            "settings": {
                "profiles": [
                    {
                        "lightIds": ["light.tambur", "light.tambur_lamp"],
                        "presenceSensorIds": ["binary_sensor.tambur_presence"],
                    }
                ]
            }
        }
    )

    assert configured_manual_light_off_protection_entities(coordinator) == {
        "light.tambur",
        "light.tambur_lamp",
        "binary_sensor.tambur_presence",
    }


def test_start_subscribes_only_configured_entities() -> None:
    async def exercise() -> None:
        coordinator = SimpleNamespace(
            snapshot=lambda: {
                "settings": {
                    "profiles": [
                        {
                            "lightIds": ["light.tambur"],
                            "presenceSensorIds": ["binary_sensor.tambur_presence"],
                        }
                    ]
                }
            },
            async_note_state_transition=AsyncMock(),
        )
        entry = SimpleNamespace(async_on_unload=Mock())
        unsubscribe = Mock()
        with patch(
            "custom_components.hausman_hub.manual_light_off_protection_events._async_track_state_change_event",
            return_value=unsubscribe,
        ) as subscribe:
            await async_start_manual_light_off_protection_events(
                SimpleNamespace(), entry, coordinator, None
            )

        subscribed_entities = subscribe.call_args.args[1]
        assert subscribed_entities == {"light.tambur", "binary_sensor.tambur_presence"}
        entry.async_on_unload.assert_called_once_with(unsubscribe)

    asyncio.run(exercise())
