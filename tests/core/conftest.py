"""Isolated FIRST stand: FIRST-LIGHT-1 profile (FIRST-DELIVERY, section 3).

Only the external environment is substituted here: time, durable store and
transport. Permission, queue, arbitration, prepare/normalize and the
operation lifecycle are production code of the candidate. MemoryStore is a
test double and never claims to be durable storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping

import pytest

from custom_components.hausman_hub.adapters.core.control_store import FileControlStore
from custom_components.hausman_hub.adapters.core.light import LightDeviceAdapter
from custom_components.hausman_hub.application.core.command_service import (
    CapabilityProfile,
    CoreCommandService,
    ModuleEntry,
    PropertyRule,
    StorageUnavailable,
    TransportOutcome,
    TransportStatus,
)
from custom_components.hausman_hub.domain.core.models import (
    Authority,
    AutomaticScope,
    ControlRecord,
    Evaluation,
    InputFrame,
    LightStateTarget,
    ModuleManifest,
    Proposal,
    ProposalKind,
    ResourceSpec,
    StabilityProfile,
)

# --- FIRST-LIGHT-1 stand constants (ids lowercase per K-02) ---------
START_UTC_MS = 0
BOOT_ID = "test.boot1"
CONTROL_REVISION_START = 12
GENERATION_START = 7
BINDING_REVISION_START = 3
CONFIG_REVISION_START = 2
SCOPE_START_REVISION = 4
MAIN_SCOPE = "test.scope"
OTHER_SCOPE = "test.other_scope"
MAIN_RESOURCE = "test.main"
MAIN_ENTITY = "light.test_main"
MAIN_ALIAS = "light.test_main_alias"
OTHER_RESOURCE = "test.other"
OTHER_ENTITY = "light.test_other"
OWNER_MODULE = "test.lighting"
OWNER_PROPOSAL = "test.light_on"
OWNER_EPISODE = "test.episode1"
OTHER_MODULE = "test.other_module"
TTL_MS = 5000
CONFIRMATION_TIMEOUT_MS = 10000
BRIGHTNESS_TOLERANCE = Decimal("0.5")
MAX_AGE_MS = 60000
MAIN_PRIORITY = "manual_test.priority"
BASE_TARGET_ON40 = LightStateTarget(
    on=True, brightness_pct=Decimal(40), color_temperature_k=None
)
TARGET_OFF = LightStateTarget(
    on=False, brightness_pct=None, color_temperature_k=None
)


class FakeClock:
    """Deterministic virtual clock; boot id matches the stand passport."""

    def __init__(self, boot_id: str = BOOT_ID) -> None:
        self.mono_ms = 0
        self.utc_ms = START_UTC_MS
        self.boot = boot_id
        self.platform_sync_confirmed = True

    def now_mono_ms(self) -> int:
        return self.mono_ms

    def now_utc_ms(self) -> int:
        return self.utc_ms

    def boot_id(self) -> str:
        return self.boot

    def wall_clock_trust(self):
        from custom_components.hausman_hub.domain.core.recovery import (
            ClockTrust,
        )

        return ClockTrust(
            trusted=self.platform_sync_confirmed,
            reason=None if self.platform_sync_confirmed else "test",
        )

    def advance(self, delta_ms: int) -> None:
        self.mono_ms += delta_ms
        self.utc_ms += delta_ms


class MemoryStore:
    """Test double of the ControlStore port; not durable storage (S-04)."""

    def __init__(self) -> None:
        self.commit_revision = 0
        self.images: list[Mapping] = []
        self.fail_next_commit = False
        self.fail_at_commit_revisions: set[int] = set()

    @property
    def state(self) -> Mapping | None:
        return self.images[-1] if self.images else None

    def load(self) -> Mapping | None:
        return self.state

    def healthy(self) -> bool:
        return True

    def commit(self, expected_commit_revision: int, next_state: Mapping) -> None:
        failing = self.fail_next_commit or (
            self.commit_revision + 1 in self.fail_at_commit_revisions
        )
        if failing:
            self.fail_next_commit = False
            raise StorageUnavailable("test store commit failure")
        if expected_commit_revision != self.commit_revision:
            raise AssertionError("commit CAS mismatch in test store")
        self.images.append(next_state)
        self.commit_revision += 1


class FakeTransport:
    """Records every physical call; reception is immediate (section 3)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_next = False
        self.prove_not_sent_on_fail = False
        self.on_call = None

    def call(self, prepared) -> TransportOutcome:
        self.calls.append(
            (
                prepared.domain,
                prepared.service,
                prepared.entity_id,
                dict(prepared.data),
            )
        )
        if self.on_call is not None:
            self.on_call()
        if self.fail_next:
            self.fail_next = False
            return TransportOutcome(
                status=TransportStatus.ERROR,
                error_detail="test transport failure",
                proven_not_sent=self.prove_not_sent_on_fail,
            )
        return TransportOutcome(status=TransportStatus.ACCEPTED)


