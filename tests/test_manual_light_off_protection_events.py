"""Event adapter tests for durable manual light-off protection."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.hausman_hub.application.scenario_command_context import (
    ScenarioCommandContextRegistry,
)
from custom_components.hausman_hub.application.manual_light_off_protection import (
    ManualLightOffProtectionCoordinator,
    ManualLightOffProtectionPersistenceError,
)
from custom_components.hausman_hub.application.scenario_light_priority import (
    LightAutomationPriority,
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
    return SimpleNamespace(
        state=value,
        last_updated=datetime.now(timezone.utc),
        attributes={},
    )


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


def test_external_transitions_update_light_ownership_before_protection() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: _state("on")))
        device = SimpleNamespace(
            entity_id="light.tambur",
            action=lambda action_id: SimpleNamespace(
                domain="light",
                service="turn_on" if action_id == "turn_on" else "turn_off",
            ),
        )
        catalog = SimpleNamespace(
            devices={"tambur": device}, device=lambda target_id: device
        )
        priority = LightAutomationPriority()
        priority._owned_revisions["light.tambur"] = None  # noqa: SLF001
        priority._owned_records["light.tambur"] = {"expiresAt": 9_999_999_999_999}  # noqa: SLF001
        obligations = SimpleNamespace(async_cancel=AsyncMock())
        listener = ManualLightOffProtectionEventListener(
            coordinator,
            None,
            {"light.tambur", "binary_sensor.tambur_presence"},
            hass=hass,
            catalog=catalog,
            light_priority=priority,
            light_safety_obligations=obligations,
            authority_lock=priority.authority_lock(),
        )

        await listener.async_handle(_event("light.tambur", "on", "off"))
        assert "light.tambur" not in priority._owned_records  # noqa: SLF001
        obligations.async_cancel.assert_awaited_once_with("tambur")
        await listener.async_handle(_event("light.tambur", "off", "on"))
        assert "light.tambur" in priority._manual_records  # noqa: SLF001
        assert coordinator.snapshot()["protections"] == []

    asyncio.run(exercise())


def test_automatic_transition_does_not_change_light_ownership() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        contexts = ScenarioCommandContextRegistry(
            context_factory=lambda: SimpleNamespace(id="automatic.light", parent_id=None)
        )
        context = contexts.create("light.tambur", "off")
        priority = SimpleNamespace(async_clear_ownership=AsyncMock())
        obligations = SimpleNamespace(async_cancel=AsyncMock())
        listener = ManualLightOffProtectionEventListener(
            coordinator,
            contexts,
            {"light.tambur"},
            hass=SimpleNamespace(),
            catalog=SimpleNamespace(devices={}),
            light_priority=priority,
            light_safety_obligations=obligations,
        )

        await listener.async_handle(_event("light.tambur", "on", "off", context))

        priority.async_clear_ownership.assert_not_awaited()
        obligations.async_cancel.assert_not_awaited()

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


@pytest.mark.parametrize(
    "unsafe_attributes",
    [{"restored": True}, {"cached": True}, {"assumed_state": True}],
)
def test_untrusted_same_state_presence_event_invalidates_prior_absence(
    unsafe_attributes: dict[str, object],
) -> None:
    """A restored off-to-off event must revoke earlier live absence evidence."""

    async def exercise() -> None:
        now = datetime.now(timezone.utc)
        store = _MemoryStore()
        coordinator = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await coordinator.async_load()
        sensors = {
            "binary_sensor.tambur_presence": SimpleNamespace(
                state="off", last_changed=now, attributes={}
            ),
            "binary_sensor.tambur_motion": SimpleNamespace(
                state="off", last_changed=now, attributes={}
            ),
        }
        await coordinator.async_arm_release_owned_direct_off(
            request_id="switch.sensor-boundary",
            light_entity_ids=("light.tambur_chandelier", "switch.tambur_points"),
            presence_sensor_entity_ids=tuple(sensors),
            sensor_states=sensors,
        )
        listener = ManualLightOffProtectionEventListener(
            coordinator, None, set(sensors)
        )
        now += timedelta(seconds=100)
        await listener.async_handle(
            SimpleNamespace(
                data={
                    "entity_id": "binary_sensor.tambur_motion",
                    "old_state": sensors["binary_sensor.tambur_motion"],
                    "new_state": SimpleNamespace(
                        state="off",
                        last_changed=now - timedelta(seconds=100),
                        attributes=unsafe_attributes,
                    ),
                },
                context=None,
            )
        )
        now += timedelta(seconds=180)

        decision = await coordinator.async_decide_entity(
            "light.tambur_chandelier", automatic=True, dry_run=False
        )
        assert not decision.allowed
        assert decision.reason == "manual_off_protection_absence_required"

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


def test_configured_entities_preserve_active_frozen_scope_after_restart() -> None:
    async def exercise() -> None:
        store = _MemoryStore()
        coordinator = ManualLightOffProtectionCoordinator(store)
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings.1", 0, _settings())
        await coordinator.async_note_state_transition(
            "light.tambur", _state("on"), _state("off"), None
        )
        removed = _settings()
        removed["profiles"] = []
        await coordinator.async_replace_settings("settings.2", 1, removed)
        restarted = ManualLightOffProtectionCoordinator(store)
        await restarted.async_load()

        assert configured_manual_light_off_protection_entities(restarted) == {
            "light.tambur",
            "binary_sensor.tambur_presence",
        }

    asyncio.run(exercise())


def test_stale_or_restored_light_off_never_creates_protection() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        listener = ManualLightOffProtectionEventListener(
            coordinator, None, {"light.tambur"}
        )
        stale = SimpleNamespace(
            state="off",
            last_updated=datetime.now(timezone.utc) - timedelta(minutes=6),
            attributes={},
        )
        restored = SimpleNamespace(
            state="off",
            last_updated=datetime.now(timezone.utc),
            attributes={"restored": True},
        )
        unavailable = SimpleNamespace(
            state="unavailable", last_updated=datetime.now(timezone.utc), attributes={}
        )
        future = SimpleNamespace(
            state="off",
            last_updated=datetime.now(timezone.utc) + timedelta(seconds=1),
            attributes={},
        )
        for new_state in (stale, restored, unavailable, future):
            await listener.async_handle(
                SimpleNamespace(
                    data={
                        "entity_id": "light.tambur",
                        "old_state": _state("on"),
                        "new_state": new_state,
                    },
                    context=None,
                )
            )

        assert coordinator.snapshot()["protections"] == []

    asyncio.run(exercise())


def test_missing_light_timestamp_never_creates_protection() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        listener = ManualLightOffProtectionEventListener(
            coordinator, None, {"light.tambur"}
        )

        await listener.async_handle(
            SimpleNamespace(
                data={
                    "entity_id": "light.tambur",
                    "old_state": SimpleNamespace(state="on", attributes={}),
                    "new_state": SimpleNamespace(state="off", attributes={}),
                },
                context=None,
            )
        )

        assert coordinator.snapshot()["protections"] == []

    asyncio.run(exercise())


def test_external_off_cleanup_failure_keeps_active_unhealthy_protection() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        priority = SimpleNamespace(
            _async_clear_ownership_unlocked=AsyncMock(side_effect=OSError("store"))
        )
        listener = ManualLightOffProtectionEventListener(
            coordinator,
            None,
            {"light.tambur"},
            light_priority=priority,
            authority_lock=asyncio.Lock(),
        )

        with pytest.raises(OSError, match="store"):
            await listener.async_handle(_event("light.tambur", "on", "off"))

        assert len(coordinator.snapshot()["protections"]) == 1
        assert coordinator.unhealthy

    asyncio.run(exercise())


def test_external_on_ownership_failure_keeps_existing_protection() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        await coordinator.async_note_state_transition(
            "light.tambur", _state("on"), _state("off"), None
        )
        priority = SimpleNamespace(
            _async_begin_direct_action_unlocked=AsyncMock(side_effect=OSError("store"))
        )
        catalog = SimpleNamespace(
            devices={"tambur": SimpleNamespace(entity_id="light.tambur")}
        )
        listener = ManualLightOffProtectionEventListener(
            coordinator,
            None,
            {"light.tambur"},
            hass=SimpleNamespace(),
            catalog=catalog,
            light_priority=priority,
            authority_lock=asyncio.Lock(),
        )

        with pytest.raises(OSError, match="store"):
            await listener.async_handle(_event("light.tambur", "off", "on"))

        assert len(coordinator.snapshot()["protections"]) == 1

    asyncio.run(exercise())


def test_external_off_obligation_failure_keeps_active_unhealthy_protection() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        priority = LightAutomationPriority()
        obligations = SimpleNamespace(async_cancel=AsyncMock(side_effect=OSError("store")))
        catalog = SimpleNamespace(
            devices={"tambur": SimpleNamespace(entity_id="light.tambur")}
        )
        listener = ManualLightOffProtectionEventListener(
            coordinator,
            None,
            {"light.tambur"},
            catalog=catalog,
            light_priority=priority,
            light_safety_obligations=obligations,
            authority_lock=priority.authority_lock(),
        )

        with pytest.raises(OSError, match="store"):
            await listener.async_handle(_event("light.tambur", "on", "off"))

        assert len(coordinator.snapshot()["protections"]) == 1
        assert coordinator.unhealthy

    asyncio.run(exercise())


def test_external_on_obligation_failure_keeps_manual_ownership_and_protection() -> None:
    async def exercise() -> None:
        coordinator = await _coordinator()
        await coordinator.async_note_state_transition(
            "light.tambur", _state("on"), _state("off"), None
        )
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: _state("on")))
        device = SimpleNamespace(
            entity_id="light.tambur",
            action=lambda _: SimpleNamespace(domain="light", service="turn_on"),
        )
        catalog = SimpleNamespace(devices={"tambur": device}, device=lambda _: device)
        priority = LightAutomationPriority()
        obligations = SimpleNamespace(async_cancel=AsyncMock(side_effect=OSError("store")))
        listener = ManualLightOffProtectionEventListener(
            coordinator,
            None,
            {"light.tambur"},
            hass=hass,
            catalog=catalog,
            light_priority=priority,
            light_safety_obligations=obligations,
            authority_lock=priority.authority_lock(),
        )

        with pytest.raises(OSError, match="store"):
            await listener.async_handle(_event("light.tambur", "off", "on"))

        assert len(coordinator.snapshot()["protections"]) == 1
        assert "light.tambur" in priority._manual_records  # noqa: SLF001

    asyncio.run(exercise())


def test_external_off_protection_store_failure_fails_closed() -> None:
    class FailingStore(_MemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.fail = False

        async def async_save(self, payload: dict[str, object]) -> None:
            if self.fail:
                raise OSError("store")
            await super().async_save(payload)

    async def exercise() -> None:
        store = FailingStore()
        coordinator = ManualLightOffProtectionCoordinator(store)
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings.1", 0, _settings())
        store.fail = True
        hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock()))
        listener = ManualLightOffProtectionEventListener(
            coordinator, None, {"light.tambur"}, hass=hass
        )

        with pytest.raises(ManualLightOffProtectionPersistenceError):
            await listener.async_handle(_event("light.tambur", "on", "off"))

        assert coordinator.unhealthy
        assert not (await coordinator.async_decide_entity(
            "light.tambur", automatic=True, dry_run=False
        )).allowed
        hass.services.async_call.assert_not_awaited()

    asyncio.run(exercise())


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
        entry.async_on_unload.call_args.args[0]()
        unsubscribe.assert_called_once()

    asyncio.run(exercise())


def test_start_rebuilds_exact_subscription_after_profile_change() -> None:
    async def exercise() -> None:
        entity_ids = {"light.tambur", "binary_sensor.tambur_presence"}
        callbacks: list[object] = []
        coordinator = SimpleNamespace(
            event_entity_ids=lambda: frozenset(entity_ids),
            add_event_entity_listener=lambda callback: callbacks.append(callback)
            or (lambda: callbacks.remove(callback)),
            async_note_state_transition=AsyncMock(),
        )
        entry = SimpleNamespace(async_on_unload=Mock())
        first_unsubscribe = Mock()
        second_unsubscribe = Mock()
        with patch(
            "custom_components.hausman_hub.manual_light_off_protection_events._async_track_state_change_event",
            side_effect=[first_unsubscribe, second_unsubscribe],
        ) as subscribe:
            await async_start_manual_light_off_protection_events(
                SimpleNamespace(async_create_task=asyncio.create_task),
                entry,
                coordinator,
                None,
            )
            entity_ids.clear()
            entity_ids.add("light.new")
            callbacks[0]()
            await asyncio.sleep(0)

        assert subscribe.call_args_list[1].args[1] == {"light.new"}
        first_unsubscribe.assert_called_once()

    asyncio.run(exercise())
