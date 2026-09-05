from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.smart_switch_runtime import (
    PASS_THROUGH_TRIGGER_CONFIGS,
    SHOWER_TRIGGER_CONFIGS,
    SmartSwitchTriggerAdapter,
    valid_smart_switch_dedup_payload,
    validate_exact_device_trigger,
)
from custom_components.hausman_hub.application.scenario_service import ScenarioService


def test_exact_trigger_validation_rejects_wrong_device_or_subtype() -> None:
    expected = SHOWER_TRIGGER_CONFIGS[0]
    assert validate_exact_device_trigger(expected, expected)
    assert not validate_exact_device_trigger({**expected, "subtype": "toggle_b2_up"}, expected)
    assert not validate_exact_device_trigger({**expected, "device_id": "other"}, expected)


def test_adapter_attaches_only_allowlisted_configs_and_unloads() -> None:
    attached: list[dict[str, object]] = []
    removed: list[str] = []

    async def get_triggers(_hass: object, device_id: str) -> list[dict[str, object]]:
        configs = SHOWER_TRIGGER_CONFIGS if device_id == SHOWER_TRIGGER_CONFIGS[0]["device_id"] else PASS_THROUGH_TRIGGER_CONFIGS
        return [dict(item) for item in configs]

    async def attach(_hass: object, config: dict[str, object], _action: object, _info: object):
        attached.append(config)
        return lambda: removed.append(str(config["subtype"]))

    async def run() -> None:
        adapter = SmartSwitchTriggerAdapter(
            SimpleNamespace(),
            SimpleNamespace(async_run_typed_intent=SimpleNamespace()),
            trigger_api=SimpleNamespace(async_get_triggers=get_triggers, async_attach_trigger=attach),
        )
        await adapter.async_start()
        assert {item["subtype"] for item in attached} == {
            "toggle_b2_down", "on_b2_down", "toggle_b2_up",
            "on_down", "toggle_down", "off_up",
        }
        adapter.async_unload()
        adapter.async_unload()
        assert set(removed) == {
            "toggle_b2_down", "on_b2_down", "toggle_b2_up",
            "on_down", "toggle_down", "off_up",
        }
        assert len(removed) == 6

    asyncio.run(run())


@pytest.mark.asyncio
async def test_shower_alias_is_deduplicated_and_up_is_ignored() -> None:
    calls: list[dict[str, object]] = []
    dispositions: list[dict[str, object]] = []
    service = SimpleNamespace(
        async_run_typed_intent=lambda **intent: calls.append(intent),
        async_record_typed_intent_disposition=lambda **item: dispositions.append(item),
    )
    store = MemoryStore()
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(), service, trigger_api=SimpleNamespace(), state_store=store,
        wall_clock=lambda: 1000.0, receipt_factory=iter(("receipt.1", "receipt.2", "receipt.3")).__next__,
    )
    await adapter.async_load_state()
    assert await adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[0], {})
    assert not await adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[1], {})
    assert not await adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[2], {})
    assert len(calls) == 1
    assert calls[0]["correlation_id"] == "receipt.1"
    assert calls[0]["intent_receipt_id"] == "receipt.1"
    assert calls[0]["dedup_disposition"] == "accepted"
    assert [item["dedup_disposition"] for item in dispositions] == [
        "deduplicated", "ignored"
    ]
    assert valid_smart_switch_dedup_payload(store.payload)


@pytest.mark.asyncio
async def test_concurrent_shower_aliases_dispatch_exactly_once() -> None:
    calls: list[dict[str, object]] = []
    dispositions: list[dict[str, object]] = []

    class YieldingStore(MemoryStore):
        async def async_save(self, payload: dict[str, object]) -> None:
            await asyncio.sleep(0)
            await super().async_save(payload)

    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(
            async_run_typed_intent=lambda **intent: calls.append(intent),
            async_record_typed_intent_disposition=lambda **item: dispositions.append(item),
        ),
        trigger_api=SimpleNamespace(),
        state_store=YieldingStore(),
        wall_clock=lambda: 1000.0,
        receipt_factory=iter(("receipt.concurrent.1", "receipt.concurrent.2")).__next__,
    )
    await adapter.async_load_state()

    results = await asyncio.gather(
        adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[0], {}),
        adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[1], {}),
    )

    assert sorted(results) == [False, True]
    assert len(calls) == 1
    assert [item["dedup_disposition"] for item in dispositions] == ["deduplicated"]


