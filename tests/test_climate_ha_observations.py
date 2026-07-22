"""Pure tests for the native Home Assistant climate observation adapter."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.climate_ha_observations import (
    ClimateHaEntityState,
    ClimateHaObservationViolation,
    build_native_ha_climate_observation,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateHomeEnvironment,
    ClimateRegistry,
    ClimateRoom,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDayPeriod,
    ClimateFanMode,
    ClimateOccupancyMode,
    ClimatePhysicalFeedback,
    ClimateRoomMode,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from custom_components.hausman_hub.domain.climate_protection import (
    ClimateDeviceProtectionState,
    ClimateProtectionMemory,
    ClimateProtectionPhase,
)
from custom_components.hausman_hub.domain.contours import (
    ClimateComfortSettings,
    ClimateContourRoom,
    ClimateProfile,
    ClimateSchedule,
    ClimateStrategy,
    ContourDefinition,
    ContourEngine,
    ContourKind,
    ContourMode,
)

NOW = 1_800_000_000_000
STALE = NOW - 10 * 60 * 1000


class MemoryStates:
    """Simple in-memory native state view."""

    def __init__(self, states: dict[str, ClimateHaEntityState]) -> None:
        self._states = states

    def entity_state(self, entity_id: str) -> ClimateHaEntityState | None:
        return self._states.get(entity_id)


def ha_state(
    entity_id: str,
    state: str,
    attributes: dict[str, object] | None = None,
    updated: int = NOW,
) -> ClimateHaEntityState:
    return ClimateHaEntityState(
        entity_id=entity_id,
        state=state,
        attributes=attributes or {},
        last_updated_ms=updated,
    )


def sensor_device(
    device_id: str,
    kind: ClimateDeviceKind,
    role: ClimateEndpointRole,
    entity_id: str | None,
) -> ClimateDevice:
    return ClimateDevice(
        device_id=device_id,
        name=device_id,
        room_id="living",
        kind=kind,
        source_id=f"source-{device_id}",
        control_scope=ClimateControlScope.OBSERVED,
        control_owner=ClimateControlOwner.OBSERVED,
        capabilities=(),
        endpoints=(
            () if entity_id is None else (ClimateEndpoint(role, entity_id),)
        ),
    )


def active_device(
    device_id: str,
    kind: ClimateDeviceKind,
    entity_id: str | None,
    capabilities: tuple[ClimateCapability, ...],
    extra_endpoints: tuple[ClimateEndpoint, ...] = (),
) -> ClimateDevice:
    endpoints = (
        ()
        if entity_id is None
        else (ClimateEndpoint(ClimateEndpointRole.CONTROL, entity_id),)
    )
    return ClimateDevice(
        device_id=device_id,
        name=device_id,
        room_id="living",
        kind=kind,
        source_id=f"source-{device_id}",
        control_scope=ClimateControlScope.MANAGED,
        control_owner=ClimateControlOwner.CLIMATE_CORE,
        capabilities=capabilities,
        endpoints=(*endpoints, *extra_endpoints),
    )


def registry(
    devices: tuple[ClimateDevice, ...],
    *,
    window: str | None = "binary_sensor.living_window",
    home: ClimateHomeEnvironment | None = None,
) -> ClimateRegistry:
    return ClimateRegistry(
        rooms=(
            ClimateRoom("living", "Living room", window_entity_id=window),
        ),
        devices=devices,
        home=home or ClimateHomeEnvironment(),
    )


def contour(mode: ContourMode = ContourMode.AUTOMATIC) -> ContourDefinition:
    settings = ClimateComfortSettings(
        target_temperature=24.0,
        target_humidity=45,
        strategy=ClimateStrategy.NORMAL,
    )
    return ContourDefinition(
        contour_id="climate",
        name="Климат",
        kind=ContourKind.CLIMATE,
        mode=mode,
        engine=ContourEngine.EXISTING_CLIMATE_CORE,
        rooms=(
            ClimateContourRoom(
                room_id="living",
                device_ids=("living_ac",),
                day_profile=settings,
                night_profile=settings,
                active_profile=ClimateProfile.DAY,
            ),
        ),
        schedule=ClimateSchedule(enabled=True, day_start="07:00", night_start="23:00"),
    )


def full_registry() -> ClimateRegistry:
    return registry(
        (
            active_device(
                "living_ac",
                ClimateDeviceKind.AIR_CONDITIONER,
                "climate.living_ac",
                (
                    ClimateCapability.POWER,
                    ClimateCapability.TARGET_TEMPERATURE,
                    ClimateCapability.HVAC_MODE,
                    ClimateCapability.FAN_MODE,
                ),
                extra_endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.PHYSICAL_FEEDBACK,
                        "binary_sensor.living_ac_flap",
                    ),
                ),
            ),
            sensor_device(
                "living_temperature",
                ClimateDeviceKind.TEMPERATURE_SENSOR,
                ClimateEndpointRole.TEMPERATURE,
                "sensor.living_temperature",
            ),
            sensor_device(
                "living_humidity",
                ClimateDeviceKind.HUMIDITY_SENSOR,
                ClimateEndpointRole.HUMIDITY,
                "sensor.living_humidity",
            ),
        ),
        home=ClimateHomeEnvironment(
            outdoor_temperature_entity_id="sensor.outdoor_temperature",
            presence_entity_id="person.ivan",
            central_heating_entity_id="switch.central_heating",
        ),
    )


def full_states() -> dict[str, ClimateHaEntityState]:
    entries = (
        ha_state(
            "climate.living_ac",
            "cool",
            {
                "hvac_action": "cooling",
                "temperature": 24.5,
                "fan_mode": "low",
                "current_temperature": 26.0,
            },
        ),
        ha_state("sensor.living_temperature", "25.5"),
        ha_state("sensor.living_humidity", "41"),
        ha_state("binary_sensor.living_window", "off"),
        ha_state("binary_sensor.living_ac_flap", "on"),
        ha_state("sensor.outdoor_temperature", "30.5"),
        ha_state("person.ivan", "home"),
        ha_state("switch.central_heating", "off"),
    )
    return {entry.entity_id: entry for entry in entries}


def empty_protection() -> ClimateProtectionMemory:
    return ClimateProtectionMemory(updated_at=NOW, devices=())


class NativeHaObservationTest(unittest.TestCase):
    """Native states become honest bounded HausmanHub observations."""

    def build(self, registry=None, states=None, **kwargs):
        return build_native_ha_climate_observation(
            registry or full_registry(),
            kwargs.pop("contour", contour()),
            MemoryStates(full_states() if states is None else states),
            observed_at=kwargs.pop("observed_at", NOW),
            protection=kwargs.pop("protection", empty_protection()),
            **kwargs,
        )

    def test_full_native_state_maps_to_bounded_facts(self) -> None:
        observation = self.build(local_time=(12, 0))

        self.assertIs(observation.data_status, ClimateDataStatus.FRESH)
        room = observation.room("living")
        self.assertIsNotNone(room)
        assert room is not None
        self.assertIs(room.data_status, ClimateDataStatus.FRESH)
        self.assertEqual(25.5, room.temperature)
        self.assertEqual(41.0, room.humidity)
        self.assertIs(room.temperature_quality, ClimateTemperatureQuality.NORMAL)
        self.assertIs(room.window, ClimateWindowState.CLOSED)
        self.assertIs(room.mode, ClimateRoomMode.AUTO)
        self.assertEqual(24.0, room.observed_target_temperature)
        self.assertEqual(45.0, room.observed_target_humidity)
        self.assertEqual("normal", room.observed_target_strategy)
        self.assertTrue(room.authority_eligible)

        self.assertIs(observation.home.period, ClimateDayPeriod.DAY)
        self.assertEqual(30.5, observation.home.outdoor_temperature)
        self.assertEqual(30.5, observation.home.heat_load_temperature)
        self.assertIs(observation.home.central_heating_on, False)
        self.assertIs(observation.home.occupancy, ClimateOccupancyMode.HOME)

        ac = observation.device("living_ac")
        self.assertIsNotNone(ac)
        assert ac is not None
        self.assertIs(ac.availability, ClimateDeviceAvailability.AVAILABLE)
        self.assertIs(ac.activity, ClimateDeviceActivity.COOLING)
        self.assertEqual(24.5, ac.current_target_temperature)
        self.assertIs(ac.fan_mode, ClimateFanMode.LOW)
        self.assertIsNone(ac.quiet)
        self.assertIs(ac.physical_feedback, ClimatePhysicalFeedback.CONFIRMED)

        temperature = observation.device("living_temperature")
        self.assertIsNotNone(temperature)
        assert temperature is not None
        self.assertIs(temperature.availability, ClimateDeviceAvailability.AVAILABLE)
        self.assertIs(temperature.activity, ClimateDeviceActivity.IDLE)

    def test_protection_memory_supplies_confirmed_transitions(self) -> None:
        protection = ClimateProtectionMemory(
            updated_at=NOW,
            devices=(
                ClimateDeviceProtectionState(
                    device_id="living_ac",
                    room_id="living",
                    phase=ClimateProtectionPhase.ACTIVE,
                    observed_at=NOW - 60_000,
                    last_started_at=NOW - 8 * 60_000,
                    last_stopped_at=NOW - 60 * 60_000,
                    confirmed_short_cycle_count=2,
                ),
            ),
        )

        observation = self.build(protection=protection)
        ac = observation.device("living_ac")

        self.assertIsNotNone(ac)
        assert ac is not None
        self.assertEqual(NOW - 8 * 60_000, ac.last_started_at)
        self.assertEqual(NOW - 60 * 60_000, ac.last_stopped_at)
        self.assertEqual(2, ac.confirmed_short_cycle_count)

    def test_missing_entities_stay_missing_without_invented_values(self) -> None:
        observation = self.build(states={})

        room = observation.room("living")
        self.assertIsNotNone(room)
        assert room is not None
        self.assertIs(room.data_status, ClimateDataStatus.UNAVAILABLE)
        self.assertIsNone(room.temperature)
        ac = observation.device("living_ac")
        self.assertIsNotNone(ac)
        assert ac is not None
        self.assertIs(ac.availability, ClimateDeviceAvailability.MISSING)
        self.assertIs(ac.activity, ClimateDeviceActivity.UNKNOWN)
        temperature = observation.device("living_temperature")
        self.assertIsNotNone(temperature)
        assert temperature is not None
        self.assertIs(temperature.availability, ClimateDeviceAvailability.MISSING)

    def test_unavailable_entities_never_become_confident_values(self) -> None:
        states = {
            "climate.living_ac": ha_state("climate.living_ac", "unavailable"),
            "sensor.living_temperature": ha_state(
                "sensor.living_temperature", "unknown"
            ),
            "binary_sensor.living_window": ha_state(
                "binary_sensor.living_window", "unavailable"
            ),
        }

        observation = self.build(states=states)

        room = observation.room("living")
        self.assertIsNotNone(room)
        assert room is not None
        self.assertIsNone(room.temperature)
        self.assertIs(room.window, ClimateWindowState.UNKNOWN)
        self.assertIs(room.temperature_quality, ClimateTemperatureQuality.UNKNOWN)
        self.assertFalse(room.authority_eligible)
        ac = observation.device("living_ac")
        self.assertIsNotNone(ac)
        assert ac is not None
        self.assertIs(ac.availability, ClimateDeviceAvailability.UNAVAILABLE)

    def test_stale_temperature_marks_only_its_room_stale(self) -> None:
        states = full_states()
        states["sensor.living_temperature"] = ha_state(
            "sensor.living_temperature", "25.5", updated=STALE
        )

        observation = self.build(states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertIs(room.data_status, ClimateDataStatus.STALE)
        self.assertEqual(25.5, room.temperature)
        self.assertFalse(room.authority_eligible)

    def test_weather_lockout_first_observation_in_band_fails_closed(self) -> None:
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("17.0"),
        )

        self.assertTrue(observation.home.weather_heating_lockout)

    def test_weather_lockout_engages_at_high_threshold(self) -> None:
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("18.0"),
        )

        self.assertTrue(observation.home.weather_heating_lockout)

    def test_weather_lockout_releases_at_low_threshold(self) -> None:
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("16.0"),
        )

        self.assertFalse(observation.home.weather_heating_lockout)

    def test_weather_lockout_hysteresis_holds_previous_permission(self) -> None:
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("17.0"),
            previous_weather_lockout=False,
        )

        self.assertFalse(observation.home.weather_heating_lockout)

    def test_weather_lockout_hysteresis_holds_previous_denial(self) -> None:
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("17.0"),
            previous_weather_lockout=True,
        )

        self.assertTrue(observation.home.weather_heating_lockout)

    def test_weather_lockout_after_unlock_stays_unlocked_in_band(self) -> None:
        unlocked = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("15.0"),
        )
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("17.0"),
            previous_weather_lockout=unlocked.home.weather_heating_lockout,
        )

        self.assertFalse(unlocked.home.weather_heating_lockout)
        self.assertFalse(observation.home.weather_heating_lockout)

    def test_weather_lockout_after_lock_stays_locked_in_band(self) -> None:
        locked = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("19.0"),
        )
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("17.0"),
            previous_weather_lockout=locked.home.weather_heating_lockout,
        )

        self.assertTrue(locked.home.weather_heating_lockout)
        self.assertTrue(observation.home.weather_heating_lockout)

    def test_weather_lockout_thresholds_are_options(self) -> None:
        relaxed = self.build(
            registry=self._weather_registry(high=22.0),
            states=self._weather_states("19.0"),
            previous_weather_lockout=False,
        )
        strict = self.build(
            registry=self._weather_registry(high=18.0),
            states=self._weather_states("19.0"),
            previous_weather_lockout=False,
        )

        self.assertFalse(relaxed.home.weather_heating_lockout)
        self.assertTrue(strict.home.weather_heating_lockout)

    def test_weather_lockout_ignores_hydraulic_activity(self) -> None:
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("17.0"),
            previous_weather_lockout=False,
        )

        self.assertFalse(observation.home.weather_heating_lockout)

    def _weather_registry(self, *, high: float = 18.0, low: float = 16.0) -> ClimateRegistry:
        return registry(
            (),
            home=ClimateHomeEnvironment(
                outdoor_temperature_entity_id="sensor.outdoor_temperature",
                heating_lockout_high=high,
                heating_lockout_low=low,
            ),
        )

    def _weather_states(self, value: str) -> dict[str, ClimateHaEntityState]:
        return {"sensor.outdoor_temperature": ha_state("sensor.outdoor_temperature", value)}

        observation = self.build(registry=full_registry().__class__(
            rooms=(ClimateRoom("living", "Living room"),),
            devices=full_registry().devices,
        ))

        room = observation.room("living")
        self.assertIsNotNone(room)
        assert room is not None
        self.assertIs(room.window, ClimateWindowState.NOT_CONFIGURED)
        self.assertIsNone(observation.home.outdoor_temperature)
        self.assertIsNone(observation.home.central_heating_on)
        self.assertFalse(observation.home.central_heating_configured)
        self.assertIs(observation.home.occupancy, ClimateOccupancyMode.HOME)
        self.assertIs(observation.home.period, ClimateDayPeriod.UNKNOWN)

    def test_away_presence_selects_setback_and_lost_presence_stays_unknown(
        self,
    ) -> None:
        states = full_states()
        states["person.ivan"] = ha_state("person.ivan", "not_home")
        away = self.build(states=states)
        self.assertIs(
            away.home.occupancy,
            ClimateOccupancyMode.AWAY_SETBACK,
        )

        states["person.ivan"] = ha_state("person.ivan", "unavailable")
        unknown = self.build(states=states)
        self.assertIs(unknown.home.occupancy, ClimateOccupancyMode.UNKNOWN)

    def test_room_temperature_falls_back_to_climate_current_temperature(
        self,
    ) -> None:
        states = full_states()
        del states["sensor.living_temperature"]

        observation = self.build(states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(26.0, room.temperature)

    def test_humidifier_and_trv_activities_follow_native_states(self) -> None:
        devices = (
            active_device(
                "living_ac",
                ClimateDeviceKind.HUMIDIFIER,
                "humidifier.living",
                (ClimateCapability.POWER, ClimateCapability.TARGET_HUMIDITY),
            ),
        )
        states = {
            "humidifier.living": ha_state(
                "humidifier.living", "on", {"humidity": 45}
            ),
        }

        observation = self.build(
            registry=registry(devices, window=None),
            states=states,
        )
        humidifier = observation.device("living_ac")

        self.assertIsNotNone(humidifier)
        assert humidifier is not None
        self.assertIs(humidifier.activity, ClimateDeviceActivity.HUMIDIFYING)
        self.assertEqual(45.0, humidifier.current_target_humidity)

    def test_no_contour_means_unknown_mode_and_no_observed_targets(self) -> None:
        observation = self.build(contour=None)

        room = observation.room("living")
        self.assertIsNotNone(room)
        assert room is not None
        self.assertIs(room.mode, ClimateRoomMode.UNKNOWN)
        self.assertIsNone(room.observed_target_temperature)
        self.assertIsNone(room.observed_target_humidity)
        self.assertIsNone(room.observed_target_strategy)
        self.assertFalse(room.authority_eligible)

    def test_schedule_disabled_or_missing_local_time_keeps_period_unknown(
        self,
    ) -> None:
        observation = self.build()
        self.assertIs(observation.home.period, ClimateDayPeriod.UNKNOWN)

        night = self.build(local_time=(2, 30))
        self.assertIs(night.home.period, ClimateDayPeriod.NIGHT)

    def test_stale_fallback_temperature_marks_the_room_stale(self) -> None:
        states = full_states()
        del states["sensor.living_temperature"]
        states["climate.living_ac"] = ha_state(
            "climate.living_ac",
            "cool",
            {"hvac_action": "cooling", "current_temperature": 26.0},
            updated=STALE,
        )

        observation = self.build(states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(26.0, room.temperature)
        self.assertIs(room.data_status, ClimateDataStatus.STALE)

    def test_stale_humidity_marks_the_room_stale(self) -> None:
        states = full_states()
        states["sensor.living_humidity"] = ha_state(
            "sensor.living_humidity", "41", updated=STALE
        )

        observation = self.build(states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertIs(room.data_status, ClimateDataStatus.STALE)

    def test_non_finite_sensor_text_stays_absent(self) -> None:
        states = full_states()
        states["sensor.living_temperature"] = ha_state(
            "sensor.living_temperature", "nan"
        )
        states["sensor.living_humidity"] = ha_state(
            "sensor.living_humidity", "inf"
        )

        observation = self.build(states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(26.0, room.temperature)
        self.assertIsNone(room.humidity)

    def test_custom_person_zone_does_not_invent_an_away_policy(self) -> None:
        states = full_states()
        states["person.ivan"] = ha_state("person.ivan", "work")

        observation = self.build(states=states)

        self.assertIs(observation.home.occupancy, ClimateOccupancyMode.HOME)

    def test_invalid_adapter_inputs_fail_closed(self) -> None:
        with self.assertRaises(ClimateHaObservationViolation):
            ClimateHaEntityState("bad", "on", {}, NOW)
        with self.assertRaises(ClimateHaObservationViolation):
            ClimateHaEntityState("sensor.x", "on" * 100, {}, NOW)
        with self.assertRaises(ClimateHaObservationViolation):
            ClimateHaEntityState("sensor.x", "on", {}, -1)
        with self.assertRaises(ClimateHaObservationViolation):
            build_native_ha_climate_observation(
                full_registry(),
                contour(),
                MemoryStates({}),
                observed_at=-1,
                protection=empty_protection(),
            )
        with self.assertRaises(ClimateHaObservationViolation):
            build_native_ha_climate_observation(
                full_registry(),
                contour(),
                MemoryStates({}),
                observed_at=NOW,
                protection=empty_protection(),
                local_time=(12,),
            )


if __name__ == "__main__":
    unittest.main()
