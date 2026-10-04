"""HH-014.b: a transferred core target reserves the legacy writer."""

from __future__ import annotations

import pytest

from custom_components.hausman_hub import compose_reserved_entity_ids_provider
from custom_components.hausman_hub.application.core_manual_input import (
    CoreManualInput,
    CoreManualInputError,
    CoreManualTargetBinding,
    synthetic_binary_light_profiles,
)
from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig,
    RoomLightingCoreBranch,
)
from custom_components.hausman_hub.application.core.command_service import (
    ClosedRegistration,
    ClosedTargetRegistration,
)
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.device_power_dependencies import (
    AUTO_TURN_ON_POLICY,
    DevicePowerDependency,
)

from test_room_lighting_runtime import (
    AWAY_ENTITY_ID,
    _ConfigService,
    _FakeHass,
    _MemoryShadowStore,
    _NOW_DT,
    _NOW_MS,
    _SpyExecutor,
    _config_payload,
    _curve_payload,
    _seed_presence_and_light,
    _trigger_config_payload,
)
from custom_components.hausman_hub.application.room_lighting_runtime import (
    RoomLightingRuntime,
)
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightAction,
    PlannedCommand,
)


def _branch(tmp_path):
    config = config_from_payload(_config_payload(commands_enabled=True))
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id=config.room_id,
            config=config,
            device_profiles=synthetic_binary_light_profiles(["light_main"]),
        ),
        store_path=tmp_path / "core-manual-reservation.json",
    )
    branch.activate_shadow()
    return branch, config


def _runtime(hass, config, executor, provider, **runtime_kwargs):
    return RoomLightingRuntime(
        hass,
        _ConfigService(config),
        __import__(
            "test_room_lighting_runtime", fromlist=["_MemoryShadowStore"]
        ).RoomLightingShadowService(_MemoryShadowStore()),
        executor=executor,
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
        reserved_entity_ids_provider=provider,
        away_entity_id=AWAY_ENTITY_ID,
        **runtime_kwargs,
    )


def _closed_registration(config):
    return ClosedRegistration(
        branch_id=config.room_id,
        import_id="registration-1",
        source_identity="test-source",
        source_revision=1,
        source_payload_digest="digest",
        targets=(ClosedTargetRegistration(
            target_id="public-main",
            resource_id="light_main",
            entity_id="light.demo_main",
            property_name="on",
            allowed_actions=("turn_on", "turn_off"),
            binding_revision=config.version,
            config_revision=config.version,
        ),),
    )


def test_typed_projection_reserves_before_a_branch_is_available(tmp_path):
    branch, config = _branch(tmp_path)
    intake = CoreManualInput()
    registration = _closed_registration(config)

    intake.mark_closed_registration_unavailable(branch_id=config.room_id)
    intake.install_closed_registration_projection(registration)

    assert intake.command_entity_ids() == frozenset({"light.demo_main"})
    assert intake.registration_ready("public-main") is False
    assert intake.core_control_state("public-main")["status"] == "unavailable"
    # Replaying exactly the durable image is harmless; attaching the branch
    # makes only this transferred target ready.
    intake.install_closed_registration_projection(registration, branch=branch)
    assert intake.registration_ready("public-main") is True


def test_scoped_projection_does_not_clear_a_global_unknown_marker(tmp_path):
    _, config = _branch(tmp_path)
    intake = CoreManualInput()
    intake.mark_closed_registration_unavailable()
    intake.install_closed_registration_projection(_closed_registration(config))

    with pytest.raises(CoreManualInputError, match="unresolved"):
        intake.command_entity_ids()