@pytest.mark.asyncio
@pytest.mark.parametrize("config", PASS_THROUGH_TRIGGER_CONFIGS)
async def test_duplicate_pass_through_semantic_intent_dispatches_once(
    config: dict[str, object],
) -> None:
    calls: list[dict[str, object]] = []
    dispositions: list[dict[str, object]] = []
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(
            async_run_typed_intent=lambda **intent: calls.append(intent),
            async_record_typed_intent_disposition=lambda **item: dispositions.append(item),
        ),
        trigger_api=SimpleNamespace(),
        state_store=MemoryStore(),
        wall_clock=lambda: 1000.0,
        receipt_factory=iter(
            (
                f"receipt.{config['subtype']}.1",
                f"receipt.{config['subtype']}.2",
            )
        ).__next__,
    )
    await adapter.async_load_state()

    assert await adapter.async_handle_trigger(config, {})
    assert not await adapter.async_handle_trigger(config, {})
    assert len(calls) == 1
    assert [item["dedup_disposition"] for item in dispositions] == ["deduplicated"]


class MemoryStore:
    def __init__(
        self,
        payload: object | None = None,
        *,
        fail_load: bool = False,
        fail_save: bool = False,
        recovered_previous: bool = False,
    ) -> None:
        self.payload = payload
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.recovered_previous = recovered_previous

    async def async_load(self) -> object | None:
        if self.fail_load:
            raise OSError("load failed")
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        if self.fail_save:
            raise OSError("save failed")
        self.payload = payload


@pytest.mark.asyncio
async def test_discovery_is_complete_before_first_attach() -> None:
    attached: list[dict[str, object]] = []

    async def get_triggers(_hass: object, device_id: str) -> list[dict[str, object]]:
        if device_id == SHOWER_TRIGGER_CONFIGS[0]["device_id"]:
            return [dict(item) for item in SHOWER_TRIGGER_CONFIGS]
        return [dict(item) for item in PASS_THROUGH_TRIGGER_CONFIGS[:-1]]

    async def attach(_hass: object, config: dict[str, object], _action: object, _info: object):
        attached.append(config)
        return lambda: None

    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(), SimpleNamespace(),
        trigger_api=SimpleNamespace(async_get_triggers=get_triggers, async_attach_trigger=attach),
    )
    with pytest.raises(RuntimeError, match="off_up"):
        await adapter.async_start()
    assert attached == []


@pytest.mark.asyncio
async def test_partial_attach_failure_cleans_every_callback_once() -> None:
    removed: list[str] = []
    attempts = 0

    async def get_triggers(_hass: object, device_id: str) -> list[dict[str, object]]:
        configs = SHOWER_TRIGGER_CONFIGS if device_id == SHOWER_TRIGGER_CONFIGS[0]["device_id"] else PASS_THROUGH_TRIGGER_CONFIGS
        return [dict(item) for item in configs]

    async def attach(_hass: object, config: dict[str, object], _action: object, _info: object):
        nonlocal attempts
        attempts += 1
        if attempts == 4:
            raise RuntimeError("attach failed")
        return lambda: removed.append(str(config["subtype"]))

    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(), SimpleNamespace(),
        trigger_api=SimpleNamespace(async_get_triggers=get_triggers, async_attach_trigger=attach),
    )
    with pytest.raises(RuntimeError, match="attach failed"):
        await adapter.async_start()
    adapter.async_unload()
    assert removed == ["toggle_b2_down", "on_b2_down", "toggle_b2_up"]


