"""Application binding of one lighting profile to the core (HH-014.b).

Assembles the verified core service for one room: the lighting plugin,
a shadow file store, the closed transport and the HA input adapter. The
branch runs only in OFF or SHADOW mode — physical sending is closed by
construction — and never touches the legacy writer's authority. HA
entity ids stay inside the input adapter; the core sees resource ids.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..adapters.core.clock import SystemClock
from ..adapters.core.closed_transport import ClosedTransport
from ..adapters.core.control_store import FileControlStore
from ..adapters.core.room_lighting import (
    EntityBinding,
    RoomLightingInputAdapter,
)
from ..domain.core.models import (
    Authority,
    AutomaticScope,
    ManualHold,
    ModuleMode,
    Observation,
    ProposalKind,
    ResourceSpec,
    StabilityProfile,
)
from ..domain.lighting_plugin import LightingPlugin
from ..domain.room_lighting import RoomLightingConfig

if TYPE_CHECKING:
    from pathlib import Path
from .core.command_service import (
    CoreCommandService,
    CoreServiceError,
    ModuleEntry,
    ModuleManifest,
)

PROFILE_TIMEZONE = "Asia/Omsk"
PROFILE_SUNRISE = "08:00"
PROFILE_SUNSET = "18:00"
SHADOW_MANUAL_POLICY_REVISION = 1
UNCERTAIN_MANUAL_AUTHORITY = "manual_authority_unavailable"


@dataclass(frozen=True, slots=True)
class ManualHoldView:
    """Manual hold snapshot transferred from the shared runtime authority.

    The runtime (the only attribution authority) produces these views; the
    branch maps them onto core ``ManualHold`` records so the frame's
    ``resource_controls`` carry the real manual priority (AUD-LP-ISO01
    cycle 6). It grants no dispatch rights: the branch stays in SHADOW.
    """

    entity_id: str
    action: str
    source: str
    proven_at_utc_ms: int
    priority_until_utc_ms: int
    evidence_id: str


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
        store_path: str | Path,
        manual_holds_provider: (
            Callable[[], Mapping[str, ManualHoldView] | None] | None
        ) = None,
    ) -> None:
        # The shadow branch is intentionally Hass-independent: closed
        # transport, own store, own clock.
        self._branch_config = branch_config
        self._clock = SystemClock()
        self._transport = ClosedTransport()
        room_id = branch_config.room_id
        self._store_path = str(store_path)
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
        restore_kwargs = dict(
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
            episode_of_proposal=None,
            id_factory=self._id_factory(),
        )
        clock_trust = self._clock.wall_clock_trust()
        if self._store.load() is not None:
            # Restart: rebuild from the durable image (K-10) and keep the
            # SHADOW mode regardless of what was persisted.
            self._service = CoreCommandService.restore(
                **restore_kwargs, clock_trust=clock_trust, core_mode=ModuleMode.SHADOW
            )
        else:
            self._service = CoreCommandService(
                **restore_kwargs,
                core_mode=ModuleMode.SHADOW,
                initial_records=self._initial_records(branch_config.config),
            )
        self._event_counter = 0
        # Input serialization (AUD-LP-ISO01 cycle 6): the runtime delivers
        # port calls from worker threads (file I/O off the event loop), so
        # two events could otherwise interleave inside the branch and the
        # store. One reentrant lock makes every processing cycle atomic.
        self._lock = threading.RLock()
        # Set by the stop hook under the same lock: after it returns, no
        # thread — including one already queued in the executor — can
        # process or write (SEC-C6-R2-02).
        self._input_closed = False
        self._manual_holds_provider = manual_holds_provider
        self._target_bindings: dict[str, str] = {
            target.id: target.entity_id
            for target in branch_config.config.devices.light_targets
            if isinstance(target.entity_id, str)
        }

    # -- mode --------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def room_id(self) -> str:
        return self._branch_config.room_id

    def activate_shadow(self) -> None:
        with self._lock:
            if self._mode is not self.MODE_OFF:
                return
            self._mode = self.MODE_SHADOW
            self._process_room_event()

    def deactivate(self) -> None:
        with self._lock:
            self._mode = self.MODE_OFF

    def on_runtime_stop(self) -> None:
        """Called by the legacy runtime on stop: close the input and flush.

        The closed flag is set under the branch lock, so a worker thread
        already inside a processing cycle finishes it first; every later
        entry — including one still queued in the executor — is dropped
        by the guard in the input path (SEC-C6-R2-02)."""

        with self._lock:
            self._input_closed = True
            self._store.flush()

    def __call__(
        self, entity_id: str, old_state: object, new_state: object
    ) -> None:
        """Port entry: the runtime calls the branch directly."""

        self.handle_runtime_event(entity_id, old_state, new_state)

    # -- input -------------------------------------------------------------

    def handle_runtime_event(
        self, entity_id: str, old_state: object, new_state: object
    ) -> None:
        # The lock serializes events delivered from worker threads; the
        # observation is created under the lock, so its timestamps follow
        # the processing order (AUD-LP-ISO01 cycle 6).
        with self._lock:
            self._handle_runtime_event_locked(
                entity_id, old_state, new_state
            )

    def _handle_runtime_event_locked(
        self, entity_id: str, old_state: object, new_state: object
    ) -> None:
        if self._input_closed or self._mode is not self.MODE_SHADOW:
            return
        if entity_id == "__clock__":
            # Minute boundary: the schedule recalculation tick.
            self._process_room_event()
            return
        observation = self._input_adapter.observation(
            entity_id, old_state, new_state
        )
        if observation is None:
            return
        self._ingest(observation)

    def process_schedule_tick(self) -> None:
        with self._lock:
            if self._input_closed or self._mode is not self.MODE_SHADOW:
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
        self._apply_manual_holds()
        self._event_counter += 1
        self._service.process_module_event(
            self._module_id,
            f"{self._module_id}.{self._event_counter}",
        )
        # Even a no-demand cycle leaves an explanation for the room.
        for resource_id in self._resource_ids(self._branch_config.config):
            self._service.evaluate_resource(resource_id)

    def _apply_manual_holds(self) -> None:
        """Transfer the runtime's manual holds onto control records.

        Runs inside the branch lock before every decision cycle, so the
        frame's ``resource_controls`` show the real manual priority and
        arbitration explains its rejections (AUD-LP-ISO01 cycle 6).
        A ``None`` snapshot means the shared authority is unavailable:
        records become uncertain — the shadow decisions never widen
        automation rights because of it (fail closed).
        """

        if self._manual_holds_provider is None:
            return
        try:
            holds = self._manual_holds_provider()
        except Exception:  # noqa: BLE001 - isolation by design
            holds = None
        now_utc_ms = self._clock.now_utc_ms()
        for resource_id, entity_id in self._target_bindings.items():
            record = self._service.record(resource_id)
            if holds is None:
                # The shared authority is unavailable: fail closed. A
                # foreign uncertainty marker (e. g. clock_uncertain) is
                # another guard's reason and must survive this transfer
                # untouched (NR-1/SEC-C6-01).
                foreign = record.uncertainty_reason not in (
                    None,
                    UNCERTAIN_MANUAL_AUTHORITY,
                )
                if foreign and record.authority is Authority.UNCERTAIN:
                    continue
                self._service.update_control_record(
                    resource_id,
                    replace(
                        record,
                        authority=Authority.UNCERTAIN,
                        uncertainty_reason=(
                            record.uncertainty_reason
                            if foreign
                            else UNCERTAIN_MANUAL_AUTHORITY
                        ),
                    ),
                )
                continue
            view = holds.get(entity_id)
            if view is not None and view.priority_until_utc_ms > now_utc_ms:
                foreign_uncertainty = (
                    record.uncertainty_reason
                    not in (None, UNCERTAIN_MANUAL_AUTHORITY)
                )
                updated = replace(
                    record,
                    authority=Authority.MANUAL,
                    manual=ManualHold(
                        action_id=view.evidence_id
                        or f"shadow.hold.{view.proven_at_utc_ms}",
                        action=view.action,
                        source=view.source,
                        proven_at_utc_ms=view.proven_at_utc_ms,
                        priority_until_utc_ms=view.priority_until_utc_ms,
                        minimum_off_until_utc_ms=None,
                        policy_revision=SHADOW_MANUAL_POLICY_REVISION,
                    ),
                    # A manual hold dominates the decision anyway; a
                    # foreign uncertainty marker must survive it.
                    uncertainty_reason=(
                        record.uncertainty_reason
                        if foreign_uncertainty
                        else None
                    ),
                )
            elif (
                record.manual is not None
                or record.authority is Authority.MANUAL
                or record.uncertainty_reason == UNCERTAIN_MANUAL_AUTHORITY
            ):
                # The hold expired or the authority recovered: restore the
                # automatic baseline. Only markers set by this transfer
                # are cleared; foreign uncertainty (e. g. clock_uncertain
                # after an untrusted restart) stays untouched (LP-C6-R4).
                foreign_uncertainty = (
                    record.uncertainty_reason
                    not in (None, UNCERTAIN_MANUAL_AUTHORITY)
                )
                updated = replace(
                    record,
                    authority=(
                        Authority.UNCERTAIN
                        if foreign_uncertainty
                        else Authority.AUTOMATIC_ELIGIBLE
                    ),
                    automatic_scope=AutomaticScope.NORMAL,
                    manual=None,
                    uncertainty_reason=(
                        record.uncertainty_reason
                        if foreign_uncertainty
                        else None
                    ),
                )
            else:
                continue
            self._service.update_control_record(resource_id, updated)

    @staticmethod
    def _id_factory():
        state = {"n": 0}

        def factory() -> str:
            state["n"] += 1
            return str(state["n"])

        return factory
