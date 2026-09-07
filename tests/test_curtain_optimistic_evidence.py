"""Curtain receipts must not promote optimistic HA echoes to physical proof."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.hausman_hub.application.curtain_command_policy import (
    ALICE_CURTAIN_TARGET,
    KITCHEN_CURTAIN_TARGET,
    LIVING_CURTAIN_TARGET,
    OFFICE_CURTAIN_TARGET,
    CurtainCommandPolicy,
    CurtainPositionEvidencePolicy,
)
from custom_components.hausman_hub.application.curtain_protection import (
    CurtainProtectionCoordinator,
)
from custom_components.hausman_hub.application.managed_switch_migration import (
    FULL_MIGRATION_MANIFEST,
)
from custom_components.hausman_hub.application.scenario_node_red import (
    NodeRedScenarioBackend,
    build_managed_flow,
    managed_source_hash,
)
from custom_components.hausman_hub.application.scenario_service import ScenarioService
from custom_components.hausman_hub.application.scenario_executor import ScenarioExecutor
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.domain.scenarios import (
    Scenario,
    ScenarioAction,
    ScenarioActionType,
    ScenarioDefinition,
    ScenarioExecutionBackend,
    ScenarioExecutionMode,
    ScenarioNodeRedMetadata,
    ScenarioNodeRedSyncStatus,
    ScenarioRegistry,
    ScenarioSafetyPolicy,
    ScenarioTrigger,
    ScenarioTriggerType,
)


LIVING_ENTITY_ID = "cover.shtory_gostinaia"
CURTAIN_ENTITY_IDS = {
    LIVING_CURTAIN_TARGET: LIVING_ENTITY_ID,
    KITCHEN_CURTAIN_TARGET: "cover.0xa4c1385a4bcce3d6",
    ALICE_CURTAIN_TARGET: "cover.0xa4c138b23cb850b4",
    OFFICE_CURTAIN_TARGET: "cover.0xa4c1381b3fb1c985",
}


class MemoryStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = copy.deepcopy(payload)

    async def async_load(self) -> object | None:
        return copy.deepcopy(self.payload)

    async def async_save(self, payload: object) -> None:
        self.payload = copy.deepcopy(payload)


def _cover_catalog(
    *, target_id: str = LIVING_CURTAIN_TARGET, entity_id: str = LIVING_ENTITY_ID
) -> ScenarioCatalog:
    actions = tuple(
        ScenarioDeviceAction(
            action_id=action_id,
            title=action_id,
            domain="cover",
            service=service,
            allowed_fields=(
                frozenset({"value"})
                if action_id == "set_position"
                else frozenset()
            ),
        )
        for action_id, service in (
            ("open_cover", "open_cover"),
            ("close_cover", "close_cover"),
            ("stop_cover", "stop_cover"),
            ("set_position", "set_cover_position"),
        )
    )
    entity_ids = {**CURTAIN_ENTITY_IDS, target_id: entity_id}
    return ScenarioCatalog(
        devices={
            curtain_target_id: ScenarioDeviceEntry(
                target_id=curtain_target_id,
                name="Шторы",
                entity_id=curtain_entity_id,
                actions=actions,
            )
            for curtain_target_id, curtain_entity_id in entity_ids.items()
        },
        scenarios={},
    )


def _state(position: int) -> SimpleNamespace:
    return SimpleNamespace(
        state="closed" if position == 0 else "open",
        attributes={"current_position": position},
        last_updated=datetime.now(timezone.utc) - timedelta(seconds=1),
    )


def _executor(
    state: SimpleNamespace,
    *,
    policy: CurtainCommandPolicy | None = None,
    protection: CurtainProtectionCoordinator | None = None,
    catalog: ScenarioCatalog | None = None,
) -> tuple[ScenarioExecutor, AsyncMock]:
    catalog = catalog or _cover_catalog()
    service = AsyncMock()

    async def optimistic_echo(_domain, called_service, data, **_kwargs):
        if called_service == "set_cover_position":
            position = data["position"]
            state.attributes = {"current_position": position}
            state.state = "closed" if position == 0 else "open"
        elif called_service == "open_cover":
            state.attributes = {"current_position": 100}
            state.state = "open"
        elif called_service == "close_cover":
            state.attributes = {"current_position": 0}
            state.state = "closed"
        state.last_updated = datetime.now(timezone.utc)

    service.side_effect = optimistic_echo
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _entity_id: state),
        services=SimpleNamespace(async_call=service),
    )

    async def nested(*_args, **_kwargs):
        return {"status": "completed", "receipts": []}

    return (
        ScenarioExecutor(
            hass,
            catalog,
            nested,
            readback_window_seconds=8,
            readback_interval_seconds=0.01,
            curtain_command_policy=policy,
            curtain_protection=protection,
        ),
        service,
    )


@pytest.mark.asyncio
async def test_manual_echo_is_sent_again_but_never_published_as_physical_proof() -> None:
    state = _state(90)
    executor, service = _executor(state)

    started = time.monotonic()
    receipt = await executor.async_execute_device_action(
        LIVING_CURTAIN_TARGET,
        "set_position",
        90,
        request_id="manual-position-after-echo",
        idempotent_actions=True,
    )
    elapsed = time.monotonic() - started

    service.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"entity_id": LIVING_ENTITY_ID, "position": 90},
        blocking=True,
    )
    assert elapsed < 0.25
    assert receipt["accepted"] is True
    assert receipt["confirmed"] is False
    assert receipt["status"] == "accepted"
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    assert receipt["message"] == (
        "Команда передана. Физическое положение не подтверждено."
    )
    assert receipt["readBack"]["attempted"] is True
    assert receipt["readBack"]["matched"] is False
    assert receipt["readBack"]["observedState"] == "open"
    assert receipt["readBack"]["attempts"] == 1
    assert "observedValue" not in receipt["readBack"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("delay_seconds", "reported"), [(0.11, 90), (5, 90), (30, 88)])
async def test_later_echo_or_changed_position_still_is_not_physical_proof(
    delay_seconds: float,
    reported: int,
) -> None:
    state = _state(40)
    executor, service = _executor(state)

    async def delayed_report(_domain, _service, _data, **_kwargs):
        state.state = "open"
        state.attributes = {"current_position": reported}
        state.last_updated = datetime.now(timezone.utc) + timedelta(
            seconds=delay_seconds
        )

    service.side_effect = delayed_report
    receipt = await executor.async_execute_device_action(
        LIVING_CURTAIN_TARGET,
        "set_position",
        90,
        request_id=f"later-report-{delay_seconds}",
    )

    assert receipt["confirmed"] is False
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    assert receipt["readBack"]["attempts"] == 1
    assert "observedValue" not in receipt["readBack"]


@pytest.mark.asyncio
async def test_stop_stays_available_but_its_echo_is_not_physical_proof() -> None:
    state = _state(50)
    executor, service = _executor(state)

    receipt = await executor.async_execute_device_action(
        LIVING_CURTAIN_TARGET,
        "stop_cover",
        request_id="manual-stop",
    )

    service.assert_awaited_once()
    assert receipt["accepted"] is True
    assert receipt["confirmed"] is False
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    assert receipt["readBack"]["attempts"] == 1


@pytest.mark.asyncio
async def test_explicit_synthetic_device_report_can_confirm_only_exact_identity() -> None:
    state = _state(0)
    evidence = CurtainPositionEvidencePolicy.with_verified_device_reports(
        {(LIVING_CURTAIN_TARGET, LIVING_ENTITY_ID)}
    )
    policy = CurtainCommandPolicy(position_evidence_policy=evidence)
    executor, _service = _executor(state, policy=policy)

    receipt = await executor.async_execute_device_action(
        LIVING_CURTAIN_TARGET,
        "open_cover",
        request_id="synthetic-proven-open",
    )

    assert receipt["confirmed"] is True
    assert receipt["status"] == "confirmed"

    replacement_catalog = _cover_catalog(entity_id="cover.replacement")
    replacement_state = _state(0)
    replacement, _ = _executor(
        replacement_state,
        policy=policy,
        catalog=replacement_catalog,
    )
    untrusted = await replacement.async_execute_device_action(
        LIVING_CURTAIN_TARGET,
        "open_cover",
        request_id="replacement-open",
    )
    assert untrusted["confirmed"] is False
    assert untrusted["reason"] == "curtain_position_provenance_unverified"


@pytest.mark.asyncio
async def test_automatic_close_is_one_unconfirmed_dispatch_per_durable_cycle() -> None:
    state = _state(50)
    catalog = _cover_catalog()
    store = MemoryStore()
    protection = CurtainProtectionCoordinator(
        store,
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 20_000,
        now_ms=lambda: 1_000,
    )
    await protection.async_load()
    executor, service = _executor(
        state,
        protection=protection,
        catalog=catalog,
    )
    definition = ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.SINGLE,
        triggers=(ScenarioTrigger("sunset", ScenarioTriggerType.SUNSET),),
        conditions=(),
        actions=(
            ScenarioAction(
                "close",
                ScenarioActionType.DEVICE_ACTION,
                target_id=LIVING_CURTAIN_TARGET,
                action_id="set_position",
                value=0,
            ),
        ),
        safety_policy=ScenarioSafetyPolicy(idempotent_actions=True),
    )

    first = await executor.async_execute(
        definition,
        "sunset-run-1",
        scenario_id="curtain-close",
        trigger_context={"source": "schedule", "trigger_id": "sunset"},
    )
    assert first["receipts"][0]["confirmed"] is False
    record = store.payload["targets"][LIVING_CURTAIN_TARGET]
    assert record["confirmedAutomaticClose"] is None
    assert record["automaticCloseIntent"]["phase"] == "unconfirmed"
    assert first["receipts"][0]["reason"] == (
        "curtain_position_provenance_unverified"
    )

    repeated = await executor.async_execute(
        definition,
        "lux-run-2",
        scenario_id="curtain-close",
        trigger_context={"source": "state", "trigger_id": "lux_below_400"},
    )
    assert repeated["receipts"][0]["reason"] == (
        "curtain_close_already_attempted"
    )
    assert repeated["receipts"][0]["physicalAttempted"] is False
    assert service.await_count == 1

    restarted = CurtainProtectionCoordinator(
        store,
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 20_000,
        now_ms=lambda: 2_000,
    )
    await restarted.async_load()
    restarted_executor, restarted_service = _executor(
        state,
        protection=restarted,
        catalog=catalog,
    )
    after_restart = await restarted_executor.async_execute(
        definition,
        "light-run-3",
        scenario_id="curtain-close",
        trigger_context={"source": "state", "trigger_id": "light_on"},
    )
    assert after_restart["receipts"][0]["reason"] == (
        "curtain_close_already_attempted"
    )
    restarted_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_open_after_uncertain_close_latches_until_trusted_sunrise() -> None:
    state = _state(50)
    catalog = _cover_catalog()
    store = MemoryStore()
    protection = CurtainProtectionCoordinator(
        store,
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 20_000,
        now_ms=lambda: 1_000,
    )
    await protection.async_load()
    executor, service = _executor(
        state,
        protection=protection,
        catalog=catalog,
    )
    definition = ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.SINGLE,
        triggers=(ScenarioTrigger("sunset", ScenarioTriggerType.SUNSET),),
        conditions=(),
        actions=(ScenarioAction(
            "close", ScenarioActionType.DEVICE_ACTION,
            target_id=LIVING_CURTAIN_TARGET,
            action_id="set_position", value=0,
        ),),
        safety_policy=ScenarioSafetyPolicy(idempotent_actions=True),
    )
    await executor.async_execute(
        definition,
        "sunset-run",
        scenario_id="curtain-close",
        trigger_context={"source": "schedule", "trigger_id": "sunset"},
    )

    manual = await executor.async_execute_device_action(
        LIVING_CURTAIN_TARGET,
        "open_cover",
        request_id="manual-open-after-uncertain-close",
        idempotent_actions=True,
    )
    assert manual["confirmed"] is False
    assert store.payload["targets"][LIVING_CURTAIN_TARGET]["latchedAtMs"] == 1_000

    state.attributes = {"current_position": 50}
    blocked = await executor.async_execute(
        definition,
        "second-close",
        scenario_id="curtain-close",
        trigger_context={"source": "state", "trigger_id": "light_on"},
    )
    assert blocked["receipts"][0]["reason"] == "curtain_manual_open_latched"
    assert service.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("target_id", tuple(CURTAIN_ENTITY_IDS))
async def test_first_automatic_close_is_one_unconfirmed_call_for_each_target(
    target_id: str,
) -> None:
    state = _state(50)
    catalog = _cover_catalog()
    store = MemoryStore()
    protection = CurtainProtectionCoordinator(
        store,
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 20_000,
        now_ms=lambda: 1_000,
    )
    await protection.async_load()
    executor, service = _executor(state, protection=protection, catalog=catalog)
    definition = ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.SINGLE,
        triggers=(ScenarioTrigger("sunset", ScenarioTriggerType.SUNSET),),
        conditions=(),
        actions=(ScenarioAction(
            "close", ScenarioActionType.DEVICE_ACTION,
            target_id=target_id, action_id="set_position", value=0,
        ),),
        safety_policy=ScenarioSafetyPolicy(idempotent_actions=True),
    )

    result = await executor.async_execute(
        definition,
        f"auto-close-{target_id}",
        scenario_id="curtain-close",
        trigger_context={"source": "schedule", "trigger_id": "sunset"},
    )

    receipt = result["receipts"][0]
    assert receipt["confirmed"] is False
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    service.assert_awaited_once()
    record = store.payload["targets"][target_id]
    assert record["automaticCloseIntent"]["phase"] == "unconfirmed"
    assert record["confirmedAutomaticClose"] is None


@pytest.mark.asyncio
async def test_proven_pre_dispatch_failure_leaves_no_automatic_attempt() -> None:
    state = _state(50)
    catalog = _cover_catalog()
    store = MemoryStore()
    protection = CurtainProtectionCoordinator(
        store,
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 20_000,
        now_ms=lambda: 1_000,
    )
    await protection.async_load()
    executor, service = _executor(state, protection=protection, catalog=catalog)

    async def fail_before_dispatch() -> None:
        raise RuntimeError("proven pre-dispatch failure")

    receipt = await executor._execute_action(  # noqa: SLF001
        ScenarioAction(
            "close", ScenarioActionType.DEVICE_ACTION,
            target_id=LIVING_CURTAIN_TARGET,
            action_id="set_position", value=0,
        ),
        "pre-dispatch-failure",
        frozenset(),
        trigger_context={"source": "schedule", "trigger_id": "sunset"},
        before_dispatch=fail_before_dispatch,
    )

    assert receipt["status"] == "failed"
    assert receipt["error"] == "proven pre-dispatch failure"
    assert store.payload["targets"][LIVING_CURTAIN_TARGET][
        "automaticCloseIntent"
    ] is None
    service.assert_not_awaited()


@pytest.mark.asyncio
async def test_crash_after_dispatch_intent_blocks_retry_after_restart() -> None:
    catalog = _cover_catalog()
    store = MemoryStore()
    protection = CurtainProtectionCoordinator(
        store,
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 20_000,
        now_ms=lambda: 1_000,
    )
    await protection.async_load()
    source_hash = "sunset-source"
    preflight = await protection.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=LIVING_ENTITY_ID,
        action_id="set_position",
        requested=0,
        applied=0,
        current_position=50,
        automatic=True,
        dry_run=False,
        receipt_id="crash-close",
        source_hash=source_hash,
    )
    dispatch = await protection.async_validate_before_dispatch(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=LIVING_ENTITY_ID,
        action_id="set_position",
        requested=0,
        applied=0,
        current_position=50,
        automatic=True,
        token=preflight.token,
        receipt_id="crash-close",
        source_hash=source_hash,
    )
    assert dispatch.allowed
    assert store.payload["targets"][LIVING_CURTAIN_TARGET][
        "automaticCloseIntent"
    ]["phase"] == "dispatch_intent"

    restarted = CurtainProtectionCoordinator(
        store,
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 20_000,
        now_ms=lambda: 2_000,
    )
    await restarted.async_load()
    retry = await restarted.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=LIVING_ENTITY_ID,
        action_id="set_position",
        requested=0,
        applied=0,
        current_position=50,
        automatic=True,
        dry_run=False,
        receipt_id="retry-close",
        source_hash="different-source",
    )
    assert not retry.allowed
    assert retry.reason == "curtain_close_already_attempted"


@pytest.mark.asyncio
async def test_office_scale_authority_never_grants_position_provenance() -> None:
    state = _state(0)
    catalog = _cover_catalog(
        target_id=OFFICE_CURTAIN_TARGET,
        entity_id=CURTAIN_ENTITY_IDS[OFFICE_CURTAIN_TARGET],
    )
    policy = CurtainCommandPolicy.with_confirmed_scales({OFFICE_CURTAIN_TARGET})
    executor, service = _executor(state, policy=policy, catalog=catalog)

    receipt = await executor.async_execute_device_action(
        OFFICE_CURTAIN_TARGET,
        "set_position",
        100,
        request_id="office-scale-is-not-evidence",
    )

    assert receipt["confirmed"] is False
    assert receipt["reason"] == "curtain_position_provenance_unverified"
    assert "observedValue" not in receipt["readBack"]
    service.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {
            "entity_id": CURTAIN_ENTITY_IDS[OFFICE_CURTAIN_TARGET],
            "position": 90,
        },
        blocking=True,
    )


@pytest.mark.asyncio
async def test_real_sunrise_service_plan_executor_event_echo_is_not_manual() -> None:
    """Own optimistic echo stays attributed across the complete runtime path."""

    source_path = Path(
        "custom_components/hausman_hub/managed_scenarios/curtains_controller.js"
    )
    source = source_path.read_text(encoding="utf-8")
    manifest = next(
        item
        for item in FULL_MIGRATION_MANIFEST
        if item.scenario_id == "system-curtains-privacy-controller"
    )
    assert managed_source_hash(source) == manifest.new_source_hash
    flow_id = "flow-system-curtains-privacy-controller"
    deployed = build_managed_flow(
        manifest.scenario_id,
        "Шторы: приватность",
        source,
        flow_id=flow_id,
    )

    callbacks: list[object] = []

    class Bus:
        def async_listen(self, event_type, callback):
            assert event_type == "state_changed"
            callbacks.append(callback)
            return lambda: None

        async def fire(self, entity_id: str, old_state: object, new_state: object):
            event = SimpleNamespace(data={
                "entity_id": entity_id,
                "old_state": old_state,
                "new_state": new_state,
            })
            await callbacks[0](event)  # type: ignore[operator]

    catalog = _cover_catalog()
    states = {
        entity_id: _state(0)
        for entity_id in CURTAIN_ENTITY_IDS.values()
    }
    bus = Bus()
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
        bus=bus,
    )

    async def apply_service(_domain, service_name, data, **_kwargs):
        entity_id = data["entity_id"]
        old = copy.deepcopy(states[entity_id])
        if service_name == "set_cover_position":
            position = data["position"]
        elif service_name == "open_cover":
            position = 100
        elif service_name == "close_cover":
            position = 0
        else:
            position = states[entity_id].attributes["current_position"]
        states[entity_id].state = "closed" if position == 0 else "open"
        states[entity_id].attributes = {"current_position": position}
        states[entity_id].last_updated = datetime.now(timezone.utc)
        await bus.fire(entity_id, old, copy.deepcopy(states[entity_id]))

    hass.services.async_call.side_effect = apply_service
    protection_store = MemoryStore()
    protection = CurtainProtectionCoordinator(
        protection_store,
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 30_000,
        now_ms=lambda: 1_000,
    )
    await protection.async_load()
    protection.start(hass)

    async def adapter(method, path, headers, payload):
        del headers
        if method == "GET" and path.endswith("/flows"):
            return 200, {"rev": "eventbus-rev", "flows": deployed["nodes"]}
        if method == "GET" and path.endswith(f"/flow/{flow_id}"):
            return 200, deployed
        if method == "POST" and path.endswith(manifest.scenario_id):
            harness = (
                "const request=JSON.parse(process.argv[1]);"
                "const source=process.argv[2];"
                "const run=new Function('msg',source);"
                "const result=run({payload:request});"
                "process.stdout.write(JSON.stringify(result.payload));"
            )
            completed = subprocess.run(
                ["node", "-e", harness, json.dumps(payload), source],
                check=True,
                capture_output=True,
                text=True,
            )
            return 200, json.loads(completed.stdout)
        raise AssertionError((method, path))

    async def controls(_scenario_id, run_id, trigger):
        return {
            "policyRevision": 0,
            "policy": {
                "kitchenCoverCapPercent": 80,
                "cabinetCoverCapPercent": 90,
            },
            "state": await protection.async_control_state(run_id, trigger),
        }

    backend = NodeRedScenarioBackend(
        hass,
        request_adapter=adapter,
        control_context_provider=controls,
    )
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001
    definition = ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.RESTART,
        execution_backend=ScenarioExecutionBackend.NODE_RED,
        node_red=ScenarioNodeRedMetadata(
            flow_id=flow_id,
            source_hash=manifest.new_source_hash,
            input_target_ids=manifest.input_target_ids,
            sync_status=ScenarioNodeRedSyncStatus.SYNCED,
        ),
        triggers=(ScenarioTrigger("sunrise", ScenarioTriggerType.SUNRISE),),
        conditions=(),
        actions=(ScenarioAction(
            "placeholder",
            ScenarioActionType.NOTIFICATION,
            message="Node-RED replaces this plan",
        ),),
    )
    registry = ScenarioRegistry(scenarios=(Scenario.from_definition(
        manifest.scenario_id,
        "Шторы: приватность",
        definition,
        enabled=True,
        group="system",
    ),))
    registry_store = MemoryStore(registry)
    service: ScenarioService

    async def run_nested(scenario_id, **kwargs):
        return await service.async_run_scenario(scenario_id, **kwargs)

    executor = ScenarioExecutor(
        hass,
        catalog,
        run_nested,
        readback_window_seconds=0.02,
        readback_interval_seconds=0.01,
        node_red_backend=backend,
        curtain_command_policy=CurtainCommandPolicy.with_confirmed_scales(
            {OFFICE_CURTAIN_TARGET}
        ),
        curtain_protection=protection,
    )
    service = ScenarioService(
        hass,
        registry_store,
        catalog,
        executor,
        node_red_backend=backend,
    )
    await service.async_load()

    sunrise = await protection.async_run_trusted_sunrise(
        2_000,
        service.async_run_scenario,
    )
    assert sunrise["node_red"]["selectedBranch"] == "trusted_sunrise"
    assert protection_store.payload["targets"][LIVING_CURTAIN_TARGET][
        "latchedAtMs"
    ] is None

    sunset = await service.async_run_scenario(
        manifest.scenario_id,
        correlation_id="eventbus-sunset",
        trigger_context={"source": "schedule", "trigger_id": "sunset"},
    )
    assert sunset["node_red"]["selectedBranch"] == "automatic_close"
    assert protection_store.payload["targets"][LIVING_CURTAIN_TARGET][
        "automaticCloseIntent"
    ]["phase"] == "unconfirmed"

    old = copy.deepcopy(states[LIVING_ENTITY_ID])
    states[LIVING_ENTITY_ID].state = "open"
    states[LIVING_ENTITY_ID].attributes = {"current_position": 40}
    states[LIVING_ENTITY_ID].last_updated = datetime.now(timezone.utc)
    await bus.fire(
        LIVING_ENTITY_ID,
        old,
        copy.deepcopy(states[LIVING_ENTITY_ID]),
    )
    assert protection_store.payload["targets"][LIVING_CURTAIN_TARGET][
        "latchedAtMs"
    ] == 1_000