@pytest.mark.asyncio
async def test_cancellation_during_attach_cleans_every_completed_callback() -> None:
    removed: list[str] = []
    second_attach_started = asyncio.Event()

    async def get_triggers(
        _hass: object,
        device_id: str,
    ) -> list[dict[str, object]]:
        configs = (
            SHOWER_TRIGGER_CONFIGS
            if device_id == SHOWER_TRIGGER_CONFIGS[0]["device_id"]
            else PASS_THROUGH_TRIGGER_CONFIGS
        )
        return [dict(item) for item in configs]

    async def attach(
        _hass: object,
        config: dict[str, object],
        _action: object,
        _info: object,
    ):
        if not removed and not second_attach_started.is_set():
            second_attach_started.set()
            return lambda: removed.append(str(config["subtype"]))
        await asyncio.Event().wait()

    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(),
        trigger_api=SimpleNamespace(
            async_get_triggers=get_triggers,
            async_attach_trigger=attach,
        ),
    )
    startup = asyncio.create_task(adapter.async_start())
    await second_attach_started.wait()
    await asyncio.sleep(0)
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup

    adapter.async_unload()
    assert removed == ["toggle_b2_down"]


@pytest.mark.asyncio
async def test_callback_during_attach_cannot_persist_or_dispatch_before_activation() -> None:
    actions: list[object] = []
    service_calls: list[dict[str, object]] = []
    removed: list[str] = []
    attempts = 0

    async def get_triggers(_hass: object, device_id: str) -> list[dict[str, object]]:
        configs = (
            SHOWER_TRIGGER_CONFIGS
            if device_id == SHOWER_TRIGGER_CONFIGS[0]["device_id"]
            else PASS_THROUGH_TRIGGER_CONFIGS
        )
        return [dict(item) for item in configs]

    async def attach(
        _hass: object,
        config: dict[str, object],
        action: object,
        _info: object,
    ) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            actions.append(action)
            await action({})
            return lambda: removed.append(str(config["subtype"]))
        raise RuntimeError("later attach failed")

    store = MemoryStore()
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(
            async_run_typed_intent=lambda **item: service_calls.append(item)
        ),
        trigger_api=SimpleNamespace(
            async_get_triggers=get_triggers,
            async_attach_trigger=attach,
        ),
        state_store=store,
        receipt_factory=lambda: "receipt.pre-activation",
    )

    with pytest.raises(RuntimeError, match="later attach failed"):
        await adapter.async_start()

    assert len(actions) == 1
    assert service_calls == []
    assert store.payload is None
    assert removed == ["toggle_b2_down"]


@pytest.mark.asyncio
async def test_readiness_is_rechecked_after_all_listeners_attach_before_activation() -> None:
    actions: list[object] = []
    removed: list[str] = []
    readiness = iter((True, False))

    async def get_triggers(_hass: object, device_id: str) -> list[dict[str, object]]:
        configs = (
            SHOWER_TRIGGER_CONFIGS
            if device_id == SHOWER_TRIGGER_CONFIGS[0]["device_id"]
            else PASS_THROUGH_TRIGGER_CONFIGS
        )
        return [dict(item) for item in configs]

    async def attach(
        _hass: object,
        config: dict[str, object],
        action: object,
        _info: object,
    ) -> object:
        actions.append(action)
        return lambda: removed.append(str(config["subtype"]))

    store = MemoryStore()
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(
            async_run_typed_intent=lambda **_item: pytest.fail(
                "inactive callback dispatched"
            )
        ),
        trigger_api=SimpleNamespace(
            async_get_triggers=get_triggers,
            async_attach_trigger=attach,
        ),
        state_store=store,
        readiness_check=lambda: next(readiness),
    )

    with pytest.raises(RuntimeError, match="readiness"):
        await adapter.async_start()
    await actions[-1]({})

    assert len(actions) == 6
    assert len(removed) == 6
    assert store.payload is None


@pytest.mark.asyncio
async def test_startup_readiness_failure_attaches_no_listener() -> None:
    discovery_calls: list[str] = []
    attach_calls: list[dict[str, object]] = []

    async def get_triggers(_hass: object, device_id: str) -> list[dict[str, object]]:
        discovery_calls.append(device_id)
        return []

    async def attach(
        _hass: object,
        config: dict[str, object],
        _action: object,
        _info: object,
    ) -> object:
        attach_calls.append(config)
        return lambda: None

    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(),
        trigger_api=SimpleNamespace(
            async_get_triggers=get_triggers,
            async_attach_trigger=attach,
        ),
        state_store=MemoryStore(),
        readiness_check=lambda: False,
    )

    with pytest.raises(RuntimeError, match="readiness"):
        await adapter.async_start()
    assert discovery_calls == []
    assert attach_calls == []


