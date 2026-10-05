"""Assembly tests of the shadow lighting branch (HH-014.b, приложение 06).

Verifies the real assembly point: the integration creates the branch
only for an explicitly configured room, starts it in SHADOW with the
closed transport, and the runtime delivers events through the input
port. No physical send is possible by construction.
"""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchError,
    CoreBranchConfig,
    RoomLightingCoreBranch,
)
from custom_components.hausman_hub.application.core.command_service import (
    ClosedRegistration,
    ClosedTargetRegistration,
    StorageUnavailable,
    _payload_digest,
)
from custom_components.hausman_hub.application.core_manual_input import CoreManualInput
from custom_components.hausman_hub.application.configuration import (
    ConfigurationViolation,
    effective_configuration,
)
from custom_components.hausman_hub.application.room_lighting_runtime import RoomLightingRuntime
from custom_components.hausman_hub.application.room_lighting_shadow import RoomLightingShadowService
from custom_components.hausman_hub import _async_start_room_lighting_with_core
from custom_components.hausman_hub.domain.room_lighting import (
    config_from_payload,
)
from test_room_lighting_runtime import (
    AWAY_ENTITY_ID,
    _ConfigService,
    _FakeHass,
    _MemoryShadowStore,
    _NOW_MS,
    _SpyExecutor,
)
from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES, FakeEntry, FakeHomeAssistant, fake_home_assistant_modules,
)


class _StartupHass(_FakeHass):
    """Existing runtime fake plus the two setup-boundary capabilities."""

    def __init__(self, config_dir) -> None:
        super().__init__()
        self.config.config_dir = str(config_dir)
        self.executor_jobs: list[tuple[object, tuple[object, ...]]] = []

    async def async_add_executor_job(self, target, *args):
        self.executor_jobs.append((target, args))
        return target(*args)


def _startup_runtime(hass, config, intake, executor):
    return RoomLightingRuntime(
        hass,
        _ConfigService(config),
        RoomLightingShadowService(_MemoryShadowStore()),
        executor=executor,
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
        reserved_entity_ids_provider=intake.command_entity_ids,
        away_entity_id=AWAY_ENTITY_ID,
    )


def _startup_registration(config):
    target = config.devices.light_targets[0]
    return ClosedRegistration(
        branch_id=config.room_id,
        import_id="startup-registration",
        source_identity="synthetic-startup",
        source_revision=1,
        source_payload_digest="0" * 64,
        targets=(ClosedTargetRegistration(
            target_id="public-startup-main",
            resource_id=target.id,
            entity_id=target.entity_id,
            property_name="on",
            allowed_actions=("turn_on", "turn_off"),
            binding_revision=config.version,
            config_revision=config.version,
        ),),
    )


def _seed_registered_startup_image(tmp_path, config):
    path = tmp_path / f"hausman_hub.core_shadow_{config.room_id}.json"
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(room_id=config.room_id, config=config),
        store_path=path,
        expected_closed_image=False,
    )
    branch.import_closed_registration(
        _startup_registration(config),
        expected_commit_revision=branch.core_service._store.commit_revision,
    )
    return path


def _seed_old_startup_image(tmp_path, config):
    path = tmp_path / f"hausman_hub.core_shadow_{config.room_id}.json"
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(room_id=config.room_id, config=config),
        store_path=path,
        expected_closed_image=False,
    )
    branch.activate_shadow()
    return path


async def _start_with_image(
    tmp_path, *, expected, image_writer=None, after_image=None, before_start=None
):
    config = config_from_payload(_config_payload("startup-room"))
    hass = _StartupHass(tmp_path)
    hass.states.set("binary_sensor.test_presence", "off")
    hass.states.set("light.test_main", "off")
    hass.states.set(AWAY_ENTITY_ID, "off")
    intake = CoreManualInput()
    executor = _SpyExecutor()
    runtime = _startup_runtime(hass, config, intake, executor)
    if before_start is not None:
        before_start(config, runtime, executor)
    if image_writer is not None:
        image_writer(tmp_path, config)
    if after_image is not None:
        after_image()
    hooks = []
    entry = SimpleNamespace(entry_id="startup-entry", async_on_unload=hooks.append)
    domain_data = {}
    branch = await _async_start_room_lighting_with_core(
        hass, entry,
        room_lighting_service=_ConfigService(config),
        room_lighting_runtime=runtime,
        core_manual_input=intake,
        domain_data=domain_data,
        core_shadow_room_id=config.room_id,
        mutation_lock=asyncio.Lock(),
        power_dependency_resolver=lambda: {},
        expected_closed_image=expected,
    )
    return config, hass, intake, executor, runtime, hooks, branch


