"""Behavioral checks of the HACS coreControl envelope (HH-014.b S-06.a client).

The real frontend modules run under Node: the helper module and the shared
device-actions executor with a recording API double. String search does not
substitute for execution (assignment §6).
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "custom_components" / "hausman_hub" / "frontend"
CORE_CONTROL_JS = FRONTEND_DIR / "hausman-hub-core-control.js"
DEVICE_ACTIONS_JS = FRONTEND_DIR / "hausman-hub-device-actions.js"
LIGHTING_SIDE_JS = FRONTEND_DIR / "hausman-hub-lighting-side.js"
SINGLE_PATH = "/api/hausman_hub/v1/device-actions"


def run_node_module(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("node", "--input-type=module", "-e", script, *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def run_js_with_assert(script: str, *args: str) -> None:
    result = run_node_module(script, *args)
    if result.returncode != 0:
        raise AssertionError(f"node failed:\n{result.stderr}\n{result.stdout}")


class CoreControlHelperTest(unittest.TestCase):
    """The pure helper: projection, decision and envelope building."""

    def test_projection_decision_matrix(self) -> None:
        script = r"""
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          // Not transferred: the legacy path stays untouched.
          assert.strictEqual(mod.coreControlStateOf({target_id: "x"}), null);
          assert.strictEqual(mod.coreControlStateOf(undefined), null);
          // Ready: exact normalized pair.
          assert.deepStrictEqual(
            mod.coreControlStateOf({coreControlState: {
              version: 1, status: "ready", controlRevision: 12, bindingRevision: 3,
            }}),
            {status: "ready", controlRevision: 12, bindingRevision: 3},
          );
          // Unavailable.
          assert.deepStrictEqual(
            mod.coreControlStateOf({coreControlState: {version: 1, status: "unavailable"}}),
            {status: "unavailable"},
          );
          // Invalid projections are refused, never guessed.
          const invalid = [
            {coreControlState: {version: 1, status: "ready", controlRevision: -1, bindingRevision: 0}},
            {coreControlState: {version: 1, status: "ready", controlRevision: 1.5, bindingRevision: 0}},
            {coreControlState: {version: 1, status: "ready", controlRevision: 0}},
            {coreControlState: {version: 2, status: "ready", controlRevision: 1, bindingRevision: 1}},
            {coreControlState: {version: 1, status: "unavailable", controlRevision: 5}},
            {coreControlState: {version: 1, status: "other"}},
            {coreControlState: "ready"},
            // Unsafe integers and non-integers are rejected outright.
            {coreControlState: {version: 1, status: "ready", controlRevision: 9007199254740993, bindingRevision: 0}},
            {coreControlState: {version: 1, status: "ready", controlRevision: NaN, bindingRevision: 0}},
            {coreControlState: {version: 1, status: "ready", controlRevision: Infinity, bindingRevision: 0}},
          ];
          for (const target of invalid) {
            const prepared = mod.coreControlForTarget(target);
            assert.ok(prepared && prepared.refused, JSON.stringify(target));
          }
          // Envelope building: the caller's requestId is the action id.
          const payload = mod.withCoreControl(
            {targetId: "t", actionId: "turn_off", requestId: "req-1", contract: {name: "x", version: 1}},
            {state: {status: "ready", controlRevision: 12, bindingRevision: 3}},
            "req-1",
          );
          assert.deepStrictEqual(payload.coreControl, {
            version: 1,
            requestId: "req-1",
            expectedControlRevision: 12,
            expectedBindingRevision: 3,
          });
          // Null prepared leaves the payload unchanged.
          const untouched = {targetId: "t", requestId: "r"};
          assert.strictEqual(mod.withCoreControl(untouched, null, "r"), untouched);
        """
        run_js_with_assert(script, str(CORE_CONTROL_JS))

    def test_real_executor_sends_envelope_for_transferred_target(self) -> None:
        """The real executor module builds the full request with the
        envelope; a conflict is shown once and never retried."""

        script = r"""
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const calls = [];
          const owner = {
            _busy: false,
            _notice: "",
            _error: false,
            _scenarios: {catalog: {devices: [
              {target_id: "stand.light.main", entity_id: "light.stand_main", actions: [
                {action_id: "turn_on"}, {action_id: "turn_off"}],
                coreControlState: {version: 1, status: "ready", controlRevision: 12, bindingRevision: 3},
              },
            ]}},
            _hass: {
              callApi: async (method, path, payload, headers) => {
                calls.push({method, path, payload, headers});
                const error = new Error("409");
                error.as_json = {code: "revision_conflict", message: "conflict"};
                throw error;
              },
            },
            async _load() {},
            _render() {},
          };
          await mod.executeDeviceAction(owner, "stand.light.main", "turn_off", null);
          assert.strictEqual(calls.length, 1, "no automatic retry after a conflict");
          const call = calls[0];
          assert.ok(
            String(call.headers["Content-Type"]).includes("device-action-request.full+json"),
            "transferred target uses the full form",
          );
          assert.strictEqual(call.payload.targetId, "stand.light.main");
          assert.deepStrictEqual(call.payload.coreControl, {
            version: 1,
            requestId: call.payload.requestId,
            expectedControlRevision: 12,
            expectedBindingRevision: 3,
          });
          assert.notStrictEqual(call.payload.coreControl.requestId, undefined);
        """
        run_js_with_assert(script, str(DEVICE_ACTIONS_JS))

    def test_executor_refuses_transferred_target_without_revisions(self) -> None:
        script = r"""
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const calls = [];
          const owner = {
            _busy: false, _notice: "", _error: false,
            _scenarios: {catalog: {devices: [
              {target_id: "stand.light.main", entity_id: "light.stand_main", actions: [
                {action_id: "turn_off"}],
                coreControlState: {version: 1, status: "unavailable"},
              },
            ]}},
            _hass: {callApi: async (...args) => { calls.push(args); return {}; }},
            async _load() {}, _render() {},
          };
          await mod.executeDeviceAction(owner, "stand.light.main", "turn_off", null);
          assert.strictEqual(calls.length, 0, "unavailable revisions never POST");
          assert.ok(owner._notice.length > 0, "the refusal is visible");
          // Invalid projection is refused the same way.
          owner._scenarios.catalog.devices[0].coreControlState = {
            version: 1, status: "ready", controlRevision: -5, bindingRevision: 1,
          };
          await mod.executeDeviceAction(owner, "stand.light.main", "turn_off", null);
          assert.strictEqual(calls.length, 0);
        """
        run_js_with_assert(script, str(DEVICE_ACTIONS_JS))

    def test_non_transferred_target_keeps_legacy_behavior(self) -> None:
        script = r"""
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const calls = [];
          const owner = {
            _busy: false, _notice: "", _error: false,
            _scenarios: {catalog: {devices: [
              {target_id: "light.neighbor", entity_id: "light.neighbor", actions: [
                {action_id: "turn_on"}, {action_id: "turn_off"}]},
            ]}},
            _hass: {
              callApi: async (method, path, payload, headers) => {
                calls.push({payload, headers});
                return {status: "confirmed"};
              },
            },
            async _load() {}, _render() {},
          };
          // Physical action: full form without the envelope (legacy behavior).
          await mod.executeDeviceAction(owner, "light.neighbor", "turn_on", null);
          assert.strictEqual(calls.length, 1);
          assert.strictEqual(calls[0].payload.coreControl, undefined);
          assert.ok("requestId" in calls[0].payload, "physical action keeps the full form");
          // Dry run: legacy form, no full headers, no envelope.
          await mod.executeDeviceAction(owner, "light.neighbor", "turn_on", null, {dryRun: true});
          assert.strictEqual(calls.length, 2);
          assert.strictEqual(calls[1].headers, undefined);
          assert.strictEqual(calls[1].payload.coreControl, undefined);
          assert.strictEqual(calls[1].payload.dryRun, true);
        """
        run_js_with_assert(script, str(DEVICE_ACTIONS_JS))

    def test_dry_run_of_transferred_target_uses_full_form_with_envelope(self) -> None:
        script = r"""
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const calls = [];
          const owner = {
            _busy: false, _notice: "", _error: false,
            _scenarios: {catalog: {devices: [
              {target_id: "stand.light.main", entity_id: "light.stand_main", actions: [
                {action_id: "turn_off"}],
                coreControlState: {version: 1, status: "ready", controlRevision: 12, bindingRevision: 3},
              },
            ]}},
            _hass: {
              callApi: async (method, path, payload, headers) => {
                calls.push({payload, headers});
                return {status: "accepted", dryRun: true};
              },
            },
            async _load() {}, _render() {},
          };
          await mod.executeDeviceAction(owner, "stand.light.main", "turn_off", null, {dryRun: true});
          assert.strictEqual(calls.length, 1);
          assert.ok(
            String(calls[0].headers["Content-Type"]).includes("device-action-request.full+json"),
            "dry run of a transferred target uses the full form",
          );
          assert.deepStrictEqual(calls[0].payload.coreControl, {
            version: 1,
            requestId: calls[0].payload.requestId,
            expectedControlRevision: 12,
            expectedBindingRevision: 3,
          });
          assert.strictEqual(calls[0].payload.dryRun, true);
        """
        run_js_with_assert(script, str(DEVICE_ACTIONS_JS))

    def test_caller_supplied_snapshot_wins_over_catalog(self) -> None:
        """The sequential group snapshots envelopes before the first
        POST: a catalog refresh must not rewrite a prepared action."""

        script = r"""
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const helper = await import(process.argv[2]);
          const calls = [];
          const owner = {
            _busy: false, _notice: "", _error: false,
            _scenarios: {catalog: {devices: [
              {target_id: "stand.light.main", entity_id: "light.stand_main", actions: [
                {action_id: "turn_off"}],
                coreControlState: {version: 1, status: "ready", controlRevision: 99, bindingRevision: 9},
              },
            ]}},
            _hass: {
              callApi: async (method, path, payload) => {
                calls.push(payload);
                return {status: "accepted"};
              },
            },
            async _load() {}, _render() {},
          };
          const snapshot = helper.coreControlForTarget({
            coreControlState: {version: 1, status: "ready", controlRevision: 12, bindingRevision: 3},
          });
          await mod.executeDeviceAction(
            owner, "stand.light.main", "turn_off", null, {coreControl: snapshot},
          );
          assert.strictEqual(calls.length, 1);
          assert.strictEqual(calls[0].coreControl.expectedControlRevision, 12);
          assert.strictEqual(calls[0].coreControl.expectedBindingRevision, 3);
        """
        run_js_with_assert(script, str(DEVICE_ACTIONS_JS), str(CORE_CONTROL_JS))


class EndToEndCatalogToCoreTest(unittest.TestCase):
    """The mandatory connected check (assignment §6 row 1): the real
    catalog view answer feeds the real JS executor, and its exact
    request is admitted by the real DeviceActionView. Removing
    coreControl from the JS breaks this test."""

    def test_catalog_get_feeds_real_js_and_real_view(self) -> None:
        import asyncio
        import importlib
        import json
        import sys
        from datetime import datetime, timezone
        from types import SimpleNamespace

        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "tests"))
        sys.path.insert(0, str(ROOT / "tests" / "core"))
        import test_local_summary_access as harness
        import test_manual_input_binding as stand

        harness_mod = harness
        api_test = importlib.import_module("test_core_manual_input_api")
        api_test.CoreManualInputApiTest.setUpClass()
        case = api_test.CoreManualInputApiTest(
            "test_catalog_publishes_core_control_state"
        )
        case.setUp()
        # The catalog view checks service readiness: reuse the same
        # prepared stub as the catalog test (registry non-None,
        # readiness snapshot present).
        case.service._registry = object()  # noqa: SLF001
        case.service.__dict__["_catalog_readiness"] = {"state": "ready"}
        from types import SimpleNamespace as _NS

        catalog_entry_stub = _NS(
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
        case.service.current_catalog = lambda: SimpleNamespace(
            devices={stand.PUBLIC_TARGET_ID: catalog_entry_stub}
        )

        async def list_scenarios_stub():
            return []

        case.service.async_list_scenarios = list_scenarios_stub
        try:
            # 1. Real catalog GET through the real ScenarioCatalogView.
            scenario_api = importlib.import_module(
                "custom_components.hausman_hub.scenario_api"
            )
            catalog_view = scenario_api.ScenarioCatalogView(case.hass)
            admin = harness_mod.reader_user("system-admin", admin=True)
            catalog_path = "/api/hausman_hub/v1/admin/scenarios/catalog"
            catalog_response = asyncio.run(
                catalog_view.get(
                    harness_mod.FakeRequest(
                        "192.168.1.20", admin, catalog_path
                    )
                )
            )
            self.assertEqual(200, catalog_response.status)
            devices = {
                item["target_id"]: item
                for item in catalog_response.payload["devices"]
            }
            self.assertIn(stand.PUBLIC_TARGET_ID, devices)
            catalog_entry = devices[stand.PUBLIC_TARGET_ID]

            # 2. The real JS executor prepares the exact request from
            # that catalog entry (no hand-written envelope).
            entry_json = json.dumps(catalog_entry, ensure_ascii=False)
            node_script = r"""
              import assert from "node:assert";
              const mod = await import(process.argv[1]);
              const entry = JSON.parse(process.argv[2]);
              const calls = [];
              const owner = {
                _busy: false, _notice: "", _error: false,
                _scenarios: {catalog: {devices: [entry]}},
                _hass: {
                  callApi: async (method, path, payload, headers) => {
                    calls.push({payload, headers});
                    return {status: "accepted"};
                  },
                },
                async _load() {}, _render() {},
              };
              await mod.executeDeviceAction(
                owner, entry.target_id, "turn_off", null,
              );
              if (calls.length !== 1) throw new Error("expected one POST");
              console.log(JSON.stringify({call: calls[0]}));
            """
            node_result = run_node_module(
                node_script, str(DEVICE_ACTIONS_JS), entry_json
            )
            self.assertEqual(0, node_result.returncode, node_result.stderr)
            call = json.loads(node_result.stdout)["call"]

            # 3. The exact prepared request is admitted by the real
            # DeviceActionView: one operation, coreOperation echo, zero
            # legacy executor involvement, nothing sent.
            executor_calls: list = []

            def _fail_if_called(*args, **kwargs):
                executor_calls.append(args)
                raise AssertionError("legacy executor was called")

            case.service.async_execute_device_action = _fail_if_called

            full_request_media = (
                "application/vnd.hausmanhub.device-action-request.full+json"
            )
            full_response_media = (
                "application/vnd.hausmanhub.device-action-receipt.full+json"
            )
            request = harness_mod.FakeJsonRequest(
                "192.168.1.20",
                case.tablet,
                SINGLE_PATH,
                call["payload"],
                content_type=full_request_media,
                accept=full_response_media,
            )
            view_response = asyncio.run(case.view.post(request))
            self.assertEqual(200, view_response.status, view_response.payload)
            self.assertTrue(view_response.payload["accepted"])
            self.assertFalse(view_response.payload["commandSent"])
            core_operation = view_response.payload["coreOperation"]
            self.assertTrue(core_operation["operationId"])
            self.assertNotEqual(
                call["payload"]["requestId"],
                core_operation["operationId"],
            )
            self.assertEqual([], executor_calls)
            operation = case.branch.core_service.operation(
                core_operation["operationId"]
            )
            self.assertEqual("CANCELLED", operation.phase.name)
            self.assertEqual("core_not_active", operation.reason_code)

            # 4. Removing coreControl from the JS payload breaks the
            # flow exactly as the matrix demands: without the envelope
            # the same request is refused before acceptance.
            broken_payload = {
                key: value
                for key, value in call["payload"].items()
                if key != "coreControl"
            }
            broken_request = harness_mod.FakeJsonRequest(
                "192.168.1.20",
                case.tablet,
                SINGLE_PATH,
                broken_payload,
                content_type=full_request_media,
                accept=full_response_media,
            )
            broken_response = asyncio.run(case.view.post(broken_request))
            self.assertEqual(409, broken_response.status)
        finally:
            case.tearDown()
            api_test.CoreManualInputApiTest.doCleanups(case)
            api_test.CoreManualInputApiTest.tearDownClass()

    def test_group_snapshot_survives_catalog_refresh_between_members(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "tests"))
        sys.path.insert(0, str(ROOT / "tests" / "core"))
        import test_local_summary_access as harness

        group_script = r"""
          import assert from "node:assert";
          const side = await import(process.argv[1]);
          const sent = [];
          const devices = [
            {name: "Основной", entityId: "light.stand_main"},
          ];
          let catalogRevision = 12;
          const panel = {
            _coreSessionId: 0,
            _notice: "",
            _catalogTargets(device) {
              return [{
                target_id: "stand.light.main",
                entity_id: device.entityId,
                actions: [{action_id: "turn_off"}],
                coreControlState: {
                  version: 1, status: "ready",
                  controlRevision: catalogRevision, bindingRevision: 3,
                },
              }];
            },
            async _executeDeviceAction(targetId, actionId, value, options) {
              sent.push({targetId, options});
            },
          };
          const entries = [{device: devices[0], targetId: "stand.light.main"}, {device: devices[0], targetId: "stand.light.main"}];
          entries[1] = {device: devices[0], targetId: "stand.light.main"};
          // A catalog refresh DURING the first member must not rewrite
          // the envelope already captured for the second member.
          panel.async_executeDeviceActionOverride = null;
          panel._executeDeviceAction = async (targetId, actionId, value, options) => {
            sent.push({targetId, options});
            catalogRevision = 99; // refresh fires while member A runs
          };
          await side.executeTurnOff(panel, entries);
          assert.strictEqual(sent.length, 2);
          assert.strictEqual(
            sent[0].options.coreControl.state.controlRevision, 12,
          );
          assert.strictEqual(
            sent[1].options.coreControl.state.controlRevision, 12,
            "the refresh rewrote a prepared group envelope",
          );
          // A session replacement DURING the group stops the remaining
          // members: they never run under the new session.
          const beforeSessionTest = sent.length;
          panel._coreSessionId = 0;
          panel._executeDeviceAction = async (targetId, actionId, value, options) => {
            sent.push({targetId, options});
            panel._coreSessionId = 7; // the connection is replaced while A runs
          };
          await side.executeTurnOff(panel, entries);
          assert.strictEqual(
            sent.length, beforeSessionTest + 1,
            "the group continued in a new session",
          );
        """
        result = run_node_module(
            group_script, str(LIGHTING_SIDE_JS)
        )
        self.assertEqual(0, result.returncode, result.stderr)


class FrontendSyntaxTest(unittest.TestCase):
    """Every changed frontend file must parse under Node."""

    def test_changed_frontend_files_parse(self) -> None:
        for path in (CORE_CONTROL_JS, DEVICE_ACTIONS_JS, LIGHTING_SIDE_JS):
            result = subprocess.run(
                ("node", "--check", str(path)),
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(0, result.returncode, f"{path.name}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