@pytest.mark.asyncio
async def test_failed_detach_after_partial_attach_leaves_callbacks_inert() -> None:
    actions: list[object] = []
    service_calls: list[dict[str, object]] = []
    attempts = 0

    async def get_triggers(_hass: object, device_id: str) -> list[dict[str, object]]:
        configs = (
            SHOWER_TRIGGER_CONFIGS
            if device_id == SHOWER_TRIGGER_CONFIGS[0]["device_id"]
            else PASS_THROUGH_TRIGGER_CONFIGS
        )
        return [dict(item) for item in configs]

    async def attach(
        _hass: object,
        _config: dict[str, object],
        action: object,
        _info: object,
    ) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise RuntimeError("attach failed")
        actions.append(action)
        if attempts == 1:
            def cleanup_failure() -> None:
                raise RuntimeError("detach failed")

            return cleanup_failure
        return lambda: None

    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(
            async_run_typed_intent=lambda **item: service_calls.append(item)
        ),
        trigger_api=SimpleNamespace(
            async_get_triggers=get_triggers,
            async_attach_trigger=attach,
        ),
        state_store=MemoryStore(),
        receipt_factory=lambda: "receipt.inert",
    )

    with pytest.raises(RuntimeError, match="cleanup"):
        await adapter.async_start()
    await actions[0]({})
    assert service_calls == []


@pytest.mark.asyncio
async def test_dedup_survives_restart_uses_wall_clock_and_ignores_payload_identity() -> None:
    calls: list[dict[str, object]] = []
    store = MemoryStore()
    first = SmartSwitchTriggerAdapter(
        SimpleNamespace(), SimpleNamespace(async_run_typed_intent=lambda **item: calls.append(item)),
        trigger_api=SimpleNamespace(), state_store=store, wall_clock=lambda: 10.0,
        receipt_factory=lambda: "receipt.persisted",
    )
    await first.async_load_state()
    assert await first.async_handle_trigger(
        SHOWER_TRIGGER_CONFIGS[0], {"correlation_id": "attacker-controlled"}
    )
    restarted = SmartSwitchTriggerAdapter(
        SimpleNamespace(), SimpleNamespace(
            async_run_typed_intent=lambda **item: calls.append(item),
            async_record_typed_intent_disposition=lambda **_item: None,
        ),
        trigger_api=SimpleNamespace(), state_store=store, wall_clock=lambda: 10.599,
        receipt_factory=lambda: "receipt.duplicate",
    )
    await restarted.async_load_state()
    assert not await restarted.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[1], {})
    assert [item["correlation_id"] for item in calls] == ["receipt.persisted"]


@pytest.mark.asyncio
async def test_dedup_receipts_expire_by_wall_clock() -> None:
    calls: list[dict[str, object]] = []
    store = MemoryStore(
        {
            "version": 1,
            "receipts": [
                {
                    "receiptId": "receipt.expired",
                    "binding": "shower-cabinet",
                    "rawSubtype": "toggle_b2_down",
                    "observedAtMs": 10_000,
                    "dedupDisposition": "accepted",
                }
            ],
        }
    )
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(async_run_typed_intent=lambda **item: calls.append(item)),
        trigger_api=SimpleNamespace(),
        state_store=store,
        wall_clock=lambda: 10.6,
        receipt_factory=lambda: "receipt.after-expiry",
    )
    await adapter.async_load_state()

    assert await adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[1], {})
    assert [item["correlation_id"] for item in calls] == ["receipt.after-expiry"]
    assert [item["receiptId"] for item in store.payload["receipts"]] == [
        "receipt.after-expiry"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store",
    [
        MemoryStore({"version": 1, "receipts": [{"bad": True}]}),
        MemoryStore(fail_load=True),
        MemoryStore(fail_save=True),
    ],
)
async def test_storage_corruption_load_or_save_failure_never_dispatches(store: MemoryStore) -> None:
    calls: list[dict[str, object]] = []
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(), SimpleNamespace(async_run_typed_intent=lambda **item: calls.append(item)),
        trigger_api=SimpleNamespace(), state_store=store,
        wall_clock=lambda: 10.0, receipt_factory=lambda: "receipt.failure",
    )
    if store.fail_save:
        await adapter.async_load_state()
        assert not await adapter.async_handle_trigger(PASS_THROUGH_TRIGGER_CONFIGS[0], {})
    else:
        with pytest.raises(RuntimeError, match="storage unavailable"):
            await adapter.async_load_state()
    assert calls == []


