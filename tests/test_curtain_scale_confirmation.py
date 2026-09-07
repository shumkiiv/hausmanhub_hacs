"""Durable authority tests for the one observed office curtain scale."""

from __future__ import annotations

import copy

import pytest

from custom_components.hausman_hub.application.curtain_command_policy import (
    KITCHEN_CURTAIN_TARGET,
    OFFICE_CURTAIN_TARGET,
)
from custom_components.hausman_hub.application.curtain_scale_confirmation import (
    OFFICE_CURTAIN_ENTITY_ID,
    CurtainScaleConfirmation,
    CurtainScaleConfirmationConflict,
    CurtainScaleIdentity,
    valid_curtain_scale_confirmation_payload,
)


class MemoryStore:
    def __init__(
        self,
        payload: object | None = None,
        *,
        fail_load: bool = False,
        fail_save: bool = False,
        recovered_previous: bool = False,
    ) -> None:
        self.payload = copy.deepcopy(payload)
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.recovered_previous = recovered_previous
        self.saves = 0

    async def async_load(self) -> object | None:
        if self.fail_load:
            raise RuntimeError("synthetic unreadable store")
        return copy.deepcopy(self.payload)

    async def async_save(self, payload: dict[str, object]) -> None:
        if self.fail_save:
            raise RuntimeError("synthetic failed save")
        self.payload = copy.deepcopy(payload)
        self.saves += 1


def office_identity(
    *,
    unique_id: str = "0xa4c1381b3fb1c985-cover",
    device_id: str = "device-office-curtain",
    identifiers: tuple[tuple[str, str], ...] = (
        ("zha", "a4:c1:38:1b:3f:b1:c9:85"),
    ),
) -> CurtainScaleIdentity:
    return CurtainScaleIdentity.create(
        target_id=OFFICE_CURTAIN_TARGET,
        entity_id=OFFICE_CURTAIN_ENTITY_ID,
        entity_unique_id=unique_id,
        device_id=device_id,
        device_identifiers=identifiers,
    )


@pytest.mark.asyncio
async def test_empty_store_is_unconfirmed_until_exact_office_identity_is_saved() -> None:
    identity = office_identity()
    store = MemoryStore()
    service = CurtainScaleConfirmation(
        store,
        entry_id="entry-1",
        identity_resolver=lambda target_id: identity if target_id == OFFICE_CURTAIN_TARGET else None,
        now_ms=lambda: 1_789_000_000_000,
    )

    await service.async_load()
    before = service.authorization_snapshot(OFFICE_CURTAIN_TARGET)
    assert before.confirmed is False
    assert before.revision == 0

    confirmed = await service.async_confirm_office(
        expected_revision=0,
        expected_identity_digest=identity.identity_digest,
    )

    assert confirmed.confirmed is True
    assert confirmed.revision == 1
    assert confirmed.identity_digest == identity.identity_digest
    assert store.payload == {
        "version": 1,
        "revision": 1,
        "entryId": "entry-1",
        "targetId": OFFICE_CURTAIN_TARGET,
        "entityId": OFFICE_CURTAIN_ENTITY_ID,
        "entityUniqueId": "0xa4c1381b3fb1c985-cover",
        "deviceId": "device-office-curtain",
        "deviceIdentifiers": [["zha", "a4:c1:38:1b:3f:b1:c9:85"]],
        "confirmedAt": 1_789_000_000_000,
        "confirmationSource": "owner_observed_current_scale",
        "state": "confirmed",
    }
    assert service.authorization_snapshot(KITCHEN_CURTAIN_TARGET).confirmed is False


@pytest.mark.asyncio
async def test_stale_revision_or_identity_digest_never_saves_authority() -> None:
    identity = office_identity()
    store = MemoryStore()
    service = CurtainScaleConfirmation(
        store,
        entry_id="entry-1",
        identity_resolver=lambda _target_id: identity,
        now_ms=lambda: 100,
    )
    await service.async_load()

    with pytest.raises(CurtainScaleConfirmationConflict, match="revision"):
        await service.async_confirm_office(1, identity.identity_digest)
    with pytest.raises(CurtainScaleConfirmationConflict, match="identity"):
        await service.async_confirm_office(0, "stale-digest")

    assert store.saves == 0
    assert service.authorization_snapshot(OFFICE_CURTAIN_TARGET).confirmed is False


@pytest.mark.asyncio
async def test_registry_replacement_between_form_and_submit_fails_cas() -> None:
    current = [office_identity()]
    store = MemoryStore()
    service = CurtainScaleConfirmation(
        store,
        entry_id="entry-1",
        identity_resolver=lambda _target_id: current[0],
        now_ms=lambda: 100,
    )
    await service.async_load()
    form = service.office_confirmation_view()
    current[0] = office_identity(unique_id="replacement-cover")

    with pytest.raises(CurtainScaleConfirmationConflict, match="identity"):
        await service.async_confirm_office(
            form.authorization.revision,
            form.identity.identity_digest,
        )

    assert store.saves == 0


