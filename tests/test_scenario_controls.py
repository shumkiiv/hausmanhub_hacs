from datetime import datetime, time, timedelta

from custom_components.hausman_hub.domain.scenario_controls import (
    OccupancyEvidence,
    brightness_after_relative_fade,
    evening_start,
    fade_steps,
    manual_command,
    maximum_brightness,
    cover_cap,
    ManualLightOwnership,
    absence_confirmed,
    power_readiness,
)
from custom_components.hausman_hub.application.scenario_executor import _trigger_asserts_presence


def test_occupancy_uses_motion_or_presence_and_unknown_is_not_absence():
    assert OccupancyEvidence.from_states("on", "off") is OccupancyEvidence.OCCUPIED
    assert OccupancyEvidence.from_states("off", "on") is OccupancyEvidence.OCCUPIED
    assert OccupancyEvidence.from_states("off", "off") is OccupancyEvidence.ABSENT
    assert OccupancyEvidence.from_states("unknown", "off") is OccupancyEvidence.UNKNOWN


def test_occupancy_supports_zones_with_only_one_sensor_type():
    assert OccupancyEvidence.from_states("off", None) is OccupancyEvidence.ABSENT
    assert OccupancyEvidence.from_states(None, "off") is OccupancyEvidence.ABSENT
    assert OccupancyEvidence.from_states("on", None) is OccupancyEvidence.OCCUPIED
    assert OccupancyEvidence.from_states(None, "on") is OccupancyEvidence.OCCUPIED


def test_unknown_or_unavailable_sensor_stays_fail_closed_even_with_other_off():
    assert OccupancyEvidence.from_states("unavailable", "off") is OccupancyEvidence.UNKNOWN
    assert OccupancyEvidence.from_states("unknown", None) is OccupancyEvidence.UNKNOWN


def test_evening_starts_at_earlier_of_sunset_and_21_00_in_local_time():
    now = datetime(2026, 9, 7, 18, 0)
    assert evening_start(now, datetime(2026, 9, 7, 20, 15)) == time(20, 15)
    assert evening_start(now, datetime(2026, 9, 7, 22, 0)) == time(21, 0)


def test_brightness_limit_reaches_five_percent_by_23_00():
    start = datetime(2026, 9, 7, 20, 0)
    assert maximum_brightness(start, start, datetime(2026, 9, 7, 23, 0), 80) == 80
    assert maximum_brightness(datetime(2026, 9, 7, 22, 0), start, datetime(2026, 9, 7, 23, 0), 80) == 30
    assert maximum_brightness(datetime(2026, 9, 7, 23, 0), start, datetime(2026, 9, 7, 23, 0), 80) == 5


def test_relative_fade_and_one_percent_steps_respect_minimum():
    assert brightness_after_relative_fade(80, minimum=20) == 72
    assert fade_steps(80, 72) == (79, 78, 77, 76, 75, 74, 73, 72)
    assert brightness_after_relative_fade(1, minimum=5) == 5


def test_manual_upper_or_hold_forces_neutral_full_brightness():
    result = manual_command("upper_press", supports_temperature=True)
    assert result == {"brightness": 100, "color_temperature": 3000, "manual": True}


def test_cover_caps_are_configurable_and_hard():
    assert cover_cap("kitchen") == 80
    assert cover_cap("cabinet") == 90
    assert cover_cap("living_room") == 100


def test_manual_off_inhibits_automation_and_absence_releases_ownership():
    at = datetime(2026, 9, 7, 12, 0)
    state = ManualLightOwnership().manual_on()
    assert not state.automatic_allowed(at, at)
    state = state.manual_off(at)
    assert not state.automatic_allowed(at + timedelta(minutes=4))
    assert state.automatic_allowed(at + timedelta(minutes=5))


def test_presence_keeps_absence_timer_open_while_motion_event_is_old():
    assert not absence_confirmed("off", "on", absent_for_seconds=600)
    assert absence_confirmed("off", "off", absent_for_seconds=10)
    assert absence_confirmed("off", None, absent_for_seconds=10)
    assert absence_confirmed(None, "off", absent_for_seconds=10)
    assert not absence_confirmed("unavailable", "off", absent_for_seconds=600)


def test_power_dependency_requires_ready_power_before_light_commands():
    assert power_readiness("off", "off") == "power_off"
    assert power_readiness("unknown", "off") == "power_unknown"
    assert power_readiness("on", "unknown") == "light_not_ready"
    assert power_readiness("on", "off") == "ready"


def test_executor_presence_trigger_uses_shared_occupancy_policy():
    device = type("Device", (), {"name": "Датчик движения", "physical_name": "", "capability_name": "", "device_type_name": "", "entity_id": "binary_sensor.motion"})()
    catalog = type("Catalog", (), {"device": lambda self, target: device if target == "motion" else None})()
    assert _trigger_asserts_presence({"source": "device_state", "new_value": "on", "target_id": "motion"}, catalog)
    assert not _trigger_asserts_presence({"source": "device_state", "new_value": "unavailable", "target_id": "motion"}, catalog)