@pytest.mark.asyncio
async def test_recovered_previous_dedup_generation_blocks_dispatch() -> None:
    calls: list[dict[str, object]] = []
    store = MemoryStore(
        {"version": 1, "receipts": []}, recovered_previous=True
    )
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(async_run_typed_intent=lambda **item: calls.append(item)),
        trigger_api=SimpleNamespace(),
        state_store=store,
        wall_clock=lambda: 10.0,
        receipt_factory=lambda: "receipt.recovered",
    )

    with pytest.raises(RuntimeError, match="previous generation"):
        await adapter.async_load_state()
    assert not await adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[0], {})
    assert calls == []


@pytest.mark.asyncio
async def test_missing_persistent_store_never_dispatches() -> None:
    calls: list[dict[str, object]] = []
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(async_run_typed_intent=lambda **item: calls.append(item)),
        trigger_api=SimpleNamespace(),
        wall_clock=lambda: 10.0,
        receipt_factory=lambda: "receipt.no-store",
    )

    assert not await adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[0], {})
    assert calls == []


@pytest.mark.asyncio
async def test_typed_intent_validates_binding_and_preserves_manual_source() -> None:
    service = ScenarioService.__new__(ScenarioService)
    calls: list[tuple[str, str, dict[str, object]]] = []
    now = datetime.now(timezone.utc)
    entities = {
        "entity_71859313239a14e4": "light.tambur_chandelier",
        "entity_cd0098e5ff95da46": "switch.tambur_points",
        "entity_b47991988cc6b9f3": "switch.tambur_power",
        "entity_156050daca86aa6c": "binary_sensor.tambur_presence",
        "entity_10b78187426f8485": "binary_sensor.tambur_motion",
        "entity_e7a7c61eec7bdff8": "switch.shower_cabinet",
    }
    states = {
        entity_id: SimpleNamespace(state="off", attributes={}, last_updated=now)
        for entity_id in entities.values()
    }
    states["switch.tambur_power"].state = "on"
    service._catalog = SimpleNamespace(
        device=lambda target_id: (
            SimpleNamespace(entity_id=entities[target_id]) if target_id in entities else None
        )
    )
    service._hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    service._manual_light_off_protection = None
    service._smart_switch_receipt_consumer = SimpleNamespace(
        async_consume_intent_receipt=lambda **_item: True
    )

    async def run(scenario_id: str, *, correlation_id: str, trigger_context: dict[str, object]) -> str:
        calls.append((scenario_id, correlation_id, trigger_context))
        return "ok"

    service.async_run_scenario = run
    await service.async_run_typed_intent(
        binding="tambur-light-group", action="on", correlation_id="receipt.1",
        source="manual", trigger_id="on_down", intent_receipt_id="receipt.1",
        raw_subtype="on_down", dedup_disposition="accepted",
    )
    assert calls == [
        (
            "system-tambur-adaptive-controller",
            "receipt.1",
            {
                "source": "manual",
                "trigger_id": "on_down",
                "recovery": False,
                "binding": "tambur-light-group",
                "typed_intent": "on",
                "direct_user_intent": "on",
                "intent_receipt_id": "receipt.1",
                "raw_subtype": "on_down",
                "dedup_disposition": "accepted",
                "correlation_id": "receipt.1",
            },
        )
    ]
    with pytest.raises(ValueError):
        await service.async_run_typed_intent(
            binding="mirror", action="off", correlation_id="receipt.2", source="manual",
            trigger_id="off_up", intent_receipt_id="receipt.2", raw_subtype="off_up",
            dedup_disposition="accepted",
        )