class ScriptedLightModule:
    """Pure test module: emits its configured target on a test event (K-04)."""

    def __init__(self, module_id: str, target) -> None:
        self._module_id = module_id
        self._target = target
        self.pending: list[tuple[str, Proposal]] = []
        self.input_revision = 0
        self.memory_seen: object = None

    def queue_proposal(self, proposal: Proposal) -> None:
        self.pending.append((self._module_id, proposal))

    def validate_config(self, config, catalog):
        expected_keys = {"resource_id", "target"}
        if set(config) != expected_keys:
            return ("bad_config",)
        return ()

    def evaluate(self, frame: InputFrame, previous_memory: object) -> Evaluation:
        self.memory_seen = previous_memory
        self.input_revision += 1
        now = frame.now_mono_ms
        proposals = tuple(
            Proposal(
                proposal_id=proposal.proposal_id,
                module_id=self._module_id,
                resource_id=proposal.resource_id,
                kind=proposal.kind,
                target=proposal.target,
                policy_priority=proposal.policy_priority,
                created_at_mono_ms=now,
                valid_until_mono_ms=now + TTL_MS,
                input_revision=self.input_revision,
                config_revision=proposal.config_revision,
                reason_code=proposal.reason_code,
                evidence_refs=proposal.evidence_refs,
            )
            for _, proposal in self.pending
        )
        self.pending = []
        return Evaluation(
            module_id=self._module_id,
            config_revision=CONFIG_REVISION_START,
            input_revision=self.input_revision,
            proposals=proposals,
        )


def light_profile(profile_id: str = "first-light-1") -> CapabilityProfile:
    return CapabilityProfile(
        profile_id=profile_id,
        version=1,
        source="first-stand",
        properties={
            "on": PropertyRule(
                property="on",
                unit="bool",
                readable=True,
                writable=True,
                min_value=None,
                max_value=None,
                step=None,
                allowed_values=("true", "false"),
                report_mode="on_change",
                state_source="test.z2m/device_report",
                max_age_ms=MAX_AGE_MS,
                confirmation="post_dispatch_report",
                tolerance=None,
                confirmation_timeout_ms=CONFIRMATION_TIMEOUT_MS,
                dispatch_timeout_ms=1000,
                readback_delay_ms=None,
            ),
            "brightness_pct": PropertyRule(
                property="brightness_pct",
                unit="pct",
                readable=True,
                writable=True,
                min_value=Decimal(1),
                max_value=Decimal(100),
                step=Decimal(1),
                allowed_values=None,
                report_mode="on_change",
                state_source="test.z2m/device_report",
                max_age_ms=MAX_AGE_MS,
                confirmation="post_dispatch_report",
                tolerance=BRIGHTNESS_TOLERANCE,
                confirmation_timeout_ms=CONFIRMATION_TIMEOUT_MS,
                dispatch_timeout_ms=1000,
                readback_delay_ms=None,
            ),
            # Battery reports exist on the device; they confirm nothing and
            # refresh only their own property age (S-T02-like isolation).
            "battery": PropertyRule(
                property="battery",
                unit="pct",
                readable=True,
                writable=False,
                min_value=Decimal(0),
                max_value=Decimal(100),
                step=Decimal(1),
                allowed_values=None,
                report_mode="on_change",
                state_source="test.z2m/battery",
                max_age_ms=MAX_AGE_MS,
                confirmation="none",
                tolerance=None,
                confirmation_timeout_ms=CONFIRMATION_TIMEOUT_MS,
                dispatch_timeout_ms=1000,
                readback_delay_ms=None,
            ),
        },
    )


