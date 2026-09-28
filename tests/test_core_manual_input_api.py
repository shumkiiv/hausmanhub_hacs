"""Common manual intake over the real HTTP handlers (S-06.a, HH-014.b).

Every positive and negative case crosses the production
``DeviceActionView``/``DeviceActionBatchView`` handlers with the
production common intake and a real shadow branch. The old executor is
replaced by a recording double only to prove it is never reached for a
transferred resource and stays the only writer for neighbours.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import test_local_summary_access as harness

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1"

# The branch-level stand builders live in tests/core; the path is
# extended in setUpClass before the fake Home Assistant modules exist.
stand = None

SINGLE_PATH = "/api/hausman_hub/v1/device-actions"
BATCH_PATH = "/api/hausman_hub/v1/device-actions/batch"
FULL_SINGLE_REQUEST_MEDIA_TYPE = (
    "application/vnd.hausmanhub.device-action-request.full+json"
)
FULL_SINGLE_RESPONSE_MEDIA_TYPE = (
    "application/vnd.hausmanhub.device-action-receipt.full+json"
)
FULL_BATCH_REQUEST_MEDIA_TYPE = (
    "application/vnd.hausmanhub.device-action-batch-request.full+json"
)
FULL_BATCH_RESPONSE_MEDIA_TYPE = (
    "application/vnd.hausmanhub.device-action-batch-receipt.full+json"
)


class RecordingExecutor:
    """Old-writer double: records every call it is allowed to see."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def async_execute_device_action(
        self, target_id, action_id, value, **options
    ):
        self.calls.append((target_id, action_id))
        return {
            "correlationId": options.get("correlation_id"),
            "requestId": str(options.get("request_id") or "legacy-request"),
            "targetId": target_id,
            "actionId": action_id,
            "accepted": True,
            "confirmed": True,
            "status": "confirmed",
            "statusName": "Выполнено",
            "observedState": "on",
        }


