"""Narrow tests for the Home Assistant lighting transport boundary."""

from __future__ import annotations

import asyncio
import threading

import pytest

from custom_components.hausman_hub.adapters.core.ha_transport import (
    HomeAssistantTransport,
)
from custom_components.hausman_hub.application.core.command_service import (
    PreparedCall,
    TransportStatus,
)


class _Services:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object], bool]] = []

    async def async_call(self, domain, service, data, *, blocking):
        self.calls.append((domain, service, dict(data), blocking))


class _Hass:
    def __init__(self) -> None:
        self.services = _Services()


def _prepared(*, domain="light", service="turn_on", entity_id="light.kitchen", data=None):
    return PreparedCall(
        domain=domain,
        service=service,
        entity_id=entity_id,
        data={} if data is None else data,
        operation_id="operation",
        permit_id="permit",
        requested_properties=("on",),
    )


@pytest.fixture
def ha_loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()


def test_transport_maps_only_allowlisted_mqtt_entity_fields(ha_loop):
    hass = _Hass()
    transport = HomeAssistantTransport(
        hass,
        loop=ha_loop,
        allowed_entities={"light.kitchen": "light", "switch.power": "switch"},
    )

    outcome = transport.call(_prepared(data={"brightness_pct": 40, "color_temperature_k": 4000}))

    assert outcome.status is TransportStatus.ACCEPTED
    assert hass.services.calls == [
        ("light", "turn_on", {"entity_id": "light.kitchen", "brightness_pct": 40, "color_temp_kelvin": 4000}, True)
    ]


@pytest.mark.parametrize(
    "prepared",
    (
        _prepared(entity_id="light.other"),
        _prepared(domain="media_player"),
        _prepared(domain="switch", entity_id="switch.power", data={"brightness_pct": 40}),
        _prepared(service="turn_off", data={"brightness_pct": 40}),
    ),
)
def test_transport_refuses_unbound_or_unsupported_call_before_ha(prepared, ha_loop):
    hass = _Hass()
    transport = HomeAssistantTransport(
        hass,
        loop=ha_loop,
        allowed_entities={"light.kitchen": "light", "switch.power": "switch"},
    )

    outcome = transport.call(prepared)

    assert outcome.status is TransportStatus.ERROR
    assert outcome.proven_not_sent is True
    assert hass.services.calls == []
