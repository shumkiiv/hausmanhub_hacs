from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.smart_switch_runtime import (
    PASS_THROUGH_TRIGGER_CONFIGS,
    SHOWER_TRIGGER_CONFIGS,
    SmartSwitchTriggerAdapter,
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
            "toggle_b2_down", "on_b2_down", "on_down", "toggle_down", "off_up",
        }
        adapter.async_unload()
        assert set(removed) == {"toggle_b2_down", "on_b2_down", "on_down", "toggle_down", "off_up"}

    asyncio.run(run())


@pytest.mark.asyncio
async def test_shower_alias_is_deduplicated_and_up_is_ignored() -> None:
    calls: list[dict[str, object]] = []
    service = SimpleNamespace(async_run_typed_intent=lambda **intent: calls.append(intent))
    adapter = SmartSwitchTriggerAdapter(SimpleNamespace(), service, trigger_api=SimpleNamespace())
    assert await adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[0], {})
    assert not await adapter.async_handle_trigger(SHOWER_TRIGGER_CONFIGS[1], {})
    assert not await adapter.async_handle_trigger({"device_id": SHOWER_TRIGGER_CONFIGS[0]["device_id"], "subtype": "toggle_b2_up"}, {})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_typed_intent_validates_binding_and_preserves_manual_source() -> None:
    service = ScenarioService.__new__(ScenarioService)
    calls: list[tuple[str, dict[str, object]]] = []

    async def run(scenario_id: str, *, trigger_context: dict[str, object]) -> str:
        calls.append((scenario_id, trigger_context))
        return "ok"

    service.async_run_scenario = run
    await service.async_run_typed_intent(
        binding="tambur-light-group", action="off", correlation_id="corr-1", source="manual", trigger_id="off_up"
    )
    assert calls == [("system-tambur-adaptive-controller", {"source": "manual", "trigger_id": "off_up", "correlation_id": "corr-1", "typed_intent": "off"})]
    with pytest.raises(ValueError):
        await service.async_run_typed_intent(binding="mirror", action="off", correlation_id="corr-2", source="manual", trigger_id="off_up")