def _config_payload(room_id: str = "test_room") -> dict:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": "Тест",
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.test_presence",
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "test_main",
                    "name": "Основной",
                    "kind": "light",
                    "entityId": "light.test_main",
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [],
        "switchBindings": [],
        "illumination": None,
        "dimming": {"enabled": False},
        "manualOffProtection": {"enabled": False},
        "awayBehavior": {"mode": "none"},
        "autoAdopt": False,
        "updatedAt": 1,
        "overrides": {},
    }


def _branch(tmp_path, room_id: str = "test_room"):
    config = config_from_payload(_config_payload(room_id))
    return RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id=room_id,
            config=config,
            timezone="Asia/Omsk",
            sunrise="08:00",
            sunset="18:00",
        ),
        store_path=tmp_path / f"core_shadow_{room_id}.json",
    )


def _state(value: str, brightness: int | None = None):
    state = MagicMock()
    state.state = value
    state.attributes = {"brightness": brightness} if brightness else {}
    return state


def test_branch_starts_off_and_activates_into_shadow(tmp_path):
    branch = _branch(tmp_path)
    assert branch.mode == "off"
    assert branch.physical_send_enabled() is False
    branch.activate_shadow()
    assert branch.mode == "shadow"
    # Activation processes one room event through the core.
    assert branch.decision_explanation() is not None


def test_expected_closed_image_never_creates_an_empty_branch(tmp_path):
    config = config_from_payload(_config_payload("expected-room"))
    with pytest.raises(CoreBranchError, match="expected closed control image"):
        RoomLightingCoreBranch(
            branch_config=CoreBranchConfig(
                room_id="expected-room", config=config,
            ),
            store_path=tmp_path / "missing-expected.json",
            expected_closed_image=True,
        )


def test_branch_events_are_observed_in_shadow_without_sending(tmp_path):
    branch = _branch(tmp_path)
    branch.activate_shadow()
    # Presence turns on: the core records the observation and evaluates.
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    explanation = branch.decision_explanation()
    assert explanation is not None
    # The closed transport never sent anything and never will.
    assert branch.physical_send_enabled() is False


def test_branch_off_ignores_events(tmp_path):
    branch = _branch(tmp_path)
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    # In OFF mode nothing is evaluated.
    assert branch.decision_explanation() is None


def test_deactivation_stops_processing(tmp_path):
    branch = _branch(tmp_path)
    branch.activate_shadow()
    before = branch.decision_explanation()
    branch.deactivate()
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    branch.process_schedule_tick()
    # After deactivation no new decision cycle replaced the stored one.
    assert branch.decision_explanation() is before


@pytest.mark.parametrize("callbacks", (("deactivate", "on_runtime_stop"), ("on_runtime_stop", "deactivate")))
def test_stop_callbacks_retry_the_same_pending_close_without_reopening_input(
    tmp_path, monkeypatch, callbacks
):
    branch = _branch(tmp_path)
    branch.activate_shadow()
    real_close = branch._service.close_dispatch_for_restore
    attempts = 0

    def fail_once_then_close():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise StorageUnavailable("injected durable close failure")
        real_close()

    monkeypatch.setattr(
        branch._service, "close_dispatch_for_restore", fail_once_then_close
    )
    with pytest.raises(StorageUnavailable):
        getattr(branch, callbacks[0])()
    getattr(branch, callbacks[1])()

    assert attempts == 2
    assert branch.input_closed is True
    assert branch.physical_send_enabled() is False


def test_resource_view_reports_facts_not_fiction(tmp_path):
    branch = _branch(tmp_path)
    branch.activate_shadow()
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    view = branch.resource_view("sensor_presence")
    assert view is not None
    # Without dispatch permission there is no operation pretending to be
    # a physical result.
    assert branch.physical_send_enabled() is False