class CoreManualInputApiTest(unittest.TestCase):
    """One stand per test: real branch + intake behind the real views."""

    @classmethod
    def setUpClass(cls) -> None:
        global stand
        cls.original_sys_path = sys.path[:]
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "tests" / "core"))
        cls.fake_modules = harness.fake_home_assistant_modules()
        # The shadow branch's SystemClock reads HA's dt_util when the
        # module exists; the summary harness ships an empty stub, so the
        # stand completes it with the standard UTC clock (same epoch).
        dt_module = cls.fake_modules.get("homeassistant.util.dt")
        if dt_module is not None and not hasattr(dt_module, "utcnow"):
            dt_module.utcnow = lambda: datetime.now(timezone.utc)
        util_module = cls.fake_modules.get("homeassistant.util")
        if util_module is not None and dt_module is not None:
            util_module.dt = dt_module
        sys.modules.update(cls.fake_modules)
        cls.device_action_api = importlib.import_module(
            "custom_components.hausman_hub.device_action_api"
        )
        stand = importlib.import_module("test_manual_input_binding")

    @classmethod
    def tearDownClass(cls) -> None:
        for name in list(cls.fake_modules):
            sys.modules.pop(name, None)
        sys.path[:] = cls.original_sys_path

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_path = Path(self._tmp.name)
        self.branch = stand.build_stand_branch(tmp_path)
        self.intake = stand.CoreManualInput()
        self.intake.register_transferred_target(
            self.branch,
            stand.CoreManualTargetBinding(
                target_id=stand.PUBLIC_TARGET_ID,
                resource_id=stand.RESOURCE_ID,
                entity_id=stand.ENTITY_ID,
                room_id=stand.ROOM_ID,
            ),
        )
        self.executor = RecordingExecutor()
        self.service = self._make_service()
        self.hass = SimpleNamespace(
            data={
                "hausman_hub": {
                    "scenario_service": self.service,
                    "core_manual_input": self.intake,
                    "device_action_idempotency": self._make_idempotency(),
                }
            },
            states=SimpleNamespace(get=lambda entity_id: None),
        )

        def async_add_executor_job(target, *args):
            return asyncio.get_running_loop().run_in_executor(
                None, lambda: target(*args)
            )

        self.hass.async_add_executor_job = async_add_executor_job
        self.view = self.device_action_api.DeviceActionView(self.hass)
        self.batch_view = self.device_action_api.DeviceActionBatchView(
            self.hass
        )
        self.tablet = harness.reader_user("system-users")
        self.admin = harness.reader_user("system-admin", admin=True)

    def _make_idempotency(self):
        """The real dangerous-action idempotency over a memory store:
        a full physical batch requires the coordinator, as in production."""

        from custom_components.hausman_hub.application.device_action_idempotency import (
            DangerousActionIdempotency,
        )

        class MemoryIdempotencyStore:
            recovered_previous = False

            def __init__(self) -> None:
                self.payload = None

            async def async_load(self):
                return self.payload

            async def async_save(self, payload):
                self.payload = json.loads(json.dumps(payload))

        coordinator = DangerousActionIdempotency(MemoryIdempotencyStore())
        asyncio.run(coordinator.async_load())
        return coordinator

    def _make_service(self):
        service = self.device_action_api.ScenarioService.__new__(
            self.device_action_api.ScenarioService
        )
        service._executor = self.executor
        service._core_manual_input = self.intake

        async def async_refresh_catalog():
            return None

        async def resolve_context(target_id, action_id):
            return None

        async def is_intercom(target_id, action_id):
            return False

        service.async_refresh_catalog = async_refresh_catalog
        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.is_contextually_dangerous_action = (
            lambda target_id, action_id: False
        )
        service.is_external_cover_action = lambda target_id, action_id: False
        service.new_run_id = lambda: "synthetic-run-1"

        async def legacy_single(target_id, action_id, value=None, **options):
            # The neighbours' old writer: records and confirms like the
            # production receipt shape.
            self.executor.calls.append((target_id, action_id))
            return {
                "correlationId": options.get("correlation_id"),
                "requestId": str(options.get("request_id") or "legacy"),
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
                "statusName": "Выполнено",
                "observedState": "on",
            }

        service.async_execute_device_action = legacy_single
        return service

    # -- helpers -------------------------------------------------------

    def _single(self, payload, *, accept=None, user=None):
        request = harness.FakeJsonRequest(
            "192.168.1.20",
            user or self.tablet,
            SINGLE_PATH,
            payload,
            content_type=(
                FULL_SINGLE_REQUEST_MEDIA_TYPE if accept else "application/json"
            ),
            accept=accept,
        )
        return asyncio.run(self.view.post(request))

    def _full_single(self, payload, *, user=None):
        """The explicitly selected full single form: the only form that
        carries the core admission envelope (0.72.1)."""

        payload = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            **payload,
        }
        return self._single(
            payload,
            accept=FULL_SINGLE_RESPONSE_MEDIA_TYPE,
            user=user,
        )

    def _batch(self, payload, *, accept=None, user=None, content_type=None):
        request = harness.FakeJsonRequest(
            "192.168.1.20",
            user or self.tablet,
            BATCH_PATH,
            payload,
            content_type=content_type or "application/json",
            accept=accept,
        )
        return asyncio.run(self.batch_view.post(request))

    def _full_batch(self, payload, *, user=None):
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-batch-request",
                "version": 1,
            },
            **payload,
        }
        return self._batch(
            payload,
            accept=FULL_BATCH_RESPONSE_MEDIA_TYPE,
            content_type=FULL_BATCH_REQUEST_MEDIA_TYPE,
            user=user,
        )

    def _envelope(self, *, request_id, control=None):
        return {
            "version": 1,
            "requestId": request_id,
            "expectedControlRevision": (
                control
                if control is not None
                else self.branch.core_service.record(
                    stand.RESOURCE_ID
                ).control_revision
            ),
            "expectedBindingRevision": self.branch.core_service.spec(
                stand.RESOURCE_ID
            ).binding_revision,
        }

    def _report(self, value: str) -> None:
        stand.report_light_state(self.branch, value)

    def _report_stale(self, value: str) -> None:
        stand.report_light_state_repeat(self.branch, value)

    def _validate_full_receipt(self, payload) -> None:
        """The live full answer must satisfy the vendored 0.72.1 schemas
        (R-S06-01): the receipt and its evidence block."""

        receipt_validator = self._schema_validator(
            "device-action-receipt.schema.json", "full"
        )
        receipt_validator.validate(payload)
        if isinstance(payload.get("evidence"), dict):
            self._schema_validator(
                "device-state-evidence.schema.json"
            ).validate(payload["evidence"])

    def _schema_validator(self, name: str, definition=None):
        import json

        from jsonschema import Draft202012Validator, RefResolver

        if not hasattr(self, "_schema_store"):
            store = {}
            for schema_path in CONTRACTS.rglob("*.json"):
                candidate = json.loads(
                    schema_path.read_text(encoding="utf-8")
                )
                if isinstance(candidate, dict) and isinstance(
                    candidate.get("$id"), str
                ):
                    store[candidate["$id"]] = candidate
            self._schema_store = store
        schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
        if definition is not None:
            schema = schema["$defs"][definition]
        return Draft202012Validator(
            schema,
            resolver=RefResolver.from_schema(
                json.loads((CONTRACTS / name).read_text(encoding="utf-8")),
                store=self._schema_store,
            ),
        )

    # -- tests ---------------------------------------------------------

    def test_transferred_resource_without_envelope_is_refused(self):
        """The old single form reaches a transferred resource and is
        refused before any acceptance or side record (S-06.a)."""

        self._report("on")
        response = self._single(
            {
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
            }
        )
        self.assertEqual(HTTPStatus.CONFLICT, response.status)
        self.assertEqual("conflict", response.payload["code"])
        self.assertEqual([], self.executor.calls)
        record = self.branch.core_service.record(stand.RESOURCE_ID)
        self.assertNotEqual("MANUAL", record.authority.name)

    def test_versioned_single_request_reaches_submit_manual(self):
        self._report("on")
        response = self._full_single(
            {
                "correlationId": "corr.api.1",
                "requestId": "core-manual-api-1",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": self._envelope(
                    request_id="core-manual-api-1"
                ),
            }
        )
        self.assertEqual(HTTPStatus.OK, response.status)
        payload = response.payload
        self.assertEqual(
            {"name": "hausman-hub-device-action-receipt", "version": 1},
            payload["contract"],
        )
        self.assertTrue(payload["accepted"])
        self.assertFalse(payload["confirmed"])
        self.assertEqual("accepted", payload["status"])
        self.assertEqual("core-manual-api-1", payload["requestId"])
        self.assertEqual([], self.executor.calls)
        record = self.branch.core_service.record(stand.RESOURCE_ID)
        self.assertEqual("MANUAL", record.authority.name)

    def test_full_single_receipt_carries_core_operation(self):
        self._report("on")
        response = self._single(
            {
                "contract": {
                    "name": "hausman-hub-device-action-request",
                    "version": 1,
                },
                "correlationId": "corr.api.full.1",
                "requestId": "core-manual-api-full",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": self._envelope(
                    request_id="core-manual-api-full"
                ),
            },
            accept=FULL_SINGLE_RESPONSE_MEDIA_TYPE,
        )
        self.assertEqual(HTTPStatus.OK, response.status)
        payload = response.payload
        self.assertEqual("light", payload["targetType"])
        self.assertFalse(payload["commandSent"])
        self.assertEqual("skipped", payload["decision"])
        self.assertEqual("core_manual_admitted", payload["reasonCode"])
        core_operation = payload["coreOperation"]
        self.assertEqual(1, core_operation["version"])
        self.assertTrue(core_operation["operationId"])
        self.assertNotEqual(
            "core-manual-api-full", core_operation["operationId"]
        )
        self._validate_full_receipt(payload)
        self.assertEqual([], self.executor.calls)

    def test_admitted_full_receipt_with_stale_fact_is_schema_valid(self):
        """A stale on-fact never claims a current on (R-S06-01): the
        admitted evidence goes suspect with an unknown effective state,
        and the live full answer stays schema-valid."""

        self._report("on")
        self._report_stale("on")
        response = self._full_single(
            {
                "requestId": "core-manual-api-stale",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": self._envelope(
                    request_id="core-manual-api-stale"
                ),
            }
        )
        self.assertEqual(HTTPStatus.OK, response.status)
        payload = response.payload
        evidence = payload["evidence"]
        self.assertEqual("suspect", evidence["confidence"])
        self.assertNotEqual("on", evidence["effectiveState"])
        self.assertEqual([], self.executor.calls)
        self._validate_full_receipt(payload)

    def test_dry_run_full_receipt_is_schema_valid(self):
        """The read-only probe answers honestly per state: a fresh on
        reads automatic_ownership_missing, a fresh off reads
        device_report_off_confirmed; both stay schema-valid."""

        self._report("off")
        off_response = self._full_single(
            {
                "requestId": "core-manual-dry-off",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_on",
                "dryRun": True,
                "coreControl": self._envelope(
                    request_id="core-manual-dry-off"
                ),
            }
        )
        self.assertEqual(HTTPStatus.OK, off_response.status)
        self.assertEqual(
            "device_report_off_confirmed",
            off_response.payload["evidence"]["reasonCode"],
        )
        self._validate_full_receipt(off_response.payload)

        self._report("on")
        on_response = self._full_single(
            {
                "requestId": "core-manual-dry-on",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "dryRun": True,
                "coreControl": self._envelope(
                    request_id="core-manual-dry-on"
                ),
            }
        )
        self.assertEqual(HTTPStatus.OK, on_response.status)
        # The legacy read semantics: an on-state with unproven ownership
        # is suspect, never a claimed current on.
        self.assertEqual(
            "state_suspect", on_response.payload["evidence"]["reasonCode"]
        )
        self.assertEqual(
            "unknown", on_response.payload["evidence"]["effectiveState"]
        )
        self._validate_full_receipt(on_response.payload)

    def test_dry_run_with_stale_binding_revision_conflicts(self):
        """The read-only probe answers what the real admission would
        answer (R-S06-02): a stale binding revision is a conflict."""

        self._report("on")
        envelope = self._envelope(request_id="core-manual-dry-stale")
        envelope["expectedBindingRevision"] += 1
        response = self._full_single(
            {
                "requestId": "core-manual-dry-stale",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "dryRun": True,
                "coreControl": envelope,
            }
        )
        self.assertEqual(HTTPStatus.CONFLICT, response.status)
        self.assertEqual("revision_conflict", response.payload["code"])
        self.assertEqual([], self.executor.calls)

    def test_malformed_envelope_is_a_protocol_refusal(self):
        self._report("on")
        envelope = self._envelope(request_id="core-manual-bad")
        envelope.pop("expectedBindingRevision")
        response = self._full_single(
            {
                "requestId": "core-manual-bad",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": envelope,
            }
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, response.status)
        self.assertEqual([], self.executor.calls)

    def test_unsupported_action_is_refused(self):
        self._report("on")
        response = self._full_single(
            {
                "requestId": "core-manual-dim",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "set_brightness",
                "value": 40,
                "coreControl": self._envelope(request_id="core-manual-dim"),
            }
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, response.status)
        self.assertEqual([], self.executor.calls)

    def test_dry_run_creates_no_manual_and_no_operation(self):
        self._report("on")
        before = self.branch.core_service.record(stand.RESOURCE_ID)
        response = self._full_single(
            {
                "requestId": "core-manual-dry",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "dryRun": True,
                "coreControl": self._envelope(request_id="core-manual-dry"),
            }
        )
        self.assertEqual(HTTPStatus.OK, response.status)
        self.assertTrue(response.payload["dryRun"])
        after = self.branch.core_service.record(stand.RESOURCE_ID)
        self.assertEqual(before.control_revision, after.control_revision)
        self.assertEqual(before.authority, after.authority)
        self.assertEqual([], self.executor.calls)

    def test_neighbour_keeps_the_legacy_writer(self):
        """A command to a non-transferred neighbour takes the old path
        and never touches the transferred resource (S-06.a control)."""

        response = self._single(
            {
                "targetId": "light.neighbor",
                "actionId": "turn_on",
            }
        )
        self.assertEqual(HTTPStatus.OK, response.status)
        self.assertEqual(
            [("light.neighbor", "turn_on")], self.executor.calls
        )
        record = self.branch.core_service.record(stand.RESOURCE_ID)
        self.assertNotEqual("MANUAL", record.authority.name)

    def test_single_wrapper_direct_call_is_blocked_for_transferred(self):
        """Even a direct call to the real legacy single wrapper cannot
        execute a transferred resource (S-06.a)."""

        real_method = (
            self.device_action_api.ScenarioService.async_execute_device_action
        )
        with self.assertRaises(Exception) as caught:
            asyncio.run(
                real_method(self.service, stand.PUBLIC_TARGET_ID, "turn_off")
            )
        self.assertIn("core manual intake", str(caught.exception))
        self.assertEqual([], self.executor.calls)

    def test_batch_participants_keep_separate_results(self):
        self._report("on")
        response = self._full_batch(
            {
                "requestId": "batch-full-1",
                "correlationId": "corr.batch.1",
                "actions": [
                    {
                        "targetId": stand.PUBLIC_TARGET_ID,
                        "actionId": "turn_off",
                        "coreControl": self._envelope(
                            request_id="core-manual-part-1"
                        ),
                    },
                    {
                        "targetId": "light.neighbor",
                        "actionId": "turn_on",
                    },
                ],
            }
        )
        self.assertEqual(HTTPStatus.OK, response.status)
        receipts = response.payload["receipts"]
        self.assertEqual(2, len(receipts))
        first, second = receipts
        self.assertEqual("core-manual-part-1", first["requestId"])
        self.assertEqual("light", first["targetType"])
        self.assertEqual("light.neighbor", second["targetId"])
        # One admission for A, one legacy call for B — nothing crossed.
        self.assertEqual(
            [("light.neighbor", "turn_on")], self.executor.calls
        )
        record = self.branch.core_service.record(stand.RESOURCE_ID)
        self.assertEqual("MANUAL", record.authority.name)

    def test_batch_participant_without_envelope_fails_alone(self):
        """A transferred participant without the envelope fails as one
        item; the neighbour still runs and no legacy call happens for
        the transferred resource (S-06.a)."""

        self._report("on")
        response = self._full_batch(
            {
                "requestId": "batch-full-2",
                "correlationId": "corr.batch.2",
                "actions": [
                    {
                        "targetId": stand.PUBLIC_TARGET_ID,
                        "actionId": "turn_off",
                    },
                    {
                        "targetId": "light.neighbor",
                        "actionId": "turn_on",
                    },
                ],
            }
        )
        self.assertEqual(HTTPStatus.OK, response.status)
        receipts = response.payload["receipts"]
        self.assertEqual("failed", receipts[0]["status"])
        self.assertEqual(
            "core_control_envelope_required", receipts[0]["error"]
        )
        self.assertTrue(receipts[1]["accepted"])
        self.assertEqual(
            [("light.neighbor", "turn_on")], self.executor.calls
        )

    def test_two_users_of_same_role_do_not_share_receipts(self):
        """The intake scope is the authenticated user's identity, not
        the role: two tablet users with the same envelope requestId get
        separate admissions (SEC-S06-02)."""

        user_a = SimpleNamespace(
            id="tablet-user-a",
            is_admin=False,
            system_generated=False,
            groups=(SimpleNamespace(id="system-users"),),
        )
        user_b = SimpleNamespace(
            id="tablet-user-b",
            is_admin=False,
            system_generated=False,
            groups=(SimpleNamespace(id="system-users"),),
        )
        self._report("on")
        envelope = self._envelope(request_id="core-manual-shared-user")
        first = self._full_single(
            {
                "requestId": "core-manual-shared-user",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": envelope,
            },
            user=user_a,
        )
        self.assertEqual(HTTPStatus.OK, first.status)
        # The same caller with the same envelope replays; user B with
        # the identical request never receives A's receipt: their own
        # scope re-evaluates the moved revision and conflicts.
        replay = self._full_single(
            {
                "requestId": "core-manual-shared-user",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": envelope,
            },
            user=user_a,
        )
        self.assertEqual(HTTPStatus.OK, replay.status)
        second = self._full_single(
            {
                "requestId": "core-manual-shared-user",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": envelope,
            },
            user=user_b,
        )
        self.assertEqual(HTTPStatus.CONFLICT, second.status)
        self.assertEqual([], self.executor.calls)

    def test_core_batch_replay_is_scoped_to_the_caller(self):
        """A full batch touching a transferred resource replays only
        for the same authenticated caller (SEC-S06-02): another user
        with the same batch body never receives the stored foreign
        response."""

        self._report("on")
        payload = {  # noqa: the batch body is reused verbatim per caller
            "requestId": "batch-shared-user",
            "correlationId": "corr.batch.shared",
            "actions": [
                {
                    "targetId": stand.PUBLIC_TARGET_ID,
                    "actionId": "turn_off",
                    "coreControl": self._envelope(
                        request_id="core-manual-shared-batch"
                    ),
                }
            ],
        }
        first = self._full_batch(payload)
        self.assertEqual(HTTPStatus.OK, first.status)
        second = self._full_batch(
            dict(payload, correlationId="corr.batch.other"),
            user=SimpleNamespace(
                id="tablet-user-b",
                is_admin=False,
                system_generated=False,
                groups=(SimpleNamespace(id="system-users"),),
            ),
        )
        # The second caller is not served the stored first response:
        # the core participant is re-evaluated for their own scope and
        # the moved revision conflicts.
        self.assertNotEqual(first.payload, second.payload)
        self.assertEqual([], self.executor.calls)

    def test_catalog_publishes_core_control_state(self):
        """Contract 0.72.2: the real catalog view publishes the
        revision pair for the transferred target (ready), nothing for
        the neighbour, and unavailable after the branch stops."""

        import types as types_module

        scenario_api = importlib.import_module(
            "custom_components.hausman_hub.scenario_api"
        )
        service = self.device_action_api.ScenarioService.__new__(
            self.device_action_api.ScenarioService
        )
        transferred = SimpleNamespace(
            target_id=stand.PUBLIC_TARGET_ID,
            name="Основной свет стенда",
            entity_id=stand.ENTITY_ID,
            properties=[],
            actions=[],
            physical_id=None,
            physical_name=None,
            room_id=None,
            room_name=None,
            device_type=None,
            device_type_name=None,
            capability_name=None,
        )
        neighbour = SimpleNamespace(
            target_id="light.neighbor",
            name="Соседний свет",
            entity_id="light.neighbor",
            properties=[],
            actions=[],
            physical_id=None,
            physical_name=None,
            room_id=None,
            room_name=None,
            device_type=None,
            device_type_name=None,
            capability_name=None,
        )
        service._registry = object()  # noqa: SLF001 - the view checks readiness
        service.current_catalog = lambda: SimpleNamespace(
            devices={
                stand.PUBLIC_TARGET_ID: transferred,
                "light.neighbor": neighbour,
            }
        )

        async def list_scenarios():
            return []

        service.async_list_scenarios = list_scenarios
        service.__dict__["_catalog_readiness"] = {"state": "ready"}
        self.hass.data["hausman_hub"]["scenario_service"] = service

        admin = harness.reader_user("system-admin", admin=True)
        view = scenario_api.ScenarioCatalogView(self.hass)
        catalog_path = "/api/hausman_hub/v1/admin/scenarios/catalog"
        response = asyncio.run(
            view.post(
                harness.FakeJsonRequest(
                    "192.168.1.20", admin, catalog_path, {}
                )
            )
            if False
            else view.get(
                harness.FakeRequest("192.168.1.20", admin, catalog_path)
            )
        )
        # FakeRequest has no .query_string default matching ""... ensure
        self.assertEqual(200, response.status)
        devices = {
            item["target_id"]: item for item in response.payload["devices"]
        }
        self.assertEqual(
            {
                "version": 1,
                "status": "ready",
                "controlRevision": self.branch.core_service.record(
                    stand.RESOURCE_ID
                ).control_revision,
                "bindingRevision": self.branch.core_service.spec(
                    stand.RESOURCE_ID
                ).binding_revision,
            },
            devices[stand.PUBLIC_TARGET_ID]["coreControlState"],
        )
        self.assertNotIn("coreControlState", devices["light.neighbor"])

        self.branch.on_runtime_stop()
        stopped = asyncio.run(
            view.get(
                harness.FakeRequest("192.168.1.20", admin, catalog_path)
            )
        )
        stopped_devices = {
            item["target_id"]: item
            for item in stopped.payload["devices"]
        }
        self.assertEqual(
            {"version": 1, "status": "unavailable"},
            stopped_devices[stand.PUBLIC_TARGET_ID]["coreControlState"],
        )

    def test_mixed_dangerous_and_transferred_batch_is_refused(self):
        """A batch mixing a transferred resource with a dangerous
        participant stays outside the slice: refused before any
        reservation, so no stored response can leak across callers
        (SEC-S06-02R1); the global dangerous guard is untouched."""

        self._report("on")
        response = self._full_batch(
            {
                "requestId": "batch-mixed-dangerous",
                "correlationId": "corr.batch.dangerous",
                "actions": [
                    {
                        "targetId": stand.PUBLIC_TARGET_ID,
                        "actionId": "turn_off",
                        "coreControl": self._envelope(
                            request_id="core-manual-mixed"
                        ),
                    },
                    {
                        "targetId": "front-door-lock",
                        "actionId": "unlock",
                        "confirmedByUser": True,
                        "idempotencyKey": "mixed-dangerous-key-1",
                    },
                ],
            }
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, response.status)
        self.assertEqual([], self.executor.calls)
        record = self.branch.core_service.record(stand.RESOURCE_ID)
        self.assertNotEqual("MANUAL", record.authority.name)

    def test_refused_batch_participant_full_item_is_schema_valid(self):
        """A refused value-action participant keeps its actionValue in
        the full receipt (R-S06-04) and the whole batch answer stays
        schema-valid."""

        self._report("on")
        response = self._full_batch(
            {
                "requestId": "batch-full-5",
                "correlationId": "corr.batch.5",
                "actions": [
                    {
                        "targetId": stand.PUBLIC_TARGET_ID,
                        "actionId": "set_brightness",
                        "value": 40,
                    }
                ],
            }
        )
        self.assertEqual(HTTPStatus.OK, response.status)
        item = response.payload["receipts"][0]
        self.assertEqual("failed", item["status"])
        self.assertEqual("core_action_unsupported", item["error"])
        self.assertEqual(40, item.get("actionValue"))
        self.assertEqual([], self.executor.calls)
        self._schema_validator(
            "device-action-batch-receipt.schema.json", "full"
        ).validate(response.payload)

    def test_batch_repeat_keeps_operation_and_does_not_extend(self):
        self._report("on")
        envelope = self._envelope(request_id="core-manual-part-repeat")
        batch_payload = {
            "requestId": "batch-full-3",
            "correlationId": "corr.batch.3",
            "actions": [
                {
                    "targetId": stand.PUBLIC_TARGET_ID,
                    "actionId": "turn_off",
                    "coreControl": envelope,
                }
            ],
        }
        first = self._full_batch(batch_payload)
        self.assertTrue(first.payload["receipts"][0]["accepted"])
        priority_until = self.branch.core_service.record(
            stand.RESOURCE_ID
        ).manual.priority_until_utc_ms
        control_revision = self.branch.core_service.record(
            stand.RESOURCE_ID
        ).control_revision
        second = self._full_batch(
            {
                "requestId": "batch-full-4",
                "correlationId": "corr.batch.4",
                "actions": [
                    {
                        "targetId": stand.PUBLIC_TARGET_ID,
                        "actionId": "turn_off",
                        "coreControl": envelope,
                    }
                ],
            }
        )
        repeat = second.payload["receipts"][0]
        self.assertTrue(repeat["accepted"])
        # The replay neither extends the manual hold nor bumps the
        # control revision again, and the old writer is never called.
        record = self.branch.core_service.record(stand.RESOURCE_ID)
        self.assertEqual(priority_until, record.manual.priority_until_utc_ms)
        self.assertEqual(control_revision, record.control_revision)
        self.assertEqual([], self.executor.calls)

    def test_two_clients_with_same_revision_one_conflict(self):
        self._report("on")
        envelope_a = self._envelope(request_id="core-manual-client-a")
        accepted = self._full_single(
            {
                "requestId": "core-manual-client-a",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": envelope_a,
            }
        )
        self.assertEqual(HTTPStatus.OK, accepted.status)
        conflicted = self._full_single(
            {
                "requestId": "core-manual-client-b",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_on",
                "coreControl": self._envelope(
                    request_id="core-manual-client-b", control=1
                ),
            }
        )
        self.assertEqual(HTTPStatus.CONFLICT, conflicted.status)
        self.assertEqual("revision_conflict", conflicted.payload["code"])
        self.assertEqual([], self.executor.calls)

    def test_core_unavailable_is_a_visible_typed_refusal(self):
        self._report("on")
        self.intake._branches.clear()
        response = self._full_single(
            {
                "requestId": "core-manual-down",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": self._envelope(request_id="core-manual-down"),
            }
        )
        self.assertEqual(HTTPStatus.SERVICE_UNAVAILABLE, response.status)
        self.assertEqual("unavailable", response.payload["code"])
        self.assertEqual([], self.executor.calls)

    def test_branch_without_profile_is_typed_not_a_crash(self):
        """A transferred resource whose branch has no capability profile
        yields the typed unavailable refusal, never AttributeError."""

        branch = stand.build_stand_branch(
            Path(self._tmp.name) / "no-profile", with_profile=False
        )
        self.addCleanup(branch.deactivate)
        self.intake.register_transferred_target(
            branch,
            stand.CoreManualTargetBinding(
                target_id="stand.light.other",
                resource_id="test_main",
                entity_id=stand.ENTITY_ID,
                room_id=stand.ROOM_ID,
            ),
        )
        response = self._full_single(
            {
                "requestId": "core-manual-noprofile",
                "targetId": "stand.light.other",
                "actionId": "turn_off",
                "coreControl": {
                    "version": 1,
                    "requestId": "core-manual-noprofile",
                    "expectedControlRevision": 1,
                    "expectedBindingRevision": 1,
                },
            }
        )
        self.assertEqual(HTTPStatus.SERVICE_UNAVAILABLE, response.status)
        self.assertEqual([], self.executor.calls)

    def test_other_principal_does_not_receive_foreign_replay(self):
        self._report("on")
        first = self._full_single(
            {
                "requestId": "core-manual-shared",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": self._envelope(
                    request_id="core-manual-shared"
                ),
            },
            user=self.tablet,
        )
        self.assertEqual(HTTPStatus.OK, first.status)
        second = self._full_single(
            {
                "requestId": "core-manual-shared",
                "targetId": stand.PUBLIC_TARGET_ID,
                "actionId": "turn_off",
                "coreControl": self._envelope(
                    request_id="core-manual-shared"
                ),
            },
            user=self.admin,
        )
        # The admin caller is a different intake scope: a fresh
        # admission with the current revision, never a foreign replay.
        self.assertEqual(HTTPStatus.OK, second.status)
        self.assertEqual([], self.executor.calls)


if __name__ == "__main__":
    unittest.main()
