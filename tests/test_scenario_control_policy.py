"""Durable policy tests for consolidated scenario controllers."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.scenario_control_policy import (
    ScenarioControlPolicyConflict,
    ScenarioControlPolicyService,
)
from custom_components.hausman_hub.domain.scenario_controls import (
    ScenarioControlDocument,
    ScenarioControlPolicy,
    scenario_control_document_from_payload,
    scenario_control_document_to_payload,
)


class MemoryStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.saved.append(payload)


def test_default_policy_round_trip_contains_required_controller_bounds() -> None:
    document = ScenarioControlDocument()
    payload = scenario_control_document_to_payload(document)

    assert payload["policyRevision"] == 0
    assert payload["policy"]["absenceConfirmationSeconds"] == 10
    assert payload["policy"]["storageAbsenceSeconds"] == 120
    assert payload["policy"]["storageExhaustTimes"] == ["11:00", "20:00"]
    assert payload["policy"]["storageExhaustRunSeconds"] == 1800
    assert payload["policy"]["storageExhaustTargetId"] is None
    assert payload["policy"]["relativeFadePercent"] == 10
    assert payload["policy"]["relativeFadePeriodSeconds"] == 300
    assert payload["policy"]["brightnessRampSeconds"] == 30
    assert payload["policy"]["dayBrightnessFloorPercent"] == 20
    assert payload["policy"]["eveningBrightnessFloorPercent"] == 5
    assert payload["policy"]["smallCorridorLuxThreshold"] == 450
    assert payload["policy"]["kitchenCoverCapPercent"] == 80
    assert payload["policy"]["cabinetCoverCapPercent"] == 90
    assert scenario_control_document_from_payload(payload) == document


@pytest.mark.parametrize(
    "policy",
    (
        replace(ScenarioControlPolicy(), absence_confirmation_seconds=9),
        replace(ScenarioControlPolicy(), storage_absence_seconds=9),
        replace(
            ScenarioControlPolicy(),
            day_brightness_floor_percent=4,
            evening_brightness_floor_percent=5,
        ),
        replace(ScenarioControlPolicy(), relative_fade_percent=0),
        replace(ScenarioControlPolicy(), lux_hysteresis=450),
        replace(ScenarioControlPolicy(), storage_exhaust_times=("11:00", "11:00")),
        replace(ScenarioControlPolicy(), storage_exhaust_run_seconds=0),
        replace(ScenarioControlPolicy(), neutral_color_temperature_kelvin=9000),
    ),
)
def test_policy_rejects_unsafe_ranges_and_relationships(
    policy: ScenarioControlPolicy,
) -> None:
    with pytest.raises(ValueError):
        scenario_control_document_to_payload(ScenarioControlDocument(policy=policy))


@pytest.mark.asyncio
async def test_service_persists_cas_revision_and_notifies_after_save() -> None:
    store = MemoryStore()
    service = ScenarioControlPolicyService(store)
    await service.async_load()
    observed: list[ScenarioControlDocument] = []

    async def observer(document: ScenarioControlDocument) -> None:
        assert store.payload == scenario_control_document_to_payload(document)
        observed.append(document)

    service.add_observer(observer)
    updated = await service.async_replace(
        0,
        replace(ScenarioControlPolicy(), storage_absence_seconds=180),
    )

    assert updated.policy_revision == 1
    assert service.current == updated
    assert observed == [updated]
    with pytest.raises(ScenarioControlPolicyConflict):
        await service.async_replace(0, ScenarioControlPolicy())


@pytest.mark.asyncio
async def test_nullable_exhaust_binding_is_capability_checked() -> None:
    actions = {
        "fan-ok": SimpleNamespace(
            name="Вытяжка кладовки",
            entity_id="fan.storage",
            action=lambda action_id: (
                SimpleNamespace(service=action_id, domain="fan")
                if action_id in {"turn_on", "turn_off"}
                else None
            )
        ),
        "fan-on-only": SimpleNamespace(
            name="Вытяжка кладовки",
            entity_id="fan.storage",
            action=lambda action_id: (
                SimpleNamespace(service="turn_on", domain="fan") if action_id == "turn_on" else None
            )
        ),
    }
    service = ScenarioControlPolicyService(
        MemoryStore(),
        capability_resolver=actions.get,
    )
    await service.async_load()

    unbound = await service.async_replace(0, ScenarioControlPolicy())
    assert unbound.policy.storage_exhaust_target_id is None
    bound = await service.async_replace(
        0,
        replace(
            ScenarioControlPolicy(),
            storage_exhaust_target_id="fan-ok",
        ),
    )
    assert bound.policy.storage_exhaust_target_id == "fan-ok"

    with pytest.raises(ValueError, match="turn_on and turn_off"):
        await service.async_replace(
            2,
            replace(
                ScenarioControlPolicy(),
                storage_exhaust_target_id="fan-on-only",
            ),
        )


@pytest.mark.asyncio
async def test_exhaust_binding_rejects_generic_relay_and_known_safety_target() -> None:
    def device(name: str, domain: str = "switch") -> object:
        return SimpleNamespace(
            name=name,
            entity_id=f"{domain}.candidate",
            action=lambda action_id: SimpleNamespace(
                service=action_id,
                domain=domain,
            ),
        )

    devices = {
        "generic-relay": device("Реле 7"),
        "water-relay": device("Вытяжка, но защищённая линия"),
    }
    service = ScenarioControlPolicyService(
        MemoryStore(),
        capability_resolver=devices.get,
        binding_safety_validator=lambda target_id, _device: target_id != "water-relay",
    )
    await service.async_load()

    with pytest.raises(ValueError, match="turn_on and turn_off"):
        await service.async_replace(
            0,
            replace(
                ScenarioControlPolicy(),
                storage_exhaust_target_id="generic-relay",
            ),
        )
    with pytest.raises(ValueError, match="safety-protected"):
        await service.async_replace(
            0,
            replace(
                ScenarioControlPolicy(),
                storage_exhaust_target_id="water-relay",
            ),
        )
    with pytest.raises(ValueError, match="not in the server catalog"):
        await service.async_replace(
            2,
            replace(
                ScenarioControlPolicy(),
                storage_exhaust_target_id="attacker-target",
            ),
        )


@pytest.mark.asyncio
async def test_service_fails_closed_on_corrupt_persisted_document() -> None:
    service = ScenarioControlPolicyService(
        MemoryStore({"version": 1, "policyRevision": 0, "policy": {}})
    )

    with pytest.raises(RuntimeError, match="invalid"):
        await service.async_load()


@pytest.mark.asyncio
async def test_recovered_previous_policy_revokes_exhaust_binding_and_revision() -> None:
    payload = scenario_control_document_to_payload(
        ScenarioControlDocument(
            7,
            replace(
                ScenarioControlPolicy(),
                storage_exhaust_target_id="old-storage-fan",
            ),
        )
    )
    store = MemoryStore(payload)
    store.recovered_previous = True
    service = ScenarioControlPolicyService(store)

    await service.async_load()

    assert service.current.policy_revision == 8
    assert service.current.policy.storage_exhaust_target_id is None
    assert store.payload == scenario_control_document_to_payload(service.current)
