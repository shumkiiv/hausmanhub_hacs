"""Offline only: bounded periodic reads use the existing physical receiver."""
import asyncio

import pytest

from custom_components.hausman_hub.adapters.device_poll_lighting import DevicePollLightingAdapter
from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import ALL_READ_CHANNELS


def test_scope_is_exactly_existing_read_channels():
    adapter = DevicePollLightingAdapter(object())
    assert adapter.entities == frozenset(row[0] for row in ALL_READ_CHANNELS)
    assert "binary_sensor.fake_presence" not in adapter.entities


@pytest.mark.asyncio
async def test_unsupported_device_never_reaches_transport():
    class Refresh:
        async def refresh_confirmation(self, entity, current):
            raise AssertionError("unsupported target reached transport")
    assert not await DevicePollLightingAdapter(Refresh()).poll("switch.untrusted")


@pytest.mark.asyncio
@pytest.mark.parametrize("result,expected", [(True, True), (False, False), (None, False), (1, False)])
async def test_only_explicit_confirmation_is_success(result, expected):
    class Refresh:
        async def refresh_confirmation(self, entity, current):
            assert current()
            return result
    adapter = DevicePollLightingAdapter(Refresh())
    assert await adapter.poll(next(iter(adapter.entities))) is expected


@pytest.mark.asyncio
async def test_cancellation_invalidates_outstanding_guard():
    gate = asyncio.Event()
    captured = []
    class Refresh:
        async def refresh_confirmation(self, entity, current):
            captured.append(current)
            gate.set()
            await asyncio.Future()
    adapter = DevicePollLightingAdapter(Refresh())
    task = asyncio.create_task(adapter.poll(next(iter(adapter.entities))))
    await gate.wait()
    assert captured[0]()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not captured[0]()


@pytest.mark.asyncio
async def test_refresh_error_does_not_confirm():
    class Refresh:
        async def refresh_confirmation(self, entity, current):
            raise OSError("no response")
    adapter = DevicePollLightingAdapter(Refresh())
    assert not await adapter.poll(next(iter(adapter.entities)))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("normal", True), ("disconnect", False), ("retained", False)])
async def test_existing_physical_report_parser_not_ha_cached_state(monkeypatch, mode, expected):
    import importlib
    from tests.test_lighting_mqtt_refresh import fixture
    from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import READ_CHANNELS
    module = importlib.import_module("custom_components.hausman_hub.adapters.lighting_mqtt_refresh")
    monkeypatch.setattr(module, "REFRESH_TIMEOUT_SECONDS", 0.01)
    refresher, _port, _native, _clock, calls, _current = fixture(mode=mode)
    adapter = DevicePollLightingAdapter(refresher)
    assert await adapter.poll(READ_CHANNELS[0][0]) is expected
    assert len(calls) == 1
    assert calls[0][0].endswith("/get")


@pytest.mark.asyncio
async def test_scheduled_read_uses_existing_parser_and_period():
    from tests.test_lighting_mqtt_refresh import fixture
    from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import READ_CHANNELS
    from custom_components.hausman_hub.application.device_poll_schedule import DevicePollSchedule, PollSettings
    refresher, _port, _native, _clock, calls, _current = fixture()
    adapter = DevicePollLightingAdapter(refresher)
    entity = READ_CHANNELS[0][0]
    schedule = DevicePollSchedule(adapter.poll)
    schedule.configure(PollSettings(enabled=True, interval_minutes=15), (entity,))
    assert await schedule.tick(0) == {entity: "confirmed"}
    assert await schedule.tick(899_999) == {}
    assert len(calls) == 1
    assert await schedule.tick(900_000) == {entity: "confirmed"}
    assert len(calls) == 2