@pytest.mark.asyncio
async def test_forged_or_unknown_receipt_is_rejected_before_planning() -> None:
    states = {
        "light.tambur_chandelier": _fresh("off"),
        "switch.tambur_points": _fresh("off"),
        "switch.tambur_power": _fresh("on"),
    }
    service, calls = _typed_service(states)
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        service,
        trigger_api=SimpleNamespace(),
        state_store=MemoryStore(),
        wall_clock=lambda: 10.0,
    )
    await adapter.async_load_state()
    service._smart_switch_receipt_consumer = adapter  # noqa: SLF001

    with pytest.raises(ValueError, match="receipt"):
        await service.async_run_typed_intent(
            binding="tambur-light-group",
            action="on",
            correlation_id="receipt.forged",
            source="manual",
            trigger_id="on_down",
            intent_receipt_id="receipt.forged",
            raw_subtype="on_down",
            dedup_disposition="accepted",
        )

    assert calls == []


@pytest.mark.asyncio
async def test_adapter_receipt_is_durably_consumed_before_planning() -> None:
    states = {
        "light.tambur_chandelier": _fresh("off"),
        "switch.tambur_points": _fresh("off"),
        "switch.tambur_power": _fresh("on"),
    }
    service, calls = _typed_service(states)
    store = MemoryStore()
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        service,
        trigger_api=SimpleNamespace(),
        state_store=store,
        wall_clock=lambda: 10.0,
        receipt_factory=lambda: "receipt.consumed",
    )
    service._smart_switch_receipt_consumer = adapter  # noqa: SLF001
    await adapter.async_load_state()

    assert await adapter.async_handle_trigger(PASS_THROUGH_TRIGGER_CONFIGS[0], {})
    assert store.payload["receipts"][0]["lifecycle"] == "consumed"
    assert (
        len(calls) == 1
        and calls[0]["scenario_id"] == "system-tambur-adaptive-controller"
    )


@pytest.mark.asyncio
async def test_receipt_can_be_consumed_once_across_concurrency_and_restart() -> None:
    captured: list[dict[str, object]] = []
    store = MemoryStore()
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(async_run_typed_intent=lambda **item: captured.append(item)),
        trigger_api=SimpleNamespace(),
        state_store=store,
        wall_clock=lambda: 10.0,
        receipt_factory=lambda: "receipt.concurrent-consume",
    )
    await adapter.async_load_state()
    assert await adapter.async_handle_trigger(PASS_THROUGH_TRIGGER_CONFIGS[0], {})

    service, calls = _typed_service(
        {
            "light.tambur_chandelier": _fresh("off"),
            "switch.tambur_points": _fresh("off"),
            "switch.tambur_power": _fresh("on"),
        }
    )
    service._smart_switch_receipt_consumer = adapter  # noqa: SLF001
    results = await asyncio.gather(
        service.async_run_typed_intent(**captured[0]),
        service.async_run_typed_intent(**captured[0]),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, ValueError) for item in results) == 1
    assert len(calls) == 1
    assert store.payload["receipts"][0]["lifecycle"] == "consumed"

    restarted = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        service,
        trigger_api=SimpleNamespace(),
        state_store=store,
        wall_clock=lambda: 10.0,
    )
    await restarted.async_load_state()
    service._smart_switch_receipt_consumer = restarted  # noqa: SLF001
    with pytest.raises(ValueError, match="already consumed"):
        await service.async_run_typed_intent(**captured[0])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_receipt_consumption_save_failure_prevents_planning() -> None:
    class FailSecondSaveStore(MemoryStore):
        saves = 0

        async def async_save(self, payload: dict[str, object]) -> None:
            self.saves += 1
            if self.saves == 2:
                raise OSError("consumption save failed")
            await super().async_save(payload)

    service, calls = _typed_service(
        {
            "light.tambur_chandelier": _fresh("off"),
            "switch.tambur_points": _fresh("off"),
            "switch.tambur_power": _fresh("on"),
        }
    )
    store = FailSecondSaveStore()
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        service,
        trigger_api=SimpleNamespace(),
        state_store=store,
        wall_clock=lambda: 10.0,
        receipt_factory=lambda: "receipt.failed-consume",
    )
    service._smart_switch_receipt_consumer = adapter  # noqa: SLF001
    await adapter.async_load_state()

    with pytest.raises(ValueError, match="receipt"):
        await adapter.async_handle_trigger(PASS_THROUGH_TRIGGER_CONFIGS[0], {})
    assert calls == []
    assert store.payload["receipts"][0]["lifecycle"] == "accepted"