def test_full_room_import_keeps_passive_sensor_outside_manual_registration(tmp_path):
    """The facade transfers the complete room image but registers lights only."""
    source = _branch(tmp_path / "source", "room-with-sensor")
    destination = _branch(tmp_path / "destination", "room-with-sensor")
    source.on_runtime_stop()
    destination.on_runtime_stop()
    config = source._branch_config.config
    registration = ClosedRegistration(
        branch_id=config.room_id,
        import_id="full-room",
        source_identity="source",
        source_revision=source.core_service._store.commit_revision,
        source_payload_digest=_payload_digest(source.core_service._store.load()),
        targets=(ClosedTargetRegistration(
            target_id="public-main",
            resource_id="test_main",
            entity_id="light.test_main",
            property_name="on",
            allowed_actions=("turn_on", "turn_off"),
            binding_revision=config.version,
            config_revision=config.version,
        ),),
    )
    destination.import_closed_registration_from(
        source.core_service,
        registration,
        expected_commit_revision=destination.core_service._store.commit_revision,
        expected_source_revision=source.core_service._store.commit_revision,
    )
    assert destination.core_service.record("sensor_presence") == source.core_service.record(
        "sensor_presence"
    )
    assert destination.core_service.closed_registrations() == (registration,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected", "image_kind"),
    (
        (True, "missing"),
        (None, "missing"),
        (None, "truncated"),
        (None, "schema999"),
        (None, "malformed_registry"),
        (None, "malformed_image"),
    ),
)
async def test_startup_invalid_images_keep_every_legacy_command_closed(
    tmp_path, expected, image_kind
):
    def write_image(root, config):
        path = root / f"hausman_hub.core_shadow_{config.room_id}.json"
        if image_kind == "truncated":
            path.write_text("{", encoding="utf-8")
        elif image_kind == "schema999":
            path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
        elif image_kind == "malformed_registry":
            path.write_text(json.dumps({
                "schema_version": 1, "commit_revision": 1,
                "records": {}, "operations": {}, "receipts": {},
                "receipt_times": {}, "receipt_fingerprints": {},
                "confirm_keys": {}, "episode_locks": {},
                "scope_revisions": {}, "config_revisions": {},
                "closed_registrations": {config.room_id: {"targets": []}},
            }), encoding="utf-8")
        elif image_kind == "malformed_image":
            path.write_text(json.dumps({
                "schema_version": 1, "commit_revision": True,
            }), encoding="utf-8")

    writer = None if image_kind == "missing" else write_image
    config, _, intake, executor, runtime, hooks, branch = await _start_with_image(
        tmp_path, expected=expected, image_writer=writer
    )
    try:
        assert branch is None
        assert hooks == [runtime.cancel]
        with pytest.raises(Exception):
            intake.command_entity_ids()
        receipt = await runtime.async_execute_command(
            config, __import__(
                "custom_components.hausman_hub.domain.room_lighting_engine",
                fromlist=["PlannedCommand", "LightAction"],
            ).PlannedCommand("test_main", __import__(
                "custom_components.hausman_hub.domain.room_lighting_engine",
                fromlist=["LightAction"],
            ).LightAction.TURN_ON))
        assert receipt["reason"] == "reserved_command_target"
        assert executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_startup_valid_registration_reserves_before_the_first_runtime_command(tmp_path):
    first_callback_receipts = []

    def wrap_first_callback(config, runtime, executor):
        real_process = runtime.async_process

        async def first_process(*args, **kwargs):
            from custom_components.hausman_hub.domain.room_lighting_engine import (
                LightAction, PlannedCommand,
            )
            first_callback_receipts.append(await runtime.async_execute_command(
                config, PlannedCommand("test_main", LightAction.TURN_ON)
            ))
            return await real_process(*args, **kwargs)

        runtime.async_process = first_process

    config, hass, intake, executor, runtime, hooks, branch = await _start_with_image(
        tmp_path, expected=True, image_writer=_seed_registered_startup_image,
        before_start=wrap_first_callback,
    )
    try:
        assert branch is not None
        assert intake.command_entity_ids() == frozenset({"light.test_main"})
        assert intake.registration_ready("public-startup-main") is True
        assert len(hass.executor_jobs) >= 4
        assert len(hooks) == 4
        assert first_callback_receipts[0]["reason"] == "reserved_command_target"
        assert executor.calls == []
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightAction, PlannedCommand,
        )
        receipt = await runtime.async_execute_command(
            config, PlannedCommand("test_main", LightAction.TURN_ON)
        )
        assert receipt["reason"] == "reserved_command_target"
        assert executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", (None, False))
