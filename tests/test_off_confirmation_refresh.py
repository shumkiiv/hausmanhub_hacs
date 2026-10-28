"""One GET may provide evidence, never authority or a second light command."""
from types import SimpleNamespace
import pytest

from tests.test_light_command_readback import harness, command
from tests.test_lighting_mqtt_refresh import fixture
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction
from custom_components.hausman_hub.domain.room_lighting_engine import DecisionReason, PlannedCommand
from custom_components.hausman_hub.application.lighting_observation import ObservedLightState, PhysicalLightObservation
from tests.test_light_command_readback import state, BASE_MS


async def test_off_reads_once_and_confirms_only_from_existing_reader(monkeypatch):
    clock, _, _, calls, hass, executor, report = harness(monkeypatch)
    reads = []
    async def refresh(entity, current):
        assert current()
        reads.append(entity)
        report(value="off")
        return True
    executor._confirmation_refresher = refresh
    receipt = await executor.execute(hass, command(LightAction.TURN_OFF))
    assert reads == ["light.main"]
    assert receipt.confirmed and len(calls) == 1 and not clock.sleeps


@pytest.mark.parametrize("action,automatic", [
    (LightAction.TURN_ON, True), (LightAction.SET_BRIGHTNESS, True),
    (LightAction.SET_COLOR_TEMPERATURE, True), (LightAction.TURN_OFF, False),
])
async def test_no_refresh_for_other_commands_or_manual(monkeypatch, action, automatic):
    _, _, _, calls, hass, executor, _ = harness(monkeypatch, window=0)
    async def forbidden(*args):
        pytest.fail("read outside automatic OFF scope")
    executor._confirmation_refresher = forbidden
    await executor.execute(hass, command(action), automatic=automatic)
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["no_report", "foreign_context", "manual", "error"])
async def test_read_success_or_failure_cannot_invent_confirmation(monkeypatch, fault):
    _, _, _, calls, hass, executor, report = harness(monkeypatch)
    valid = {"value": True}
    async def refresh(entity, current):
        if fault == "error": raise OSError("no answer")
        if fault == "foreign_context": report(value="off", context=SimpleNamespace(id="other"))
        if fault == "manual":
            report(value="off")
            valid["value"] = False
        return True
    executor._confirmation_refresher = refresh
    receipt = await executor.execute(hass, command(LightAction.TURN_OFF),
                                     dispatch_guard=lambda: valid["value"])
    assert not receipt.confirmed and len(calls) == 1


async def test_adapter_reads_only_exact_known_target_without_set():
    adapter, _, _, _, calls, current = fixture()
    assert callable(getattr(adapter, "refresh_confirmation", None))
    assert await adapter.refresh_confirmation("light.liustra_tambur", lambda: current["value"])
    assert len(calls) == 1 and calls[0][0].endswith('/get')
    assert not await adapter.refresh_confirmation("light.foreign", lambda: True)
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["manual", "disconnect", "rebind", "old", "retained", "error"])
async def test_adapter_rejects_invalidated_or_unproven_read(mode):
    adapter, _, _, _, calls, current = fixture(mode=mode)
    assert callable(getattr(adapter, "refresh_confirmation", None))
    assert not await adapter.refresh_confirmation("light.liustra_tambur", lambda: current["value"])
    assert len(calls) == 1


async def test_away_off_also_requests_evidence_without_changing_exception_guard(monkeypatch):
    _, _, _, calls, hass, executor, report = harness(monkeypatch)
    reads=[]
    async def refresh(entity, current):
        assert current()
        reads.append(entity)
        report(value="off")
        return True
    executor._confirmation_refresher=refresh
    receipt=await executor.execute_away_off(hass,
        PlannedCommand("main",LightAction.TURN_OFF,reason=DecisionReason.AWAY),is_current=lambda:True)
    assert receipt.confirmed and reads==["light.main"] and len(calls)==1


@pytest.mark.parametrize('fault', ['none','context','cached','old','manual'])
async def test_actual_physical_view_requires_causal_fresh_report(monkeypatch,fault):
    _, context, cache, calls, hass, executor, report = harness(monkeypatch)
    before=state('on',context=context,at=BASE_MS-1000)
    before.entity_id='light.main'
    cache['light.main']=before
    physical={'value':PhysicalLightObservation('light.main','on',BASE_MS-1000,'session',1,'ieee',1)}
    executor._state_reader=lambda entity:ObservedLightState(cache[entity],physical['value'])
    valid={'value':True}
    async def refresh(entity,current):
        report(value='off',context=context if fault!='context' else SimpleNamespace(id='other'))
        cache[entity].entity_id=entity
        if fault=='cached': cache[entity].attributes['restored']=True
        physical['value']=PhysicalLightObservation(entity,'off',
            BASE_MS if fault!='old' else BASE_MS-500,'session',2,'ieee',1)
        if fault=='manual': valid['value']=False
        return True
    executor._confirmation_refresher=refresh
    receipt=await executor.execute(hass,command(LightAction.TURN_OFF),dispatch_guard=lambda:valid['value'])
    assert receipt.confirmed is (fault=='none')
    assert len(calls)==1
