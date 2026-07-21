"""Runtime wiring tests for the native Home Assistant observation path."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.climate_ha_observations import (
    ClimateHaEntityState,
)
from custom_components.hausman_hub.application.climate_runtime import (
    ClimateRuntime,
)
from custom_components.hausman_hub.application.configuration import (
    SafeConfiguration,
)
from custom_components.hausman_hub.climate_ha_state_view import (
    HomeAssistantClimateStateView,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateRegistry,
    ClimateRoom,
)
from custom_components.hausman_hub.domain.climate_isolation import ClimateRoomIsolationStatus
from custom_components.hausman_hub.domain.climate_trial import ClimateTrialStatus
from custom_components.hausman_hub.domain.climate_bridge import (
    ClimateBridgeMode,
    climate_bridge_target,
)
from custom_components.hausman_hub.domain.native_climate import (
    NativeClimateMode,
    NativeClimatePolicy,
)
from custom_components.hausman_hub.domain.contours import (
    ClimateComfortSettings,
    ClimateContourRoom,
    ClimateProfile,
    ClimateStrategy,
    ContourDefinition,
    ContourEngine,
    ContourKind,
    ContourMode,
    ContourRegistry,
)

NOW = 1_800_000_000_000


class PoisonBridge:
    """A bridge that fails loudly on any use and counts every attempt."""

    def __init__(self) -> None:
        self.fetch_count = 0

    async def async_fetch_state(self):
        self.fetch_count += 1
        raise RuntimeError("external climate module is gone")

    async def async_execute(self, plan):
        raise RuntimeError("external climate module is gone")


class MemoryStore:
    def __init__(self, value) -> None:
        self.value = value
        self.saved: list[object] = []

    async def async_load(self):
        return self.value

    async def async_save(self, value) -> None:
        self.saved.append(value)
        self.value = value


class CountingStateView:
    """In-memory native state view counting every read."""

    def __init__(self, states: dict[str, ClimateHaEntityState]) -> None:
        self._states = states
        self.reads = 0

    def entity_state(self, entity_id: str) -> ClimateHaEntityState | None:
        self.reads += 1
        return self._states.get(entity_id)


class RecordingTrialExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def async_execute(self, calls) -> int:
        self.calls.append(calls)
        return len(calls)


def ha_state(
    entity_id: str,
    state: str,
    attributes: dict[str, object] | None = None,
) -> ClimateHaEntityState:
    return ClimateHaEntityState(
        entity_id=entity_id,
        state=state,
        attributes=attributes or {},
        last_updated_ms=NOW,
    )


def native_registry(scope: ClimateControlScope) -> ClimateRegistry:
    return ClimateRegistry(
        rooms=(
            ClimateRoom(
                "living",
                "Living room",
                window_entity_id="binary_sensor.living_window",
            ),
        ),
        devices=(
            ClimateDevice(
                device_id="living_ac",
                name="Living AC",
                room_id="living",
                kind=ClimateDeviceKind.AIR_CONDITIONER,
                source_id="synthetic-ac-source-living",
                control_scope=scope,
                control_owner=ClimateControlOwner.CLIMATE_CORE,
                capabilities=(
                    ClimateCapability.POWER,
                    ClimateCapability.TARGET_TEMPERATURE,
                    ClimateCapability.HVAC_MODE,
                    ClimateCapability.FAN_MODE,
                ),
                endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.CONTROL,
                        "climate.living_ac",
                    ),
                ),
            ),
            ClimateDevice(
                device_id="living_temperature",
                name="Living temperature",
                room_id="living",
                kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
                source_id="synthetic-temperature-source-living",
                control_scope=ClimateControlScope.OBSERVED,
                control_owner=ClimateControlOwner.OBSERVED,
                capabilities=(),
                endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.TEMPERATURE,
                        "sensor.living_temperature",
                    ),
                ),
            ),
        ),
    )


def native_contours() -> ContourRegistry:
    settings = ClimateComfortSettings(
        target_temperature=24.0,
        target_humidity=45,
        strategy=ClimateStrategy.NORMAL,
    )
    return ContourRegistry(
        contours=(
            ContourDefinition(
                contour_id="climate",
                name="Климат",
                kind=ContourKind.CLIMATE,
                mode=ContourMode.AUTOMATIC,
                engine=ContourEngine.EXISTING_CLIMATE_CORE,
                rooms=(
                    ClimateContourRoom(
                        room_id="living",
                        device_ids=("living_ac", "living_temperature"),
                        day_profile=settings,
                        night_profile=settings,
                        active_profile=ClimateProfile.DAY,
                    ),
                ),
            ),
        ),
    )


def healthy_states(ac_state: str = "off") -> dict[str, ClimateHaEntityState]:
    entries = (
        ha_state("climate.living_ac", ac_state),
        ha_state("sensor.living_temperature", "26.5"),
        ha_state("binary_sensor.living_window", "off"),
    )
    return {entry.entity_id: entry for entry in entries}


def configuration(mode: ClimateBridgeMode) -> SafeConfiguration:
    return SafeConfiguration(
        mode="shadow",
        climate_bridge_mode=mode,
        climate_bridge_target=climate_bridge_target("http://127.0.0.1:1880"),
        climate_canary_room_id=(
            "living" if mode is ClimateBridgeMode.CANARY else None
        ),
    )


def runtime(
    mode: ClimateBridgeMode,
    view: CountingStateView,
    bridge: PoisonBridge,
    *,
    scope: ClimateControlScope = ClimateControlScope.CANARY,
    executor: RecordingTrialExecutor | None = None,
) -> ClimateRuntime:
    return ClimateRuntime(
        entry_id="entry",
        configuration=configuration(mode),
        registry_store=MemoryStore(native_registry(scope)),
        contour_store=MemoryStore(native_contours()),
        bridge_client=bridge,
        trial_executor=executor,
        ha_state_view=view,
        now_ms=lambda: NOW,
    )


class NativeObservationRuntimeTest(unittest.IsolatedAsyncioTestCase):
    """The internal pipeline runs from native states without the bridge."""

    async def test_native_pipeline_never_touches_the_bridge(self) -> None:
        bridge = PoisonBridge()
        view = CountingStateView(healthy_states(ac_state="cool"))
        instance = runtime(ClimateBridgeMode.MANAGED, view, bridge)
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        isolation = await instance.async_native_climate_isolation()

        self.assertIsNotNone(isolation)
        room = isolation.room("living")  # type: ignore[union-attr]
        self.assertIsNotNone(room)
        self.assertIs(room.status, ClimateRoomIsolationStatus.READY)
        self.assertEqual(fetches_after_start, bridge.fetch_count)

    async def test_trial_tick_executes_native_divergence_without_bridge(
        self,
    ) -> None:
        bridge = PoisonBridge()
        humidifier_registry = ClimateRegistry(
            rooms=(
                ClimateRoom(
                    "living",
                    "Living room",
                    window_entity_id="binary_sensor.living_window",
                ),
            ),
            devices=(
                ClimateDevice(
                    device_id="living_humidifier",
                    name="Living humidifier",
                    room_id="living",
                    kind=ClimateDeviceKind.HUMIDIFIER,
                    source_id="synthetic-humidifier-source-living",
                    control_scope=ClimateControlScope.CANARY,
                    control_owner=ClimateControlOwner.CLIMATE_CORE,
                    capabilities=(
                        ClimateCapability.POWER,
                        ClimateCapability.TARGET_HUMIDITY,
                    ),
                    endpoints=(
                        ClimateEndpoint(
                            ClimateEndpointRole.CONTROL,
                            "humidifier.living",
                        ),
                    ),
                ),
                ClimateDevice(
                    device_id="living_temperature",
                    name="Living temperature",
                    room_id="living",
                    kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
                    source_id="synthetic-temperature-source-living",
                    control_scope=ClimateControlScope.OBSERVED,
                    control_owner=ClimateControlOwner.OBSERVED,
                    capabilities=(),
                    endpoints=(
                        ClimateEndpoint(
                            ClimateEndpointRole.TEMPERATURE,
                            "sensor.living_temperature",
                        ),
                    ),
                ),
                ClimateDevice(
                    device_id="living_humidity",
                    name="Living humidity",
                    room_id="living",
                    kind=ClimateDeviceKind.HUMIDITY_SENSOR,
                    source_id="synthetic-humidity-source-living",
                    control_scope=ClimateControlScope.OBSERVED,
                    control_owner=ClimateControlOwner.OBSERVED,
                    capabilities=(),
                    endpoints=(
                        ClimateEndpoint(
                            ClimateEndpointRole.HUMIDITY,
                            "sensor.living_humidity",
                        ),
                    ),
                ),
            ),
        )
        settings = ClimateComfortSettings(
            target_temperature=24.0,
            target_humidity=45,
            strategy=ClimateStrategy.NORMAL,
        )
        humidifier_contours = ContourRegistry(
            contours=(
                ContourDefinition(
                    contour_id="climate",
                    name="Климат",
                    kind=ContourKind.CLIMATE,
                    mode=ContourMode.AUTOMATIC,
                    engine=ContourEngine.EXISTING_CLIMATE_CORE,
                    rooms=(
                        ClimateContourRoom(
                            room_id="living",
                            device_ids=(
                                "living_humidifier",
                                "living_temperature",
                                "living_humidity",
                            ),
                            day_profile=settings,
                            night_profile=settings,
                            active_profile=ClimateProfile.DAY,
                        ),
                    ),
                ),
            ),
        )
        entries = (
            ha_state("humidifier.living", "on"),
            ha_state("sensor.living_temperature", "24.0"),
            ha_state("sensor.living_humidity", "50"),
            ha_state("binary_sensor.living_window", "off"),
        )
        view = CountingStateView({entry.entity_id: entry for entry in entries})
        executor = RecordingTrialExecutor()
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(humidifier_registry),
            contour_store=MemoryStore(humidifier_contours),
            bridge_client=bridge,
            trial_executor=executor,
            ha_state_view=view,
            now_ms=lambda: NOW,
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        receipt = await instance.async_run_climate_trial()

        self.assertIsNotNone(receipt)
        self.assertIs(receipt.status, ClimateTrialStatus.APPLIED)  # type: ignore[union-attr]
        self.assertEqual(1, len(executor.calls))
        self.assertEqual(fetches_after_start, bridge.fetch_count)

    async def test_disabled_mode_ignores_the_native_view(self) -> None:
        bridge = PoisonBridge()
        view = CountingStateView(healthy_states(ac_state="cool"))
        instance = runtime(ClimateBridgeMode.DISABLED, view, bridge)
        await instance.async_start()

        isolation = await instance.async_native_climate_isolation()

        self.assertIsNotNone(isolation)
        room = isolation.room("living")  # type: ignore[union-attr]
        self.assertIsNotNone(room)
        self.assertIsNot(room.status, ClimateRoomIsolationStatus.READY)
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual(0, view.reads)

    async def test_broken_view_fails_closed_without_bridge_fallback(self) -> None:
        class BrokenView:
            def __init__(self) -> None:
                self.reads = 0

            def entity_state(self, entity_id: str) -> ClimateHaEntityState | None:
                self.reads += 1
                raise RuntimeError("state machine is broken")

        bridge = PoisonBridge()
        broken = BrokenView()
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(native_registry(ClimateControlScope.MANAGED)),
            contour_store=MemoryStore(native_contours()),
            bridge_client=bridge,
            ha_state_view=broken,
            now_ms=lambda: NOW,
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        isolation = await instance.async_native_climate_isolation()

        self.assertIsNotNone(isolation)
        room = isolation.room("living")  # type: ignore[union-attr]
        self.assertIsNotNone(room)
        self.assertIsNot(room.status, ClimateRoomIsolationStatus.READY)
        self.assertGreater(broken.reads, 0)
        self.assertEqual(fetches_after_start, bridge.fetch_count)

    async def test_preview_without_state_view_never_touches_the_bridge(
        self,
    ) -> None:
        bridge = PoisonBridge()
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(
                native_registry(ClimateControlScope.MANAGED)
            ),
            contour_store=MemoryStore(native_contours()),
            bridge_client=bridge,
            now_ms=lambda: NOW,
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        preview = await instance.async_native_climate_preview(
            NativeClimatePolicy(
                mode=NativeClimateMode.PREVIEW,
                room_id="living",
                target_temperature=24.0,
                target_humidity=45,
            )
        )

        self.assertEqual("unavailable", preview["status"])
        self.assertEqual(fetches_after_start, bridge.fetch_count)

    async def test_managed_rooms_without_state_view_deny_without_bridge(
        self,
    ) -> None:
        bridge = PoisonBridge()
        executor = RecordingTrialExecutor()
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(
                native_registry(ClimateControlScope.MANAGED)
            ),
            contour_store=MemoryStore(native_contours()),
            bridge_client=bridge,
            trial_executor=executor,
            now_ms=lambda: NOW,
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        receipts = await instance.async_run_climate_managed()

        self.assertEqual(1, len(receipts))
        self.assertIs(receipts[0].status, ClimateTrialStatus.DENIED)
        self.assertEqual([], executor.calls)
        self.assertEqual(fetches_after_start, bridge.fetch_count)


class _FakeState:
    def __init__(self, state: str, attributes: dict[str, object]) -> None:
        self.state = state
        self.attributes = attributes
        self.last_updated = _FakeTimestamp()


class _FakeTimestamp:
    def timestamp(self) -> float:
        return NOW / 1000


class _FakeStates:
    def __init__(self, values: dict[str, _FakeState]) -> None:
        self._values = values

    def get(self, entity_id: str) -> _FakeState | None:
        return self._values.get(entity_id)


class _FakeHass:
    def __init__(self, values: dict[str, _FakeState]) -> None:
        self.states = _FakeStates(values)


class HomeAssistantStateViewTest(unittest.TestCase):
    """The outer boundary exposes only bounded whitelisted state facts."""

    def test_view_bounds_and_whitelists_entity_state(self) -> None:
        hass = _FakeHass(
            {
                "climate.living_ac": _FakeState(
                    "cool",
                    {
                        "hvac_action": "cooling",
                        "temperature": 24.0,
                        "current_temperature": 26.5,
                        "fan_mode": "low",
                        "humidity": 45,
                        "friendly_name": "Living AC",
                        "min_temp": 16,
                        "supported_features": 409,
                    },
                ),
            }
        )
        view = HomeAssistantClimateStateView(hass)  # type: ignore[arg-type]

        state = view.entity_state("climate.living_ac")

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual("cool", state.state)
        self.assertEqual(
            {
                "hvac_action": "cooling",
                "temperature": 24.0,
                "current_temperature": 26.5,
                "fan_mode": "low",
                "humidity": 45,
            },
            dict(state.attributes),
        )
        self.assertEqual(NOW, state.last_updated_ms)

    def test_missing_and_oversized_states_stay_unobserved(self) -> None:
        hass = _FakeHass(
            {"sensor.weird": _FakeState("x" * 65, {})},
        )
        view = HomeAssistantClimateStateView(hass)  # type: ignore[arg-type]

        self.assertIsNone(view.entity_state("sensor.missing"))
        self.assertIsNone(view.entity_state("sensor.weird"))


if __name__ == "__main__":
    unittest.main()
