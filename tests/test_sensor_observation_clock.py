"""Observation freshness never replaces the state-transition clock."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.room_lighting_ha_state import RoomLightingHaStateProvider
from custom_components.hausman_hub.domain.room_lighting import RoomSensor, SensorKind
from custom_components.hausman_hub.domain.room_lighting_engine import (
    SensorSnapshot, RoomLightingEngineViolation, sensor_observation_is_fresh,
    _absence_evidence, EnginePolicy,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def snapshot(raw="on", reported=NOW, **attributes):
    state = SimpleNamespace(state=raw, last_changed=NOW-timedelta(hours=1),
        last_updated=NOW-timedelta(minutes=30), last_reported=reported, attributes=attributes)
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity: state))
    return RoomLightingHaStateProvider()._sensor_snapshot(
        hass, RoomSensor("presence", "Presence", SensorKind.PRESENCE, "binary_sensor.presence"), NOW_MS)


def test_stationary_fresh_presence_uses_report_not_transition():
    sensor = snapshot()
    assert sensor.last_changed == NOW_MS - 3600000
    assert sensor.observed_at_ms == NOW_MS
    assert sensor_observation_is_fresh(sensor, NOW_MS, 300000)
    assert not sensor_observation_is_fresh(sensor, NOW_MS + 300001, 300000)


@pytest.mark.parametrize("reported", [NOW+timedelta(seconds=1), "bad", float("inf"), float("nan"), True])
def test_bad_latest_observation_cannot_fall_back_to_older_fresh_evidence(reported):
    sensor = snapshot(reported=reported)
    assert sensor.state is SensorState.UNKNOWN
    assert not sensor_observation_is_fresh(sensor, NOW_MS, 300000)


@pytest.mark.parametrize("flag", ["restored", "assumed_state", "cached"])
def test_restored_or_cached_reports_do_not_prove_presence(flag):
    assert snapshot(**{flag: True}).state is SensorState.UNKNOWN


@pytest.mark.parametrize("raw", ["unknown", "unavailable"])
def test_fresh_timestamp_does_not_make_unknown_state_known(raw):
    assert not sensor_observation_is_fresh(snapshot(raw), NOW_MS, 300000)


def test_off_report_keeps_absence_anchor_and_restart_gap():
    sensor = snapshot("off")
    context = SimpleNamespace(sensors=(sensor,), now=NOW_MS, unobserved_since=None)
    assert _absence_evidence(context, EnginePolicy()) == (True, NOW_MS-3600000, False)
    context.sensors = (replace(sensor, observed_at_ms=NOW_MS+1000),)
    context.now += 1000
    assert _absence_evidence(context, EnginePolicy()) == (True, NOW_MS-3600000, False)
    context.unobserved_since = NOW_MS
    assert _absence_evidence(context, EnginePolicy()) == (True, NOW_MS, False)
    context.sensors = (replace(sensor, state=SensorState.ON, observed_at_ms=NOW_MS+1000),)
    assert _absence_evidence(context, EnginePolicy()) == (False, None, False)


def test_legacy_snapshot_uses_transition_and_future_never_proves_freshness():
    sensor = SensorSnapshot("presence", SensorKind.PRESENCE, SensorState.ON, NOW_MS)
    assert sensor_observation_is_fresh(sensor, NOW_MS, 300000)
    assert not sensor_observation_is_fresh(sensor, NOW_MS-1, 300000)
    with pytest.raises(RoomLightingEngineViolation):
        replace(sensor, observed_at_ms=True)


@pytest.mark.parametrize("changed", [NOW+timedelta(seconds=1), float("nan"), float("inf"), "bad", None])
def test_bad_transition_is_not_invented_absence(changed):
    state = SimpleNamespace(state="off", last_changed=changed, last_reported=NOW, attributes={})
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity: state))
    sensor = RoomLightingHaStateProvider()._sensor_snapshot(hass,
        RoomSensor("presence", "Presence", SensorKind.PRESENCE, "binary_sensor.presence"), NOW_MS)
    assert sensor.state is SensorState.UNKNOWN