def _typed_service(
    states: dict[str, object], *, protection: object | None = None
) -> tuple[ScenarioService, list[dict[str, object]]]:
    entities = {
        "entity_71859313239a14e4": "light.tambur_chandelier",
        "entity_cd0098e5ff95da46": "switch.tambur_points",
        "entity_b47991988cc6b9f3": "switch.tambur_power",
        "entity_156050daca86aa6c": "binary_sensor.tambur_presence",
        "entity_10b78187426f8485": "binary_sensor.tambur_motion",
        "entity_e7a7c61eec7bdff8": "switch.shower_cabinet",
    }
    service = ScenarioService.__new__(ScenarioService)
    service._catalog = SimpleNamespace(
        device=lambda target_id: (
            SimpleNamespace(entity_id=entities[target_id]) if target_id in entities else None
        )
    )
    service._hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    service._manual_light_off_protection = protection
    service._smart_switch_receipt_consumer = SimpleNamespace(
        async_consume_intent_receipt=lambda **_item: True
    )
    calls: list[dict[str, object]] = []

    async def run(scenario_id: str, **options: object) -> dict[str, object]:
        calls.append({"scenario_id": scenario_id, **options})
        return {"status": "completed"}

    async def record(result: dict[str, object]) -> None:
        calls.append({"recorded": result})

    service.async_run_scenario = run
    service._async_record_scenario_result = record
    return service, calls


def _fresh(state: str, *, restored: bool = False, age_seconds: int = 0) -> object:
    return SimpleNamespace(
        state=state,
        attributes={"restored": True} if restored else {},
        last_updated=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "unavailable", "on", "off"])