def main_spec(arbitration_mode) -> ResourceSpec:
    return ResourceSpec(
        resource_id=MAIN_RESOURCE,
        owner_module=OWNER_MODULE,
        adapter_id="test.light_adapter",
        capability_profile_id="first-light-1",
        binding_revision=BINDING_REVISION_START,
        aliases=(MAIN_ALIAS,),
        power_dependencies=("test.power",),
        manual_scopes=(MAIN_SCOPE,),
        arbitration=arbitration_mode,
        idle_target=TARGET_OFF,
    )


def other_spec(arbitration_mode) -> ResourceSpec:
    return ResourceSpec(
        resource_id=OTHER_RESOURCE,
        owner_module=OTHER_MODULE,
        adapter_id="test.light_adapter",
        capability_profile_id="first-light-1",
        binding_revision=BINDING_REVISION_START,
        aliases=(),
        power_dependencies=(),
        manual_scopes=(OTHER_SCOPE,),
        arbitration=arbitration_mode,
        idle_target=TARGET_OFF,
    )


def initial_record(resource_id: str, scope: str) -> ControlRecord:
    return ControlRecord(
        resource_id=resource_id,
        control_revision=CONTROL_REVISION_START,
        generation=GENERATION_START,
        binding_revision=BINDING_REVISION_START,
        authority=Authority.AUTOMATIC_ELIGIBLE,
        automatic_scope=AutomaticScope.NORMAL,
        manual=None,
        auto_proof=None,
        uncertainty_reason=None,
    )


@dataclass
class Counter:
    value: int = 0

    def next(self) -> str:
        self.value += 1
        return str(self.value)


class FaultInjector:
    """One-shot named failure points of the file store (D-08, D-12)."""

    def __init__(self) -> None:
        self.faults: dict[str, Exception] = {}

    def __call__(self, point: str) -> None:
        fault = self.faults.pop(point, None)
        if fault is not None:
            raise fault


@dataclass
class Stand:
    """Assembled stand: service plus the doubles tests drive directly."""

    service: CoreCommandService
    clock: FakeClock
    store: object
    transport: FakeTransport
    adapter: LightDeviceAdapter
    modules: dict = field(default_factory=dict)
    counter: Counter = field(default_factory=Counter)
    specs: dict = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)
    store_path: object = None

    def raw_report(
        self,
        resource_id: str,
        sequence: int,
        mono_ms: int,
        properties: Mapping[str, object],
        evidence: str = "device_report",
    ) -> dict:
        return {
            "resource_id": resource_id,
            "properties": dict(properties),
            "source_sequence": sequence,
            "received_at_mono_ms": mono_ms,
            "received_at_utc_ms": mono_ms,
            "source_id": "test.z2m",
            "source_epoch": "test.epoch1",
            "evidence": evidence,
        }

    def ingest(
        self,
        resource_id: str,
        sequence: int,
        mono_ms: int,
        properties: Mapping[str, object],
        evidence: str = "device_report",
    ) -> str:
        raw = self.raw_report(
            resource_id, sequence, mono_ms, properties, evidence
        )
        for observation in self.adapter.normalize(raw):
            result = self.service.ingest_observation(observation)
        return result