async def test_startup_old_or_explicit_first_create_remains_legacy(tmp_path, expected):
    config, _, intake, executor, runtime, _, branch = await _start_with_image(
        tmp_path, expected=expected,
        image_writer=_seed_old_startup_image if expected is None else None,
    )
    try:
        if expected is None:
            assert branch is None
            with pytest.raises(Exception):
                intake.command_entity_ids()
        else:
            assert branch is not None
            assert intake.command_entity_ids() == frozenset()
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("plugin", "restore"))
async def test_registered_startup_failure_keeps_exact_deny_only_reservation(
    tmp_path, monkeypatch, failure
):
    import custom_components.hausman_hub.application.room_lighting_core as core_module

    def install_failure():
        if failure == "plugin":
            monkeypatch.setattr(
                core_module, "LightingPlugin",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("plugin fault")),
            )
        else:
            monkeypatch.setattr(
                core_module.CoreCommandService, "restore",
                classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(
                    StorageUnavailable("restore fault")
                )),
            )
    config, _, intake, executor, runtime, hooks, branch = await _start_with_image(
        tmp_path, expected=True, image_writer=_seed_registered_startup_image,
        after_image=install_failure,
    )
    try:
        assert branch is None
        assert hooks == [runtime.cancel]
        assert intake.command_entity_ids() == frozenset({"light.test_main"})
        assert intake.registration_ready("public-startup-main") is False
        assert executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recheck", "change"),
    (
        ("raises", "bound"),
        ("none", "bound"),
        ("corrupt", "bound"),
        ("corrupt", "emptytuple"),
        ("corrupt", "objecttuple"),
        ("otherroom", "bound"),
        ("valid", "unchanged"),
        ("valid", "bound"),
        ("valid", "empty"),
        ("valid", "unbound"),
        ("plugin", "unchanged"),
    ),
)
async def test_startup_recheck_matrix_keeps_denied_targets_without_a_window(
    tmp_path, request, recheck, change
):
    """One service feeds the startup helper and the real runtime.

    HH-014.b B06-03/B06-02R1: the awaited recheck runs behind a re-armed
    unknown barrier (the pending recheck denies globally); raises/None,
    readable-but-corrupt objects (a faked empty or object-filled
    ``light_targets`` tuple) and a valid configuration of another room
    keep that global deny; a proved changed room reserves old plus its
    real bound new entities; a proved empty/unbound room closes only its
    own old scope; a constructor failure after a proved recheck keeps the
    exact old scope while the neighbour keeps exactly one legacy command.
    """

    old = config_from_payload(_config_payload("startup-room"))
    changed_payload = _config_payload("startup-room")
    changed_payload["version"] = 2
    changed_payload["devices"]["light_targets"][0]["entityId"] = "light.changed"
    if change == "empty":
        changed_payload["devices"]["light_targets"] = []
    if change == "unbound":
        changed_payload["devices"]["light_targets"][0]["entityId"] = None
    changed = old if change == "unchanged" else config_from_payload(changed_payload)
    other_room_payload = _config_payload("other-room")
    other_room_payload["version"] = 2
    other_room_payload["devices"]["light_targets"][0]["entityId"] = "light.changed"
    other_room = config_from_payload(other_room_payload)
    neighbor_payload = _config_payload("neighbor-room")
    neighbor_payload["devices"]["light_targets"][0]["id"] = "neighbor-light"
    neighbor_payload["devices"]["light_targets"][0]["entityId"] = "light.neighbor"
    neighbor = config_from_payload(neighbor_payload)
    _seed_registered_startup_image(tmp_path, old)

    class _MovingService(_ConfigService):
        def __init__(self):
            super().__init__(changed, neighbor)
            self.get_calls = 0
            self.list_calls = 0
            self.pending_probe = None

        async def async_get_config(self, room_id):
            self.get_calls += 1
            if self.get_calls == 2 and self.pending_probe is not None:
                probe, self.pending_probe = self.pending_probe, None
                await probe()
            if self.get_calls == 1:
                return old
            if recheck == "raises":
                raise RuntimeError("synthetic recheck storage unavailable")
            if recheck == "none":
                return None
            if recheck == "corrupt":
                if change == "emptytuple":
                    return SimpleNamespace(
                        devices=SimpleNamespace(light_targets=())
                    )
                if change == "objecttuple":
                    return SimpleNamespace(
                        devices=SimpleNamespace(light_targets=(object(),))
                    )
                return SimpleNamespace(devices=None)
            if recheck == "otherroom":
                return other_room
            return changed

        async def async_list_configs(self):
            self.list_calls += 1
            return await super().async_list_configs()

    hass = _StartupHass(tmp_path)
    for entity_id in (
        "binary_sensor.test_presence",
        "light.test_main",
        "light.changed",
        "light.neighbor",
        AWAY_ENTITY_ID,
    ):
        hass.states.set(entity_id, "off")
    intake = CoreManualInput()
    executor = _SpyExecutor()
    service = _MovingService()
    runtime = RoomLightingRuntime(
        hass,
        service,
        RoomLightingShadowService(_MemoryShadowStore()),
        executor=executor,
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
        reserved_entity_ids_provider=intake.command_entity_ids,
        away_entity_id=AWAY_ENTITY_ID,
    )
    if recheck == "plugin":
        import custom_components.hausman_hub.application.room_lighting_core as core_module

        monkeypatch = request.getfixturevalue("monkeypatch")
        monkeypatch.setattr(
            core_module,
            "LightingPlugin",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("plugin fault")),
        )

    async def assert_pending_recheck_is_globally_closed():
        with pytest.raises(Exception):
            intake.command_entity_ids()
        assert intake.registration_ready("public-startup-main") is False

    service.pending_probe = assert_pending_recheck_is_globally_closed
    hooks = []
    try:
        branch = await _async_start_room_lighting_with_core(
            hass,
            SimpleNamespace(entry_id="recheck-matrix", async_on_unload=hooks.append),
            room_lighting_service=service,
            room_lighting_runtime=runtime,
            core_manual_input=intake,
            domain_data={},
            core_shadow_room_id=old.room_id,
            mutation_lock=asyncio.Lock(),
            power_dependency_resolver=lambda: {},
            expected_closed_image=True,
        )
        assert service.get_calls == 2
        assert service.list_calls == 1
        assert runtime.running is True
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightAction,
            PlannedCommand,
        )

        old_receipt = await runtime.async_execute_command(
            old, PlannedCommand("test_main", LightAction.TURN_ON)
        )
        assert old_receipt["reason"] == "reserved_command_target"
        neighbor_receipt = await runtime.async_execute_command(
            neighbor, PlannedCommand("neighbor-light", LightAction.TURN_ON)
        )
        if recheck == "valid" and change == "unchanged":
            assert branch is not None
            assert intake.command_entity_ids() == frozenset({"light.test_main"})
            assert intake.registration_ready("public-startup-main") is True
        else:
            assert branch is None
            assert hooks == [runtime.cancel]
            if recheck == "valid" and change == "bound":
                new_receipt = await runtime.async_execute_command(
                    changed, PlannedCommand("test_main", LightAction.TURN_ON)
                )
                assert new_receipt["reason"] == "reserved_command_target"
                assert intake.command_entity_ids() == frozenset(
                    {"light.test_main", "light.changed"}
                )
            elif recheck == "plugin":
                assert intake.command_entity_ids() == frozenset({"light.test_main"})
                assert intake.registration_ready("public-startup-main") is False
            elif recheck == "valid":
                assert intake.command_entity_ids() == frozenset({"light.test_main"})
            else:
                with pytest.raises(Exception):
                    intake.command_entity_ids()
                new_receipt = await runtime.async_execute_command(
                    changed, PlannedCommand("test_main", LightAction.TURN_ON)
                )
                assert new_receipt["reason"] == "reserved_command_target"
        if recheck in ("valid", "plugin"):
            assert neighbor_receipt.get("reason") is None
            assert [command.target_id for command in executor.calls] == [
                "neighbor-light"
            ]
        else:
            assert neighbor_receipt["reason"] == "reserved_command_target"
            assert executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_repeated_startup_does_not_add_hooks_or_start_again(tmp_path):
    config, hass, intake, executor, runtime, hooks, branch = await _start_with_image(
        tmp_path, expected=True, image_writer=_seed_registered_startup_image
    )
    try:
        before_hooks = tuple(hooks)
        repeated = await _async_start_room_lighting_with_core(
            hass, SimpleNamespace(entry_id="startup-entry", async_on_unload=hooks.append),
            room_lighting_service=_ConfigService(config),
            room_lighting_runtime=runtime, core_manual_input=intake,
            domain_data={"core_lighting_branch": branch},
            core_shadow_room_id=config.room_id, mutation_lock=asyncio.Lock(),
            power_dependency_resolver=lambda: {}, expected_closed_image=True,
        )
        assert repeated is branch
        assert tuple(hooks) == before_hooks
        assert executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_startup_refuses_a_configuration_race_before_branch_restore(tmp_path):
    config = config_from_payload(_config_payload("startup-room"))
    changed_payload = _config_payload("startup-room")
    changed_payload["version"] = 2
    changed_payload["devices"]["light_targets"][0]["entityId"] = "light.changed"
    changed = config_from_payload(changed_payload)
    _seed_registered_startup_image(tmp_path, config)

    class _MovingConfigService(_ConfigService):
        def __init__(self):
            super().__init__(config)
            self.calls = 0

        async def async_get_config(self, room_id):
            self.calls += 1
            return config if self.calls == 1 else changed

    hass = _StartupHass(tmp_path)
    hass.states.set("binary_sensor.test_presence", "off")
    hass.states.set("light.test_main", "off")
    hass.states.set(AWAY_ENTITY_ID, "off")
    intake = CoreManualInput()
    executor = _SpyExecutor()
    runtime = _startup_runtime(hass, config, intake, executor)
    hooks = []
    try:
        branch = await _async_start_room_lighting_with_core(
            hass, SimpleNamespace(entry_id="race", async_on_unload=hooks.append),
            room_lighting_service=_MovingConfigService(),
            room_lighting_runtime=runtime, core_manual_input=intake,
            domain_data={}, core_shadow_room_id=config.room_id,
            mutation_lock=asyncio.Lock(), power_dependency_resolver=lambda: {},
            expected_closed_image=True,
        )
        assert branch is None
        assert hooks == [runtime.cancel]
        assert intake.registration_ready("public-startup-main") is False
        assert executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_actual_setup_entry_calls_the_startup_helper_once(monkeypatch):
    """Assembly coverage: the production entry point awaits the helper."""
    import custom_components.hausman_hub as integration

    hass = FakeHomeAssistant()
    entry = FakeEntry(
        {
            "mode": "read-only",
            "direct_execution_status": "direct_execution_blocked",
            "core_shadow_room_id": "startup-room",
        },
        {},
        entry_id="startup-assembly",
    )
    hass.config_entries.entries = [entry]
    helper = AsyncMock(return_value=None)
    monkeypatch.setattr(integration, "_async_start_room_lighting_with_core", helper)
    original_configuration = integration.effective_configuration
    monkeypatch.setattr(
        integration, "effective_configuration",
        lambda data, options: original_configuration(
            {key: value for key, value in data.items() if key != "core_shadow_room_id"},
            options,
        ),
    )
    # The setup boundary imports HA adapters lazily.  This is the project's
    # existing synthetic module surface, never a connected HA instance.
    for name, module in fake_home_assistant_modules().items():
        monkeypatch.setitem(sys.modules, name, module)

    assert await integration.async_setup_entry(hass, entry) is True
    assert helper.await_count == 1
    assert helper.await_args.kwargs["core_shadow_room_id"] == "startup-room"
    intake = helper.await_args.kwargs["core_manual_input"]
    assert intake.registration_ready("any-target") is False


def test_core_shadow_room_identifier_is_strictly_validated_in_entry_data():
    entry = {
        "mode": "read-only",
        "direct_execution_status": "direct_execution_blocked",
        "core_shadow_room_id": "shower_1",
    }
    assert effective_configuration(entry, {}).mode == "read-only"

    entry["core_shadow_room_id"] = "../outside"
    with pytest.raises(ConfigurationViolation, match="core shadow room id"):
        effective_configuration(entry, {})