async def test_cabinet_toggle_requires_fresh_trusted_on_or_off(state: str) -> None:
    states = {"switch.shower_cabinet": _fresh(state)}
    service, calls = _typed_service(states)
    result = await service.async_run_typed_intent(
        binding="shower-cabinet", action="toggle", correlation_id="receipt.cabinet",
        source="manual", trigger_id="toggle_b2_down", intent_receipt_id="receipt.cabinet",
        raw_subtype="toggle_b2_down", dedup_disposition="accepted",
    )
    if state in {"on", "off"}:
        assert calls[0]["trigger_context"]["direct_user_intent"] == (
            "off" if state == "on" else "on"
        )
        assert result["status"] == "completed"
    else:
        assert result["reason"] == "smart_switch_state_untrusted"
        assert "scenario_id" not in calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cabinet",
    [_fresh("on", restored=True), _fresh("off", age_seconds=301)],
)
async def test_cabinet_restored_or_stale_state_skips(cabinet: object) -> None:
    service, calls = _typed_service({"switch.shower_cabinet": cabinet})
    result = await service.async_run_typed_intent(
        binding="shower-cabinet", action="toggle", correlation_id="receipt.cabinet.bad",
        source="manual", trigger_id="on_b2_down", intent_receipt_id="receipt.cabinet.bad",
        raw_subtype="on_b2_down", dedup_disposition="accepted",
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "smart_switch_state_untrusted"
    assert len(calls) == 1 and "recorded" in calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "chandelier", "points", "resolved"),
    [
        ("on", "off", "off", "on"),
        ("off", "on", "on", "off"),
        ("toggle", "off", "off", "on"),
        ("toggle", "on", "off", "off"),
        ("toggle", "off", "on", "off"),
    ],
)
async def test_pass_through_on_off_toggle_matrix(
    action: str, chandelier: str, points: str, resolved: str
) -> None:
    states = {
        "light.tambur_chandelier": _fresh(chandelier),
        "switch.tambur_points": _fresh(points),
        "switch.tambur_power": _fresh("on"),
        "binary_sensor.tambur_presence": _fresh("off"),
        "binary_sensor.tambur_motion": _fresh("off"),
    }
    protection = SimpleNamespace(async_arm_release_owned_direct_off=lambda **_item: {"ok": True})
    service, calls = _typed_service(states, protection=protection)
    trigger = {"on": "on_down", "off": "off_up", "toggle": "toggle_down"}[action]
    await service.async_run_typed_intent(
        binding="tambur-light-group", action=action, correlation_id=f"receipt.{action}",
        source="manual", trigger_id=trigger, intent_receipt_id=f"receipt.{action}",
        raw_subtype=trigger, dedup_disposition="accepted",
    )
    assert calls[-1]["trigger_context"]["direct_user_intent"] == resolved


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_target",
    [
        _fresh("unknown"),
        _fresh("unavailable"),
        _fresh("on", restored=True),
        SimpleNamespace(
            state="on",
            attributes={"cached": True},
            last_updated=datetime.now(timezone.utc),
        ),
        _fresh("on", age_seconds=301),
    ],
)
async def test_pass_through_off_requires_both_targets_fresh_and_trusted(
    unsafe_target: object,
) -> None:
    arm_calls: list[dict[str, object]] = []
    states = {
        "light.tambur_chandelier": unsafe_target,
        "switch.tambur_points": _fresh("on"),
        "switch.tambur_power": _fresh("on"),
    }
    service, calls = _typed_service(
        states,
        protection=SimpleNamespace(
            async_arm_release_owned_direct_off=lambda **item: arm_calls.append(item)
        ),
    )

    result = await service.async_run_typed_intent(
        binding="tambur-light-group",
        action="off",
        correlation_id="receipt.off-untrusted",
        source="manual",
        trigger_id="off_up",
        intent_receipt_id="receipt.off-untrusted",
        raw_subtype="off_up",
        dedup_disposition="accepted",
    )

    assert result["reason"] == "smart_switch_state_untrusted"
    assert arm_calls == []
    assert len(calls) == 1 and "recorded" in calls[0]


@pytest.mark.asyncio
async def test_direct_off_arms_before_scenario_dispatch_and_failure_sends_no_command() -> None:
    order: list[str] = []
    states = {
        "light.tambur_chandelier": _fresh("on"),
        "switch.tambur_points": _fresh("on"),
        "switch.tambur_power": _fresh("on"),
        "binary_sensor.tambur_presence": _fresh("off"),
        "binary_sensor.tambur_motion": _fresh("off"),
    }

    async def arm(**item: object) -> dict[str, object]:
        order.append("arm")
        assert item["request_id"] == "receipt.off"
        assert set(item["light_entity_ids"]) == {
            "light.tambur_chandelier", "switch.tambur_points"
        }
        raise OSError("verified save failed")

    service, calls = _typed_service(
        states, protection=SimpleNamespace(async_arm_release_owned_direct_off=arm)
    )
    with pytest.raises(OSError, match="verified save failed"):
        await service.async_run_typed_intent(
            binding="tambur-light-group", action="off", correlation_id="receipt.off",
            source="manual", trigger_id="off_up", intent_receipt_id="receipt.off",
            raw_subtype="off_up", dedup_disposition="accepted",
        )
    assert order == ["arm"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("power", [_fresh("unknown"), _fresh("on", restored=True), _fresh("on", age_seconds=301)])
async def test_untrusted_power_dependency_blocks_whole_group(power: object) -> None:
    states = {
        "light.tambur_chandelier": _fresh("off"),
        "switch.tambur_points": _fresh("off"),
        "switch.tambur_power": power,
    }
    service, calls = _typed_service(states)
    result = await service.async_run_typed_intent(
        binding="tambur-light-group", action="on", correlation_id="receipt.power",
        source="manual", trigger_id="on_down", intent_receipt_id="receipt.power",
        raw_subtype="on_down", dedup_disposition="accepted",
    )
    assert result["reason"] == "smart_switch_power_untrusted"
    assert len(calls) == 1 and "recorded" in calls[0]
