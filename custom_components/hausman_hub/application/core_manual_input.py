"""Common manual intake of transferred light resources (S-06.a, HH-014.b).

One admission path for every manual entry of a resource that was
transferred to the Hausman core: resolve the exact target from the
server-side binding, validate the 0.72.1 ``coreControl`` envelope, run
the core manual admission (``CoreCommandService.submit_manual``) and
project honest v1 receipts. The legacy executor is never called here,
with or without a healthy core: a failed core is a visible typed
failure, never a fallback.

The running integration registers no transferred resources: transfer is
a stand-only configuration (synthetic binary light), so the registry is
inert in production and the legacy paths are untouched.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from ..domain.core.models import LightStateTarget, Quality
from .core.command_service import (
    CapacityExceeded,
    CoreServiceError,
    IdempotencyConflict,
    ManualReceipt,
    ManualStatus,
    RevisionConflict,
    StorageUnavailable,
    UnknownResource,
)
from .device_action_protocol import (
    StrictJsonError,
    validate_core_control,
)
from .room_lighting_core import (
    CORE_MANUAL_HOLD_SOURCE,
    CoreBranchAdmissionError,
    CoreBranchError,
    ProfileIncompleteError,
    RoomLightingCoreBranch,
)

if TYPE_CHECKING:
    from .core.command_service import ResourceView

REASON_CORE_MANUAL_ADMITTED = "core_manual_admitted"
SUPPORTED_MANUAL_ACTIONS = ("turn_on", "turn_off")
# The synthetic stand passport window (S-06.a): the same value the
# synthetic binary profile is built with.
SYNTHETIC_PROFILE_MAX_AGE_MS = 60_000


class CoreManualInputError(CoreServiceError):
    """Typed refusal of the common intake; never a legacy fallback."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int,
        api_code: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.api_code = api_code


@dataclass(frozen=True)
class CoreManualTargetBinding:
    """Immutable server-side descriptor of one transferred target.

    The public ``targetId`` is resolved through this binding only: the
    core resource, its property, the owning branch and the allowed
    actions are server facts, never derived from the request or the HA
    domain (S-06.a)."""

    target_id: str
    resource_id: str
    entity_id: str
    room_id: str
    property_name: str = "on"
    allowed_actions: tuple[str, ...] = SUPPORTED_MANUAL_ACTIONS
    synthetic: bool = True


def synthetic_binary_light_profile():
    """Explicit synthetic capability passport of the stand resource.

    Binary ``on`` only, marked synthetic end to end; the timing values
    come from the FIRST stand fixture. There is no brightness, color or
    any other property, and the profile is never copied onto real
    ``light.*`` devices."""

    from decimal import Decimal

    from .core.command_service import CapabilityProfile, PropertyRule

    return CapabilityProfile(
        profile_id="synthetic-binary-light",
        version=1,
        source="synthetic-stand",
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
                state_source="synthetic.stand/device_report",
                max_age_ms=SYNTHETIC_PROFILE_MAX_AGE_MS,
                confirmation="post_dispatch_report",
                tolerance=None,
                confirmation_timeout_ms=10_000,
                dispatch_timeout_ms=1_000,
                readback_delay_ms=None,
            )
        },
    )


def synthetic_binary_light_profiles(resource_ids) -> dict:
    """One synthetic binary profile per named stand resource."""

    profile = synthetic_binary_light_profile()
    return {resource_id: profile for resource_id in resource_ids}


