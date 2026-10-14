"""One exact provenance contract feeds authority and OFF protection alike."""

from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
from custom_components.hausman_hub.application.room_lighting_ha_executor import RoomLightingHaExecutor
from custom_components.hausman_hub.application.scenario_command_context import ScenarioCommandContextRegistry
from custom_components.hausman_hub.manual_light_authority_events import ManualLightAuthorityEvents
from custom_components.hausman_hub.manual_light_off_protection_events import ManualLightOffProtectionEventListener
from tests.test_manual_light_off_protection_events import _coordinator, _event


@pytest.mark.parametrize("origin", ["room", "scenario"])
@pytest.mark.parametrize("action", ["on", "off"])
@pytest.mark.parametrize("lineage", ["same", "parent"])
async def test_exact_own_commands_feed_both_consumers(monkeypatch, origin, action, lineage):
    clock = [100.0]
    from custom_components.hausman_hub.application import room_lighting_ha_executor as room_module
    monkeypatch.setattr(room_module.time, "monotonic", lambda: clock[0])
    context = SimpleNamespace(id="own-command", parent_id=None)
    monkeypatch.setattr(room_module, "_new_service_context", lambda: context)
    registry = ScenarioCommandContextRegistry(monotonic=lambda: clock[0], context_factory=lambda: context)
    executor = RoomLightingHaExecutor()
    async def native_call(*args, **kwargs):
        assert kwargs["context"] is context
    hass = SimpleNamespace(services=SimpleNamespace(async_call=native_call))
    if origin == "room":
        await executor._call(hass, "light", "turn_" + action, {"entity_id": "light.tambur"})
    else:
        registry.create("light.tambur", action)
    authority = ManualLightAuthorityEvents(hass, ManualLightAuthority(), lambda: {"light.tambur"},
                                          registry, lambda: executor)
    report = context if lineage == "same" else SimpleNamespace(id="child", parent_id=context.id)
    attribution = authority.automatic_attribution(report, "light.tambur", action)
    assert attribution is not None and attribution.source == "automatic"
    assert authority._is_automatic(report, "light.tambur", action)
    assert authority.automatic_attribution(report, "light.other", action) is None
    assert authority.automatic_attribution(report, "light.tambur", "off" if action == "on" else "on") is None
    assert authority.automatic_attribution(SimpleNamespace(id="foreign", user_id=None), "light.tambur", action) is None
    coordinator = await _coordinator()
    # Arm genuine manual OFF before an automatic ON: automatic provenance
    # must not accidentally cancel the user's separate OFF protection.
    if action == "on":
        await coordinator.async_note_state_transition("light.tambur", SimpleNamespace(state="on"),
                                                      SimpleNamespace(state="off"), None)
    before = coordinator.snapshot()["protections"]
    listener = ManualLightOffProtectionEventListener(coordinator, registry, {"light.tambur"},
                                                    automatic_attribution=authority.automatic_attribution)
    await listener.async_handle(_event("light.tambur", "off" if action == "on" else "on", action, report))
    assert coordinator.snapshot()["protections"] == before
    # Repeated exact reports remain attributable, but the same lineage expires.
    assert authority.automatic_attribution(report, "light.tambur", action) is not None
    clock[0] = 130.0
    assert authority.automatic_attribution(report, "light.tambur", action) is None
    assert not authority._is_automatic(report, "light.tambur", action)


async def test_shared_port_rejection_does_not_fall_back_to_weaker_registry():
    context = SimpleNamespace(id="known-to-registry", parent_id=None)
    registry = ScenarioCommandContextRegistry(context_factory=lambda: context)
    registry.create("light.tambur", "off")
    coordinator = await _coordinator()
    listener = ManualLightOffProtectionEventListener(coordinator, registry, {"light.tambur"},
                                                    automatic_attribution=lambda *args: None)
    await listener.async_handle(_event("light.tambur", "on", "off", context))
    assert coordinator.snapshot()["protections"]


async def test_attribution_failure_propagates_without_reporting_automatic_success():
    coordinator = await _coordinator()
    def broken(*args):
        raise RuntimeError("provenance unavailable")
    listener = ManualLightOffProtectionEventListener(coordinator, None, {"light.tambur"},
                                                    automatic_attribution=broken)
    with pytest.raises(RuntimeError, match="provenance unavailable"):
        await listener.async_handle(_event("light.tambur", "on", "off"))
