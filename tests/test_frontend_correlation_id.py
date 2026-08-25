from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "custom_components" / "hausman_hub" / "frontend"
CORRELATION_JS = FRONTEND_DIR / "hausman-hub-correlation.js"
PANEL_JS = FRONTEND_DIR / "hausman-hub-panel.js"
CLIMATE_OVERVIEW_JS = FRONTEND_DIR / "hausman-hub-climate-overview.js"
DEVICE_INVENTORY_JS = FRONTEND_DIR / "hausman-hub-device-inventory.js"
DEVICE_DISCOVERY_JS = FRONTEND_DIR / "hausman-hub-device-discovery.js"
MATRIX_JSON = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "correlation-surfaces.json"
)
FIXTURES = ROOT / "fixtures"
OPERATION_IDS = (
    "applyHausmanHubContour",
    "setHausmanHubTemporaryTemperature",
    "setHausmanHubHomeClimateTargets",
    "executeHausmanHubClimateAction",
    "executeHausmanHubDeviceAction",
    "maintainHausmanHubDevice",
    "runHausmanHubScenario",
    "dispatchHausmanHubScenarioAction",
    "cancelHausmanHubScenarioUpcoming",
    "testHausmanHubVoiceGreeting",
)


def run_node_module(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("node", "--input-type=module", "-e", script, *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


class FrontendCorrelationSurfacesTest(unittest.TestCase):
    """The pinned frontend matrix stays in lockstep with the vendored contract."""

    def test_vendored_matrix_matches_contract_counters(self) -> None:
        matrix = json.loads(MATRIX_JSON.read_text(encoding="utf-8"))
        self.assertEqual(
            {"name": "hausman-hub-correlation-surfaces", "version": 1},
            matrix["contract"],
        )
        self.assertEqual("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", matrix["idPattern"])
        self.assertEqual(
            {
                "clientMayProvide": True,
                "serverGeneratesWhenMissing": True,
                "serverMustPreserveValidClientValue": True,
                "fallbackToRequestIdAllowed": True,
            },
            matrix["generation"],
        )
        commands = matrix["commands"]
        self.assertEqual(10, len(commands))
        self.assertEqual(list(OPERATION_IDS), [c["operationId"] for c in commands])
        for command in commands:
            self.assertIn(command["requestField"], ("correlation_id", "correlationId"))
            self.assertEqual(command["requestField"], command["receiptField"])
            self.assertIn(command["journalSource"], ("climate", "device", "scenario", "voice"))
        self.assertEqual("correlation_id", matrix["events"]["messageField"])
        self.assertEqual("data.correlation_id", matrix["events"]["commandReceiptField"])
        self.assertEqual(
            ["hello", "snapshot_invalidated", "scenario_changed", "critical_alert",
             "attention_alert", "command_receipt", "heartbeat"],
            matrix["events"]["allTypes"],
        )
        self.assertEqual("correlation_id", matrix["operationJournal"]["recordField"])
        self.assertIs(True, matrix["operationJournal"]["mustMatchCommandReceipt"])
        notifications = matrix["notifications"]
        self.assertEqual(5, len(notifications))
        self.assertEqual(
            ["device_discovery", "dashboard_alarm", "dashboard_event",
             "sse_alert", "scenario_notify_service"],
            [entry["surface"] for entry in notifications],
        )

    def test_pinned_snapshot_matches_vendored_matrix(self) -> None:
        script = r"""
          import fs from "node:fs";
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const matrix = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const snapshot = JSON.parse(JSON.stringify(mod.CORRELATION_SURFACES_SNAPSHOT));
          assert.deepStrictEqual(snapshot, matrix, "snapshot drift");
          if (!mod.validateCorrelationSurfaces(matrix)) throw new Error("vendored matrix rejected");
        """
        result = run_node_module(script, str(CORRELATION_JS), str(MATRIX_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_all_ten_command_surfaces_share_one_id(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          for (const command of mod.CORRELATION_SURFACES_SNAPSHOT.commands) {
            const id = mod.newCorrelationId();
            const request = { [command.requestField]: id };
            const receipt = { [command.receiptField]: id, status: "confirmed" };
            const journalRecord = { correlation_id: id, source: command.journalSource };
            const event = { type: "command_receipt", correlation_id: id, data: { correlation_id: id } };
            if (mod.extractCorrelationId(request, command.requestField) !== id) {
              fail(`${command.operationId}: request lost the ID`);
            }
            if (mod.extractCorrelationId(receipt, command.receiptField) !== id) {
              fail(`${command.operationId}: receipt lost the ID`);
            }
            if (!mod.correlationIdsOf(journalRecord).includes(id)) {
              fail(`${command.operationId}: journal record lost the ID`);
            }
            const eventIds = mod.correlationIdsOf(event);
            if (eventIds.filter((value) => value === id).length !== 2) {
              fail(`${command.operationId}: event message/data ID mismatch`);
            }
            const surface = mod.commandSurface(command.operationId);
            if (!surface || surface.requestField !== command.requestField) {
              fail(`${command.operationId}: surface lookup drifted`);
            }
          }
        """
        result = run_node_module(script, str(CORRELATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_generated_ids_are_valid_unique_and_non_private(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const ids = new Set();
          for (let index = 0; index < 200; index += 1) {
            const id = mod.newCorrelationId();
            if (!mod.isValidCorrelationId(id)) fail(`generated ID invalid: ${id}`);
            if (!id.startsWith("corr.panel.")) fail(`unexpected prefix: ${id}`);
            if (id.length > 128) fail(`generated ID too long: ${id.length}`);
            if (!/^corr\.panel\.[0-9a-f]{32}$/.test(id)) fail(`private-looking ID body: ${id}`);
            if (ids.has(id)) fail(`duplicate ID: ${id}`);
            ids.add(id);
          }
        """
        result = run_node_module(script, str(CORRELATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_invalid_id_blocked_before_api_call(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const invalid = ["", " bad", "bad id", "-lead", ".lead", "x".repeat(129),
            "контур", "corr@panel", "a/b", 42, null, undefined, {}, []];
          for (const value of invalid) {
            if (mod.isValidCorrelationId(value)) fail(`invalid ID accepted: ${JSON.stringify(value)}`);
            let thrown = false;
            try { mod.requireValidCorrelationId(value); } catch { thrown = true; }
            if (!thrown) fail(`requireValidCorrelationId passed: ${JSON.stringify(value)}`);
          }
          const calls = [];
          const fakeCallApi = async (...args) => { calls.push(args); };
          const invalidInPayload = ["", " bad", "bad id", "-lead", ".lead", "x".repeat(129),
            "контур", "corr@panel", "a/b", 42, null, {}, []];
          for (const value of invalidInPayload) {
            let blocked = false;
            try {
              const payload = mod.withCorrelationId(
                "hausman_hub/v1/device-actions", { correlationId: value });
              await fakeCallApi("POST", "hausman_hub/v1/device-actions", payload);
            } catch { blocked = true; }
            if (!blocked) fail(`invalid caller ID reached the transport: ${JSON.stringify(value)}`);
          }
          if (calls.length) fail("blocked command still called the API");
          const edge = ["a", "A" + "0".repeat(127), "corr.panel:room_1.x-y"];
          for (const value of edge) {
            if (!mod.isValidCorrelationId(value)) fail(`valid ID rejected: ${value}`);
          }
          if (mod.isValidCorrelationId("a".repeat(129))) fail("129 chars accepted");
        """
        result = run_node_module(script, str(CORRELATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_command_paths_inject_and_unknown_paths_stay_legacy(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const cases = [
            ["hausman_hub/v1/admin/panel/apply", "correlation_id"],
            ["hausman_hub/v1/admin/panel/temporary-temperature", "correlation_id"],
            ["hausman_hub/v1/climate/actions", "correlation_id"],
            ["hausman_hub/v1/device-actions", "correlationId"],
            ["hausman_hub/v1/admin/device-maintenance", "correlationId"],
            ["hausman_hub/v1/admin/scenarios/run", "correlationId"],
            ["hausman_hub/v1/scenarios/upcoming/cancel", "correlationId"],
          ];
          for (const [path, field] of cases) {
            if (mod.correlationFieldForPath(path) !== field) fail(`${path}: field drift`);
            const payload = { request_id: "panel-probe-1" };
            const sent = mod.withCorrelationId(path, payload);
            if (sent === payload) fail(`${path}: payload mutated in place`);
            if (payload[field] !== undefined) fail(`${path}: source payload polluted`);
            if (!mod.isValidCorrelationId(sent[field])) fail(`${path}: injected ID invalid`);
            if (sent.request_id !== "panel-probe-1") fail(`${path}: payload lost fields`);
            const kept = mod.withCorrelationId(path, { [field]: "caller.id-1" });
            if (kept[field] !== "caller.id-1") fail(`${path}: valid caller ID not preserved`);
          }
          for (const path of ["hausman_hub/v1/admin/panel", "hausman_hub/v1/dashboard",
              "hausman_hub/v1/admin/reset", "", null]) {
            if (mod.correlationFieldForPath(path) !== null) fail(`${path}: unknown path mapped`);
            const legacy = { request_id: "panel-probe-1" };
            if (mod.withCorrelationId(path, legacy) !== legacy) {
              fail(`${path}: unknown path payload changed`);
            }
          }
        """
        result = run_node_module(script, str(CORRELATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_notification_dedup_tracker_is_bounded(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const list = [
            { id: "n1", correlationId: "corr.a" },
            { id: "n2", correlationId: "corr.a" },
            { id: "n3", correlationId: "corr.b" },
            { id: "n4" },
            { id: "n5", correlationId: "bad id" },
            { id: "n6", correlation_id: "corr.c" },
            { id: "n7", correlation_id: "corr.c" },
          ];
          const deduped = mod.dedupeCorrelationNotifications(list);
          if (deduped.map((item) => item.id).join(",") !== "n1,n3,n4,n5,n6") {
            fail("dedup did not keep the first card per ID: " + deduped.map((i) => i.id));
          }
          if (mod.dedupeCorrelationNotifications(null).length) fail("garbage list survived");
          const tracker = mod.createCorrelationTracker(8);
          for (let index = 0; index < 20; index += 1) {
            if (!tracker.track(`corr.${index}`)) fail(`new ID ${index} deduped`);
          }
          if (tracker.size !== 8) fail(`tracker not bounded: ${tracker.size}`);
          if (tracker.has("corr.0")) fail("oldest entry not evicted");
          if (!tracker.has("corr.19")) fail("newest entry missing");
          if (tracker.track("corr.19")) fail("repeat event created a new card");
          if (tracker.track("corr.18")) fail("journal re-read created a new card");
          if (!tracker.track(undefined) || !tracker.track("bad id")) {
            fail("legacy payload without a valid ID was hidden");
          }
        """
        result = run_node_module(script, str(CORRELATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_legacy_payloads_without_id_keep_working(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const legacyReceipt = { status: "confirmed", contract: { name: "x", version: 1 } };
          if (mod.correlationIdsOf(legacyReceipt).length) fail("legacy receipt gained an ID");
          if (mod.extractCorrelationId(legacyReceipt, "correlation_id") !== null) {
            fail("legacy receipt extracted an ID");
          }
          const legacyList = [{ id: "n1" }, { id: "n2", correlationId: null }];
          if (mod.dedupeCorrelationNotifications(legacyList).length !== 2) {
            fail("legacy notifications were dropped");
          }
          for (const garbage of [null, undefined, 42, "corr", []]) {
            if (mod.correlationIdsOf(garbage).length) fail("garbage produced IDs");
            if (mod.withCorrelationId("hausman_hub/v1/device-actions", garbage) !== garbage) {
              fail("garbage payload was changed");
            }
          }
        """
        result = run_node_module(script, str(CORRELATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_semantic_mutations_fail_closed(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const matrix = () => JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const mutations = [
            ["contract name", (doc) => { doc.contract.name = "other"; }],
            ["contract version", (doc) => { doc.contract.version = 2; }],
            ["id pattern", (doc) => { doc.idPattern = "^.*$"; }],
            ["generation flag", (doc) => { doc.generation.serverMustPreserveValidClientValue = false; }],
            ["generation extra key", (doc) => { doc.generation.extra = true; }],
            ["nine commands", (doc) => { doc.commands.pop(); }],
            ["eleven commands", (doc) => { doc.commands.push({ ...doc.commands[0], operationId: "extraOp" }); }],
            ["duplicate operation", (doc) => { doc.commands[9].operationId = doc.commands[0].operationId; }],
            ["request field unknown", (doc) => { doc.commands[0].requestField = "correlation-id"; }],
            ["receipt field mismatch", (doc) => { doc.commands[0].receiptField = "correlationId"; }],
            ["journal source", (doc) => { doc.commands[0].journalSource = "panel"; }],
            ["bad schema path", (doc) => { doc.commands[0].requestSchema = "schemas/v2/x.json"; }],
            ["events field", (doc) => { doc.events.messageField = "correlationId"; }],
            ["events receipt field", (doc) => { doc.events.commandReceiptField = "correlation_id"; }],
            ["events type missing", (doc) => { doc.events.allTypes = doc.events.allTypes.slice(1); }],
            ["events type added", (doc) => { doc.events.allTypes.push("debug"); }],
            ["journal field", (doc) => { doc.operationJournal.recordField = "correlationId"; }],
            ["journal match off", (doc) => { doc.operationJournal.mustMatchCommandReceipt = false; }],
            ["four notifications", (doc) => { doc.notifications.pop(); }],
            ["notification surface", (doc) => { doc.notifications[0].surface = "panel_toast"; }],
            ["extra top key", (doc) => { doc.extra = []; }],
            ["missing commands", (doc) => { delete doc.commands; }],
          ];
          for (const [name, mutate] of mutations) {
            const doc = matrix();
            mutate(doc);
            if (mod.validateCorrelationSurfaces(doc)) fail(`mutation accepted: ${name}`);
            const normalized = mod.normalizeCorrelationSurfaces(doc);
            if (!mod.validateCorrelationSurfaces(normalized)) fail(`fail-closed invalid: ${name}`);
            if (JSON.stringify(normalized) !== JSON.stringify(mod.CORRELATION_SURFACES_SNAPSHOT)) {
              fail(`fail-closed is not the pinned snapshot: ${name}`);
            }
          }
          for (const raw of [null, undefined, 42, "matrix", [], {}]) {
            if (mod.validateCorrelationSurfaces(raw)) fail("garbage validated");
            if (!mod.validateCorrelationSurfaces(mod.normalizeCorrelationSurfaces(raw))) {
              fail("garbage did not fail closed");
            }
          }
        """
        result = run_node_module(script, str(CORRELATION_JS), str(MATRIX_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_contract_fixtures_carry_matching_ids(self) -> None:
        script = r"""
          import fs from "node:fs";
          import path from "node:path";
          const mod = await import(process.argv[1]);
          const fixtures = process.argv[2];
          const fail = (message) => { throw new Error(message); };
          const read = (...parts) => JSON.parse(fs.readFileSync(path.join(fixtures, ...parts), "utf8"));
          const pairs = [
            ["hausmanhub_contour_apply_v1/request.json", "hausmanhub_climate_control_receipt_v1/apply.json", "correlation_id"],
            ["hausmanhub_temporary_temperature_v1/request.json", "hausmanhub_climate_control_receipt_v1/temporary.json", "correlation_id"],
            ["hausmanhub_device_actions_v1/request.json", "hausmanhub_device_actions_v1/confirmed.json", "correlationId"],
          ];
          for (const [requestPath, receiptPath, field] of pairs) {
            const requestId = mod.extractCorrelationId(read(requestPath), field);
            const receiptId = mod.extractCorrelationId(read(receiptPath), field);
            if (!requestId || requestId !== receiptId) {
              fail(`${requestPath} -> ${receiptPath}: ID mismatch (${requestId} vs ${receiptId})`);
            }
          }
          const journal = read("hausmanhub_operation_journal_v1", "journal.json");
          const journalIds = mod.correlationIdsOf(journal);
          if (journalIds.length < 3) fail("journal records lost correlation IDs");
          if (!journalIds.includes("scenario-run-0003")) {
            fail("shadow scenario journal record lost its correlation ID");
          }
          const event = read("hausmanhub_event_stream_v1", "message.json");
          if (mod.extractCorrelationId(event, "correlation_id") !== "corr.event.42") {
            fail("event message lost its correlation ID");
          }
          const discovery = read("hausmanhub_device_discovery_v1", "device-discovery.json");
          if (!mod.correlationIdsOf(discovery).includes("corr.notice.0123456789abcdef")) {
            fail("device discovery notification lost its correlation ID");
          }
          const dashboard = read("hausmanhub_dashboard_v1", "dashboard.json");
          if (!mod.correlationIdsOf(dashboard).includes("corr.dashboard.event-1")) {
            fail("dashboard event lost its correlation ID");
          }
        """
        result = run_node_module(script, str(CORRELATION_JS), str(FIXTURES))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_panel_wiring_uses_correlation_module(self) -> None:
        panel_source = PANEL_JS.read_text(encoding="utf-8")
        climate_source = CLIMATE_OVERVIEW_JS.read_text(encoding="utf-8")
        inventory_source = DEVICE_INVENTORY_JS.read_text(encoding="utf-8")
        discovery_source = DEVICE_DISCOVERY_JS.read_text(encoding="utf-8")

        self.assertIn('from "./hausman-hub-correlation.js?v=', panel_source)
        self.assertIn("withCorrelationId(path, payload)", panel_source)
        self.assertIn("withCorrelationId(DEVICE_ACTIONS_API, payload)", panel_source)
        self.assertIn('from "./hausman-hub-correlation.js?v=', climate_source)
        self.assertEqual(4, climate_source.count("withCorrelationId(CLIMATE_ACTION_API"))
        self.assertIn('from "./hausman-hub-correlation.js?v=', inventory_source)
        self.assertIn("withCorrelationId(DEVICE_MAINTENANCE_API", inventory_source)
        self.assertIn('from "./hausman-hub-correlation.js?v=', discovery_source)
        self.assertIn("dedupeCorrelationNotifications(", discovery_source)


if __name__ == "__main__":
    unittest.main()
