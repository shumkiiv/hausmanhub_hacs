"""Application binding of one lighting profile to the core (HH-014.b).

Assembles the verified core service for one room: the lighting plugin,
a shadow file store, the closed transport and the HA input adapter. The
branch runs only in OFF or SHADOW mode — physical sending is closed by
construction — and never touches the legacy writer's authority. HA
entity ids stay inside the input adapter; the core sees resource ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..adapters.core.clock import SystemClock
from ..adapters.core.closed_transport import ClosedTransport
from ..adapters.core.control_store import FileControlStore
from ..adapters.core.room_lighting import (
    EntityBinding,
    RoomLightingInputAdapter,
)
from ..domain.core.models import (
    ModuleMode,
    Observation,
    ResourceSpec,
    StabilityProfile,
)
from ..domain.lighting_plugin import LightingPlugin
from ..domain.room_lighting import RoomLightingConfig
from .core.command_service import (
    CoreCommandService,
    CoreServiceError,
    ModuleEntry,
    ModuleManifest,
)
from ..domain.core.models import ProposalKind

PROFILE_TIMEZONE = "Asia/Omsk"
PROFILE_SUNRISE = "08:00"
PROFILE_SUNSET = "18:00"


class CoreBranchError(CoreServiceError):
    pass


@dataclass(frozen=True, slots=True)
class CoreBranchConfig:
    """Frozen profile settings of one shadow branch."""

    room_id: str
    config: RoomLightingConfig
    timezone: str = PROFILE_TIMEZONE
    sunrise: str = PROFILE_SUNRISE
    sunset: str = PROFILE_SUNSET

    def resolved_timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception as exc:  # noqa: BLE001 - invalid profile data
            raise CoreBranchError(
                f"profile timezone is invalid: {self.timezone}"
            ) from exc

    def resolved_sunrise(self):
        from datetime import time as dt_time

        hour, minute = self.sunrise.split(":")
        return dt_time(int(hour), int(minute))

    def resolved_sunset(self):
        from datetime import time as dt_time

        hour, minute = self.sunset.split(":")
        return dt_time(int(hour), int(minute))


class RoomLightingCoreBranch:
    """Owns the shadow lifecycle of one profile (OFF/SHADOW only)."""

    MODE_OFF = "off"
    MODE_SHADOW = "shadow"

    def __init__(
        self,
        *,
        branch_config: CoreBranchConfig,
        store_path: Path,
    ) -> None:
        # The shadow branch is intentionally Hass-independent: closed
        # transport, own store, own clock.
        self._branch_config = branch_config
        self._clock = SystemClock()
        self._transport = ClosedTransport()
        room_id = branch_config.room_id
        self._store_path = Path(store_path)
        self._store = FileControlStore(self._store_path)
        self._bindings = self._bindings_from(branch_config.config)
        self._input_adapter = RoomLightingInputAdapter(
            bindings=self._bindings,
            clock=self._clock,
            source_epoch=f"{room_id}.shadow",
        )
        self._module = LightingPlugin(
            module_id=f"room_lighting.{room_id}",
            config=branch_config.config,
            timezone=branch_config.resolved_timezone(),
            sunrise=branch_config.resolved_sunrise(),
            sunset=branch_config.resolved_sunset(),
        )
        resource_ids = self._resource_ids(branch_config.config)
        manifest = ModuleManifest(
            module_id=self._module.module_id,
            api_version=1,
            config_schema_version=1,
            resource_roles=("light",),
            allowed_proposal_kinds=(ProposalKind.TARGET,),
            observation_subscriptions=resource_ids,
            policy_priority=50,
        )
        self._mode = self.MODE_OFF
        self._module_id = self._module.module_id
        self._counter = {"n": 0}
        self._service = CoreCommandService(
            clock=self._clock,
            store=self._store,
            adapter=self._input_adapter,
            transport=self._transport,
            specs=self._specs_from(branch_config.config),
            bindings=self._bindings_from(branch_config.config),
            modules={
                self._module.module_id: ModuleEntry(
                    manifest=manifest,
                    instance=self._module,
                )
            },
            stability_profiles={
                "default": StabilityProfile(
                    profile_id="default",
                    candidate_hold_ms=0,
                    min_on_ms=0,
                    min_off_ms=0,
                    min_mode_change_ms=0,
                )
            },
            config_revisions={
                resource_id: branch_config.config.version
                for resource_id in self._resource_ids(branch_config.config)
            },
            scope_revisions={},
            core_mode=ModuleMode.SHADOW,
            id_factory=self._id_factory(),
            initial_records=self._initial_records(branch_config.config),
        )
        self._event_counter = 0

    def _id_factory(self):
        state = {"n": 0}

        def factory() -> str:
            state["n"] += 1
            return str(state["n"])

        return factory

    # -- mode --------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def room_id(self) -> str:
        return self._branch_config.room_id

    def activate_shadow(self) -> None:
        if self._mode is not self.MODE_OFF:
            return
        self._mode = self.MODE_SHADOW
        self._process_room_event()

    def deactivate(self) -> None:
        self._mode = self.MODE_OFF

    # -- input -------------------------------------------------------------

    def handle_runtime_event(
        self, entity_id: str, old_state: object, new_state: object
    ) -> None:
        if self._mode is not self.MODE_SHADOW:
            return
        observation = self._input_adapter.observation(
            entity_id, old_state, new_state
        )
        if observation is None:
            return
        self._ingest(observation)

    def process_schedule_tick(self) -> None:
        if self._mode is not self.MODE_SHADOW:
            return
        self._process_room_event()

    # -- read model --------------------------------------------------------

    def decision_explanation(self):
        # The room explanation lives on its first light target resource.
        targets = self._branch_config.config.devices.light_targets
        if not targets:
            return None
        return self._service.explanation(targets[0].id)

    def resource_view(self, resource_id: str):
        return self._service.resource_view(resource_id)

    def physical_send_enabled(self) -> bool:
        return False

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _resource_ids(config: RoomLightingConfig) -> tuple[str, ...]:
        sensor_ids = tuple(sensor.id for sensor in config.devices.sensors)
        target_ids = tuple(target.id for target in config.devices.light_targets)
        return sensor_ids + target_ids

    @staticmethod
    def _specs_from(config: RoomLightingConfig) -> dict:
        specs: dict[str, ResourceSpec] = {}
        for target in config.devices.light_targets:
            specs[target.id] = ResourceSpec(
                resource_id=target.id,
                owner_module=f"room_lighting.{config.room_id}",
                adapter_id="ha.light",
                capability_profile_id="light-basic",
                binding_revision=config.version,
                manual_scopes=(config.room_id,),
            )
        for sensor in config.devices.sensors:
            specs[sensor.id] = ResourceSpec(
                resource_id=sensor.id,
                owner_module=f"room_lighting.{config.room_id}",
                adapter_id="ha.sensor",
                capability_profile_id="sensor-basic",
                binding_revision=config.version,
                manual_scopes=(config.room_id,),
            )
        return specs

    @staticmethod
    def _initial_records(config: RoomLightingConfig) -> dict:
        from ..domain.core.models import (
            Authority,
            AutomaticScope,
            ControlRecord,
        )

        records = {}
        for resource_id in RoomLightingCoreBranch._resource_ids(config):
            records[resource_id] = ControlRecord(
                resource_id=resource_id,
                control_revision=config.version,
                generation=1,
                binding_revision=config.version,
                authority=Authority.AUTOMATIC_ELIGIBLE,
                automatic_scope=AutomaticScope.NORMAL,
            )
        return records

    @staticmethod
    def _bindings_from(config: RoomLightingConfig) -> dict[str, EntityBinding]:
        bindings: dict[str, EntityBinding] = {}
        for sensor in config.devices.sensors:
            if sensor.entity_id is None:
                continue
            property_name = (
                "occupancy"
                if sensor.kind.value in ("presence", "motion")
                else "value"
            )
            bindings[sensor.entity_id] = EntityBinding(
                resource_id=sensor.id,
                property=property_name,
                unit="bool" if property_name == "occupancy" else "value",
            )
        for target in config.devices.light_targets:
            if target.entity_id is None:
                continue
            bindings[target.entity_id] = EntityBinding(
                resource_id=target.id,
                property="on",
                unit="bool",
            )
            if target.brightness:
                bindings[f"{target.entity_id}.brightness"] = EntityBinding(
                    resource_id=target.id,
                    property="brightness_pct",
                    unit="pct",
                )
        return bindings

    def _ingest(self, observation: Observation) -> None:
        self._service.ingest_observation(observation)
        self._process_room_event()

    def _process_room_event(self) -> None:
        self._event_counter += 1
        self._service.process_module_event(
            self._module_id,
            f"{self._module_id}.{self._event_counter}",
        )
        # Even a no-demand cycle leaves an explanation for the room.
        for resource_id in self._resource_ids(self._branch_config.config):
            self._service.evaluate_resource(resource_id)

    @staticmethod
    def _id_factory():
        state = {"n": 0}

        def factory() -> str:
            state["n"] += 1
            return str(state["n"])

        return factory
