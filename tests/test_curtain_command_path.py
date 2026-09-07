"""Safety and receipt tests for the shared curtain command path."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from custom_components.hausman_hub.application.curtain_command_policy import (
    ALICE_CURTAIN_TARGET,
    KITCHEN_CURTAIN_TARGET,
    LIVING_CURTAIN_TARGET,
    OFFICE_CURTAIN_TARGET,
    CurtainCommandPolicy,
    CurtainPolicyError,
    trusted_curtain_position,
)
from custom_components.hausman_hub.application.scenario_executor import ScenarioExecutor
from custom_components.hausman_hub.application.scenario_service import ScenarioService
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.domain.scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioCommandMode,
    ScenarioDefinition,
    ScenarioExecutionMode,
    ScenarioSafetyPolicy,
    ScenarioTrigger,
    ScenarioTriggerType,
)
from custom_components.hausman_hub.domain.scenario_controls import (
    ScenarioControlDocument,
    ScenarioControlPolicy,
)


def _action(action_id: str, service: str) -> SimpleNamespace:
    return SimpleNamespace(action_id=action_id, domain="cover", service=service)


def _device(*, target_id: str = KITCHEN_CURTAIN_TARGET) -> SimpleNamespace:
    actions = {
        "open_cover": _action("open_cover", "open_cover"),
        "close_cover": _action("close_cover", "close_cover"),
        "stop_cover": _action("stop_cover", "stop_cover"),
        "set_position": _action("set_position", "set_cover_position"),
    }
    return SimpleNamespace(
        target_id=target_id,
        entity_id="cover.test",
        action=actions.get,
    )


def _state(position: object) -> SimpleNamespace:
    return SimpleNamespace(
        state="open",
        attributes={"current_position": position},
        last_updated=datetime.now(timezone.utc),
    )


def _executor_for_target(
    *,
    target_id: str = KITCHEN_CURTAIN_TARGET,
    initial_position: object = 0,
    observed_position: object | None = None,
    advance_revision: bool = True,
    policy: CurtainCommandPolicy | None = None,
    include_set_position: bool = True,
) -> tuple[ScenarioExecutor, SimpleNamespace, SimpleNamespace, dict[str, ScenarioDeviceEntry]]:
    before_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    state = SimpleNamespace(
        state="closed" if initial_position == 0 else "open",
        attributes={"current_position": initial_position},
        last_updated=before_at,
        last_changed=before_at - timedelta(days=1),
    )
    hass = SimpleNamespace()
    hass.states = SimpleNamespace(get=lambda _entity_id: state)
    hass.services = SimpleNamespace(async_call=AsyncMock())

    async def apply_service(
        _domain: str,
        service: str,
        data: dict[str, object],
        **_kwargs: object,
    ) -> None:
        if service == "set_cover_position":
            position = data["position"] if observed_position is None else observed_position
        elif service == "close_cover":
            position = 0 if observed_position is None else observed_position
        else:
            position = 100 if observed_position is None else observed_position
        state.state = "closed" if position == 0 else "open"
        state.attributes = {"current_position": position}
        if advance_revision:
            state.last_updated = datetime.now(timezone.utc)

    hass.services.async_call.side_effect = apply_service
    actions = [
        ScenarioDeviceAction(
            action_id="open_cover",
            title="Открыть",
            domain="cover",
            service="open_cover",
            allowed_fields=frozenset(),
        ),
        ScenarioDeviceAction(
            action_id="close_cover",
            title="Закрыть",
            domain="cover",
            service="close_cover",
            allowed_fields=frozenset(),
        ),
        ScenarioDeviceAction(
            action_id="stop_cover",
            title="Остановить",
            domain="cover",
            service="stop_cover",
            allowed_fields=frozenset(),
        ),
    ]
    if include_set_position:
        actions.append(
            ScenarioDeviceAction(
                action_id="set_position",
                title="Положение",
                domain="cover",
                service="set_cover_position",
                allowed_fields=frozenset({"value"}),
            )
        )
    device = ScenarioDeviceEntry(
        target_id=target_id,
        name="Шторы",
        entity_id="cover.test",
        actions=tuple(actions),
    )
    devices = {target_id: device}
    catalog = ScenarioCatalog(devices=devices, scenarios={"child": "Child"})

    async def nested(_scenario_id: str, **_kwargs: object) -> dict[str, object]:
        return {"status": "completed", "receipts": []}

    executor = ScenarioExecutor(
        hass,
        catalog,
        nested,
        readback_window_seconds=0.02,
        readback_interval_seconds=0.01,
        curtain_command_policy=policy,
    )
    return executor, hass, state, devices


def _definition(*actions: ScenarioAction, idempotent: bool = False) -> ScenarioDefinition:
    return ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.SINGLE,
        command_mode=ScenarioCommandMode.LIVE,
        triggers=(ScenarioTrigger(id="manual", type=ScenarioTriggerType.MANUAL),),
        conditions=(),
        actions=actions,
        safety_policy=ScenarioSafetyPolicy(idempotent_actions=idempotent),
    )


def test_limited_open_uses_confirmed_cap_and_keeps_requested_value() -> None:
    policy = CurtainCommandPolicy.with_confirmed_scales(
        {KITCHEN_CURTAIN_TARGET}
    )

    plan = policy.plan(
        device=_device(),
        action_id="set_position",
        requested=100,
        current_state=_state(0),
    )

    assert plan.requested == 100
    assert plan.applied == 80
    assert plan.service == "set_cover_position"
    assert plan.service_data == {"entity_id": "cover.test", "position": 80}
    assert plan.confirmation_action_id == "set_position"
    assert plan.confirmation_value == 80
    assert plan.limited is True


def test_unconfirmed_scale_blocks_opening_but_not_safe_closing() -> None:
    policy = CurtainCommandPolicy()

    with pytest.raises(CurtainPolicyError, match="curtain_scale_unconfirmed"):
        policy.plan(
            device=_device(),
            action_id="open_cover",
            requested=None,
            current_state=_state(0),
        )

    closing = policy.plan(
        device=_device(),
        action_id="set_position",
        requested=0,
        current_state=_state(80),
    )
    assert closing.applied == 0

    closing_without_position = policy.plan(
        device=_device(),
        action_id="set_position",
        requested=0,
        current_state=_state(None),
    )
    assert closing_without_position.applied == 0


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 101])
def test_position_rejects_non_finite_bool_and_out_of_range_before_limit(value: object) -> None:
    policy = CurtainCommandPolicy.with_confirmed_scales(
        {KITCHEN_CURTAIN_TARGET}
    )

    with pytest.raises(CurtainPolicyError, match="curtain_position_invalid"):
        policy.plan(
            device=_device(),
            action_id="set_position",
            requested=value,
            current_state=_state(0),
        )


def test_office_close_guard_covers_numeric_zero_and_unknown_evidence() -> None:
    policy = CurtainCommandPolicy.with_confirmed_scales({OFFICE_CURTAIN_TARGET})
    office = _device(target_id=OFFICE_CURTAIN_TARGET)

    with pytest.raises(CurtainPolicyError, match="office_close_guard"):
        policy.plan(
            device=office,
            action_id="set_position",
            requested=0,
            current_state=_state(20),
        )
    with pytest.raises(CurtainPolicyError, match="office_position_unknown"):
        policy.plan(
            device=office,
            action_id="close_cover",
            requested=None,
            current_state=_state(None),
        )


@pytest.mark.parametrize("timestamp", [None, "not-a-datetime", 1788000000000])
def test_undated_position_is_never_trusted(timestamp: object) -> None:
    state = SimpleNamespace(
        state="open",
        attributes={"current_position": 20},
        last_updated=timestamp,
    )

    assert trusted_curtain_position(state) is None
    with pytest.raises(CurtainPolicyError, match="office_position_unknown"):
        CurtainCommandPolicy.with_confirmed_scales(
            {OFFICE_CURTAIN_TARGET}
        ).plan(
            device=_device(target_id=OFFICE_CURTAIN_TARGET),
            action_id="close_cover",
            requested=None,
            current_state=state,
        )


def test_similar_target_name_does_not_receive_a_limit() -> None:
    policy = CurtainCommandPolicy.with_confirmed_scales(
        {KITCHEN_CURTAIN_TARGET}
    )
    assert (
        policy.plan(
            device=_device(target_id=f"{KITCHEN_CURTAIN_TARGET}-copy"),
            action_id="open_cover",
            requested=None,
            current_state=_state(0),
        )
        is None
    )


def test_broken_policy_provider_does_not_affect_non_curtain_target() -> None:
    def broken_provider() -> ScenarioControlDocument:
        raise RuntimeError("synthetic policy failure")

    policy = CurtainCommandPolicy(broken_provider)

    assert (
        policy.plan(
            device=_device(target_id="light_unrelated"),
            action_id="open_cover",
            requested=None,
            current_state=_state(0),
        )
        is None
    )
    assert policy.target("light_unrelated") is None


def test_stop_remains_available_when_cap_document_is_unavailable() -> None:
    def broken_provider() -> ScenarioControlDocument:
        raise RuntimeError("synthetic policy failure")

    policy = CurtainCommandPolicy(broken_provider)

    plan = policy.plan(
        device=_device(),
        action_id="stop_cover",
        requested=None,
        current_state=_state(None),
    )

    assert plan is not None
    assert plan.service == "stop_cover"
    assert plan.service_data == {"entity_id": "cover.test"}
    assert plan.policy_revision == "curtain-safe-stop.v1"


def test_stop_with_broken_policy_still_requires_exact_descriptor() -> None:
    def broken_provider() -> ScenarioControlDocument:
        raise RuntimeError("synthetic policy failure")

    device = _device()
    device.action = {
        "stop_cover": _action("stop_cover", "open_cover"),
    }.get

    with pytest.raises(CurtainPolicyError, match="curtain_dispatch_descriptor_invalid"):
        CurtainCommandPolicy(broken_provider).plan(
            device=device,
            action_id="stop_cover",
            requested=None,
            current_state=_state(None),
        )


def test_stop_with_broken_policy_rejects_a_value_before_dispatch() -> None:
    def broken_provider() -> ScenarioControlDocument:
        raise RuntimeError("synthetic policy failure")

    with pytest.raises(
        CurtainPolicyError, match="curtain_action_does_not_accept_value"
    ):
        CurtainCommandPolicy(broken_provider).plan(
            device=_device(),
            action_id="stop_cover",
            requested=0,
            current_state=_state(None),
        )


@pytest.mark.parametrize("invalid_cap", [True, 0, 101, float("nan")])
def test_invalid_editable_cap_fails_closed(invalid_cap: object) -> None:
    document = ScenarioControlDocument(
        policy=replace(
            ScenarioControlPolicy(),
            kitchen_cover_cap_percent=invalid_cap,
        )
    )
    policy = CurtainCommandPolicy.with_confirmed_scales(
        {KITCHEN_CURTAIN_TARGET},
        control_document_provider=lambda: document,
    )

    with pytest.raises(CurtainPolicyError, match="curtain_policy_unavailable"):
        policy.plan(
            device=_device(),
            action_id="set_position",
            requested=100,
            current_state=_state(0),
        )


@pytest.mark.asyncio
async def test_executor_limited_open_dispatches_position_instead_of_raw_open() -> None:
    before_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    state = SimpleNamespace(
        state="closed",
        attributes={"current_position": 0},
        last_updated=before_at,
    )
    hass = SimpleNamespace()
    hass.states = SimpleNamespace(get=lambda _entity_id: state)
    hass.services = SimpleNamespace(async_call=AsyncMock())

    async def apply_service(
        _domain: str,
        service: str,
        data: dict[str, object],
        **_kwargs: object,
    ) -> None:
        state.state = "open"
        state.attributes = {
            "current_position": (
                data["position"] if service == "set_cover_position" else 100
            )
        }
        state.last_updated = datetime.now(timezone.utc)

    hass.services.async_call.side_effect = apply_service
    actions = (
        ScenarioDeviceAction(
            action_id="open_cover",
            title="Открыть",
            domain="cover",
            service="open_cover",
            allowed_fields=frozenset(),
        ),
        ScenarioDeviceAction(
            action_id="set_position",
            title="Положение",
            domain="cover",
            service="set_cover_position",
            allowed_fields=frozenset({"value"}),
        ),
    )
    device = ScenarioDeviceEntry(
        target_id=KITCHEN_CURTAIN_TARGET,
        name="Шторы кухня",
        entity_id="cover.kitchen",
        actions=actions,
    )
    catalog = ScenarioCatalog(
        devices={KITCHEN_CURTAIN_TARGET: device}, scenarios={}
    )

    async def nested(_scenario_id: str, **_kwargs: object) -> dict[str, object]:
        return {"status": "completed", "receipts": []}

    executor = ScenarioExecutor(
        hass,
        catalog,
        nested,
        readback_window_seconds=0.02,
        readback_interval_seconds=0.01,
        curtain_command_policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "open_cover"
    )

    assert receipt["actionId"] == "open_cover"
    assert receipt["confirmed"] is False
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    assert "observedValue" not in receipt["readBack"]
    hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"entity_id": "cover.kitchen", "position": 80},
        blocking=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("actual", [80, 79])
async def test_executor_reports_actual_position_not_requested(
    actual: int,
) -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        observed_position=actual,
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "set_position", 100
    )

    assert receipt["confirmed"] is False
    assert "observedValue" not in receipt["readBack"]
    assert "evidenceRevision" not in receipt["readBack"]
    assert "evidenceSequence" not in receipt["readBack"]
    assert receipt["appliedAt"] <= receipt["readBack"]["observedAt"]
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_position_with_only_new_timestamp_does_not_confirm_movement() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        observed_position=0,
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "set_position", 100
    )

    assert receipt["confirmed"] is False
    assert "observedValue" not in receipt["readBack"]
    assert receipt["readBack"]["isNewEvidence"] is False
    hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_position_attribute_confirms_with_unchanged_old_last_changed() -> None:
    executor, hass, state, _devices = _executor_for_target(
        initial_position=10,
        observed_position=80,
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )
    old_last_changed = state.last_changed

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "set_position", 100
    )

    assert state.state == "open"
    assert state.last_changed == old_last_changed
    assert receipt["confirmed"] is False
    assert "observedValue" not in receipt["readBack"]
    hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_position_does_not_confirm_and_never_retries_dispatch() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        observed_position=80,
        advance_revision=False,
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "set_position", 100
    )

    assert receipt["confirmed"] is False
    assert "observedValue" not in receipt["readBack"]
    assert receipt["readBack"]["isNewEvidence"] is False
    hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reported_state", "position", "extra_attributes"),
    [
        ("unknown", 80, {}),
        ("open", True, {}),
        ("open", float("nan"), {}),
        ("open", 80, {"restored": True}),
        ("open", 80, {"cached": True}),
    ],
)
async def test_untrusted_post_command_position_never_confirms_or_retries(
    reported_state: str,
    position: object,
    extra_attributes: dict[str, object],
) -> None:
    executor, hass, state, _devices = _executor_for_target(
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )
    before_at = state.last_updated

    async def publish_untrusted(
        _domain: str,
        _service: str,
        _data: dict[str, object],
        **_kwargs: object,
    ) -> None:
        state.state = reported_state
        state.attributes = {"current_position": position, **extra_attributes}
        state.last_updated = before_at + timedelta(seconds=1)

    hass.services.async_call.side_effect = publish_untrusted

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "set_position", 100
    )

    assert receipt["confirmed"] is False
    assert "observedValue" not in receipt["readBack"]
    hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_policy_blocks_limited_open_without_scale_authority() -> None:
    executor, hass, _state_value, _devices = _executor_for_target()

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "open_cover"
    )

    assert receipt["accepted"] is False
    assert receipt["error"] == "curtain_scale_unconfirmed"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_policy_still_allows_safe_kitchen_close_and_stop() -> None:
    closing, closing_hass, _state_value, _devices = _executor_for_target(
        initial_position=80,
    )

    closed = await closing.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "set_position", 0
    )

    assert closed["confirmed"] is False
    assert closed["reason"] == "curtain_position_provenance_unverified"
    closing_hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"entity_id": "cover.test", "position": 0},
        blocking=True,
    )

    stopping, stopping_hass, _state_value, _devices = _executor_for_target(
        initial_position=None,
    )
    stopped = await stopping.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "stop_cover"
    )
    assert stopped["accepted"] is True
    assert stopped["confirmed"] is False
    assert stopped["reason"] == "curtain_position_provenance_unverified"
    stopping_hass.services.async_call.assert_awaited_once_with(
        "cover", "stop_cover", {"entity_id": "cover.test"}, blocking=True
    )


@pytest.mark.asyncio
async def test_kitchen_numeric_zero_with_unknown_position_uses_position_readback() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        initial_position=None,
        observed_position=0,
    )

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "set_position", 0
    )

    assert receipt["confirmed"] is False
    assert "observedValue" not in receipt["readBack"]
    assert receipt["readBack"]["isNewEvidence"] is False
    hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"entity_id": "cover.test", "position": 0},
        blocking=True,
    )


@pytest.mark.asyncio
async def test_undated_zero_position_cannot_dispatch_or_become_no_op() -> None:
    executor, hass, state, _devices = _executor_for_target(
        initial_position=0,
        observed_position=0,
    )
    del state.last_updated
    del state.last_changed

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET,
        "set_position",
        0,
        idempotent_actions=True,
    )

    assert receipt["accepted"] is False
    assert receipt["error"] == "curtain_evidence_unavailable"
    assert "skipped" not in receipt
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_office_numeric_zero_cannot_bypass_close_guard() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        target_id=OFFICE_CURTAIN_TARGET,
        initial_position=20,
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {OFFICE_CURTAIN_TARGET}
        ),
    )

    receipt = await executor.async_execute_device_action(
        OFFICE_CURTAIN_TARGET, "set_position", 0
    )

    assert receipt["skipped"] is True
    assert receipt["confirmed"] is False
    assert receipt["reason"] == "office_close_guard"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_office_unknown_position_never_authorizes_close() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        target_id=OFFICE_CURTAIN_TARGET,
        initial_position=None,
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {OFFICE_CURTAIN_TARGET}
        ),
    )

    receipt = await executor.async_execute_device_action(
        OFFICE_CURTAIN_TARGET, "close_cover"
    )

    assert receipt["accepted"] is False
    assert receipt["error"] == "office_position_unknown"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_office_close_above_guard_remains_allowed_without_scale_grant() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        target_id=OFFICE_CURTAIN_TARGET,
        initial_position=21,
        observed_position=0,
    )

    receipt = await executor.async_execute_device_action(
        OFFICE_CURTAIN_TARGET, "close_cover"
    )

    assert receipt["confirmed"] is False
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    hass.services.async_call.assert_awaited_once_with(
        "cover", "close_cover", {"entity_id": "cover.test"}, blocking=True
    )


@pytest.mark.asyncio
async def test_non_office_close_is_not_blocked_by_missing_position() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        target_id=LIVING_CURTAIN_TARGET,
        initial_position=None,
        observed_position=0,
    )

    receipt = await executor.async_execute_device_action(
        LIVING_CURTAIN_TARGET, "close_cover"
    )

    assert receipt["confirmed"] is False
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    hass.services.async_call.assert_awaited_once_with(
        "cover", "close_cover", {"entity_id": "cover.test"}, blocking=True
    )


@pytest.mark.asyncio
async def test_office_safe_opening_from_zero_is_not_treated_as_target_twenty() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        target_id=OFFICE_CURTAIN_TARGET,
        initial_position=0,
        observed_position=90,
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {OFFICE_CURTAIN_TARGET}
        ),
    )

    receipt = await executor.async_execute_device_action(
        OFFICE_CURTAIN_TARGET, "set_position", 100
    )

    assert receipt["confirmed"] is False
    assert "observedValue" not in receipt["readBack"]
    hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"entity_id": "cover.test", "position": 90},
        blocking=True,
    )


@pytest.mark.asyncio
async def test_missing_position_descriptor_blocks_limited_open() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        include_set_position=False,
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "open_cover"
    )

    assert receipt["accepted"] is False
    assert receipt["error"] == "curtain_dispatch_descriptor_invalid"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_generation_change_before_dispatch_fails_closed() -> None:
    document = {"value": ScenarioControlDocument()}
    policy = CurtainCommandPolicy.with_confirmed_scales(
        {KITCHEN_CURTAIN_TARGET},
        control_document_provider=lambda: document["value"],
    )
    executor, hass, _state_value, _devices = _executor_for_target(
        policy=policy,
    )

    async def replace_policy() -> None:
        document["value"] = ScenarioControlDocument(
            policy_revision=1,
            policy=replace(
                ScenarioControlPolicy(), kitchen_cover_cap_percent=70
            ),
        )

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET,
        "open_cover",
        before_dispatch=replace_policy,
    )

    assert receipt["accepted"] is False
    assert receipt["error"] == "curtain_dispatch_plan_changed"
    hass.services.async_call.assert_not_awaited()


def test_dynamic_scale_authority_is_exact_and_part_of_policy_revision() -> None:
    authority = {
        "value": SimpleNamespace(
            confirmed=True,
            revision=7,
            identity_digest="office-identity-v1",
        )
    }
    policy = CurtainCommandPolicy(
        scale_authorization_provider=lambda target_id: (
            authority["value"]
            if target_id == OFFICE_CURTAIN_TARGET
            else SimpleNamespace(confirmed=False, revision=7, identity_digest=None)
        )
    )

    office = policy.plan(
        device=_device(target_id=OFFICE_CURTAIN_TARGET),
        action_id="set_position",
        requested=100,
        current_state=_state(0),
    )
    assert office is not None
    assert office.applied == 90
    assert "office-identity-v1" not in office.policy_revision

    with pytest.raises(CurtainPolicyError, match="curtain_scale_unconfirmed"):
        policy.plan(
            device=_device(target_id=KITCHEN_CURTAIN_TARGET),
            action_id="open_cover",
            requested=None,
            current_state=_state(0),
        )

    authority["value"] = SimpleNamespace(
        confirmed=False,
        revision=8,
        identity_digest="office-identity-v1",
    )
    with pytest.raises(CurtainPolicyError, match="curtain_scale_unconfirmed"):
        policy.plan(
            device=_device(target_id=OFFICE_CURTAIN_TARGET),
            action_id="open_cover",
            requested=None,
            current_state=_state(0),
        )


def test_broken_scale_authority_blocks_open_but_not_emergency_stop() -> None:
    def broken_authority(_target_id: str) -> object:
        raise RuntimeError("synthetic authority failure")

    policy = CurtainCommandPolicy(
        scale_authorization_provider=broken_authority,
    )
    with pytest.raises(CurtainPolicyError, match="curtain_scale_unconfirmed"):
        policy.plan(
            device=_device(target_id=OFFICE_CURTAIN_TARGET),
            action_id="open_cover",
            requested=None,
            current_state=_state(0),
        )
    stopped = policy.plan(
        device=_device(target_id=OFFICE_CURTAIN_TARGET),
        action_id="stop_cover",
        requested=None,
        current_state=_state(None),
    )
    assert stopped is not None
    assert stopped.service == "stop_cover"


@pytest.mark.asyncio
async def test_scale_revoke_after_planning_prevents_physical_dispatch() -> None:
    authority = {
        "value": SimpleNamespace(
            confirmed=True,
            revision=1,
            identity_digest="office-identity-v1",
        )
    }
    policy = CurtainCommandPolicy(
        scale_authorization_provider=lambda _target_id: authority["value"],
    )
    executor, hass, _state_value, _devices = _executor_for_target(
        target_id=OFFICE_CURTAIN_TARGET,
        policy=policy,
    )

    async def revoke() -> None:
        authority["value"] = SimpleNamespace(
            confirmed=False,
            revision=2,
            identity_digest="office-identity-v1",
        )

    receipt = await executor.async_execute_device_action(
        OFFICE_CURTAIN_TARGET,
        "set_position",
        100,
        before_dispatch=revoke,
    )

    assert receipt["accepted"] is False
    assert receipt["error"] == "curtain_dispatch_plan_changed"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_similar_target_uses_its_catalog_descriptor_without_limit() -> None:
    similar = f"{KITCHEN_CURTAIN_TARGET}-copy"
    executor, hass, _state_value, _devices = _executor_for_target(
        target_id=similar,
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )

    receipt = await executor.async_execute_device_action(similar, "open_cover")

    assert receipt["confirmed"] is True
    hass.services.async_call.assert_awaited_once_with(
        "cover", "open_cover", {"entity_id": "cover.test"}, blocking=True
    )


@pytest.mark.asyncio
async def test_broken_curtain_policy_does_not_block_unrelated_dispatch() -> None:
    def broken_provider() -> ScenarioControlDocument:
        raise RuntimeError("synthetic policy failure")

    executor, hass, _state_value, _devices = _executor_for_target(
        target_id="unrelated_cover",
        policy=CurtainCommandPolicy(broken_provider),
    )

    receipt = await executor.async_execute_device_action(
        "unrelated_cover", "open_cover"
    )

    assert receipt["confirmed"] is True
    hass.services.async_call.assert_awaited_once_with(
        "cover", "open_cover", {"entity_id": "cover.test"}, blocking=True
    )


@pytest.mark.asyncio
async def test_broken_cap_document_does_not_block_managed_stop() -> None:
    def broken_provider() -> ScenarioControlDocument:
        raise RuntimeError("synthetic policy failure")

    executor, hass, _state_value, _devices = _executor_for_target(
        initial_position=None,
        policy=CurtainCommandPolicy(broken_provider),
    )

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "stop_cover"
    )

    assert receipt["accepted"] is True
    hass.services.async_call.assert_awaited_once_with(
        "cover", "stop_cover", {"entity_id": "cover.test"}, blocking=True
    )


@pytest.mark.asyncio
async def test_position_evidence_must_be_timestamped_after_dispatch() -> None:
    executor, hass, state, _devices = _executor_for_target(
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )
    before_at = state.last_updated

    async def publish_old_observation(
        _domain: str,
        _service: str,
        _data: dict[str, object],
        **_kwargs: object,
    ) -> None:
        state.state = "open"
        state.attributes = {"current_position": 80}
        state.last_updated = before_at + timedelta(microseconds=1)

    hass.services.async_call.side_effect = publish_old_observation

    receipt = await executor.async_execute_device_action(
        KITCHEN_CURTAIN_TARGET, "set_position", 100
    )

    assert receipt["confirmed"] is False
    assert receipt["readBack"]["isNewEvidence"] is False
    assert "observedValue" not in receipt["readBack"]
    hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_position_readback_never_mixes_value_with_later_unknown_sample() -> None:
    executor, _hass, _state_value, _devices = _executor_for_target(
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )
    observed_at = datetime.now(timezone.utc)
    position = SimpleNamespace(
        state="open",
        attributes={"current_position": 79},
        last_updated=observed_at,
    )
    unknown = SimpleNamespace(
        state="unknown",
        attributes={"current_position": None},
        last_updated=observed_at + timedelta(microseconds=1),
    )
    reads = iter((position, unknown))
    executor._hass.states.get = lambda _entity_id: next(reads, unknown)

    read_back = await executor._read_back_device(
        "cover.test",
        "set_position",
        80,
        after_revision="before",
        require_new_evidence=True,
        window_seconds=0.01,
        before_position=0,
        position_readback=True,
        position_not_before=observed_at - timedelta(microseconds=1),
    )

    assert read_back["matched"] is False
    assert read_back["observedState"] == "unknown"
    assert "observedValue" not in read_back
    assert "evidenceRevision" not in read_back
    assert "evidenceSequence" not in read_back


@pytest.mark.asyncio
async def test_non_curtain_unmatched_new_revision_keeps_legacy_evidence_flag() -> None:
    executor, _hass, state, _devices = _executor_for_target(
        target_id="unrelated_cover"
    )
    state.state = "off"
    state.attributes = {}
    state.last_updated = datetime.now(timezone.utc)

    read_back = await executor._read_back_device(
        "cover.test",
        "turn_on",
        None,
        after_revision="before",
        require_new_evidence=True,
        window_seconds=0.01,
    )

    assert read_back["matched"] is False
    assert read_back["isNewEvidence"] is False


@pytest.mark.asyncio
async def test_non_curtain_no_op_does_not_read_broken_curtain_policy() -> None:
    def broken_provider() -> ScenarioControlDocument:
        raise RuntimeError("synthetic policy failure")

    observed_at = datetime.now(timezone.utc)
    state = SimpleNamespace(
        state="on",
        attributes={},
        last_updated=observed_at,
    )
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _entity_id: state),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    device = ScenarioDeviceEntry(
        target_id="ordinary_light",
        name="Обычный свет",
        entity_id="light.ordinary",
        actions=(
            ScenarioDeviceAction(
                action_id="turn_on",
                title="Включить",
                domain="light",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
        ),
    )
    catalog = ScenarioCatalog(
        devices={"ordinary_light": device},
        scenarios={},
    )

    async def nested(_scenario_id: str, **_kwargs: object) -> dict[str, object]:
        return {"status": "completed", "receipts": []}

    executor = ScenarioExecutor(
        hass,
        catalog,
        nested,
        curtain_command_policy=CurtainCommandPolicy(broken_provider),
    )

    receipt = await executor.async_execute_device_action(
        "ordinary_light", "turn_on", idempotent_actions=True
    )

    assert receipt["skipped"] is True
    assert receipt["confirmed"] is True
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_core_open_keeps_raw_service() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        target_id=LIVING_CURTAIN_TARGET,
    )

    receipt = await executor.async_execute_device_action(
        LIVING_CURTAIN_TARGET, "open_cover"
    )

    assert receipt["confirmed"] is False
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    hass.services.async_call.assert_awaited_once_with(
        "cover", "open_cover", {"entity_id": "cover.test"}, blocking=True
    )


@pytest.mark.asyncio
async def test_batch_keeps_order_and_uses_the_same_limited_path() -> None:
    executor, hass, _state_value, devices = _executor_for_target(
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )
    second = ScenarioDeviceEntry(
        target_id=LIVING_CURTAIN_TARGET,
        name="Шторы гостиная",
        entity_id="cover.living",
        actions=devices[KITCHEN_CURTAIN_TARGET].actions,
    )
    devices[LIVING_CURTAIN_TARGET] = second
    service = ScenarioService(
        hass,
        object(),
        executor._catalog,
        executor,
    )

    receipts = await service.async_execute_device_action_batch(
        [
            {"targetId": KITCHEN_CURTAIN_TARGET, "actionId": "open_cover"},
            {"targetId": LIVING_CURTAIN_TARGET, "actionId": "stop_cover"},
        ],
        correlation_id="curtain.batch.1",
    )

    assert [item["targetId"] for item in receipts] == [
        KITCHEN_CURTAIN_TARGET,
        LIVING_CURTAIN_TARGET,
    ]
    assert hass.services.async_call.await_args_list[0].args[:2] == (
        "cover",
        "set_cover_position",
    )


@pytest.mark.asyncio
async def test_regular_scenario_uses_limited_dispatch_and_position_readback() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )
    definition = _definition(
        ScenarioAction(
            id="open-kitchen",
            type=ScenarioActionType.DEVICE_ACTION,
            target_id=KITCHEN_CURTAIN_TARGET,
            action_id="open_cover",
        )
    )

    result = await executor.async_execute(
        definition, "curtain-run-1", scenario_id="curtain-regular"
    )

    assert result["receipts"][0]["confirmed"] is False
    assert result["receipts"][0]["service"] == "set_cover_position"
    assert "observedValue" not in result["receipts"][0]["read_back"]
    assert result["receipts"][0]["reason"] == (
        "curtain_position_provenance_unverified"
    )
    hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_nested_scenario_reaches_the_same_limited_dispatch_path() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
    )
    child = _definition(
        ScenarioAction(
            id="open-kitchen",
            type=ScenarioActionType.DEVICE_ACTION,
            target_id=KITCHEN_CURTAIN_TARGET,
            action_id="open_cover",
        )
    )

    async def run_child(
        scenario_id: str, **_kwargs: object
    ) -> dict[str, object]:
        assert scenario_id == "child"
        return await executor.async_execute(
            child, "curtain-child-run", scenario_id="child"
        )

    executor._run_callback = run_child
    parent = _definition(
        ScenarioAction(
            id="run-child",
            type=ScenarioActionType.RUN_SCENARIO,
            scenario_id="child",
        )
    )

    result = await executor.async_execute(
        parent, "curtain-parent-run", scenario_id="parent"
    )

    assert result["confirmed"] is True
    hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"entity_id": "cover.test", "position": 80},
        blocking=True,
    )


@pytest.mark.asyncio
async def test_full_core_no_op_sends_nothing_and_is_not_new_confirmation() -> None:
    executor, hass, _state_value, _devices = _executor_for_target(
        target_id=ALICE_CURTAIN_TARGET,
        initial_position=100,
    )

    receipt = await executor.async_execute_device_action(
        ALICE_CURTAIN_TARGET,
        "open_cover",
        idempotent_actions=True,
    )

    assert "skipped" not in receipt
    assert receipt["confirmed"] is False
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    assert receipt["readBack"]["attempted"] is True
    hass.services.async_call.assert_awaited_once()