@pytest.mark.asyncio
async def test_registered_core_target_blocks_legacy_writer(tmp_path):
    """A registered core binding blocks the real legacy executor."""

    branch, config = _branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(
        branch,
        CoreManualTargetBinding(
            target_id="public-main",
            resource_id="light_main",
            entity_id="light.demo_main",
            room_id=config.room_id,
        ),
    )
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _runtime(
        hass,
        config,
        executor,
        compose_reserved_entity_ids_provider(
            lambda: frozenset(), intake.command_entity_ids
        ),
    )

    await runtime.start(hass, "entry")
    try:
        receipt = await runtime.async_execute_command(
            config, PlannedCommand("light_main", LightAction.TURN_ON)
        )
        assert executor.calls == []
        assert receipt["reason"] == "reserved_command_target"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_core_reservation_is_dynamic_and_keeps_other_reservations(tmp_path):
    """The assembled provider observes late registration and unions sources."""

    branch, config = _branch(tmp_path)
    intake = CoreManualInput()
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _runtime(
        hass,
        config,
        executor,
        compose_reserved_entity_ids_provider(
            lambda: frozenset({"light.legacy_reserved"}),
            intake.command_entity_ids,
        ),
    )
    await runtime.start(hass, "entry")
    try:
        before_direct_command = len(executor.calls)
        await runtime.async_execute_command(
            config, PlannedCommand("light_main", LightAction.TURN_ON)
        )
        assert len(executor.calls) == before_direct_command + 1

        intake.register_transferred_target(
            branch,
            CoreManualTargetBinding(
                target_id="public-main",
                resource_id="light_main",
                entity_id="light.demo_main",
                room_id=config.room_id,
            ),
        )
        receipt = await runtime.async_execute_command(
            config, PlannedCommand("light_main", LightAction.TURN_ON)
        )
        assert receipt["reason"] == "reserved_command_target"
        assert len(executor.calls) == before_direct_command + 1
        assert runtime._reserved_entity_ids() == frozenset(
            {"light.demo_main", "light.legacy_reserved"}
        )
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_entity_id", ("   ", "not-an-entity"))
async def test_invalid_core_binding_fails_closed_in_runtime(
    tmp_path, invalid_entity_id
):
    branch, config = _branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(
        branch,
        CoreManualTargetBinding(
            target_id="broken-main",
            resource_id="light_main",
            entity_id=invalid_entity_id,
            room_id=config.room_id,
        ),
    )
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _runtime(
        hass,
        config,
        executor,
        compose_reserved_entity_ids_provider(
            lambda: frozenset(), intake.command_entity_ids
        ),
    )
    await runtime.start(hass, "entry")
    try:
        receipt = await runtime.async_execute_command(
            config, PlannedCommand("light_main", LightAction.TURN_ON)
        )
        assert executor.calls == []
        assert receipt["reason"] == "reserved_command_target"
    finally:
        await runtime.stop()


def test_projection_deduplicates_alias_bindings_and_invalid_binding_fails(tmp_path):
    intake = CoreManualInput()
    branch = object()
    binding = CoreManualTargetBinding(
        target_id="public-main",
        resource_id="light_main",
        entity_id="light.demo_main",
        room_id="test_room",
    )
    alias = CoreManualTargetBinding(
        target_id="public-main-alias",
        resource_id="light_main",
        entity_id="light.demo_main",
        room_id="test_room",
    )
    intake.register_transferred_target(branch, binding)
    intake.register_transferred_target(branch, alias)
    assert intake.command_entity_ids() == frozenset({"light.demo_main"})

    bad = CoreManualInput()
    bad.register_transferred_target(
        branch,
        CoreManualTargetBinding(
            target_id="bad",
            resource_id="light_main",
            entity_id="",  # type: ignore[arg-type]
            room_id="test_room",
        ),
    )
    with pytest.raises(CoreManualInputError, match="valid command entity"):
        bad.command_entity_ids()

    closed_branch, config = _branch(tmp_path)
    closed = CoreManualInput()
    closed.register_transferred_target(
        closed_branch,
        CoreManualTargetBinding(
            target_id="closed-main",
            resource_id="light_main",
            entity_id="light.demo_main",
            room_id=config.room_id,
        ),
    )
    closed_branch.on_runtime_stop()
    assert closed.command_entity_ids() == frozenset({"light.demo_main"})


@pytest.mark.asyncio
async def test_registration_during_power_wait_closes_final_legacy_send(tmp_path):
    """A core registration after pending persistence still blocks the send."""

    branch, _ = _branch(tmp_path)
    intake = CoreManualInput()
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("light.demo_mirror", "off", last_changed=_NOW_DT)
    hass.states.set("switch.demo_power", "off", last_changed=_NOW_DT)
    payload = _curve_payload(commands_enabled=True)
    payload["devices"]["power_switch"] = _config_payload(
        power_switch_entity="switch.demo_power"
    )["devices"]["power_switch"]
    config = config_from_payload(payload)

    class Executor(_SpyExecutor):
        async def async_power_on(self, hass, entity_id):
            intake.register_transferred_target(
                branch,
                CoreManualTargetBinding(
                    target_id="public-main",
                    resource_id="light_main",
                    entity_id="light.demo_main",
                    room_id=branch.room_id,
                ),
            )
            hass.states.set(entity_id, "on", last_changed=_NOW_DT)
            return True

    executor = Executor()
    runtime = _runtime(
        hass,
        config,
        executor,
        compose_reserved_entity_ids_provider(
            lambda: frozenset(), intake.command_entity_ids
        ),
    )
    await runtime.start(hass, "entry")
    try:
        assert executor.calls == []
        dispatch = runtime._shadow.journal_payload()["entries"][-1]["dispatch"][0]
        assert dispatch["reason"] == "reserved_command_target"
        assert dispatch["sent"] is False
        assert dispatch["sendOutcome"] == "not_sent"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("exact", "legacy"))
