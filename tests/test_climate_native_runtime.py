"""Runtime wiring tests for the native Home Assistant observation path."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import unittest
from unittest.mock import AsyncMock, patch
from typing import assert_never

from custom_components.hausman_hub.application.climate_ha_observations import (
    ClimateHaEntityState,
)
from custom_components.hausman_hub.application.climate_runtime import (
    ClimateRuntime,
    ClimateRuntimeUnavailable,
)
from custom_components.hausman_hub.application.configuration import (
    SafeConfiguration,
)
from custom_components.hausman_hub.application.contours import (
    with_applied_climate_schedule_profile,
    with_climate_schedule,
    with_climate_temporary_temperature,
)
from custom_components.hausman_hub.application.contour_apply import (
    ContourApplyStatus,
    ContourApplyViolation,
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
from custom_components.hausman_hub.domain.climate_ha_calls import ClimateHaService
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
        self.execute_count = 0

    async def async_fetch_state(self):
        self.fetch_count += 1
        raise RuntimeError("external climate module is gone")

    async def async_execute(self, plan):
        self.execute_count += 1
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

    def entity_catalog(self):
        from custom_components.hausman_hub.application.climate_native_setup import (
            ClimateHaCatalogEntry,
            ClimateHaEntityCatalog,
        )

        return ClimateHaEntityCatalog(
            entries=tuple(
                ClimateHaCatalogEntry(
                    entity_id=state.entity_id,
                    domain=state.entity_id.split(".", 1)[0],
                    state=state.state,
                    device_class=None,
                    supported_features=137,
                    friendly_name=state.entity_id,
                    available=state.state not in {"", "unavailable", "unknown"},
                    last_updated_ms=state.last_updated_ms,
                )
                for state in sorted(
                    self._states.values(), key=lambda state: state.entity_id
                )
            )
        )


class RecordingTrialExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def async_execute(self, calls) -> int:
        self.calls.append(calls)
        return len(calls)


class MutableStateView(CountingStateView):
    def __init__(self, states: dict[str, ClimateHaEntityState]) -> None:
        super().__init__(states)
        self.broken = False

    def entity_state(self, entity_id: str) -> ClimateHaEntityState | None:
        if self.broken:
            raise RuntimeError("native state view is broken")
        return super().entity_state(entity_id)


class ReflectingStrictExecutor:
    def __init__(
        self,
        view: MutableStateView,
        *,
        reflect_on_execute: bool = True,
        completed_count: int | None = None,
        break_view_after_execute: bool = False,
    ) -> None:
        self._view = view
        self._reflect_on_execute = reflect_on_execute
        self._completed_count = completed_count
        self._break_view_after_execute = break_view_after_execute
        self.calls: list[tuple[object, ...]] = []

    async def async_execute(self, calls) -> int:
        self.calls.append(calls)
        if self._reflect_on_execute:
            self.reflect_latest_calls()
        if self._break_view_after_execute:
            self._view.broken = True
        return len(calls) if self._completed_count is None else self._completed_count

    def reflect_latest_calls(self) -> None:
        for call in self.calls[-1]:
            current = self._view._states[call.entity_id]
            attributes = dict(current.attributes)
            match call.service:
                case ClimateHaService.CLIMATE_SET_HVAC_MODE:
                    state = call.hvac_mode.value
                case ClimateHaService.CLIMATE_SET_TEMPERATURE:
                    state = current.state
                    attributes["temperature"] = call.temperature
                case ClimateHaService.CLIMATE_SET_FAN_MODE:
                    state = current.state
                    attributes["fan_mode"] = call.fan_mode.value
                case ClimateHaService.HUMIDIFIER_TURN_ON:
                    state = "on"
                case ClimateHaService.HUMIDIFIER_TURN_OFF:
                    state = "off"
                case ClimateHaService.HUMIDIFIER_SET_HUMIDITY:
                    state = current.state
                    attributes["humidity"] = call.humidity
                case unreachable:
                    assert_never(unreachable)
            self._view._states[call.entity_id] = replace(
                current,
                state=state,
                attributes=attributes,
            )


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


def two_actuator_registry() -> ClimateRegistry:
    registry = native_registry(ClimateControlScope.MANAGED)
    return ClimateRegistry(
        rooms=registry.rooms,
        devices=(
            *registry.devices,
            ClimateDevice(
                device_id="living_humidifier",
                name="Living humidifier",
                room_id="living",
                kind=ClimateDeviceKind.HUMIDIFIER,
                source_id="synthetic-humidifier-source-living",
                control_scope=ClimateControlScope.MANAGED,
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
        ),
    )


def two_actuator_contours() -> ContourRegistry:
    contours = native_contours()
    contour = contours.contour("climate")
    if contour is None:
        raise AssertionError("native climate contour is unavailable")
    return replace(
        contours,
        contours=(
            replace(
                contour,
                rooms=(
                    replace(
                        contour.rooms[0],
                        device_ids=(
                            "living_ac",
                            "living_temperature",
                            "living_humidifier",
                        ),
                    ),
                ),
            ),
        ),
    )


def temporary_native_contours() -> ContourRegistry:
    scheduled = with_climate_schedule(
        native_contours(),
        enabled=True,
        day_start="07:00",
        night_start="23:00",
    )
    return with_applied_climate_schedule_profile(scheduled, ClimateProfile.DAY)


def temporary_two_actuator_contours() -> ContourRegistry:
    scheduled = with_climate_schedule(
        two_actuator_contours(),
        enabled=True,
        day_start="07:00",
        night_start="23:00",
    )
    return with_applied_climate_schedule_profile(scheduled, ClimateProfile.DAY)


def unrelated_broken_room_registry() -> ClimateRegistry:
    registry = native_registry(ClimateControlScope.MANAGED)
    return ClimateRegistry(
        rooms=(*registry.rooms, ClimateRoom("kids", "Kids room")),
        devices=(
            *registry.devices,
            ClimateDevice(
                device_id="kids_ac",
                name="Kids AC",
                room_id="kids",
                kind=ClimateDeviceKind.AIR_CONDITIONER,
                source_id="synthetic-ac-source-kids",
                control_scope=ClimateControlScope.MANAGED,
                control_owner=ClimateControlOwner.CLIMATE_CORE,
                capabilities=(
                    ClimateCapability.POWER,
                    ClimateCapability.TARGET_TEMPERATURE,
                    ClimateCapability.HVAC_MODE,
                    ClimateCapability.FAN_MODE,
                ),
                endpoints=(
                    ClimateEndpoint(ClimateEndpointRole.CONTROL, "climate.kids_ac"),
                ),
            ),
        ),
    )


def unrelated_broken_room_contours() -> ContourRegistry:
    contours = temporary_native_contours()
    contour = contours.contour("climate")
    if contour is None:
        raise AssertionError("native climate contour is unavailable")
    return replace(
        contours,
        contours=(
            replace(
                contour,
                rooms=(
                    *contour.rooms,
                    replace(
                        contour.rooms[0],
                        room_id="kids",
                        device_ids=("kids_ac",),
                    ),
                ),
            ),
        ),
    )


def healthy_states(ac_state: str = "cool") -> dict[str, ClimateHaEntityState]:
    entries = (
        ha_state("climate.living_ac", ac_state),
        ha_state("sensor.living_temperature", "24.0"),
        ha_state("binary_sensor.living_window", "off"),
    )
    return {entry.entity_id: entry for entry in entries}


def safe_stop_states() -> dict[str, ClimateHaEntityState]:
    states = healthy_states()
    window = states["binary_sensor.living_window"]
    states[window.entity_id] = replace(window, state="on")
    return states


def two_actuator_states() -> dict[str, ClimateHaEntityState]:
    states = safe_stop_states()
    humidifier = ha_state("humidifier.living", "on")
    states[humidifier.entity_id] = humidifier
    return states


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
        strict_ha_call_executor=executor,
        ha_state_view=view,
        now_ms=lambda: NOW,
    )


def native_application_runtime(
    mode: ClimateBridgeMode,
    view: MutableStateView,
    executor: ReflectingStrictExecutor,
    *,
    bridge_client: PoisonBridge | None,
    scope: ClimateControlScope = ClimateControlScope.MANAGED,
    contours: ContourRegistry | None = None,
    registry: ClimateRegistry | None = None,
) -> ClimateRuntime:
    return ClimateRuntime(
        entry_id="entry",
        configuration=configuration(mode),
        registry_store=MemoryStore(registry or native_registry(scope)),
        contour_store=MemoryStore(contours or native_contours()),
        bridge_client=bridge_client,
        strict_ha_call_executor=executor,
        ha_state_view=view,
        now_ms=lambda: NOW,
        local_now=lambda: datetime(2026, 7, 19, 12, 0),
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
            strict_ha_call_executor=executor,
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
            strict_ha_call_executor=executor,
            now_ms=lambda: NOW,
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        receipts = await instance.async_run_climate_managed()

        self.assertEqual(1, len(receipts))
        self.assertIs(receipts[0].status, ClimateTrialStatus.DENIED)
        self.assertEqual([], executor.calls)
        self.assertEqual(fetches_after_start, bridge.fetch_count)


class NativeApplicationRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_apply_works_without_a_bridge_client(self) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        contour_store = MemoryStore(native_contours())
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(
                native_registry(ClimateControlScope.MANAGED)
            ),
            contour_store=contour_store,
            bridge_client=None,
            strict_ha_call_executor=executor,
            ha_state_view=view,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await instance.async_start()

        receipt = await instance.async_apply_contour(
            {
                "request_id": "native-apply-no-bridge",
                "contour_id": "climate",
                "confirm": True,
            }
        )

        self.assertIs(receipt.status, ContourApplyStatus.CONFIRMED)
        self.assertEqual(1, len(executor.calls))
        self.assertEqual(receipt.command_count, receipt.accepted_count)
        self.assertEqual([], contour_store.saved)

    async def test_temporary_actions_work_without_a_bridge_dependency(self) -> None:
        scheduled = temporary_native_contours()
        overridden = with_climate_temporary_temperature(
            scheduled,
            room_id="living",
            target_temperature=23.5,
        )
        for action, target_temperature, contours in (
            ("set", 23.0, scheduled),
            ("clear", None, overridden),
        ):
            for bridge_name, bridge_client in (
                ("none", None),
                ("poison", PoisonBridge()),
            ):
                with self.subTest(action=action, bridge=bridge_name):
                    view = MutableStateView(safe_stop_states())
                    executor = ReflectingStrictExecutor(view)
                    instance = native_application_runtime(
                        ClimateBridgeMode.MANAGED,
                        view,
                        executor,
                        bridge_client=bridge_client,
                        contours=contours,
                    )
                    await instance.async_start()
                    bridge_baseline = (
                        None
                        if bridge_client is None
                        else (
                            bridge_client.fetch_count,
                            bridge_client.execute_count,
                        )
                    )

                    receipt = await instance.async_temporary_temperature(
                        {
                            "request_id": (
                                f"native-temporary-{action}-{bridge_name}"
                            ),
                            "contour_id": "climate",
                            "room_id": "living",
                            "action": action,
                            "target_temperature": target_temperature,
                            "confirm": True,
                        },
                        datetime(2026, 7, 19, 12, 0),
                    )

                    self.assertIs(receipt.status, ContourApplyStatus.CONFIRMED)
                    self.assertEqual(1, receipt.command_count)
                    self.assertEqual(1, receipt.accepted_count)
                    self.assertEqual(1, len(executor.calls))
                    if bridge_client is not None:
                        self.assertEqual(
                            bridge_baseline,
                            (bridge_client.fetch_count, bridge_client.execute_count),
                        )

    async def test_apply_never_uses_the_poison_bridge(self) -> None:
        bridge = PoisonBridge()
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            executor,
            bridge_client=bridge,
        )
        await instance.async_start()
        bridge_baseline = (bridge.fetch_count, bridge.execute_count)

        applied = await instance.async_apply_contour(
            {
                "request_id": "native-apply-poison",
                "contour_id": "climate",
                "confirm": True,
            }
        )
        self.assertEqual(
            bridge_baseline,
            (bridge.fetch_count, bridge.execute_count),
        )

        self.assertIs(applied.status, ContourApplyStatus.CONFIRMED)
        self.assertEqual(1, len(executor.calls))

    async def test_schedule_uses_native_application_without_the_poison_bridge(
        self,
    ) -> None:
        bridge = PoisonBridge()
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        contour_store = MemoryStore(temporary_native_contours())
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(native_registry(ClimateControlScope.MANAGED)),
            contour_store=contour_store,
            bridge_client=bridge,
            strict_ha_call_executor=executor,
            ha_state_view=view,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 23, 0),
        )
        await instance.async_start()
        bridge_baseline = (bridge.fetch_count, bridge.execute_count)

        receipt = await instance.async_run_climate_schedule(
            datetime(2026, 7, 19, 23, 0)
        )

        if receipt is None:
            self.fail("schedule did not produce a receipt")
        self.assertIs(receipt.status, ContourApplyStatus.CONFIRMED)
        self.assertEqual(
            bridge_baseline,
            (bridge.fetch_count, bridge.execute_count),
        )
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual(1, len(executor.calls))

    async def test_schedule_noops_before_persistence_or_observation_outside_managed(
        self,
    ) -> None:
        for mode in (
            ClimateBridgeMode.DISABLED,
            ClimateBridgeMode.SHADOW,
            ClimateBridgeMode.CANARY,
        ):
            with self.subTest(mode=mode):
                bridge = PoisonBridge()
                view = MutableStateView(safe_stop_states())
                executor = ReflectingStrictExecutor(view)
                contour_store = MemoryStore(temporary_native_contours())
                instance = ClimateRuntime(
                    entry_id="entry",
                    configuration=configuration(mode),
                    registry_store=MemoryStore(
                        native_registry(ClimateControlScope.MANAGED)
                    ),
                    contour_store=contour_store,
                    bridge_client=bridge,
                    strict_ha_call_executor=executor,
                    ha_state_view=view,
                    now_ms=lambda: NOW,
                    local_now=lambda: datetime(2026, 7, 19, 23, 0),
                )
                await instance.async_start()
                reads_after_start = view.reads
                fetches_after_start = bridge.fetch_count

                receipt = await instance.async_run_climate_schedule(
                    datetime(2026, 7, 19, 23, 0)
                )

                self.assertIsNone(receipt)
                self.assertEqual([], contour_store.saved)
                self.assertEqual(reads_after_start, view.reads)
                self.assertEqual(fetches_after_start, bridge.fetch_count)
                self.assertEqual(0, bridge.execute_count)
                self.assertEqual([], executor.calls)

    async def test_denied_schedule_persists_period_and_never_retries_a_partial_contour(
        self,
    ) -> None:
        base_registry = native_registry(ClimateControlScope.MANAGED)
        base_contours = temporary_native_contours()
        base_contour = base_contours.contour("climate")
        if base_contour is None:
            raise AssertionError("native climate contour is unavailable")
        kids_room = ClimateRoom(
            "kids",
            "Kids room",
            window_entity_id="binary_sensor.kids_window",
        )
        kids_ac = ClimateDevice(
            device_id="kids_ac",
            name="Kids AC",
            room_id="kids",
            kind=ClimateDeviceKind.AIR_CONDITIONER,
            source_id="synthetic-ac-source-kids",
            control_scope=ClimateControlScope.CANARY,
            control_owner=ClimateControlOwner.CLIMATE_CORE,
            capabilities=(
                ClimateCapability.POWER,
                ClimateCapability.TARGET_TEMPERATURE,
                ClimateCapability.HVAC_MODE,
                ClimateCapability.FAN_MODE,
            ),
            endpoints=(
                ClimateEndpoint(ClimateEndpointRole.CONTROL, "climate.kids_ac"),
            ),
        )
        kids_temperature = ClimateDevice(
            device_id="kids_temperature",
            name="Kids temperature",
            room_id="kids",
            kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
            source_id="synthetic-temperature-source-kids",
            control_scope=ClimateControlScope.OBSERVED,
            control_owner=ClimateControlOwner.OBSERVED,
            capabilities=(),
            endpoints=(
                ClimateEndpoint(
                    ClimateEndpointRole.TEMPERATURE,
                    "sensor.kids_temperature",
                ),
            ),
        )
        registry = ClimateRegistry(
            rooms=(*base_registry.rooms, kids_room),
            devices=(*base_registry.devices, kids_ac, kids_temperature),
        )
        kids_contour_room = ClimateContourRoom(
            room_id="kids",
            device_ids=("kids_ac", "kids_temperature"),
            day_profile=base_contour.rooms[0].day_profile,
            night_profile=base_contour.rooms[0].night_profile,
            active_profile=ClimateProfile.DAY,
        )
        contours = replace(
            base_contours,
            contours=(
                replace(
                    base_contour,
                    rooms=(*base_contour.rooms, kids_contour_room),
                ),
            ),
        )
        states = safe_stop_states()
        states.update(
            {
                "climate.kids_ac": ha_state("climate.kids_ac", "cool"),
                "sensor.kids_temperature": ha_state("sensor.kids_temperature", "24.0"),
                "binary_sensor.kids_window": ha_state("binary_sensor.kids_window", "off"),
            }
        )
        view = MutableStateView(states)
        executor = ReflectingStrictExecutor(view)
        contour_store = MemoryStore(contours)
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=contour_store,
            bridge_client=None,
            strict_ha_call_executor=executor,
            ha_state_view=view,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 23, 0),
        )
        await instance.async_start()

        receipt = await instance.async_run_climate_schedule(
            datetime(2026, 7, 19, 23, 0)
        )
        repeated = await instance.async_run_climate_schedule(
            datetime(2026, 7, 19, 23, 1)
        )

        if receipt is None:
            self.fail("schedule did not produce a receipt")
        self.assertIs(receipt.status, ContourApplyStatus.UNAVAILABLE)
        self.assertEqual(0, receipt.command_count)
        self.assertEqual([], executor.calls)
        self.assertEqual(1, len(contour_store.saved))
        stored = contour_store.value.contour("climate")
        if stored is None:
            self.fail("climate contour is unavailable")
        self.assertIs(stored.schedule.last_applied_profile, ClimateProfile.NIGHT)
        self.assertIsNone(repeated)

    async def test_schedule_confirms_delayed_convergence_without_reexecution(
        self,
    ) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view, reflect_on_execute=False)
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            executor,
            bridge_client=None,
            contours=temporary_native_contours(),
        )
        await instance.async_start()

        async def converge(_: float) -> None:
            executor.reflect_latest_calls()

        with patch(
            "custom_components.hausman_hub.application.climate_runtime.asyncio.sleep",
            new=AsyncMock(side_effect=converge),
        ) as sleep:
            receipt = await instance.async_run_climate_schedule(
                datetime(2026, 7, 19, 23, 0)
            )

        if receipt is None:
            self.fail("schedule did not produce a receipt")
        self.assertIs(receipt.status, ContourApplyStatus.CONFIRMED)
        self.assertEqual(1, sleep.await_count)
        self.assertEqual(1, len(executor.calls))

    async def test_schedule_timeout_stays_pending_without_timer_reexecution(
        self,
    ) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view, reflect_on_execute=False)
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            executor,
            bridge_client=None,
            contours=temporary_native_contours(),
        )
        await instance.async_start()

        with patch(
            "custom_components.hausman_hub.application.climate_runtime.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            receipt = await instance.async_run_climate_schedule(
                datetime(2026, 7, 19, 23, 0)
            )
        repeated = await instance.async_run_climate_schedule(
            datetime(2026, 7, 19, 23, 1)
        )

        if receipt is None:
            self.fail("schedule did not produce a receipt")
        self.assertIs(receipt.status, ContourApplyStatus.PENDING)
        self.assertEqual(("state_not_confirmed",), receipt.reasons)
        self.assertEqual(10, sleep.await_count)
        self.assertEqual(1, len(executor.calls))
        self.assertIsNone(repeated)

    async def test_non_managed_modes_deny_before_native_reads_or_bridge_calls(self) -> None:
        for mode in (
            ClimateBridgeMode.DISABLED,
            ClimateBridgeMode.SHADOW,
            ClimateBridgeMode.CANARY,
        ):
            with self.subTest(mode=mode):
                bridge = PoisonBridge()
                view = MutableStateView(safe_stop_states())
                executor = ReflectingStrictExecutor(view)
                instance = native_application_runtime(
                    mode,
                    view,
                    executor,
                    bridge_client=bridge,
                    contours=temporary_native_contours(),
                )
                await instance.async_start()
                reads_after_start = view.reads
                fetches_after_start = bridge.fetch_count

                with self.assertRaises(ClimateRuntimeUnavailable):
                    await instance.async_apply_contour(
                        {
                            "request_id": f"native-mode-apply-{mode.value}",
                            "contour_id": "climate",
                            "confirm": True,
                        }
                    )
                with self.assertRaises(ClimateRuntimeUnavailable):
                    await instance.async_temporary_temperature(
                        {
                            "request_id": f"native-mode-temp-{mode.value}",
                            "contour_id": "climate",
                            "room_id": "living",
                            "action": "set",
                            "target_temperature": 23.0,
                            "confirm": True,
                        },
                        datetime(2026, 7, 19, 12, 0),
                    )
                with self.assertRaises(ClimateRuntimeUnavailable):
                    await instance.async_temporary_temperature(
                        {
                            "request_id": f"native-mode-clear-{mode.value}",
                            "contour_id": "climate",
                            "room_id": "living",
                            "action": "clear",
                            "target_temperature": None,
                            "confirm": True,
                        },
                        datetime(2026, 7, 19, 12, 5),
                    )

                self.assertEqual(reads_after_start, view.reads)
                self.assertEqual(fetches_after_start, bridge.fetch_count)
                self.assertEqual(0, bridge.execute_count)
                self.assertEqual([], executor.calls)

    async def test_broken_native_view_denies_apply_without_executor_or_bridge(self) -> None:
        bridge = PoisonBridge()
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            executor,
            bridge_client=bridge,
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count
        view.broken = True

        receipt = await instance.async_apply_contour(
            {
                "request_id": "native-broken-view",
                "contour_id": "climate",
                "confirm": True,
            }
        )

        self.assertIs(receipt.status, ContourApplyStatus.UNAVAILABLE)
        self.assertEqual(("engine_rejected",), receipt.reasons)
        self.assertEqual(0, receipt.command_count)
        self.assertEqual([], executor.calls)
        self.assertEqual(fetches_after_start, bridge.fetch_count)
        self.assertEqual(0, bridge.execute_count)

    async def test_broken_native_view_denies_schedule_without_calls(self) -> None:
        bridge = PoisonBridge()
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            executor,
            bridge_client=bridge,
            contours=temporary_native_contours(),
        )
        await instance.async_start()
        bridge_baseline = (bridge.fetch_count, bridge.execute_count)
        view.broken = True

        receipt = await instance.async_run_climate_schedule(
            datetime(2026, 7, 19, 23, 0)
        )

        if receipt is None:
            self.fail("schedule did not produce a receipt")
        self.assertIs(receipt.status, ContourApplyStatus.UNAVAILABLE)
        self.assertEqual(("engine_rejected",), receipt.reasons)
        self.assertEqual(0, receipt.command_count)
        self.assertEqual([], executor.calls)
        self.assertEqual(
            bridge_baseline,
            (bridge.fetch_count, bridge.execute_count),
        )

    async def test_preflight_denial_never_executes_a_partial_contour_batch(self) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            executor,
            bridge_client=None,
            scope=ClimateControlScope.CANARY,
        )
        await instance.async_start()

        receipt = await instance.async_apply_contour(
            {
                "request_id": "native-preflight-denied",
                "contour_id": "climate",
                "confirm": True,
            }
        )

        self.assertIs(receipt.status, ContourApplyStatus.UNAVAILABLE)
        self.assertEqual(0, receipt.command_count)
        self.assertEqual([], executor.calls)

    async def test_zero_and_partial_executor_results_map_without_polling(self) -> None:
        for completed_count, status in (
            (0, ContourApplyStatus.UNAVAILABLE),
            (1, ContourApplyStatus.PARTIAL),
        ):
            with self.subTest(completed_count=completed_count):
                view = MutableStateView(
                    two_actuator_states()
                    if completed_count
                    else safe_stop_states()
                )
                executor = ReflectingStrictExecutor(
                    view,
                    completed_count=completed_count,
                )
                instance = native_application_runtime(
                    ClimateBridgeMode.MANAGED,
                    view,
                    executor,
                    bridge_client=None,
                    registry=(two_actuator_registry() if completed_count else None),
                    contours=(two_actuator_contours() if completed_count else None),
                )
                await instance.async_start()

                receipt = await instance.async_apply_contour(
                    {
                        "request_id": f"native-short-{completed_count}",
                        "contour_id": "climate",
                        "confirm": True,
                    }
                )

                self.assertIs(receipt.status, status)
                self.assertEqual(completed_count, receipt.accepted_count)
                self.assertEqual(("command_result_unavailable",), receipt.reasons)
                self.assertEqual(1, len(executor.calls))

    async def test_polling_confirms_delayed_native_convergence_without_reexecution(
        self,
    ) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view, reflect_on_execute=False)
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            executor,
            bridge_client=None,
        )
        await instance.async_start()

        async def converge(_: float) -> None:
            executor.reflect_latest_calls()

        with patch(
            "custom_components.hausman_hub.application.climate_runtime.asyncio.sleep",
            new=AsyncMock(side_effect=converge),
        ) as sleep:
            receipt = await instance.async_apply_contour(
                {
                    "request_id": "native-delayed-convergence",
                    "contour_id": "climate",
                    "confirm": True,
                }
            )

        self.assertIs(receipt.status, ContourApplyStatus.CONFIRMED)
        self.assertEqual(1, sleep.await_count)
        self.assertEqual(1, len(executor.calls))

    async def test_poll_timeout_and_broken_post_observation_stay_pending(self) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view, reflect_on_execute=False)
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            executor,
            bridge_client=None,
        )
        await instance.async_start()

        with patch(
            "custom_components.hausman_hub.application.climate_runtime.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            timed_out = await instance.async_apply_contour(
                {
                    "request_id": "native-poll-timeout",
                    "contour_id": "climate",
                    "confirm": True,
                }
            )

        self.assertIs(timed_out.status, ContourApplyStatus.PENDING)
        self.assertEqual(("state_not_confirmed",), timed_out.reasons)
        self.assertEqual(10, sleep.await_count)
        self.assertEqual(1, len(executor.calls))

        broken_view = MutableStateView(safe_stop_states())
        broken_executor = ReflectingStrictExecutor(
            broken_view,
            break_view_after_execute=True,
        )
        broken = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            broken_view,
            broken_executor,
            bridge_client=None,
        )
        await broken.async_start()

        unavailable_verification = await broken.async_apply_contour(
            {
                "request_id": "native-verification-unavailable",
                "contour_id": "climate",
                "confirm": True,
            }
        )

        self.assertIs(unavailable_verification.status, ContourApplyStatus.PENDING)
        self.assertEqual(
            ("verification_unavailable",),
            unavailable_verification.reasons,
        )
        self.assertEqual(1, len(broken_executor.calls))

    async def test_duplicate_reobserves_and_promotes_without_reexecution(self) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view, reflect_on_execute=False)
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            executor,
            bridge_client=None,
        )
        await instance.async_start()
        request = {
            "request_id": "native-duplicate-promotion",
            "contour_id": "climate",
            "confirm": True,
        }

        with patch(
            "custom_components.hausman_hub.application.climate_runtime.asyncio.sleep",
            new=AsyncMock(),
        ):
            pending = await instance.async_apply_contour(request)
        executor.reflect_latest_calls()
        promoted = await instance.async_apply_contour(dict(request))

        self.assertIs(pending.status, ContourApplyStatus.PENDING)
        self.assertIs(promoted.status, ContourApplyStatus.CONFIRMED)
        self.assertEqual(1, len(executor.calls))
        with self.assertRaises(ContourApplyViolation):
            await instance.async_temporary_temperature(
                {
                    "request_id": request["request_id"],
                    "contour_id": "climate",
                    "room_id": "living",
                    "action": "set",
                    "target_temperature": 23.0,
                    "confirm": True,
                },
                datetime(2026, 7, 19, 12, 0),
            )

    async def test_duplicate_clear_pending_reobserves_without_a_second_write_or_batch(
        self,
    ) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        contour_store = MemoryStore(temporary_native_contours())
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(
                native_registry(ClimateControlScope.MANAGED)
            ),
            contour_store=contour_store,
            bridge_client=None,
            strict_ha_call_executor=executor,
            ha_state_view=view,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await instance.async_start()
        await instance.async_temporary_temperature(
            {
                "request_id": "duplicate-clear-pending-set",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 23.0,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        current = view._states["climate.living_ac"]
        view._states[current.entity_id] = replace(current, state="cool")
        executor._reflect_on_execute = False
        request = {
            "request_id": "duplicate-clear-pending",
            "contour_id": "climate",
            "room_id": "living",
            "action": "clear",
            "target_temperature": None,
            "confirm": True,
        }

        with patch(
            "custom_components.hausman_hub.application.climate_runtime.asyncio.sleep",
            new=AsyncMock(),
        ):
            first = await instance.async_temporary_temperature(
                request,
                datetime(2026, 7, 19, 12, 5),
            )
        duplicate = await instance.async_temporary_temperature(
            dict(request),
            datetime(2026, 7, 19, 12, 6),
        )

        self.assertIs(first.status, ContourApplyStatus.PENDING)
        self.assertIs(duplicate.status, ContourApplyStatus.PENDING)
        self.assertEqual(2, len(contour_store.saved))
        self.assertEqual(2, len(executor.calls))

    async def test_duplicate_clear_unavailable_reobserves_without_a_second_batch(
        self,
    ) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        contour_store = MemoryStore(temporary_native_contours())
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(
                native_registry(ClimateControlScope.MANAGED)
            ),
            contour_store=contour_store,
            bridge_client=None,
            strict_ha_call_executor=executor,
            ha_state_view=view,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await instance.async_start()
        await instance.async_temporary_temperature(
            {
                "request_id": "duplicate-clear-unavailable-set",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 23.0,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        current = view._states["climate.living_ac"]
        view._states[current.entity_id] = replace(current, state="cool")
        executor._reflect_on_execute = False
        executor._completed_count = 0
        request = {
            "request_id": "duplicate-clear-unavailable",
            "contour_id": "climate",
            "room_id": "living",
            "action": "clear",
            "target_temperature": None,
            "confirm": True,
        }

        first = await instance.async_temporary_temperature(
            request,
            datetime(2026, 7, 19, 12, 5),
        )
        duplicate = await instance.async_temporary_temperature(
            dict(request),
            datetime(2026, 7, 19, 12, 6),
        )

        self.assertIs(first.status, ContourApplyStatus.UNAVAILABLE)
        self.assertIs(duplicate.status, ContourApplyStatus.UNAVAILABLE)
        self.assertEqual(2, len(contour_store.saved))
        self.assertEqual(2, len(executor.calls))

    async def test_duplicate_clear_promotes_pending_receipt_without_reexecution(
        self,
    ) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        contour_store = MemoryStore(temporary_native_contours())
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(
                native_registry(ClimateControlScope.MANAGED)
            ),
            contour_store=contour_store,
            bridge_client=None,
            strict_ha_call_executor=executor,
            ha_state_view=view,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await instance.async_start()
        await instance.async_temporary_temperature(
            {
                "request_id": "duplicate-clear-promote-set",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 23.0,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        current = view._states["climate.living_ac"]
        view._states[current.entity_id] = replace(current, state="cool")
        executor._reflect_on_execute = False
        request = {
            "request_id": "duplicate-clear-promote",
            "contour_id": "climate",
            "room_id": "living",
            "action": "clear",
            "target_temperature": None,
            "confirm": True,
        }

        with patch(
            "custom_components.hausman_hub.application.climate_runtime.asyncio.sleep",
            new=AsyncMock(),
        ):
            pending = await instance.async_temporary_temperature(
                request,
                datetime(2026, 7, 19, 12, 5),
            )
        executor.reflect_latest_calls()
        promoted = await instance.async_temporary_temperature(
            dict(request),
            datetime(2026, 7, 19, 12, 6),
        )

        self.assertIs(pending.status, ContourApplyStatus.PENDING)
        self.assertIs(promoted.status, ContourApplyStatus.CONFIRMED)
        self.assertEqual(2, len(contour_store.saved))
        self.assertEqual(2, len(executor.calls))

    async def test_duplicate_clear_partial_reobserves_without_a_second_batch(self) -> None:
        view = MutableStateView(two_actuator_states())
        executor = ReflectingStrictExecutor(view)
        contour_store = MemoryStore(temporary_two_actuator_contours())
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(two_actuator_registry()),
            contour_store=contour_store,
            bridge_client=None,
            strict_ha_call_executor=executor,
            ha_state_view=view,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await instance.async_start()
        await instance.async_temporary_temperature(
            {
                "request_id": "duplicate-clear-partial-set",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 23.0,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        for entity_id, state in view._states.items():
            if entity_id in {"climate.living_ac", "humidifier.living"}:
                view._states[entity_id] = replace(
                    state,
                    state="cool" if entity_id.startswith("climate.") else "on",
                )
        executor._reflect_on_execute = False
        executor._completed_count = 1
        request = {
            "request_id": "duplicate-clear-partial",
            "contour_id": "climate",
            "room_id": "living",
            "action": "clear",
            "target_temperature": None,
            "confirm": True,
        }

        first = await instance.async_temporary_temperature(
            request,
            datetime(2026, 7, 19, 12, 5),
        )
        duplicate = await instance.async_temporary_temperature(
            dict(request),
            datetime(2026, 7, 19, 12, 6),
        )

        self.assertIs(first.status, ContourApplyStatus.PARTIAL)
        self.assertIs(duplicate.status, ContourApplyStatus.PARTIAL)
        self.assertEqual(2, len(contour_store.saved))
        self.assertEqual(2, len(executor.calls))

    async def test_temporary_set_and_clear_ignore_an_unrelated_broken_room(self) -> None:
        view = MutableStateView(safe_stop_states())
        executor = ReflectingStrictExecutor(view)
        contour_store = MemoryStore(unrelated_broken_room_contours())
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(unrelated_broken_room_registry()),
            contour_store=contour_store,
            bridge_client=None,
            strict_ha_call_executor=executor,
            ha_state_view=view,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await instance.async_start()

        set_receipt = await instance.async_temporary_temperature(
            {
                "request_id": "unrelated-broken-set",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 23.0,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        clear_receipt = await instance.async_temporary_temperature(
            {
                "request_id": "unrelated-broken-clear",
                "contour_id": "climate",
                "room_id": "living",
                "action": "clear",
                "target_temperature": None,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 5),
        )

        self.assertIs(set_receipt.status, ContourApplyStatus.CONFIRMED)
        self.assertIs(clear_receipt.status, ContourApplyStatus.CONFIRMED)
        self.assertEqual(2, len(contour_store.saved))


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


class NativeProjectionSwitchTest(unittest.IsolatedAsyncioTestCase):
    """36e2 poison acceptance: managed projections never touch the bridge."""

    async def test_managed_projections_run_without_the_bridge(self) -> None:
        bridge = PoisonBridge()
        view = MutableStateView(safe_stop_states())
        instance = native_application_runtime(
            ClimateBridgeMode.MANAGED,
            view,
            ReflectingStrictExecutor(view),
            bridge_client=bridge,
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        public = await instance.async_public_snapshot()
        contours = await instance.async_contours_snapshot()
        preview = await instance.async_contour_apply_preview()
        readiness = await instance.async_readiness()
        admin = await instance.async_admin_import_snapshot()

        self.assertEqual("hausman-hub-home", public["contract"]["name"])
        self.assertEqual(12, public["contract"]["version"])
        self.assertEqual("living", public["rooms"][0]["id"])  # type: ignore[index]
        self.assertEqual("hausman-hub-contours", contours["contract"]["name"])
        self.assertEqual(
            "hausman-hub-contour-apply-preview", preview["contract"]["name"]
        )
        self.assertEqual("ready", preview["status"])
        self.assertEqual(
            "hausman-hub-climate-readiness", readiness["contract"]["name"]
        )
        self.assertEqual("ready", readiness["status"])
        self.assertEqual(
            "synthetic-ac-source-living",
            admin["candidates"][0]["source_id"],  # type: ignore[index]
        )
        self.assertEqual(fetches_after_start, bridge.fetch_count)
        self.assertEqual(0, bridge.execute_count)

    async def test_disabled_projections_never_touch_the_bridge(self) -> None:
        bridge = PoisonBridge()
        view = CountingStateView(healthy_states(ac_state="cool"))
        instance = runtime(
            ClimateBridgeMode.DISABLED,
            view,
            bridge,
            scope=ClimateControlScope.MANAGED,
        )
        await instance.async_start()

        contours = await instance.async_contours_snapshot()
        readiness = await instance.async_readiness()
        for call in (
            instance.async_public_snapshot,
            instance.async_admin_import_snapshot,
            instance.async_contour_apply_preview,
        ):
            with self.assertRaises(ClimateRuntimeUnavailable):
                await call()

        self.assertEqual("unavailable", contours["contours"][0]["status"])  # type: ignore[index]
        self.assertEqual("disabled", readiness["status"])
        self.assertEqual(["bridge_disabled"], readiness["reasons"])
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual(0, bridge.execute_count)

    async def test_shadow_projections_still_read_the_bridge(self) -> None:
        bridge = PoisonBridge()
        view = CountingStateView(healthy_states(ac_state="cool"))
        instance = runtime(
            ClimateBridgeMode.SHADOW,
            view,
            bridge,
            scope=ClimateControlScope.CANARY,
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        contours = await instance.async_contours_snapshot()

        self.assertEqual("unavailable", contours["contours"][0]["status"])  # type: ignore[index]
        self.assertGreater(bridge.fetch_count, fetches_after_start)

    async def test_managed_projections_fail_closed_without_a_state_view(
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
            strict_ha_call_executor=None,
            ha_state_view=None,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        for call in (
            instance.async_public_snapshot,
            instance.async_admin_import_snapshot,
        ):
            with self.assertRaises(ClimateRuntimeUnavailable):
                await call()
        contours = await instance.async_contours_snapshot()
        readiness = await instance.async_readiness()

        self.assertEqual("unavailable", contours["contours"][0]["status"])  # type: ignore[index]
        self.assertEqual("unavailable", readiness["status"])
        self.assertEqual(["climate_state_unavailable"], readiness["reasons"])
        self.assertEqual(fetches_after_start, bridge.fetch_count)
        self.assertEqual(0, bridge.execute_count)

    async def test_wizards_never_touch_the_bridge_in_any_mode(self) -> None:
        for mode in (
            ClimateBridgeMode.MANAGED,
            ClimateBridgeMode.SHADOW,
            ClimateBridgeMode.CANARY,
        ):
            with self.subTest(mode=mode):
                bridge = PoisonBridge()
                view = MutableStateView(safe_stop_states())
                instance = native_application_runtime(
                    mode,
                    view,
                    ReflectingStrictExecutor(view),
                    bridge_client=bridge,
                )
                await instance.async_start()
                fetches_after_start = bridge.fetch_count

                await instance.async_registry_import_snapshot()
                await instance.async_climate_setup_options()
                await instance.async_current_contour_setup()

                self.assertEqual(fetches_after_start, bridge.fetch_count)
                self.assertEqual(0, bridge.execute_count)

    async def test_wizards_fail_closed_without_a_state_view(self) -> None:
        bridge = PoisonBridge()
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(
                native_registry(ClimateControlScope.MANAGED)
            ),
            contour_store=MemoryStore(native_contours()),
            bridge_client=bridge,
            ha_state_view=None,
            now_ms=lambda: NOW,
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        calls = (
            (instance.async_registry_import_snapshot, ()),
            (instance.async_climate_setup_options, ()),
            (instance.async_current_contour_setup, ()),
            (instance.async_create_contour_draft, ({},)),
            (instance.async_validate_contour_draft, ({},)),
        )
        for call, args in calls:
            with self.subTest(call=call.__name__):
                try:
                    await call(*args)
                except ClimateRuntimeUnavailable:
                    pass
                except Exception as error:
                    raise AssertionError(
                        f"{call.__name__} raised {type(error).__name__}"
                    ) from error

        self.assertEqual(fetches_after_start, bridge.fetch_count)
        self.assertEqual(0, bridge.execute_count)

    async def test_managed_draft_save_binds_native_candidate_without_bridge(
        self,
    ) -> None:
        bridge = PoisonBridge()
        view = MutableStateView(safe_stop_states())
        contour_store = MemoryStore(ContourRegistry())
        registry_store = MemoryStore(
            ClimateRegistry(
                rooms=(
                    ClimateRoom(
                        "living",
                        "Living room",
                        window_entity_id="binary_sensor.living_window",
                    ),
                ),
                devices=(),
            )
        )
        instance = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=registry_store,
            contour_store=contour_store,
            bridge_client=bridge,
            strict_ha_call_executor=ReflectingStrictExecutor(view),
            ha_state_view=view,
            now_ms=lambda: NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await instance.async_start()
        fetches_after_start = bridge.fetch_count

        options = await instance.async_climate_setup_options()
        draft = await instance.async_create_contour_draft(
            {
                "snapshot_revision": options["snapshot_revision"],
                "name": "Климат",
                "mode": "automatic",
                "rooms": [
                    {
                        "room_id": "living",
                        "target_temperature": 24.0,
                        "target_humidity": 45,
                        "strategy": "normal",
                        "devices": [
                            {
                                "candidate_id": "candidate_0002",
                                "type": "air_conditioner",
                            }
                        ],
                    }
                ],
            }
        )
        validation = await instance.async_validate_contour_draft(draft)
        receipt = await instance.async_save_contour_draft(draft)

        self.assertTrue(validation["save_allowed"])
        self.assertEqual("saved", receipt["status"])
        saved_registry = registry_store.saved[-1]
        device = saved_registry.devices[0]
        self.assertEqual(
            "hausmanhub-native-climate.living_ac", device.source_id
        )
        self.assertEqual("living", device.room_id)
        self.assertEqual("living_air_conditioner", device.device_id)
        self.assertEqual(fetches_after_start, bridge.fetch_count)
        self.assertEqual(0, bridge.execute_count)