@pytest.mark.asyncio
async def test_failed_save_never_publishes_confirmation_in_memory() -> None:
    identity = office_identity()
    service = CurtainScaleConfirmation(
        MemoryStore(fail_save=True),
        entry_id="entry-1",
        identity_resolver=lambda _target_id: identity,
        now_ms=lambda: 100,
    )
    await service.async_load()

    with pytest.raises(RuntimeError, match="failed save"):
        await service.async_confirm_office(0, identity.identity_digest)

    assert service.authorization_snapshot(OFFICE_CURTAIN_TARGET).confirmed is False


@pytest.mark.asyncio
async def test_confirmation_restart_revoke_and_second_restart_stay_fail_closed() -> None:
    identity = office_identity()
    store = MemoryStore()

    async def start() -> CurtainScaleConfirmation:
        service = CurtainScaleConfirmation(
            store,
            entry_id="entry-1",
            identity_resolver=lambda _target_id: identity,
            now_ms=lambda: 100,
        )
        await service.async_load()
        return service

    first = await start()
    await first.async_confirm_office(0, identity.identity_digest)
    restarted = await start()
    assert restarted.authorization_snapshot(OFFICE_CURTAIN_TARGET).confirmed is True

    revoked = await restarted.async_revoke(expected_revision=1)
    assert revoked.confirmed is False
    assert revoked.revision == 2
    restarted_again = await start()
    assert restarted_again.authorization_snapshot(OFFICE_CURTAIN_TARGET).confirmed is False
    assert restarted_again.authorization_snapshot(OFFICE_CURTAIN_TARGET).revision == 2


@pytest.mark.asyncio
async def test_identity_change_or_recovered_previous_revokes_effective_authority() -> None:
    original = office_identity()
    store = MemoryStore()
    current = [original]
    service = CurtainScaleConfirmation(
        store,
        entry_id="entry-1",
        identity_resolver=lambda _target_id: current[0],
        now_ms=lambda: 100,
    )
    await service.async_load()
    await service.async_confirm_office(0, original.identity_digest)

    for replacement in (
        office_identity(unique_id="replacement"),
        office_identity(device_id="replacement-device"),
        office_identity(identifiers=(("zha", "replacement-ieee"),)),
    ):
        current[0] = replacement
        assert service.authorization_snapshot(OFFICE_CURTAIN_TARGET).confirmed is False

    recovered = CurtainScaleConfirmation(
        MemoryStore(store.payload, recovered_previous=True),
        entry_id="entry-1",
        identity_resolver=lambda _target_id: original,
        now_ms=lambda: 100,
    )
    await recovered.async_load()
    assert recovered.authorization_snapshot(OFFICE_CURTAIN_TARGET).confirmed is False


@pytest.mark.asyncio
async def test_unreadable_or_invalid_store_closes_only_scale_authority() -> None:
    identity = office_identity()
    for store in (
        MemoryStore(fail_load=True),
        MemoryStore({"version": 1, "state": "confirmed"}),
    ):
        service = CurtainScaleConfirmation(
            store,
            entry_id="entry-1",
            identity_resolver=lambda _target_id: identity,
            now_ms=lambda: 100,
        )
        await service.async_load()
        snapshot = service.authorization_snapshot(OFFICE_CURTAIN_TARGET)
        assert snapshot.confirmed is False
        assert service.healthy is False


def test_identity_and_payload_validators_reject_nonphysical_or_extra_data() -> None:
    with pytest.raises(ValueError, match="physical"):
        office_identity(identifiers=())
    with pytest.raises(ValueError, match="office"):
        CurtainScaleIdentity.create(
            target_id=KITCHEN_CURTAIN_TARGET,
            entity_id="cover.kitchen",
            entity_unique_id="kitchen-cover",
            device_id="device-kitchen",
            device_identifiers=(("zha", "kitchen-ieee"),),
        )

    payload = {
        "version": 1,
        "revision": 1,
        "entryId": "entry-1",
        "targetId": OFFICE_CURTAIN_TARGET,
        "entityId": OFFICE_CURTAIN_ENTITY_ID,
        "entityUniqueId": "0xa4c1381b3fb1c985-cover",
        "deviceId": "device-office-curtain",
        "deviceIdentifiers": [["zha", "a4:c1:38:1b:3f:b1:c9:85"]],
        "confirmedAt": 100,
        "confirmationSource": "owner_observed_current_scale",
        "state": "confirmed",
    }
    assert valid_curtain_scale_confirmation_payload(payload)
    assert not valid_curtain_scale_confirmation_payload({**payload, "confirmed": True})
    assert not valid_curtain_scale_confirmation_payload({**payload, "deviceIdentifiers": []})