def build_stand(
    arbitration_mode,
    module_specs: Mapping[str, str] | None = None,
    episode_map: Mapping[str, str] | None = None,
    extra_specs: Mapping[str, ResourceSpec] | None = None,
    use_file_store: bool = False,
    store_path=None,
) -> Stand:
    """Assemble the whole stand with production candidate code only.

    ``module_specs`` maps module ids to the resource they subscribe to; the
    registry is an ordinary constructor argument of the service (K-04), so
    adding a module never needs a core code branch.
    """

    clock = FakeClock()
    counter = Counter()
    specs = {
        MAIN_RESOURCE: main_spec(arbitration_mode),
        OTHER_RESOURCE: other_spec(arbitration_mode),
    }
    if extra_specs:
        specs.update(extra_specs)
    if use_file_store:
        injector = FaultInjector()
        store = FileControlStore(Path(store_path), fault_injector=injector)
        store.injector = injector
    else:
        store = MemoryStore()
    transport = FakeTransport()
    if module_specs is None:
        module_specs = {OWNER_MODULE: MAIN_RESOURCE}
    profiles = {
        resource_id: light_profile() for resource_id in specs
    }
    bindings = {
        resource_id: (
            MAIN_ENTITY
            if resource_id == MAIN_RESOURCE
            else f"{MAIN_ENTITY.split('.')[0]}.{resource_id.replace('.', '_')}"
        )
        for resource_id in specs
    }
    adapter = LightDeviceAdapter(
        profiles_by_resource=profiles,
        binding_revisions={
            resource_id: BINDING_REVISION_START for resource_id in specs
        },
        boot_id=BOOT_ID,
        id_factory=counter.next,
    )
    script_modules: dict[str, ScriptedLightModule] = {}
    entries: dict[str, ModuleEntry] = {}
    for module_id, resource_id in module_specs.items():
        module = ScriptedLightModule(module_id, BASE_TARGET_ON40)
        script_modules[module_id] = module
        entries[module_id] = ModuleEntry(
            manifest=ModuleManifest(
                module_id=module_id,
                api_version=1,
                config_schema_version=1,
                resource_roles=("light",),
                allowed_proposal_kinds=(
                    ProposalKind.TARGET,
                    ProposalKind.REQUIRE_ON,
                ),
                observation_subscriptions=(resource_id,),
                policy_priority=50,
            ),
            instance=module,
        )
    records = {
        resource_id: initial_record(resource_id, scope)
        for resource_id, scope in (
            (MAIN_RESOURCE, MAIN_SCOPE),
            (OTHER_RESOURCE, OTHER_SCOPE),
        )
        if resource_id in specs
    }
    service = CoreCommandService(
        clock=clock,
        store=store,
        adapter=adapter,
        transport=transport,
        specs=specs,
        bindings=bindings,
        modules=entries,
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
            resource_id: CONFIG_REVISION_START for resource_id in specs
        },
        scope_revisions={
            MAIN_SCOPE: SCOPE_START_REVISION,
            OTHER_SCOPE: SCOPE_START_REVISION,
        },
        episode_of_proposal=dict(episode_map or {}),
        id_factory=counter.next,
        initial_records=records,
    )
    stand = Stand(
        service=service,
        clock=clock,
        store=store,
        transport=transport,
        adapter=adapter,
        modules=script_modules,
        counter=counter,
        specs=dict(specs),
        bindings=dict(bindings),
        store_path=store_path,
    )
    # Initial facts of the passport: main and other OFF from device_report.
    stand.ingest(MAIN_RESOURCE, sequence=1, mono_ms=0, properties={"on": False})
    if OTHER_RESOURCE in specs:
        stand.ingest(
            OTHER_RESOURCE, sequence=1, mono_ms=0, properties={"on": False}
        )
    return stand


def other_spec_needed(extra_specs) -> bool:
    return True


def owner_proposal(
    proposal_id: str = OWNER_PROPOSAL,
    resource_id: str = MAIN_RESOURCE,
    target: LightStateTarget = BASE_TARGET_ON40,
    kind: ProposalKind = ProposalKind.TARGET,
    config_revision: int = CONFIG_REVISION_START,
    priority: int = 50,
    module_id: str = OWNER_MODULE,
) -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        module_id=module_id,
        resource_id=resource_id,
        kind=kind,
        target=target,
        policy_priority=priority,
        created_at_mono_ms=0,
        valid_until_mono_ms=TTL_MS,
        input_revision=1,
        config_revision=config_revision,
        reason_code="test.reason",
    )


def auto_on40_confirmed_anyon(stand) -> None:
    """F-11 setup: an any_on REQUIRE_ON demand of the owner module turns
    the lamp on and is confirmed; the owner then withdraws its one-shot
    demand (an Evaluation replaces the whole previous set)."""

    stand.modules[OWNER_MODULE].queue_proposal(
        owner_proposal(kind=ProposalKind.REQUIRE_ON)
    )
    stand.service.process_module_event(OWNER_MODULE, "ev-start")
    stand.clock.advance(1)
    stand.service.drain()
    stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
    )
    stand.service.process_module_event(OWNER_MODULE, "ev-start-gone")