class CoreManualInput:
    """Registry of transferred targets and their common admission path."""

    def __init__(self, *, mono_now=None) -> None:
        self._bindings: dict[str, CoreManualTargetBinding] = {}
        self._branches: dict[str, RoomLightingCoreBranch] = {}
        # Monotonic milliseconds source for the evidence freshness
        # (SEC-S06-03): a stored fact is fresh by its own age, not by a
        # per-request re-stamp. Defaults to the process monotonic clock
        # (the same source the branch's SystemClock reads); tests inject
        # a deterministic one.
        self._mono_now = mono_now or (
            lambda: time.monotonic_ns() // 1_000_000
        )

    # ------------------------------------------------------------------
    # stand wiring

    def register_transferred_target(
        self,
        branch: RoomLightingCoreBranch,
        binding: CoreManualTargetBinding,
    ) -> None:
        """Register one stand transfer: the branch owning the resource
        and its exact public binding."""

        if binding.target_id in self._bindings:
            raise CoreManualInputError(
                "core_target_already_registered",
                f"target {binding.target_id} is already transferred",
                http_status=409,
                api_code="conflict",
            )
        self._bindings[binding.target_id] = binding
        self._branches[binding.room_id] = branch

    def is_transferred(self, target_id: object) -> bool:
        return isinstance(target_id, str) and target_id in self._bindings

    def transferred_target_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._bindings))

    def resolve_target(self, target_id: str) -> CoreManualTargetBinding:
        binding = self._bindings.get(target_id)
        if binding is None:
            raise CoreManualInputError(
                "core_target_unknown",
                f"target {target_id} is not transferred to the core",
                http_status=404,
                api_code="not_found",
            )
        return binding

    def wire_target_type(self, target_id: str) -> str:
        """The wire target type of a transferred resource. Only binary
        light resources are transferred, so the answer is ``light``;
        anything else is a wiring error."""

        self.resolve_target(target_id)
        return "light"

    def core_control_state(self, target_id: str) -> dict:
        """Contract 0.72.2 ``coreControlState`` for one target.

        Tolerant read for the catalog projection: an unknown target or
        a missing branch answers ``unavailable`` instead of raising, so
        one broken entry cannot hide the transfer or break the neighbor
        devices. The caller runs this off the event loop."""

        binding = self._bindings.get(target_id)
        if binding is None:
            return {"version": 1, "status": "unavailable"}
        branch = self._branches.get(binding.room_id)
        if branch is None:
            return {"version": 1, "status": "unavailable"}
        return branch.read_core_control_state(binding.resource_id)

    # ------------------------------------------------------------------
    # admission

    async def async_submit(
        self,
        *,
        target_id: str,
        action_id: str,
        core_control: object,
        principal: str,
        correlation_id: str | None,
        request_id: str | None,
        dry_run: bool = False,
        full: bool = False,
    ) -> dict:
        """One common manual admission of a single explicit action."""

        binding = self.resolve_target(target_id)
        action_id = self._validated_action(binding, action_id)
        if dry_run:
            return self._read_only_projection(
                binding,
                core_control=core_control,
                action_id=action_id,
                correlation_id=correlation_id,
                request_id=request_id,
                full=full,
            )
        envelope = self._validated_envelope(core_control)
        receipt, branch = await self._admit(
            binding,
            envelope=envelope,
            principal=principal,
            action_id=action_id,
        )
        # The projection uses the branch instance the admission just
        # ran on (SEC-S06-06): a stop that lands between the saved
        # admission and this projection must not turn an already
        # accepted result into a refusal.
        return self._receipt_projection(
            binding,
            branch=branch,
            receipt=receipt,
            action_id=action_id,
            envelope_request_id=envelope["requestId"],
            correlation_id=correlation_id,
            request_id=request_id,
            full=full,
        )

    async def async_submit_batch_item(
        self,
        *,
        target_id: str,
        action_id: str,
        core_control: object,
        principal: str | None,
        correlation_id: str | None,
        request_id: str | None,
        dry_run: bool = False,
        action_index: int | None = None,
        full: bool = False,
        value: object = None,
    ) -> dict:
        """The same admission for one batch participant; a typed refusal
        becomes this participant's failed receipt, never a legacy call."""

        try:
            receipt = await self.async_submit(
                target_id=target_id,
                action_id=action_id,
                core_control=core_control,
                principal=principal or "unscoped",
                correlation_id=correlation_id,
                request_id=request_id,
                dry_run=dry_run,
                full=full,
            )
        except CoreManualInputError as error:
            refused_request_id = (
                request_id
                if isinstance(request_id, str) and request_id
                else f"core-manual-error.{int(time.time_ns() // 1_000_000)}"
            )
            item: dict = {
                "contract": {
                    "name": "hausman-hub-device-action-receipt",
                    "version": 1,
                },
                "correlationId": correlation_id,
                "requestId": refused_request_id,
                "targetId": target_id,
                "targetType": "light",
                "actionId": str(action_id),
                "accepted": False,
                "confirmed": False,
                "status": "failed",
                "error": error.code,
            }
            if dry_run:
                item["dryRun"] = True
            if full:
                # A refused participant is a complete honest full receipt:
                # nothing was admitted, nothing was sent. A carried value
                # stays visible as actionValue (R-S06-04): the full
                # schema expects it for value actions even when refused.
                now_ms = int(time.time_ns() // 1_000_000)
                if value is not None:
                    item["actionValue"] = value
                item.update(
                    {
                        "statusName": "Не выполнено",
                        "decision": "blocked",
                        "decisionAt": now_ms,
                        "commandSent": False,
                        "decisionTerminal": True,
                        "commandSource": "manual",
                        "reasonCode": error.code,
                        "evidenceRevision": (
                            f"core.manual.refused.{now_ms}"
                        ),
                        "ownership": "unknown",
                        "blockedReasons": [error.code],
                        "evidence": {
                            "contract": {
                                "name": "hausman-hub-device-state-evidence",
                                "version": 1,
                            },
                            "targetId": target_id,
                            "reportedState": "unknown",
                            "effectiveState": "unknown",
                            "confidence": "suspect",
                            "ownership": "unknown",
                            "observedAt": now_ms,
                            "freshUntil": (
                                now_ms + SYNTHETIC_PROFILE_MAX_AGE_MS
                            ),
                            "freshnessWindowMs": (
                                SYNTHETIC_PROFILE_MAX_AGE_MS
                            ),
                            "ownershipSource": "unknown",
                            "evidenceRevision": (
                                f"core.manual.refused.{now_ms}"
                            ),
                            "evidenceSequence": now_ms,
                            "reasonCode": "state_suspect",
                            "reassertBudget": 0,
                            "reassertPolicy": "none",
                            "allowedActions": [],
                            "blockedReasons": [error.code],
                        },
                    }
                )
            return item
        if action_index is not None and full:
            receipt["actionIndex"] = action_index
        if not full and receipt.get("status") != "failed":
            # The batch wrap keeps an item's own target type when present
            # (S-06.a): a transferred stand light is never reported as a
            # generic sensor participant.
            receipt.setdefault("targetType", "light")
        return receipt

    # ------------------------------------------------------------------
    # steps

    @staticmethod
    def _validated_action(
        binding: CoreManualTargetBinding, action_id: object
    ) -> str:
        if not isinstance(action_id, str) or (
            action_id not in binding.allowed_actions
        ):
            raise CoreManualInputError(
                "core_action_unsupported",
                f"action {action_id!r} is outside the transferred profile",
                http_status=400,
                api_code="invalid_request",
            )
        return action_id

    @staticmethod
    def _validated_envelope(core_control: object) -> Mapping[str, object]:
        if core_control is None:
            # A transferred resource without the new envelope is refused
            # before acceptance; the server never substitutes the current
            # revision (S-06.a compatibility boundary).
            raise CoreManualInputError(
                "core_control_envelope_required",
                "the transferred resource requires the coreControl envelope",
                http_status=409,
                api_code="conflict",
            )
        try:
            return validate_core_control(core_control)
        except StrictJsonError as exc:
            raise CoreManualInputError(
                "core_control_envelope_invalid",
                str(exc),
                http_status=400,
                api_code="invalid_request",
            ) from exc

    def _branch_for(
        self, binding: CoreManualTargetBinding
    ) -> RoomLightingCoreBranch:
        branch = self._branches.get(binding.room_id)
        if branch is None or branch.input_closed:
            # A missing or closed core is a visible refusal; the legacy
            # executor never opens for a transferred resource.
            raise CoreManualInputError(
                "core_unavailable",
                "the core branch of the transferred resource is unavailable",
                http_status=503,
                api_code="unavailable",
            )
        return branch

    async def _admit(
        self,
        binding: CoreManualTargetBinding,
        *,
        envelope: Mapping[str, object],
        principal: str,
        action_id: str,
    ) -> tuple[ManualReceipt, RoomLightingCoreBranch]:
        import asyncio

        branch = self._branch_for(binding)
        service = branch.core_service
        try:
            spec = service.spec(binding.resource_id)
        except UnknownResource as exc:
            raise CoreManualInputError(
                "core_binding_unknown",
                str(exc),
                http_status=409,
                api_code="conflict",
            ) from exc
        if spec.binding_revision != envelope["expectedBindingRevision"]:
            raise CoreManualInputError(
                "core_binding_revision_conflict",
                (
                    f"binding revision moved: expected "
                    f"{envelope['expectedBindingRevision']}, "
                    f"actual {spec.binding_revision}"
                ),
                http_status=409,
                api_code="revision_conflict",
            )
        target = LightStateTarget(
            on=action_id == "turn_on",
            brightness_pct=None,
            color_temperature_k=None,
        )
        # The admission is serialized with the branch input and runs off
        # the event loop: the branch lock guards the tick/admission
        # race and the core commit performs file I/O (SEC-S06-01).
        loop = asyncio.get_running_loop()
        try:
            receipt = await loop.run_in_executor(
                None,
                lambda: branch.admit_manual(
                    resource_id=binding.resource_id,
                    request_id=str(envelope["requestId"]),
                    action_id=action_id,
                    target=target,
                    source=CORE_MANUAL_HOLD_SOURCE,
                    expected_control_revision=int(
                        envelope["expectedControlRevision"]
                    ),
                    expected_binding_revision=int(
                        envelope["expectedBindingRevision"]
                    ),
                    receipt_scope=principal,
                ),
            )
        except RevisionConflict as exc:
            raise CoreManualInputError(
                "core_control_revision_conflict",
                str(exc),
                http_status=409,
                api_code="revision_conflict",
            ) from exc
        except IdempotencyConflict as exc:
            raise CoreManualInputError(
                "core_manual_idempotency_conflict",
                str(exc),
                http_status=409,
                api_code="conflict",
            ) from exc
        except CoreBranchAdmissionError as exc:
            if exc.code == "core_binding_revision_conflict":
                raise CoreManualInputError(
                    exc.code,
                    str(exc),
                    http_status=409,
                    api_code="revision_conflict",
                ) from exc
            raise CoreManualInputError(
                "core_unavailable",
                str(exc),
                http_status=503,
                api_code="unavailable",
            ) from exc
        except (CapacityExceeded, StorageUnavailable) as exc:
            raise CoreManualInputError(
                "core_unavailable",
                str(exc),
                http_status=503,
                api_code="unavailable",
            ) from exc
        except (CoreBranchError, ProfileIncompleteError) as exc:
            raise CoreManualInputError(
                "core_unavailable",
                str(exc),
                http_status=503,
                api_code="unavailable",
            ) from exc
        return receipt, branch

    # ------------------------------------------------------------------
    # projections

    def _read_only_projection(
        self,
        binding: CoreManualTargetBinding,
        *,
        core_control: object,
        action_id: str,
        correlation_id: str | None,
        request_id: str | None,
        full: bool,
    ) -> dict:
        """Dry-run: resolve and validate without any acceptance.

        No manual hold, operation, revision change or send happens here;
        a provided envelope is checked against the live binding only."""

        envelope_conflict: CoreManualInputError | None = None
        envelope: Mapping[str, object] | None = None
        if core_control is None:
            envelope_conflict = CoreManualInputError(
                "core_control_envelope_required",
                "the transferred resource requires the coreControl envelope",
                http_status=409,
                api_code="conflict",
            )
        else:
            try:
                envelope = validate_core_control(core_control)
            except StrictJsonError as exc:
                envelope_conflict = CoreManualInputError(
                    "core_control_envelope_invalid",
                    str(exc),
                    http_status=400,
                    api_code="invalid_request",
                )
        branch = self._branch_for(binding)
        service = branch.core_service
        try:
            spec = service.spec(binding.resource_id)
        except UnknownResource as exc:
            raise CoreManualInputError(
                "core_binding_unknown",
                str(exc),
                http_status=409,
                api_code="conflict",
            ) from exc
        if envelope is not None and (
            spec.binding_revision != envelope["expectedBindingRevision"]
        ):
            # The read-only probe answers exactly what the real
            # admission would answer (R-S06-02): a stale binding
            # revision is a conflict, never a green light.
            raise CoreManualInputError(
                "core_binding_revision_conflict",
                (
                    f"binding revision moved: expected "
                    f"{envelope['expectedBindingRevision']}, "
                    f"actual {spec.binding_revision}"
                ),
                http_status=409,
                api_code="revision_conflict",
            )
        try:
            view = service.resource_view(binding.resource_id)
        except UnknownResource as exc:
            raise CoreManualInputError(
                "core_binding_unknown",
                str(exc),
                http_status=409,
                api_code="conflict",
            ) from exc
        if envelope is not None and (
            view.control_revision != envelope["expectedControlRevision"]
        ):
            envelope_conflict = CoreManualInputError(
                "core_control_revision_conflict",
                (
                    f"control revision moved: expected "
                    f"{envelope['expectedControlRevision']}, "
                    f"actual {view.control_revision}"
                ),
                http_status=409,
                api_code="revision_conflict",
            )
        if envelope_conflict is not None:
            raise envelope_conflict
        evidence = self._evidence(
            binding, view, operation_id=None, action_id=None
        )
        receipt: dict = {
            "contract": {
                "name": "hausman-hub-device-action-receipt",
                "version": 1,
            },
            "correlationId": correlation_id,
            "requestId": self._wire_request_id(request_id, prefix="dry-run"),
            "accepted": True,
            "confirmed": False,
            "status": "accepted",
            "statusName": "Проверяется",
            "targetId": binding.target_id,
            "actionId": action_id,
            "dryRun": True,
            "message": "Сухая проверка: команда не выполнялась",
        }
        if full:
            receipt.update(
                {
                    "targetType": "light",
                    # The v1 contract marks a passed dry run "executed"
                    # with dryRun=true (the legacy projection does the
                    # same): commandSent stays false and nothing was
                    # admitted, so no coreOperation echo appears.
                    "decision": "executed",
                    "decisionAt": int(time.time_ns() // 1_000_000),
                    "commandSent": False,
                    "decisionTerminal": True,
                    "commandSource": "manual",
                    "reasonCode": evidence["reasonCode"],
                    "evidenceRevision": evidence["evidenceRevision"],
                    "preCommandEvidenceRevision": evidence["evidenceRevision"],
                    "preCommandOwnership": "unknown",
                    "ownership": evidence["ownership"],
                    "blockedReasons": evidence["blockedReasons"],
                    "evidence": evidence,
                    "preCommandEvidence": evidence,
                }
            )
        return receipt

    def _receipt_projection(
        self,
        binding: CoreManualTargetBinding,
        *,
        branch: RoomLightingCoreBranch,
        receipt: ManualReceipt,
        action_id: str,
        envelope_request_id: str,
        correlation_id: str | None,
        request_id: str | None,
        full: bool,
    ) -> dict:
        service = branch.core_service
        operation = None
        if receipt.operation_id is not None:
            try:
                operation = service.operation(receipt.operation_id)
            except KeyError:
                operation = None
        command_sent = bool(
            operation is not None
            and operation.dispatch_started_at_mono_ms is not None
        )
        terminal = bool(operation.is_terminal()) if operation else False
        binding_revision = service.spec(binding.resource_id).binding_revision
        base: dict = {
            "contract": {
                "name": "hausman-hub-device-action-receipt",
                "version": 1,
            },
            "correlationId": correlation_id,
            "requestId": envelope_request_id,
            "accepted": receipt.status in (ManualStatus.ACCEPTED, ManualStatus.NO_OP),
            "confirmed": False,
            "status": (
                "accepted"
                if receipt.status in (ManualStatus.ACCEPTED, ManualStatus.NO_OP)
                else "failed"
            ),
            "statusName": (
                "Проверяется"
                if receipt.status in (ManualStatus.ACCEPTED, ManualStatus.NO_OP)
                else "Не выполнено"
            ),
            "targetId": binding.target_id,
            "actionId": action_id,
        }
        if receipt.status is ManualStatus.NO_OP:
            base["message"] = "Свет уже в нужном состоянии"
        if receipt.status not in (ManualStatus.ACCEPTED, ManualStatus.NO_OP):
            base["error"] = receipt.reason_code or "core_manual_rejected"
            return base
        if not full:
            return base
        view = service.resource_view(binding.resource_id)
        evidence = self._evidence(
            binding,
            view,
            operation_id=receipt.operation_id,
            action_id=action_id,
        )
        base.update(
            {
                "targetType": "light",
                "decision": "executed" if command_sent else "skipped",
                "decisionAt": int(time.time_ns() // 1_000_000),
                "commandSent": command_sent,
                "decisionTerminal": terminal,
                "dryRun": False,
                "commandSource": "manual",
                "reasonCode": REASON_CORE_MANUAL_ADMITTED,
                "evidenceRevision": evidence["evidenceRevision"],
                "preCommandEvidenceRevision": evidence["evidenceRevision"],
                "preCommandOwnership": "unknown",
                "ownership": evidence["ownership"],
                "blockedReasons": [],
                "evidence": evidence,
                "preCommandEvidence": evidence,
                "coreOperation": {
                    "version": 1,
                    "operationId": receipt.operation_id,
                    "controlRevision": receipt.control_revision,
                    "bindingRevision": binding_revision,
                },
            }
        )
        return base

    @staticmethod
    def _wire_request_id(
        request_id: str | None, *, prefix: str
    ) -> str:
        if isinstance(request_id, str) and request_id:
            return request_id
        return f"{prefix}.{int(time.time_ns() // 1_000_000)}"

    def _evidence(
        self,
        binding: CoreManualTargetBinding,
        view: "ResourceView",
        *,
        operation_id: str | None,
        action_id: str | None = None,
    ) -> dict:
        """One bounded evidence snapshot built from the public core view.

        The combination always matches one reviewed branch of the
        vendored ``device-state-evidence`` schema (R-S06-01):
        an admitted command reads ``core_manual_admitted`` (a fresh fact
        keeps the reported state; a stale fact never claims a current
        ``on``); a read-only dry run describes the state without any
        admission claim. Freshness is judged by the fact's own age on
        the monotonic clock, and ``observedAt`` carries the original
        observation time, not the time of this read (SEC-S06-03).
        Nothing is claimed as sent or owned."""

        on_fact = next(
            (fact for fact in view.facts if fact.property == "on"),
            None,
        )
        now_mono = self._mono_now()
        if on_fact is None or on_fact.quality is Quality.INVALID:
            fresh = False
        else:
            fact_age_ms = max(0, now_mono - on_fact.observed_at_mono_ms)
            fresh = (
                on_fact.quality is Quality.FRESH
                and fact_age_ms <= SYNTHETIC_PROFILE_MAX_AGE_MS
            )
        if on_fact is None or on_fact.quality is Quality.INVALID:
            reported = "unknown"
        else:
            reported = "on" if on_fact.value is True else (
                "off" if on_fact.value is False else "unknown"
            )
        now_ms = int(time.time_ns() // 1_000_000)
        if on_fact is not None and on_fact.received_at_utc_ms is not None:
            # The observation's own stored wall-clock stamp (a
            # legitimate zero included): a wall clock jump at read time
            # must not move it (SEC-S06-03R1).
            observed_at = max(0, on_fact.received_at_utc_ms)
        else:
            observed_at = now_ms
        revision = "core.manual.evidence.read"
        if operation_id:
            revision = f"core.manual.evidence.{operation_id}"
        if operation_id:
            # Admission: the reason code of the reviewed confirmation
            # category carries no state claim of its own.
            reason = REASON_CORE_MANUAL_ADMITTED
            confidence = "fresh" if fresh else "suspect"
            if confidence == "fresh":
                effective = reported if reported in ("on", "off") else "unknown"
                allowed = [action_id] if action_id else []
            else:
                effective = reported if reported == "off" else "unknown"
                allowed = []
            blocked: list[str] = []
            ownership_source = "device_report"
        elif reported == "off" and fresh:
            reason = "device_report_off_confirmed"
            confidence = "confirmed"
            effective = "off"
            allowed = ["turn_on"]
            blocked = []
            ownership_source = "device_report"
        else:
            # The legacy read semantics for a state nobody proven owns
            # (including a fresh on-report): suspect, no actions, no
            # ownership claims (R-S06-01).
            reason = "state_suspect"
            confidence = "suspect"
            effective = reported if reported == "off" else "unknown"
            allowed = []
            blocked = [reason]
            ownership_source = "device_report"
        return {
            "contract": {
                "name": "hausman-hub-device-state-evidence",
                "version": 1,
            },
            "targetId": binding.target_id,
            "reportedState": reported,
            "effectiveState": effective,
            "confidence": confidence,
            "ownership": "unknown",
            "observedAt": observed_at,
            "freshUntil": observed_at + SYNTHETIC_PROFILE_MAX_AGE_MS,
            "freshnessWindowMs": SYNTHETIC_PROFILE_MAX_AGE_MS,
            "ownershipSource": ownership_source,
            "evidenceRevision": revision,
            "evidenceSequence": observed_at,
            "reasonCode": reason,
            "reassertBudget": 0,
            "reassertPolicy": "none",
            "allowedActions": allowed,
            "blockedReasons": blocked,
        }