@pytest.mark.parametrize("transition", ("register", "provider_error", "allowed"))
async def test_power_transition_after_pending_persist(
    tmp_path, monkeypatch, path, transition
):
    """The final power boundary rechecks the real dynamic core registry."""

    branch, config = _branch(tmp_path)
    runtime_config = config_from_payload(_config_payload(commands_enabled=False))
    intake = CoreManualInput()
    provider_state = {"failed": False}

    def core_reservations():
        if provider_state["failed"]:
            raise RuntimeError("core reservation source unavailable")
        return intake.command_entity_ids()
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("switch.demo_power", "off", last_changed=_NOW_DT)

    class PowerExecutor(_SpyExecutor):
        def __init__(self):
            super().__init__()
            self.power_calls = []

        async def async_power_on(self, hass, entity_id):
            self.power_calls.append(entity_id)
            hass.states.set(entity_id, "on", last_changed=_NOW_DT)
            return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

    executor = PowerExecutor()
    runtime = _runtime(
        hass,
        runtime_config,
        executor,
        compose_reserved_entity_ids_provider(
            lambda: frozenset(), core_reservations
        ),
        electrical_breaker_resolver=lambda entity_id: False,
    )
    original_persist = runtime._persist_pending_power_dispatch

    async def persist_then_register(**kwargs):
        persisted = await original_persist(**kwargs)
        if transition == "register":
            intake.register_transferred_target(
                branch,
                CoreManualTargetBinding(
                    target_id=f"power-{path}",
                    resource_id="light_main",
                    entity_id="switch.demo_power",
                    room_id=config.room_id,
                ),
            )
        elif transition == "provider_error":
            provider_state["failed"] = True
        return persisted

    monkeypatch.setattr(
        runtime, "_persist_pending_power_dispatch", persist_then_register
    )
    await runtime.start(hass, "entry")
    try:
        if path == "exact":
            powered, detail = await runtime._ensure_power_source_locked(
                DevicePowerDependency(
                    dependent_entity_id="light.demo_main",
                    power_source_entity_id="switch.demo_power",
                    policy=AUTO_TURN_ON_POLICY,
                    warmup_seconds=0,
                ),
                at=_NOW_MS,
                room_id=config.room_id,
                operation_id=f"power-{path}",
                config_version=1,
            )
        else:
            powered, detail = await runtime._ensure_legacy_room_power_locked(
                "switch.demo_power",
                at=_NOW_MS,
                room_id=config.room_id,
                operation_id=f"power-{path}",
                config_version=1,
            )
        if transition == "allowed":
            assert powered
            assert detail["sent"] is True
            assert executor.power_calls == ["switch.demo_power"]
        else:
            assert not powered
            assert detail["reason"] == "power_source_reserved"
            assert detail["sent"] is False
            assert detail["sendOutcome"] == "not_sent"
            assert executor.power_calls == []
            closed_power_receipts = [
                receipt["power"]
                for entry in runtime._shadow.journal_payload()["entries"]
                for receipt in entry.get("dispatch", [])
                if receipt.get("power", {}).get("sourceEntityId")
                == "switch.demo_power"
            ]
            assert closed_power_receipts[-1]["reason"] == "power_source_reserved"
            assert closed_power_receipts[-1]["sendOutcome"] == "not_sent"
        assert executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ("register", "provider_error", "allowed"))
async def test_manual_group_rechecks_core_reservation_after_first_send(
    tmp_path, transition
):
    """A manual group preserves its first effect but closes later sends."""

    branch, _ = _branch(tmp_path)
    intake = CoreManualInput()
    provider_state = {"failed": False}

    def core_reservations():
        if provider_state["failed"]:
            raise RuntimeError("core reservation source unavailable")
        return intake.command_entity_ids()

    payload = _trigger_config_payload()
    payload["commandsEnabled"] = True
    payload["devices"]["light_targets"].append(  # type: ignore[index]
        {
            "id": "light_second",
            "name": "Второй свет",
            "kind": "switch",
            "entityId": "switch.demo_second",
            "role": None,
            "groupId": None,
            "brightness": False,
            "color_temperature": False,
            "autoAdoptOverride": None,
        }
    )
    payload["switchBindings"][0]["targets"]["lightTargets"] = [  # type: ignore[index]
        "light_main", "light_second"
    ]
    config = config_from_payload(payload)
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("switch.demo_second", "off", last_changed=_NOW_DT)

    class Executor(_SpyExecutor):
        async def execute_manual(self, hass, command):
            self.calls.append(command)
            if len(self.calls) == 1:
                if transition == "register":
                    intake.register_transferred_target(
                        branch,
                        CoreManualTargetBinding(
                            target_id="group-second",
                            resource_id="light_main",
                            entity_id="switch.demo_second",
                            room_id=branch.room_id,
                        ),
                    )
                elif transition == "provider_error":
                    provider_state["failed"] = True
            return {"confirmed": True}

    executor = Executor()
    runtime = _runtime(
        hass,
        config,
        executor,
        compose_reserved_entity_ids_provider(
            lambda: frozenset(), core_reservations
        ),
    )
    await runtime.start(hass, "entry")
    try:
        assert executor.calls == []
        await runtime._handle_device_trigger(
            config, "device_demo_mirror", "sw_mirror", "1_single"
        )
        expected = ["light_main", "light_second"] if transition == "allowed" else ["light_main"]
        assert [command.target_id for command in executor.calls] == expected
    finally:
        await runtime.stop()