@pytest.fixture
def counter() -> Counter:
    return Counter()


@pytest.fixture
def ranked_stand() -> Stand:
    return build_stand(
        arbitration_mode_from_flag(False),
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )


def arbitration_mode_from_flag(any_on: bool):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    return ArbitrationMode.ANY_ON if any_on else ArbitrationMode.RANKED_TARGET


def restore_file_stand(
    stand: Stand,
    platform_sync: bool = True,
    episode_map: Mapping[str, str] | None = None,
) -> Stand:
    """Build a brand-new service from the durable image of the file store:
    new clock (mono restarts at zero), new transport, new adapter; nothing
    is carried over from the old instance in memory."""

    from custom_components.hausman_hub.application.core.command_service import (
        CoreCommandService as Service,
    )
    from custom_components.hausman_hub.domain.core.recovery import (
        evaluate_clock_trust,
    )

    # A restart is a new process: a new boot id makes the old
    # monotonic boundary data incomparable (K-10).
    clock = FakeClock(boot_id="test.boot2")
    clock.utc_ms = stand.clock.utc_ms
    clock.platform_sync_confirmed = platform_sync
    counter = Counter()
    transport = FakeTransport()
    injector = FaultInjector()
    store = FileControlStore(stand.store_path, fault_injector=injector)
    store.injector = injector
    profiles = {
        resource_id: light_profile() for resource_id in stand.specs
    }
    adapter = LightDeviceAdapter(
        profiles_by_resource=profiles,
        binding_revisions={
            resource_id: BINDING_REVISION_START
            for resource_id in stand.specs
        },
        boot_id="test.boot2",
        id_factory=counter.next,
    )
    entries: dict[str, ModuleEntry] = {}
    for module_id, module in stand.modules.items():
        resource_id = module_spec_resource(stand, module_id)
        entries[module_id] = ModuleEntry(
            manifest=ModuleManifest(
                module_id=module_id,
                api_version=1,
                config_schema_version=1,
                resource_roles=("light",),
                allowed_proposal_kinds=(
                    ProposalKind.TARGET,
                    ProposalKind.REQUIRE_ON,
                ),
                observation_subscriptions=(resource_id,),
                policy_priority=50,
            ),
            instance=module,
        )
    trust = evaluate_clock_trust(
        platform_sync_confirmed=platform_sync,
        previous_utc_ms=None,
        previous_mono_ms=None,
        current_utc_ms=clock.utc_ms,
        current_mono_ms=clock.mono_ms,
    )
    service = Service.restore(
        clock=clock,
        store=store,
        adapter=adapter,
        transport=transport,
        specs=stand.specs,
        bindings=dict(stand.bindings),
        modules=entries,
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
            resource_id: CONFIG_REVISION_START for resource_id in stand.specs
        },
        scope_revisions={
            MAIN_SCOPE: SCOPE_START_REVISION,
            OTHER_SCOPE: SCOPE_START_REVISION,
        },
        episode_of_proposal=dict(episode_map or {}),
        id_factory=counter.next,
        clock_trust=trust,
    )
    return Stand(
        service=service,
        clock=clock,
        store=store,
        transport=transport,
        adapter=adapter,
        modules=dict(stand.modules),
        counter=counter,
        specs=dict(stand.specs),
        bindings=dict(stand.bindings),
        store_path=stand.store_path,
    )


def module_spec_resource(stand: Stand, module_id: str) -> str:
    """Resource a module subscribed to in the original build."""
    if module_id == OWNER_MODULE:
        return MAIN_RESOURCE
    if module_id in ("test.module_a", "test.module_b"):
        return MAIN_RESOURCE
    return OTHER_RESOURCE


def build_file_stand(
    tmp_path,
    arbitration_mode,
    module_specs: Mapping[str, str] | None = None,
    episode_map: Mapping[str, str] | None = None,
) -> Stand:
    return build_stand(
        arbitration_mode,
        module_specs=module_specs,
        episode_map=episode_map,
        use_file_store=True,
        store_path=str(Path(tmp_path) / "control_store.json"),
    )
