from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "custom_components" / "hausman_hub" / "frontend"
TAXONOMY_JS = FRONTEND_DIR / "hausman-hub-error-taxonomy.js"
CLIMATE_OVERVIEW_JS = FRONTEND_DIR / "hausman-hub-climate-overview.js"
CORRELATION_JS = FRONTEND_DIR / "hausman-hub-correlation.js"
CANONICAL_JSON = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "error-taxonomy.json"
)


def run_node_module(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("node", "--input-type=module", "-e", script, *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


class FrontendErrorTaxonomyTest(unittest.TestCase):
    """The pinned frontend taxonomy stays in lockstep with the canonical inventory."""

    def test_pinned_snapshot_matches_canonical_inventory(self) -> None:
        script = r"""
          import fs from "node:fs";
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const canonical = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const snapshot = JSON.parse(JSON.stringify(mod.ERROR_TAXONOMY_SNAPSHOT));
          assert.deepStrictEqual(snapshot, canonical);
        """
        result = run_node_module(script, str(TAXONOMY_JS), str(CANONICAL_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_all_canonical_codes_resolve_with_safe_message_only(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const canonical = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const fail = (message) => { throw new Error(message); };
          for (const entry of canonical.entries) {
            const error = {
              status_code: entry.httpStatus,
              body: {
                contract: { name: "hausman-hub-error", version: 1 },
                code: entry.code,
                message: "RAW server text token-123 /etc/passwd",
                retryable: entry.retryable,
                details: { leakedKey: "secret", expectedRevision: 1, actualRevision: 2, ageSeconds: 5, operationId: "op", observedState: "on", attempts: 2, remainingSeconds: 3, retryAfterSeconds: 9 },
              },
            };
            const policy = mod.resolveApiError(error);
            if (policy.code !== entry.code) fail(`code mismatch for ${entry.code}`);
            if (policy.clientState !== entry.clientState) fail(`clientState mismatch for ${entry.code}`);
            if (policy.recoveryAction !== entry.recoveryAction) fail(`recoveryAction mismatch for ${entry.code}`);
            if (policy.retryPolicy !== entry.retryPolicy) fail(`retryPolicy mismatch for ${entry.code}`);
            if (policy.retryable !== entry.retryable) fail(`retryable mismatch for ${entry.code}`);
            if (policy.safeMessage !== entry.safeMessage) fail(`safeMessage mismatch for ${entry.code}`);
            if (policy.safeMessage.includes("RAW server text")) fail(`raw message leaked for ${entry.code}`);
            const keys = Object.keys(policy.details);
            if (keys.includes("leakedKey")) fail(`unknown detail leaked for ${entry.code}`);
            if (entry.detailsPolicy === "discard" && keys.length) fail(`discard kept details for ${entry.code}`);
            if (entry.detailsPolicy === "allowlisted") {
              for (const key of keys) {
                if (!entry.allowedDetailKeys.includes(key)) fail(`detail ${key} not allowlisted for ${entry.code}`);
              }
            }
            if (mod.apiErrorMessage(error) !== entry.safeMessage) fail(`apiErrorMessage mismatch for ${entry.code}`);
          }
        """
        result = run_node_module(script, str(TAXONOMY_JS), str(CANONICAL_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_unknown_code_and_raw_text_fail_closed(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const unknown = mod.resolveApiError({
            status: 409,
            body: {
              contract: { name: "hausman-hub-error", version: 1 },
              code: "not_a_real_code",
              message: "raw detail",
              retryable: true,
              details: { expectedRevision: 1 },
            },
          });
          if (unknown.code !== "internal_error" || unknown.clientState !== "failed") {
            fail("unknown code did not fail closed to internal_error/failed");
          }
          if (Object.keys(unknown.details).length) fail("unknown code kept details");
          for (const raw of [
            new Error("network boom"),
            { error: "Request error", status_code: undefined, body: undefined },
            { status_code: 500, body: "<html>token abc123</html>" },
            null,
            "plain string",
          ]) {
            const policy = mod.resolveApiError(raw);
            const known = mod.taxonomyEntry(policy.code);
            if (!known) fail(`unresolved code ${policy.code}`);
            if (policy.safeMessage !== known.safeMessage) fail("safe message drift");
            if (/token|boom|html/.test(policy.safeMessage)) fail("raw text rendered");
            if (Object.keys(policy.details).length) fail("raw failure kept details");
          }
          const unavailable = mod.resolveApiError({ status_code: 503, body: { message: "raw" } });
          if (unavailable.code !== "unavailable" || unavailable.clientState !== "offline") {
            fail("503 legacy route did not map to unavailable/offline");
          }
          const conflict = mod.resolveApiError({ status: 409 });
          if (conflict.code !== "conflict" || conflict.clientState !== "stale"
              || conflict.retryPolicy !== "after_refresh") {
            fail("409 legacy route did not map to conflict/stale/after_refresh");
          }
          const rejected = mod.resolveApiError({ status_code: 422 });
          if (rejected.code !== "invalid_request") fail("422 did not map to invalid_request");
        """
        result = run_node_module(script, str(TAXONOMY_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_no_automatic_retry_for_physical_commands(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const canonical = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const fail = (message) => { throw new Error(message); };
          for (const entry of canonical.entries) {
            for (const kind of ["command", "write", "save"]) {
              if (mod.automaticRetryAllowed(entry, kind)) {
                fail(`automatic retry allowed for ${entry.code} as ${kind}`);
              }
            }
            const readRetry = mod.automaticRetryAllowed(entry, "read");
            const expected = entry.retryPolicy === "read_only";
            if (readRetry !== expected) fail(`read retry mismatch for ${entry.code}`);
          }
        """
        result = run_node_module(script, str(TAXONOMY_JS), str(CANONICAL_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_aliases_and_climate_receipts_resolve_by_surface_and_code(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const canonical = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const fail = (message) => { throw new Error(message); };
          let aliasCount = 0;
          for (const entry of canonical.entries) {
            for (const alias of entry.aliases) {
              aliasCount += 1;
              const resolved = mod.taxonomyAliasEntry(alias.surface, alias.code);
              if (!resolved || resolved.code !== entry.code) {
                fail(`alias ${alias.surface}:${alias.code} resolved to ${resolved && resolved.code}`);
              }
            }
          }
          if (aliasCount === 0) fail("canonical inventory has no aliases");
          if (mod.taxonomyAliasEntry("climate_operation_reason", "made_up")) fail("unknown alias resolved");
          if (mod.taxonomyAliasEntry("wrong_surface", "climate_disabled")) fail("wrong surface resolved");
          for (const entry of canonical.entries) {
            for (const alias of entry.aliases) {
              if (alias.surface !== "climate_operation_reason") continue;
              const receipt = { confirmed: false, reason: alias.code, operation_id: "0123456789abcdef0123456789abcdef", status: "pending" };
              const policy = mod.resolveClimateReceipt(receipt);
              if (!policy || policy.code !== entry.code) {
                fail(`receipt reason ${alias.code} mapped to ${policy && policy.code}`);
              }
              if (policy.operationId !== receipt.operation_id) fail("receipt operation id lost");
            }
          }
          if (mod.resolveClimateReceipt({ confirmed: true }) !== null) fail("confirmed receipt is a failure");
          if (mod.resolveClimateReceipt(null) !== null) fail("empty receipt is a failure");
          const unknownReceipt = mod.resolveClimateReceipt({ confirmed: false, reason: "mystery" });
          if (unknownReceipt.code !== "internal_error" || unknownReceipt.clientState !== "failed") {
            fail("unknown receipt reason did not fail closed");
          }
        """
        result = run_node_module(script, str(TAXONOMY_JS), str(CANONICAL_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_climate_conflict_pending_and_confirmation_flows(self) -> None:
        script = r"""
          import fs from "node:fs";
          const taxonomyUrl = `data:text/javascript;base64,${Buffer.from(
            fs.readFileSync(process.argv[1], "utf8")).toString("base64")}`;
          const correlationUrl = `data:text/javascript;base64,${Buffer.from(
            fs.readFileSync(process.argv[3], "utf8")).toString("base64")}`;
          let source = fs.readFileSync(process.argv[2], "utf8");
          source = source
            .replace(/^import .*library-hero.*$/m, "const createLibraryHero = () => null;")
            .replace(/^import .*modal.*$/m, "const enhanceAppendedModal = () => null;")
            .replace(/^import .*room-icons.*$/m, 'const roomIconName = () => ""; const roomSvgIcon = () => null;')
            .replace(/^import .*climate-side.*$/m, "const renderClimateSide = () => null;")
            .replace(/^import .*correlation.*$/m,
              `import { withCorrelationId } from "${correlationUrl}";`)
            .replace(/^import .*error-taxonomy.*$/m,
              `import { pendingOperationId, requiresSnapshotRefresh, resolveApiError, resolveClimateReceipt } from "${taxonomyUrl}";`);
          const mod = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
          const fail = (message) => { throw new Error(message); };
          const envelope = (code, details) => ({
            contract: { name: "hausman-hub-error", version: 1 },
            code,
            message: "raw server text",
            retryable: true,
            ...(details ? { details } : {}),
          });
          const panel = (postBehavior) => {
            const calls = [];
            const state = {
              _busy: false,
              _climateRuntime: { state_revision: 7 },
              _climateModePendingKey: null,
              _climateSyncPending: false,
              _notice: "",
              _error: false,
              loads: 0,
              calls,
              _render() {},
              async _load() { this.loads += 1; },
              _hass: {
                callApi: async (method, path, payload) => {
                  calls.push({ method, path });
                  if (method === "POST") return postBehavior();
                  return { contract: { name: "hausman-hub-climate-operation", version: 1 }, status: "confirmed" };
                },
              },
            };
            return state;
          };
          const manualArgs = ["living", "device-1", true];

          const conflictPanel = panel(() => {
            const error = new Error("Response error: 409");
            error.status = 409;
            error.body = envelope("revision_conflict", { expectedRevision: 1, actualRevision: 3, raw: "secret" });
            throw error;
          });
          if (await mod.setClimateManualMode(conflictPanel, ...manualArgs) !== false) fail("conflict reported success");
          if (conflictPanel._notice !== "Настройки изменились на другом клиенте. Обновите данные и повторите попытку.") {
            fail(`conflict notice mismatch: ${conflictPanel._notice}`);
          }
          if (conflictPanel.loads < 1) fail("conflict did not refresh the snapshot first");
          if (conflictPanel.calls.filter((call) => call.method === "POST").length !== 1) {
            fail("conflict retried the physical command automatically");
          }

          const pendingPanel = panel(() => {
            const error = new Error("Response error: 409");
            error.status_code = 409;
            error.body = envelope("climate_operation_pending", { operationId: "0123456789abcdef0123456789abcdef", extra: "drop" });
            throw error;
          });
          if (await mod.setClimateManualMode(pendingPanel, ...manualArgs) !== false) fail("pending reported success");
          if (pendingPanel._notice !== "Предыдущая климатическая команда ещё проверяется.") {
            fail(`pending notice mismatch: ${pendingPanel._notice}`);
          }
          const operationReads = pendingPanel.calls.filter((call) =>
            call.method === "GET" && call.path === "hausman_hub/v1/climate/operations/0123456789abcdef0123456789abcdef");
          if (operationReads.length !== 1) fail("pending did not read the existing operation");
          if (pendingPanel.calls.filter((call) => call.method === "POST").length !== 1) {
            fail("pending reissued the physical command");
          }

          const confirmationPanel = panel(() => ({
            confirmed: false,
            reason: "read_back_mismatch",
            operation_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            status: "timed_out",
          }));
          if (await mod.setClimateManualMode(confirmationPanel, ...manualArgs) !== false) fail("confirmation failure reported success");
          if (confirmationPanel._notice !== "Устройство не подтвердило команду. Проверьте его состояние перед повтором.") {
            fail(`confirmation notice mismatch: ${confirmationPanel._notice}`);
          }
          if (confirmationPanel.loads < 1) fail("confirmation failure skipped the readback refresh");
          if (confirmationPanel.calls.filter((call) => call.method === "POST").length !== 1) {
            fail("confirmation failure retried the physical command automatically");
          }

          const confirmedPanel = panel(() => ({ confirmed: true }));
          if (await mod.setClimateManualMode(confirmedPanel, ...manualArgs) !== true) fail("confirmed action failed");
          if (confirmedPanel._notice !== "Ручной режим включён.") fail("success notice lost");
        """
        result = run_node_module(script, str(TAXONOMY_JS), str(CLIMATE_OVERVIEW_JS), str(CORRELATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_panel_wiring_uses_taxonomy_module(self) -> None:
        panel_source = (FRONTEND_DIR / "hausman-hub-panel.js").read_text(encoding="utf-8")
        climate_source = CLIMATE_OVERVIEW_JS.read_text(encoding="utf-8")

        self.assertIn('from "./hausman-hub-error-taxonomy.js?v=', panel_source)
        self.assertIn('from "./hausman-hub-error-taxonomy.js?v=', climate_source)
        self.assertNotIn("body.message", panel_source)
        self.assertNotIn("body.message", climate_source)
        self.assertNotIn("responseText", panel_source)
        self.assertNotIn("responseText", climate_source)
        self.assertNotIn("setTimeout(() => this._post(", panel_source)
        self.assertNotIn("setTimeout(() => setClimate", climate_source)
        self.assertIn("pendingOperationId(policy)", climate_source)
        self.assertIn('policy.code === "command_not_confirmed"', climate_source)


if __name__ == "__main__":
    unittest.main()
