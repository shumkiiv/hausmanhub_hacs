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
