from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "custom_components" / "hausman_hub" / "frontend"
UI_STATE_JS = FRONTEND_DIR / "hausman-hub-ui-state.js"
PANEL_JS = FRONTEND_DIR / "hausman-hub-panel.js"
FIXTURES_DIR = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "ui-state"
)
UI_STATES = ("loading", "stale", "offline", "pending", "confirmed", "failed", "disabled")


def run_node_module(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("node", "--input-type=module", "-e", script, *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


class FrontendUiStateTest(unittest.TestCase):
    """The pinned frontend UI state projection stays in lockstep with the golden fixtures."""

    def test_vendored_fixtures_cover_all_seven_states(self) -> None:
        for state in UI_STATES:
            path = FIXTURES_DIR / f"ui-state-{state}.json"
            self.assertTrue(path.is_file(), f"missing vendored fixture {path}")
            fixture = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({"name": "hausman-hub-ui-state", "version": 1}, fixture["contract"])
            self.assertEqual(state, fixture["state"])

    def test_pinned_snapshot_matches_vendored_fixtures(self) -> None:
        script = r"""
          import fs from "node:fs";
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const dir = process.argv[2];
          const snapshot = JSON.parse(JSON.stringify(mod.UI_STATE_SNAPSHOT));
          for (const state of ["loading", "stale", "offline", "pending", "confirmed", "failed", "disabled"]) {
            const fixture = JSON.parse(fs.readFileSync(`${dir}/ui-state-${state}.json`, "utf8"));
            assert.deepStrictEqual(snapshot[state], fixture, `snapshot drift for ${state}`);
          }
          assert.deepStrictEqual(Object.keys(snapshot).sort(),
            ["confirmed", "disabled", "failed", "loading", "offline", "pending", "stale"]);
        """
        result = run_node_module(script, str(UI_STATE_JS), str(FIXTURES_DIR))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_all_seven_fixtures_validate_with_contract_invariants(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const dir = process.argv[2];
          const fail = (message) => { throw new Error(message); };
          const expected = {
            loading: { scope: ["screen", "slice"], commandsAllowed: false, retryAction: "none" },
            stale: { scope: ["screen", "slice"], commandsAllowed: false, retryAction: "refresh" },
            offline: { scope: ["screen", "slice"], commandsAllowed: false, retryAction: "reconnect" },
            pending: { scope: ["command"], commandsAllowed: false, retryAction: "none" },
            confirmed: { scope: ["command"], commandsAllowed: true, retryAction: "none" },
            failed: { scope: ["screen", "slice", "command"], commandsAllowed: false,
              retryAction: ["refresh", "reconnect", "retry", "reauthenticate", "update_client"] },
            disabled: { scope: ["screen", "slice", "command"], commandsAllowed: false,
              retryAction: ["none", "reauthenticate", "update_client"] },
          };
          for (const [state, rule] of Object.entries(expected)) {
            const fixture = JSON.parse(fs.readFileSync(`${dir}/ui-state-${state}.json`, "utf8"));
            if (!mod.validateUiState(fixture)) fail(`golden fixture ${state} rejected`);
            if (!rule.scope.includes(fixture.scope)) fail(`scope mismatch for ${state}`);
            if (fixture.commandsAllowed !== rule.commandsAllowed) fail(`commandsAllowed mismatch for ${state}`);
            const retry = Array.isArray(rule.retryAction) ? rule.retryAction : [rule.retryAction];
            if (!retry.includes(fixture.retryAction)) fail(`retryAction mismatch for ${state}`);
          }
        """
        result = run_node_module(script, str(UI_STATE_JS), str(FIXTURES_DIR))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_command_gating_blocks_stale_offline_pending_failed_disabled(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const dir = process.argv[2];
          const fail = (message) => { throw new Error(message); };
          for (const state of ["loading", "stale", "offline", "pending", "failed", "disabled"]) {
            const fixture = JSON.parse(fs.readFileSync(`${dir}/ui-state-${state}.json`, "utf8"));
            if (mod.canExecuteCommand(fixture)) fail(`commands allowed in ${state}`);
          }
          const confirmed = JSON.parse(fs.readFileSync(`${dir}/ui-state-confirmed.json`, "utf8"));
          if (!mod.canExecuteCommand(confirmed)) fail("commands blocked in confirmed");
          if (!mod.canExecuteCommand(null)) fail("domain view blocked commands");
          if (!mod.canExecuteCommand(undefined)) fail("missing state blocked commands");
          if (mod.canExecuteCommand({ state: "confirmed", commandsAllowed: false })) {
            fail("unconfirmed flag combination allowed commands");
          }
        """
        result = run_node_module(script, str(UI_STATE_JS), str(FIXTURES_DIR))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_screen_projection_transitions_and_recovery(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const loading = mod.projectScreenUiState({ connected: true, hasData: false });
          if (loading.state !== "loading" || loading.commandsAllowed !== false) fail("no loading before first read");
          if (loading.message !== "Загружаем данные") fail("loading message drift");
          const domain = mod.projectScreenUiState({ connected: true, hasData: true });
          if (domain !== null) fail("successful read did not exit to the domain view");
          const stale = mod.projectScreenUiState({ connected: true, hasData: true, stale: true, dataAgeSeconds: 75 });
          if (stale.state !== "stale" || mod.canExecuteCommand(stale)) fail("stale did not block commands");
          if (mod.resolveRecoveryAction(stale) !== "refresh") fail("stale recovery is not refresh");
          if (stale.message !== "Данные устарели") fail("stale message drift");
          const offline = mod.projectScreenUiState({ connected: false, hasData: true, dataAgeSeconds: 180 });
          if (offline.state !== "offline" || offline.dataAgeSeconds !== 180) fail("offline lost cached age");
          if (mod.resolveRecoveryAction(offline) !== "reconnect") fail("offline recovery is not reconnect");
          if (offline.message !== "Нет связи с Home Assistant") fail("offline message drift");
          if (mod.canExecuteCommand(offline)) fail("offline allowed commands");
          const offlineNoCache = mod.projectScreenUiState({ connected: false, hasData: false });
          if (offlineNoCache.state !== "offline" || offlineNoCache.dataAgeSeconds !== null) {
            fail("offline without cache kept an age");
          }
          const badStale = mod.projectScreenUiState({ connected: true, hasData: true, stale: true, dataAgeSeconds: 0 });
          if (badStale.state === "stale") fail("stale accepted a zero age");
          if (!mod.validateUiState(badStale) || mod.canExecuteCommand(badStale)) {
            fail("invalid stale input did not fail closed");
          }
        """
        result = run_node_module(script, str(UI_STATE_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_command_lifecycle_pending_confirmed_failed(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const op = "operation-demo-001";
          const pending = mod.pendingUiState(op);
          if (pending.state !== "pending" || pending.operationId !== op) fail("pending lost operation id");
          if (mod.canExecuteCommand(pending)) fail("pending allowed commands");
          if (mod.resolveRecoveryAction(pending) !== "none") fail("pending must not auto-retry");
          if (pending.message !== "Команда принята, ждём подтверждение") fail("pending message drift");
          for (const bad of ["", null, undefined, 42, "has spaces"]) {
            const state = mod.pendingUiState(bad);
            if (state.state === "pending") fail(`pending accepted operation id ${JSON.stringify(bad)}`);
            if (mod.canExecuteCommand(state)) fail("invalid pending input allowed commands");
          }
          const confirmed = mod.confirmCommandUiState({ confirmed: true, operation_id: op }, op, true);
          if (confirmed.state !== "confirmed" || !mod.canExecuteCommand(confirmed)) {
            fail("receipt confirmed=true plus read-back did not confirm");
          }
          const httpOnly = mod.confirmCommandUiState(null, op, true);
          if (httpOnly.state === "confirmed") fail("HTTP 2xx without receipt confirmed the command");
          const noReadBack = mod.confirmCommandUiState({ confirmed: true, operation_id: op }, op, false);
          if (noReadBack.state === "confirmed") fail("receipt without read-back confirmed the command");
          if (noReadBack.state !== "pending") fail("missing read-back did not stay pending");
          const denied = mod.confirmCommandUiState({ confirmed: false, reason: "denied", operation_id: op }, op, false);
          if (denied.state !== "failed" || denied.operationId !== op) fail("failed lost the operation id");
          if (mod.canExecuteCommand(denied)) fail("failed allowed commands");
          const mismatch = mod.confirmCommandUiState({ confirmed: true, operation_id: "other-op" }, op, true);
          if (mismatch.state === "confirmed") fail("operation id mismatch confirmed the command");
          const failed = mod.failedUiState("command", "command_failed", op);
          if (failed.operationId !== op || failed.retryAction !== "retry") fail("failed state drift");
          if (failed.message !== "Не удалось выполнить команду") fail("failed message drift");
        """
        result = run_node_module(script, str(UI_STATE_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_disabled_is_never_masked(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          for (const scope of ["screen", "slice", "command"]) {
            const state = mod.disabledUiState(scope, "feature_disabled");
            if (state.state !== "disabled") fail(`disabled masked as ${state.state}`);
            if (mod.canExecuteCommand(state)) fail("disabled allowed commands");
            if (!["none", "reauthenticate", "update_client"].includes(state.retryAction)) {
              fail("disabled recovery outside the contract");
            }
          }
          const state = mod.disabledUiState("slice", "feature_disabled");
          if (state.message !== "Раздел недоступен") fail("disabled message drift");
          const normalized = mod.normalizeUiState(state);
          if (normalized.state !== "disabled") fail("normalize masked disabled");
          const badRetry = mod.disabledUiState("slice", "feature_disabled", "retry");
          if (badRetry.state === "disabled") fail("disabled accepted a retry recovery");
          if (!mod.validateUiState(badRetry)) fail("invalid disabled input did not fail closed");
        """
        result = run_node_module(script, str(UI_STATE_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_optional_slice_failure_is_isolated(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const projected = mod.projectSliceUiStates({
            energy: { ok: true },
            intercom: { ok: false },
            media: { ok: true },
          });
          if (projected.energy !== null || projected.media !== null) {
            fail("failing slice hid healthy slices");
          }
          if (!projected.intercom || projected.intercom.scope !== "slice") fail("slice failure lost its scope");
          if (mod.canExecuteCommand(projected.intercom)) fail("failing slice allowed commands");
          const withState = mod.projectSliceUiStates({
            assistant: { ok: false, uiState: mod.disabledUiState("slice", "feature_disabled") },
            climate: { ok: true },
          });
          if (withState.assistant.state !== "disabled") fail("slice-provided state not preserved");
          if (withState.climate !== null) fail("slice failure leaked into climate");
          const garbage = mod.projectSliceUiStates({ broken: { ok: false, uiState: { raw: true } }, fine: { ok: true } });
          if (!mod.validateUiState(garbage.broken)) fail("garbage slice state did not fail closed");
          if (garbage.fine !== null) fail("garbage slice state hid a healthy slice");
        """
        result = run_node_module(script, str(UI_STATE_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_semantic_mutations_fail_closed(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const dir = process.argv[2];
          const fail = (message) => { throw new Error(message); };
          const fixture = (state) => JSON.parse(fs.readFileSync(`${dir}/ui-state-${state}.json`, "utf8"));
          const mutations = [
            ["loading", (f) => { f.commandsAllowed = true; }],
            ["stale", (f) => { f.retryAction = "none"; }],
            ["stale", (f) => { f.dataAgeSeconds = 0; }],
            ["offline", (f) => { f.reasonCode = "data_stale"; }],
            ["pending", (f) => { f.operationId = null; }],
            ["pending", (f) => { f.scope = "screen"; }],
            ["confirmed", (f) => { f.commandsAllowed = false; }],
            ["confirmed", (f) => { f.operationId = ""; }],
            ["failed", (f) => { f.reasonCode = null; }],
            ["failed", (f) => { f.retryAction = "none"; }],
            ["disabled", (f) => { f.operationId = "operation-demo-001"; }],
            ["disabled", (f) => { f.retryAction = "refresh"; }],
            ["loading", (f) => { f.extraKey = "leak"; }],
            ["stale", (f) => { f.message = ""; }],
          ];
          for (const [state, mutate] of mutations) {
            const raw = fixture(state);
            mutate(raw);
            if (mod.validateUiState(raw)) fail(`mutation accepted for ${state}`);
            const normalized = mod.normalizeUiState(raw);
            if (!mod.validateUiState(normalized)) fail(`fail-closed state invalid for ${state}`);
            if (normalized.commandsAllowed !== false) fail(`fail-closed allowed commands for ${state}`);
            if (normalized.reasonCode !== "invalid_ui_state") fail(`fail-closed reason drift for ${state}`);
          }
          for (const raw of [null, undefined, 42, "stale", [], { state: "made_up" }]) {
            if (mod.validateUiState(raw)) fail("garbage validated");
            const normalized = mod.normalizeUiState(raw);
            if (!mod.validateUiState(normalized) || normalized.commandsAllowed !== false) {
              fail("garbage did not fail closed");
            }
          }
        """
        result = run_node_module(script, str(UI_STATE_JS), str(FIXTURES_DIR))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_panel_wiring_uses_ui_state_module(self) -> None:
        panel_source = PANEL_JS.read_text(encoding="utf-8")

        self.assertIn('from "./hausman-hub-ui-state.js?v=', panel_source)
        self.assertIn("canExecuteCommand(", panel_source)
        self.assertIn("_screenUiState(", panel_source)


if __name__ == "__main__":
    unittest.main()
