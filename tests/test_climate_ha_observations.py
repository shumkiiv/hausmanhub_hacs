"""Pure tests for the native Home Assistant climate observation adapter."""

from __future__ import annotations

from dataclasses import replace
import unittest

from custom_components.hausman_hub.application.climate_ha_observations import (
    MAX_NATIVE_STATE_AGE_MS,
    MAX_OUTDOOR_SENSOR_STATE_AGE_MS,
    MAX_ROOM_SENSOR_STATE_AGE_MS,
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
    ClimateOutdoorTemperatureSource,
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
STALE = NOW - MAX_NATIVE_STATE_AGE_MS - 5 * 60 * 1000
ROOM_STALE = NOW - MAX_ROOM_SENSOR_STATE_AGE_MS - 5 * 60 * 1000


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
        self.assertEqual(NOW, ac.observed_at)

        temperature = observation.device("living_temperature")
        self.assertIsNotNone(temperature)
        assert temperature is not None
        self.assertIs(temperature.availability, ClimateDeviceAvailability.AVAILABLE)
        self.assertIs(temperature.activity, ClimateDeviceActivity.IDLE)
        self.assertEqual(NOW, temperature.observed_at)

    def test_radiator_temperature_infers_central_heating_with_hysteresis(self) -> None:
        registry = replace(
            full_registry(),
            home=ClimateHomeEnvironment(
                central_heating_entity_id="sensor.battery_temperature",
                central_heating_temperature_on=35.0,
                central_heating_temperature_off=30.0,
            ),
        )
        hot = self.build(
            registry=registry,
            states={"sensor.battery_temperature": ha_state("sensor.battery_temperature", "42.5")},
        )
        self.assertIs(hot.home.central_heating_on, True)
        holding = self.build(
            registry=registry,
            states={"sensor.battery_temperature": ha_state("sensor.battery_temperature", "33")},
            previous_central_heating_on=hot.home.central_heating_on,
        )
        self.assertIs(holding.home.central_heating_on, True)
        cold = self.build(
            registry=registry,
            states={"sensor.battery_temperature": ha_state("sensor.battery_temperature", "27")},
            previous_central_heating_on=holding.home.central_heating_on,
        )
        self.assertIs(cold.home.central_heating_on, False)
        unknown = self.build(
            registry=registry,
            states={"sensor.battery_temperature": ha_state("sensor.battery_temperature", "33")},
        )
        self.assertIsNone(unknown.home.central_heating_on)

    def test_weather_entity_temperature_attribute_is_a_real_outdoor_source(self) -> None:
        registry = full_registry()
        registry = replace(
            registry,
            home=replace(
                registry.home,
                outdoor_temperature_entity_id="weather.home",
            ),
        )
        states = full_states()
        states["weather.home"] = ha_state(
            "weather.home",
            "sunny",
            {"temperature": 7.5},
        )

        observation = self.build(registry=registry, states=states)

        self.assertEqual(7.5, observation.home.outdoor_temperature)
        self.assertEqual(7.5, observation.home.heat_load_temperature)

    def test_outdoor_temperature_uses_the_first_available_source_by_priority(self) -> None:
        registry = full_registry()
        registry = replace(
            registry,
            home=replace(
                registry.home,
                outdoor_temperature_entity_id="sensor.outdoor_primary",
                outdoor_temperature_entity_ids=(
                    "sensor.outdoor_primary",
                    "weather.home",
                ),
            ),
        )
        states = full_states()
        states["sensor.outdoor_primary"] = ha_state(
            "sensor.outdoor_primary", "unavailable"
        )
        states["weather.home"] = ha_state(
            "weather.home", "cloudy", {"temperature": 6.5}
        )

        observation = self.build(registry=registry, states=states)

        self.assertEqual(6.5, observation.home.outdoor_temperature)
        self.assertEqual(6.5, observation.home.heat_load_temperature)

    def test_outdoor_temperature_keeps_the_available_primary_source(self) -> None:
        registry = full_registry()
        registry = replace(
            registry,
            home=replace(
                registry.home,
                outdoor_temperature_entity_id="sensor.outdoor_temperature",
                outdoor_temperature_entity_ids=(
                    "sensor.outdoor_temperature",
                    "weather.home",
                ),
            ),
        )
        states = full_states()
        states["weather.home"] = ha_state(
            "weather.home", "cloudy", {"temperature": 6.5}
        )

        observation = self.build(registry=registry, states=states)

        self.assertEqual(30.5, observation.home.outdoor_temperature)

    def test_fresh_physical_sensor_is_primary_and_the_service_cross_checks(
        self,
    ) -> None:
        registry = full_registry()
        registry = replace(
            registry,
            home=replace(
                registry.home,
                outdoor_temperature_entity_id="weather.home",
                outdoor_temperature_entity_ids=(
                    "weather.home",
                    "sensor.outdoor_temperature",
                ),
            ),
        )
        states = full_states()
        states["weather.home"] = ha_state(
            "weather.home", "cloudy", {"temperature": 29.0}
        )

        observation = self.build(registry=registry, states=states)

        self.assertEqual(30.5, observation.home.outdoor_temperature)
        self.assertIs(
            observation.home.outdoor_temperature_source,
            ClimateOutdoorTemperatureSource.PHYSICAL_SENSOR,
        )
        self.assertEqual(29.0, observation.home.outdoor_provider_temperature)
        self.assertEqual(1.5, observation.home.outdoor_source_divergence_c)

    def test_divergence_above_three_degrees_is_reported_not_blocked(self) -> None:
        registry = full_registry()
        registry = replace(
            registry,
            home=replace(
                registry.home,
                outdoor_temperature_entity_id="sensor.outdoor_temperature",
                outdoor_temperature_entity_ids=(
                    "sensor.outdoor_temperature",
                    "weather.home",
                ),
            ),
        )
        states = full_states()
        states["weather.home"] = ha_state(
            "weather.home", "cloudy", {"temperature": 25.0}
        )

        observation = self.build(registry=registry, states=states)

        self.assertIs(observation.data_status, ClimateDataStatus.FRESH)
        self.assertEqual(30.5, observation.home.outdoor_temperature)
        self.assertEqual(5.5, observation.home.outdoor_source_divergence_c)
        room = observation.room("living")
        self.assertIsNotNone(room)
        assert room is not None
        self.assertTrue(room.authority_eligible)

    def test_stale_physical_sensor_falls_back_to_the_weather_service(self) -> None:
        registry = full_registry()
        registry = replace(
            registry,
            home=replace(
                registry.home,
                outdoor_temperature_entity_id="sensor.outdoor_temperature",
                outdoor_temperature_entity_ids=(
                    "sensor.outdoor_temperature",
                    "weather.home",
                ),
            ),
        )
        states = full_states()
        states["sensor.outdoor_temperature"] = ha_state(
            "sensor.outdoor_temperature",
            "30.5",
            updated=NOW - MAX_OUTDOOR_SENSOR_STATE_AGE_MS - 60_000,
        )
        states["weather.home"] = ha_state(
            "weather.home", "cloudy", {"temperature": 27.0}
        )

        observation = self.build(registry=registry, states=states)

        self.assertEqual(27.0, observation.home.outdoor_temperature)
        self.assertIs(
            observation.home.outdoor_temperature_source,
            ClimateOutdoorTemperatureSource.WEATHER_PROVIDER,
        )
        self.assertEqual(27.0, observation.home.outdoor_provider_temperature)
        self.assertIsNone(observation.home.outdoor_source_divergence_c)

    def test_stale_sensor_without_a_service_keeps_the_historical_value(self) -> None:
        registry = full_registry()
        states = full_states()
        states["sensor.outdoor_temperature"] = ha_state(
            "sensor.outdoor_temperature",
            "30.5",
            updated=NOW - MAX_OUTDOOR_SENSOR_STATE_AGE_MS - 60_000,
        )

        observation = self.build(registry=registry, states=states)

        self.assertEqual(30.5, observation.home.outdoor_temperature)
        self.assertIs(
            observation.home.outdoor_temperature_source,
            ClimateOutdoorTemperatureSource.PHYSICAL_SENSOR_STALE,
        )
        self.assertIsNone(observation.home.outdoor_provider_temperature)
        self.assertIsNone(observation.home.outdoor_source_divergence_c)

    def test_missing_sources_report_an_honest_none(self) -> None:
        observation = self.build(
            registry=registry((), home=ClimateHomeEnvironment()),
            states={},
        )

        self.assertIsNone(observation.home.outdoor_temperature)
        self.assertIs(
            observation.home.outdoor_temperature_source,
            ClimateOutdoorTemperatureSource.NONE,
        )
        self.assertIsNone(observation.home.outdoor_provider_temperature)

    def test_dashboard_weather_entity_cross_checks_when_unconfigured(self) -> None:
        class DashboardStates(MemoryStates):
            def weather_entity_state(self) -> ClimateHaEntityState | None:
                return self._states.get("weather.home")

        states = full_states()
        states["weather.home"] = ha_state(
            "weather.home", "cloudy", {"temperature": 28.0}
        )

        observation = build_native_ha_climate_observation(
            full_registry(),
            contour(),
            DashboardStates(states),
            observed_at=NOW,
            protection=empty_protection(),
        )

        self.assertEqual(30.5, observation.home.outdoor_temperature)
        self.assertIs(
            observation.home.outdoor_temperature_source,
            ClimateOutdoorTemperatureSource.PHYSICAL_SENSOR,
        )
        self.assertEqual(28.0, observation.home.outdoor_provider_temperature)
        self.assertEqual(2.5, observation.home.outdoor_source_divergence_c)

    def test_without_a_dashboard_weather_probe_the_cross_check_stays_empty(
        self,
    ) -> None:
        observation = self.build()

        self.assertEqual(30.5, observation.home.outdoor_temperature)
        self.assertIs(
            observation.home.outdoor_temperature_source,
            ClimateOutdoorTemperatureSource.PHYSICAL_SENSOR,
        )
        self.assertIsNone(observation.home.outdoor_provider_temperature)
        self.assertIsNone(observation.home.outdoor_source_divergence_c)

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
            "sensor.living_temperature", "25.5", updated=ROOM_STALE
        )

        observation = self.build(states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertIs(room.data_status, ClimateDataStatus.STALE)
        self.assertEqual(25.5, room.temperature)
        self.assertFalse(room.authority_eligible)

    def test_native_stale_room_sensor_stays_fresh_within_room_window(self) -> None:
        states = full_states()
        states["sensor.living_temperature"] = ha_state(
            "sensor.living_temperature", "25.5", updated=STALE
        )
        states["sensor.living_humidity"] = ha_state(
            "sensor.living_humidity", "41", updated=STALE
        )

        observation = self.build(states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertIs(room.data_status, ClimateDataStatus.FRESH)
        self.assertEqual(25.5, room.temperature)
        self.assertEqual(41.0, room.humidity)
        self.assertTrue(room.authority_eligible)

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

    def test_air_conditioner_outdoor_guard_uses_configured_exact_boundary(self) -> None:
        blocked = self.build(
            registry=self._weather_registry(air_conditioner_minimum=-7.0),
            states=self._weather_states("-7.0"),
        )
        allowed = self.build(
            registry=self._weather_registry(air_conditioner_minimum=-7.0),
            states=self._weather_states("-6.9"),
        )

        self.assertTrue(blocked.home.air_conditioner_outdoor_lockout)
        self.assertFalse(allowed.home.air_conditioner_outdoor_lockout)

    def test_air_conditioner_guard_fails_closed_when_configured_source_is_lost(self) -> None:
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("unavailable"),
        )

        self.assertTrue(observation.home.air_conditioner_outdoor_guard_configured)
        self.assertIsNone(observation.home.outdoor_temperature)
        self.assertTrue(observation.home.air_conditioner_outdoor_lockout)

    def test_air_conditioner_guard_stays_inactive_when_no_source_is_configured(self) -> None:
        observation = self.build(
            registry=registry((), home=ClimateHomeEnvironment()),
            states={},
        )

        self.assertFalse(observation.home.air_conditioner_outdoor_guard_configured)
        self.assertFalse(observation.home.air_conditioner_outdoor_lockout)

    def test_weather_lockout_ignores_hydraulic_activity(self) -> None:
        observation = self.build(
            registry=self._weather_registry(),
            states=self._weather_states("17.0"),
            previous_weather_lockout=False,
        )

        self.assertFalse(observation.home.weather_heating_lockout)

    def _weather_registry(
        self,
        *,
        high: float = 18.0,
        low: float = 16.0,
        air_conditioner_minimum: float = -5.0,
    ) -> ClimateRegistry:
        return registry(
            (),
            home=ClimateHomeEnvironment(
                outdoor_temperature_entity_id="sensor.outdoor_temperature",
                heating_lockout_high=high,
                heating_lockout_low=low,
                air_conditioner_minimum_outdoor_temperature=(
                    air_conditioner_minimum
                ),
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
        for raw_state in ("unavailable", "unknown"):
            with self.subTest(raw_state=raw_state):
                states["person.ivan"] = ha_state("person.ivan", raw_state)
                unknown = self.build(states=states)
                self.assertIs(unknown.home.occupancy, ClimateOccupancyMode.UNKNOWN)

    def test_away_mode_binary_sensor_uses_inverted_home_semantics(self) -> None:
        registry = full_registry()
        registry = replace(
            registry,
            home=replace(
                registry.home,
                presence_entity_id="binary_sensor.a100_away",
            ),
        )
        states = full_states()
        states["binary_sensor.a100_away"] = ha_state(
            "binary_sensor.a100_away", "on", {"friendly_name": "A100 Away"}
        )
        self.assertIs(
            self.build(registry=registry, states=states).home.occupancy,
            ClimateOccupancyMode.AWAY_SETBACK,
        )
        states["binary_sensor.a100_away"] = ha_state(
            "binary_sensor.a100_away", "off", {"friendly_name": "A100 Away"}
        )
        self.assertIs(
            self.build(registry=registry, states=states).home.occupancy,
            ClimateOccupancyMode.HOME,
        )

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
            updated=ROOM_STALE,
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
            "sensor.living_humidity", "41", updated=ROOM_STALE
        )

        observation = self.build(states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertIs(room.data_status, ClimateDataStatus.STALE)

    def test_multiple_temperature_sensors_aggregate_to_median(self) -> None:
        base = full_registry()
        extra = sensor_device(
            "living_temperature_2",
            ClimateDeviceKind.TEMPERATURE_SENSOR,
            ClimateEndpointRole.TEMPERATURE,
            "sensor.living_temperature_2",
        )
        extra_3 = sensor_device(
            "living_temperature_3",
            ClimateDeviceKind.TEMPERATURE_SENSOR,
            ClimateEndpointRole.TEMPERATURE,
            "sensor.living_temperature_3",
        )
        merged = ClimateRegistry(
            rooms=base.rooms,
            devices=(*base.devices, extra, extra_3),
            home=base.home,
        )
        states = full_states()
        states["sensor.living_temperature"] = ha_state(
            "sensor.living_temperature", "22.0"
        )
        states["sensor.living_temperature_2"] = ha_state(
            "sensor.living_temperature_2", "26.0"
        )
        states["sensor.living_temperature_3"] = ha_state(
            "sensor.living_temperature_3", "24.0"
        )

        observation = self.build(registry=merged, states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(24.0, room.temperature)
        self.assertIs(room.data_status, ClimateDataStatus.FRESH)

    def test_two_temperature_sensors_aggregate_to_average_of_pair(self) -> None:
        base = full_registry()
        extra = sensor_device(
            "living_temperature_2",
            ClimateDeviceKind.TEMPERATURE_SENSOR,
            ClimateEndpointRole.TEMPERATURE,
            "sensor.living_temperature_2",
        )
        merged = ClimateRegistry(
            rooms=base.rooms,
            devices=(*base.devices, extra),
            home=base.home,
        )
        states = full_states()
        states["sensor.living_temperature"] = ha_state(
            "sensor.living_temperature", "22.0"
        )
        states["sensor.living_temperature_2"] = ha_state(
            "sensor.living_temperature_2", "24.0"
        )

        observation = self.build(registry=merged, states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(23.0, room.temperature)

    def test_unavailable_sensor_is_excluded_from_median(self) -> None:
        base = full_registry()
        extra = sensor_device(
            "living_temperature_2",
            ClimateDeviceKind.TEMPERATURE_SENSOR,
            ClimateEndpointRole.TEMPERATURE,
            "sensor.living_temperature_2",
        )
        extra_3 = sensor_device(
            "living_temperature_3",
            ClimateDeviceKind.TEMPERATURE_SENSOR,
            ClimateEndpointRole.TEMPERATURE,
            "sensor.living_temperature_3",
        )
        merged = ClimateRegistry(
            rooms=base.rooms,
            devices=(*base.devices, extra, extra_3),
            home=base.home,
        )
        states = full_states()
        states["sensor.living_temperature"] = ha_state(
            "sensor.living_temperature", "22.0"
        )
        states["sensor.living_temperature_2"] = ha_state(
            "sensor.living_temperature_2", "unavailable"
        )
        states["sensor.living_temperature_3"] = ha_state(
            "sensor.living_temperature_3", "24.0"
        )

        observation = self.build(registry=merged, states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(23.0, room.temperature)
        self.assertIs(room.data_status, ClimateDataStatus.FRESH)

    def test_stale_sensor_is_excluded_when_fresh_group_values_exist(self) -> None:
        base = full_registry()
        extra = sensor_device(
            "living_humidity_2",
            ClimateDeviceKind.HUMIDITY_SENSOR,
            ClimateEndpointRole.HUMIDITY,
            "sensor.living_humidity_2",
        )
        merged = ClimateRegistry(
            rooms=base.rooms,
            devices=(*base.devices, extra),
            home=base.home,
        )
        states = full_states()
        states["sensor.living_humidity"] = ha_state("sensor.living_humidity", "41")
        states["sensor.living_humidity_2"] = ha_state(
            "sensor.living_humidity_2", "45", updated=ROOM_STALE
        )

        observation = self.build(registry=merged, states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(41.0, room.humidity)
        self.assertIs(room.data_status, ClimateDataStatus.FRESH)

    def test_all_stale_sensor_values_keep_a_stale_median(self) -> None:
        base = full_registry()
        extra = sensor_device(
            "living_humidity_2",
            ClimateDeviceKind.HUMIDITY_SENSOR,
            ClimateEndpointRole.HUMIDITY,
            "sensor.living_humidity_2",
        )
        merged = ClimateRegistry(
            rooms=base.rooms,
            devices=(*base.devices, extra),
            home=base.home,
        )
        states = full_states()
        states["sensor.living_humidity"] = ha_state(
            "sensor.living_humidity", "41", updated=ROOM_STALE
        )
        states["sensor.living_humidity_2"] = ha_state(
            "sensor.living_humidity_2", "45", updated=ROOM_STALE
        )

        observation = self.build(registry=merged, states=states)
        room = observation.room("living")

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(43.0, room.humidity)
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
