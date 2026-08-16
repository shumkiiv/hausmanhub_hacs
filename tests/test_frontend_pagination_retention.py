from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "custom_components" / "hausman_hub" / "frontend"
PAGINATION_JS = FRONTEND_DIR / "hausman-hub-pagination.js"
PANEL_JS = FRONTEND_DIR / "hausman-hub-panel.js"
ENERGY_JS = FRONTEND_DIR / "hausman-hub-energy.js"
MATRIX_JSON = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "pagination-retention.json"
)
FIXTURES = ROOT / "fixtures"
NEW_FIXTURES = FIXTURES / "hausmanhub_pagination_retention_v1"


def run_node_module(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("node", "--input-type=module", "-e", script, *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


class FrontendPaginationRetentionMatrixTest(unittest.TestCase):
    """The pinned frontend matrix stays in lockstep with the vendored contract."""

    def test_vendored_matrix_matches_contract_counters(self) -> None:
        matrix = json.loads(MATRIX_JSON.read_text(encoding="utf-8"))
        self.assertEqual(
            {"name": "hausman-hub-pagination-retention", "version": 1},
            matrix["contract"],
        )
        self.assertEqual(1, matrix["apiMajorVersion"])
        surfaces = {surface["id"]: surface for surface in matrix["surfaces"]}
        self.assertEqual(
            {"event_stream", "energy_history", "operation_journal",
             "dashboard_events", "manual_energy_readings"},
            set(surfaces),
        )
        stream = surfaces["event_stream"]
        self.assertEqual("last_event_id", stream["pagination"]["strategy"])
        self.assertEqual("Last-Event-ID", stream["pagination"]["requestHeader"])
        self.assertEqual(
            "snapshot_invalidated.data.replay_status=gap",
            stream["pagination"]["gapSignal"],
        )
        self.assertEqual(128, stream["retention"]["maxItems"])
        self.assertEqual(32, stream["retention"]["deliveryQueueLimit"])
        self.assertEqual(["hello", "heartbeat"], stream["retention"]["sessionOnlyTypes"])
        energy = surfaces["energy_history"]
        self.assertEqual(31, energy["pagination"]["maxWindowDays"])
        self.assertEqual(128, energy["pagination"]["maxSeries"])
        self.assertEqual(8928, energy["pagination"]["maxPointsPerSeries"])
        self.assertIs(True, energy["pagination"]["fromInclusive"])
        self.assertIs(True, energy["pagination"]["toExclusive"])
        journal = surfaces["operation_journal"]
        self.assertEqual("keyset", journal["pagination"]["strategy"])
        self.assertEqual("before_sequence", journal["pagination"]["cursorParameter"])
        self.assertEqual("page.next_before_sequence", journal["pagination"]["cursorResponseField"])
        self.assertEqual(100, journal["pagination"]["defaultLimit"])
        self.assertEqual(512, journal["pagination"]["maxLimit"])
        self.assertIs(True, journal["pagination"]["cursorExclusive"])
        self.assertEqual(512, journal["retention"]["maxItems"])
        dashboard = surfaces["dashboard_events"]
        self.assertEqual(100, dashboard["pagination"]["maxItems"])
        self.assertEqual(
            "getHausmanHubOperationJournal",
            dashboard["pagination"]["continuationOperationId"],
        )
        readings = surfaces["manual_energy_readings"]
        self.assertEqual(60, readings["pagination"]["maxItems"])
        self.assertIsNone(readings["pagination"]["continuationOperationId"])

    def test_pinned_snapshot_matches_vendored_matrix(self) -> None:
        script = r"""
          import fs from "node:fs";
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const matrix = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const snapshot = JSON.parse(JSON.stringify(mod.PAGINATION_RETENTION_SNAPSHOT));
          assert.deepStrictEqual(snapshot, matrix, "snapshot drift");
          if (!mod.validatePaginationRetentionMatrix(matrix)) {
            throw new Error("vendored matrix rejected");
          }
        """
        result = run_node_module(script, str(PAGINATION_JS), str(MATRIX_JSON))
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
            ["api major", (doc) => { doc.apiMajorVersion = 2; }],
            ["four surfaces", (doc) => { doc.surfaces.pop(); }],
            ["six surfaces", (doc) => { doc.surfaces.push({ ...doc.surfaces[0] }); }],
            ["queue limit", (doc) => { doc.surfaces[0].retention.deliveryQueueLimit = 64; }],
            ["replay size", (doc) => { doc.surfaces[0].retention.maxItems = 256; }],
            ["gap signal", (doc) => { doc.surfaces[0].pagination.gapSignal = "gap"; }],
            ["session type missing", (doc) => { doc.surfaces[0].retention.sessionOnlyTypes.pop(); }],
            ["window days", (doc) => { doc.surfaces[1].pagination.maxWindowDays = 62; }],
            ["to inclusive", (doc) => { doc.surfaces[1].pagination.toExclusive = false; }],
            ["journal cursor", (doc) => { doc.surfaces[2].pagination.cursorParameter = "after_sequence"; }],
            ["journal inclusive", (doc) => { doc.surfaces[2].pagination.cursorExclusive = false; }],
            ["journal order", (doc) => { doc.surfaces[2].pagination.order = "sequence_asc"; }],
            ["dashboard head", (doc) => { doc.surfaces[3].pagination.maxItems = 200; }],
            ["readings head", (doc) => { doc.surfaces[4].pagination.maxItems = 120; }],
            ["extra top key", (doc) => { doc.extra = []; }],
            ["missing surfaces", (doc) => { delete doc.surfaces; }],
            ["extra surface key", (doc) => { doc.surfaces[0].extra = true; }],
            ["null survival", (doc) => { doc.surfaces[4].retention.ttlSeconds = 60; }],
          ];
          for (const [name, mutate] of mutations) {
            const doc = matrix();
            mutate(doc);
            if (mod.validatePaginationRetentionMatrix(doc)) fail(`mutation accepted: ${name}`);
            const normalized = mod.normalizePaginationRetention(doc);
            if (!mod.validatePaginationRetentionMatrix(normalized)) {
              fail(`fail-closed invalid: ${name}`);
            }
            if (JSON.stringify(normalized) !== JSON.stringify(mod.PAGINATION_RETENTION_SNAPSHOT)) {
              fail(`fail-closed is not the pinned snapshot: ${name}`);
            }
          }
          for (const raw of [null, undefined, 42, "matrix", [], {}]) {
            if (mod.validatePaginationRetentionMatrix(raw)) fail("garbage validated");
            if (!mod.validatePaginationRetentionMatrix(mod.normalizePaginationRetention(raw))) {
              fail("garbage did not fail closed");
            }
          }
        """
        result = run_node_module(script, str(PAGINATION_JS), str(MATRIX_JSON))
        self.assertEqual(0, result.returncode, result.stderr)


class FrontendEventStreamClientTest(unittest.TestCase):
    """SSE cursor, gap flow, bounded queue and backoff against a fake source."""

    def test_reconnect_with_known_cursor_replays_only_following_events(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
          const cursors = [];
          const sources = [];
          const delivered = [];
          const timers = [];
          const client = mod.createEventStreamClient({
            connect: (cursor) => {
              cursors.push(cursor);
              const source = { closed: false, close() { this.closed = true; } };
              sources.push(source);
              return source;
            },
            onDomainEvent: (event) => { delivered.push(event.data.sequence); },
            onGap: () => fail("unexpected gap"),
            setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
            clearTimeout: () => {},
          });
          client.start();
          const first = sources[0];
          const message = (id, sequence) => ({
            id,
            data: JSON.stringify({ type: "critical_alert", data: { sequence } }),
          });
          first.onmessage({ id: "evt-s1-1", data: JSON.stringify({ type: "hello", data: { stream_id: "s1" } }) });
          first.onmessage(message("evt-s1-2", 1));
          first.onmessage(message("evt-s1-3", 2));
          await tick();
          if (client.lastEventId !== "evt-s1-3") fail(`cursor drift: ${client.lastEventId}`);
          first.onerror();
          if (timers.length !== 1 || timers[0].ms !== 1000) fail("backoff schedule broken");
          timers[0].fn();
          if (cursors[1] !== "evt-s1-3") fail(`reconnect cursor lost: ${cursors[1]}`);
          const second = sources[1];
          // Server replays strictly after the cursor; a stale duplicate is dropped.
          second.onopen();
          second.onmessage({ id: "evt-s1-1", data: JSON.stringify({ type: "hello", data: { stream_id: "s1" } }) });
          second.onmessage(message("evt-s1-3", 2));
          second.onmessage(message("evt-s1-4", 3));
          await tick();
          if (JSON.stringify(delivered) !== "[1,2,3]") {
            fail(`duplicate replay reached the user: ${JSON.stringify(delivered)}`);
          }
          if (client.streamId !== "s1") fail("stream id lost on reconnect");
          client.stop();
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_gap_flow_single_snapshot_refresh_and_new_stream_id(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
          const cursors = [];
          const sources = [];
          const delivered = [];
          const gaps = [];
          const timers = [];
          const client = mod.createEventStreamClient({
            connect: (cursor) => {
              cursors.push(cursor);
              const source = { close() {} };
              sources.push(source);
              return source;
            },
            onDomainEvent: (event) => { delivered.push(event.type); },
            onGap: () => { gaps.push(client.streamId); },
            setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
            clearTimeout: () => {},
          });
          client.start();
          // Cursor from a foreign stream: the server answers with hello of the
          // new stream plus the gap signal instead of a replay.
          const first = sources[0];
          first.onmessage({ id: "evt-s2-1", data: JSON.stringify({ type: "hello", data: { stream_id: "s2" } }) });
          const gap = (id) => ({
            id,
            data: JSON.stringify({
              type: "snapshot_invalidated",
              data: { reason: "state_changed", replay_status: "gap" },
            }),
          });
          first.onmessage(gap("evt-s2-2"));
          first.onmessage(gap("evt-s2-3"));
          first.onmessage({ id: "evt-s2-4", data: JSON.stringify({ type: "attention_alert", data: {} }) });
          await tick();
          await tick();
          if (gaps.length !== 1) fail(`snapshot refreshed ${gaps.length} times`);
          if (gaps[0] !== "s2") fail("new stream id not accepted before the gap flow");
          if (JSON.stringify(delivered) !== '["attention_alert"]') {
            fail(`gap signal leaked into domain events: ${JSON.stringify(delivered)}`);
          }
          if (client.lastEventId !== "evt-s2-4") fail("cursor not advanced past the gap");
          client.stop();
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_slow_consumer_bounded_queue_recovers_via_gap(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
          const cursors = [];
          const sources = [];
          const gaps = [];
          const timers = [];
          let release = null;
          let processed = 0;
          let maxPending = 0;
          const client = mod.createEventStreamClient({
            connect: (cursor) => {
              cursors.push(cursor);
              const source = { close() {} };
              sources.push(source);
              return source;
            },
            onDomainEvent: async () => {
              processed += 1;
              await new Promise((resolve) => { release = resolve; });
            },
            onGap: () => { gaps.push(1); },
            setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
            clearTimeout: () => {},
          });
          client.start();
          const first = sources[0];
          const push = (index) => first.onmessage({
            id: `evt-s1-${index}`,
            data: JSON.stringify({ type: "critical_alert", data: { index } }),
          });
          push(1); // blocks the pump behind the slow consumer
          for (let index = 2; index <= 40; index += 1) {
            push(index);
            if (client.pendingCount > maxPending) maxPending = client.pendingCount;
          }
          if (maxPending > 32) fail(`queue grew past 32: ${maxPending}`);
          if (client.lastEventId !== null) fail("stale cursor kept after overflow");
          await tick();
          await tick();
          if (gaps.length !== 1) fail(`overflow gap flow ran ${gaps.length} times`);
          if (timers.length !== 1) fail("overflow did not schedule a reconnect");
          timers[0].fn();
          if (cursors.length !== 2 || cursors[1] !== null) {
            fail(`reconnect reused a stale cursor: ${JSON.stringify(cursors)}`);
          }
          if (processed !== 1) fail(`dropped events were still delivered: ${processed}`);
          client.stop();
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_backoff_is_capped_at_30_seconds(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const expected = [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000];
          expected.forEach((delay, index) => {
            const actual = mod.sseBackoffDelayMs(index + 1);
            if (actual !== delay) fail(`attempt ${index + 1}: ${actual} != ${delay}`);
          });
          if (mod.sseBackoffDelayMs(100) !== 30000) fail("backoff exceeded the cap");
          if (mod.sseBackoffDelayMs(0) !== 1000) fail("zero attempt mishandled");
          if (mod.sseBackoffDelayMs(Number.NaN) !== 1000) fail("garbage attempt mishandled");
          if (mod.sseBackoffDelayMs(3, 500, 5000) !== 2000) fail("custom base broken");
          if (mod.sseBackoffDelayMs(99, 500, 5000) !== 5000) fail("custom cap broken");
          if (mod.SSE_BACKOFF_CAP_MS !== 30000) fail("exported cap drifted");
          if (mod.SSE_QUEUE_LIMIT !== 32 || mod.SSE_REPLAY_RETENTION !== 128) {
            fail("exported stream limits drifted");
          }
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_hello_and_heartbeat_never_reach_user_history(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
          const delivered = [];
          const streamIds = [];
          const sources = [];
          const client = mod.createEventStreamClient({
            connect: () => { const source = { close() {} }; sources.push(source); return source; },
            onDomainEvent: (event) => { delivered.push(event.type); },
            onStreamId: (id) => { streamIds.push(id); },
          });
          client.start();
          const source = sources[0];
          source.onmessage({ id: "evt-a-1", data: JSON.stringify({ type: "hello", data: { stream_id: "a", replay: { max_events: 128 } } }) });
          source.onmessage({ id: "evt-a-2", data: JSON.stringify({ type: "heartbeat", data: { sequence: 1 } }) });
          source.onmessage({ id: "evt-a-3", data: JSON.stringify({ type: "command_receipt", data: {} }) });
          source.onmessage({ id: "evt-a-4", data: JSON.stringify({ type: "heartbeat", data: { sequence: 2 } }) });
          await tick();
          if (JSON.stringify(delivered) !== '["command_receipt"]') {
            fail(`session events reached the user history: ${JSON.stringify(delivered)}`);
          }
          if (client.lastEventId !== "evt-a-4") fail("heartbeat did not advance the cursor");
          if (JSON.stringify(streamIds) !== '["a"]') fail("stream id callback drifted");
          if (!mod.isSessionOnlyEvent("hello") || !mod.isSessionOnlyEvent("heartbeat")) {
            fail("session-only classifier drifted");
          }
          if (mod.isSessionOnlyEvent("critical_alert")) fail("domain event classified as session-only");
          client.stop();
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)


class FrontendEnergyWindowsTest(unittest.TestCase):
    """Energy history windows: bounded, adjacent, merged without duplicates."""

    def test_window_validation_rejects_over_31_days_before_render(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const DAY = 24 * 60 * 60 * 1000;
          const ok = mod.validateEnergyWindow(0, 31 * DAY);
          if (ok.fromMs !== 0 || ok.toMs !== 31 * DAY) fail("31-day window rejected");
          mod.validateEnergyWindow(0, DAY);
          const invalid = [
            [0, 31 * DAY + 1],
            [0, 90 * DAY],
            [DAY, DAY],
            [DAY, 0],
            [Number.NaN, DAY],
            [0, Number.POSITIVE_INFINITY],
            ["2026-08-01", "2026-08-02"],
            [null, undefined],
          ];
          for (const [fromMs, toMs] of invalid) {
            let thrown = false;
            try { mod.validateEnergyWindow(fromMs, toMs); } catch { thrown = true; }
            if (!thrown) fail(`invalid window accepted: ${fromMs} -> ${toMs}`);
          }
          if (mod.ENERGY_WINDOW_MAX_DAYS !== 31) fail("exported window limit drifted");
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_split_windows_are_adjacent_and_legal(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const DAY = 24 * 60 * 60 * 1000;
          const single = mod.splitEnergyWindows(0, DAY);
          if (single.length !== 1 || single[0].fromMs !== 0 || single[0].toMs !== DAY) {
            fail("short range split");
          }
          const windows = mod.splitEnergyWindows(0, 365 * DAY);
          if (windows.length !== 12) fail(`year split into ${windows.length} windows`);
          windows.forEach((window, index) => {
            mod.validateEnergyWindow(window.fromMs, window.toMs);
            if (index > 0 && window.fromMs !== windows[index - 1].toMs) {
              fail(`window ${index} is not adjacent to the previous one`);
            }
          });
          if (windows[0].fromMs !== 0 || windows[11].toMs !== 365 * DAY) {
            fail("split lost range edges");
          }
          const boundary = mod.splitEnergyWindows(0, 62 * DAY);
          if (boundary.length !== 2 || boundary[1].toMs - boundary[1].fromMs !== 31 * DAY) {
            fail("exact multiple produced a broken tail window");
          }
          let thrown = false;
          try { mod.splitEnergyWindows(DAY, 0); } catch { thrown = true; }
          if (!thrown) fail("reversed range split");
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_merge_dedupes_boundary_and_never_fabricates_zeros(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const point = (at, value) => ({ at, value });
          const windowA = {
            contract: { name: "hausman-hub-energy-history", version: 1 },
            page: { strategy: "time_window" },
            retention: { mode: "source_bound" },
            series: [{
              sourceId: "sensor_a", deviceId: "dev_a", metric: "power", unit: "W",
              scope: "device",
              points: [
                point("2026-08-01T00:00:00Z", 10),
                point("2026-08-02T00:00:00Z", 12),
                point("2026-08-31T00:00:00Z", 9),
              ],
            }],
          };
          const windowB = {
            series: [{
              sourceId: "sensor_a", deviceId: "dev_a", metric: "power", unit: "W",
              scope: "device",
              points: [
                point("2026-08-31T00:00:00Z", 9),
                point("2026-09-02T00:00:00Z", 11),
              ],
            }],
          };
          const merged = mod.mergeEnergyHistoryResponses([windowA, windowB]);
          if (merged.series.length !== 1) fail(`series duplicated: ${merged.series.length}`);
          const points = merged.series[0].points;
          const stamps = points.map((item) => item.at);
          if (new Set(stamps).size !== stamps.length) fail("boundary point duplicated");
          if (JSON.stringify(stamps) !== JSON.stringify([
            "2026-08-01T00:00:00Z",
            "2026-08-02T00:00:00Z",
            "2026-08-31T00:00:00Z",
            "2026-09-02T00:00:00Z",
          ])) fail(`ascending order broken: ${JSON.stringify(stamps)}`);
          // 2026-08-03..2026-08-30 stay missing: no zero points were fabricated.
          if (points.some((item) => item.value === 0)) fail("missing points became zeros");
          if (!merged.contract || !merged.page || !merged.retention) {
            fail("merged response lost page/retention metadata");
          }
          // Null values from Recorder stay null, never zero.
          const withNull = mod.mergeEnergyHistoryResponses([{ series: [{
            sourceId: "s", deviceId: "d", metric: "power", unit: "W", scope: "device",
            points: [point("2026-08-01T00:00:00Z", null)],
          }] }]);
          if (withNull.series[0].points[0].value !== null) fail("null point rewritten");
          const garbage = mod.mergeEnergyHistoryResponses([null, 42, { series: "x" }]);
          if (garbage.series.length !== 0) fail("garbage response produced series");
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)


class FrontendOperationJournalPagerTest(unittest.TestCase):
    """Keyset pager: exclusive cursor, filters kept, no repeated sequences."""

    def test_two_pages_no_repeated_sequence_and_filter_kept(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const calls = [];
          const pages = [
            {
              sequence: 4,
              page: { order: "sequence_desc", limit: 3, returned: 3, has_more: true,
                next_before_sequence: 2, retained_records: 4, retention_limit: 512 },
              records: [
                { sequence: 4, source: "voice" },
                { sequence: 3, source: "voice" },
                { sequence: 2, source: "voice" },
              ],
            },
            {
              sequence: 4,
              page: { order: "sequence_desc", limit: 3, returned: 1, has_more: false,
                next_before_sequence: null, retained_records: 4, retention_limit: 512 },
              records: [{ sequence: 1, source: "voice" }],
            },
          ];
          const callApi = async (method, path) => {
            calls.push({ method, path });
            return pages[calls.length - 1];
          };
          const result = await mod.readOperationJournal(callApi, { limit: 3, source: "voice" });
          if (result.pages !== 2) fail(`pages: ${result.pages}`);
          const sequences = result.records.map((record) => record.sequence);
          if (JSON.stringify(sequences) !== "[4,3,2,1]") {
            fail(`repeated or lost sequence: ${JSON.stringify(sequences)}`);
          }
          const second = new URLSearchParams(calls[1].path.split("?")[1]);
          if (second.get("before_sequence") !== "2") fail("cursor not exclusive");
          if (second.get("source") !== "voice") fail("filter lost between pages");
          if (second.get("limit") !== "3") fail("limit lost between pages");
          if (calls[0].method !== "GET") fail("journal read used a mutating method");
          if (!calls[0].path.startsWith("hausman_hub/v1/admin/operations?")) {
            fail(`journal path drifted: ${calls[0].path}`);
          }
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_pager_fail_closed_on_repeats_order_and_cursor(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const expectThrow = async (name, pages) => {
            const callApi = async () => pages.shift();
            let thrown = false;
            try { await mod.readOperationJournal(callApi, {}); } catch { thrown = true; }
            if (!thrown) fail(`mutation accepted: ${name}`);
          };
          await expectThrow("repeated sequence", [
            { page: { has_more: true, next_before_sequence: 3 },
              records: [{ sequence: 5 }, { sequence: 3 }] },
            { page: { has_more: false }, records: [{ sequence: 3 }, { sequence: 1 }] },
          ]);
          await expectThrow("ascending order", [
            { page: { has_more: false }, records: [{ sequence: 1 }, { sequence: 2 }] },
          ]);
          await expectThrow("invalid continuation", [
            { page: { has_more: true, next_before_sequence: 0 }, records: [{ sequence: 5 }] },
          ]);
          await expectThrow("non-exclusive continuation", [
            { page: { has_more: true, next_before_sequence: 7 }, records: [{ sequence: 5 }] },
          ]);
          for (const options of [{ limit: 0 }, { limit: 513 }, { limit: 2.5 },
              { beforeSequence: 0 }, { beforeSequence: -1 }, { source: 42 }]) {
            let thrown = false;
            try { mod.journalPageQuery(options); } catch { thrown = true; }
            if (!thrown) fail(`invalid query accepted: ${JSON.stringify(options)}`);
          }
          const query = mod.journalPageQuery({ limit: 512, beforeSequence: 9, source: "climate" });
          if (query.limit !== "512" || query.before_sequence !== "9" || query.source !== "climate") {
            fail(`query encoding broken: ${JSON.stringify(query)}`);
          }
          const defaults = mod.journalPageQuery();
          if (defaults.limit !== "100" || "before_sequence" in defaults) {
            fail(`default query broken: ${JSON.stringify(defaults)}`);
          }
          if (mod.JOURNAL_LIMIT_DEFAULT !== 100 || mod.JOURNAL_LIMIT_MAX !== 512) {
            fail("exported journal limits drifted");
          }
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)


class FrontendPaginationFixturesTest(unittest.TestCase):
    """Legacy fixtures without metadata and new fixtures with metadata pass."""

    def test_legacy_fixtures_without_metadata(self) -> None:
        script = r"""
          import fs from "node:fs";
          import path from "node:path";
          const mod = await import(process.argv[1]);
          const fixtures = process.argv[2];
          const fail = (message) => { throw new Error(message); };
          const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
          const read = (...parts) => JSON.parse(fs.readFileSync(path.join(fixtures, ...parts), "utf8"));
          // Legacy SSE message without replay metadata: a plain domain event.
          const legacyEvent = read("hausmanhub_event_stream_v1", "message.json");
          if (legacyEvent.data && legacyEvent.data.replay_status) fail("legacy fixture grew metadata");
          const delivered = [];
          const gaps = [];
          const sources = [];
          const client = mod.createEventStreamClient({
            connect: () => { const source = { close() {} }; sources.push(source); return source; },
            onDomainEvent: (event) => { delivered.push(event.type); },
            onGap: () => { gaps.push(1); },
          });
          client.start();
          sources[0].onmessage({ id: legacyEvent.id, data: JSON.stringify(legacyEvent) });
          await tick();
          if (JSON.stringify(delivered) !== '["snapshot_invalidated"]') {
            fail(`legacy event not delivered: ${JSON.stringify(delivered)}`);
          }
          if (gaps.length) fail("legacy event triggered the gap flow");
          if (client.lastEventId !== "evt-42") fail("legacy event did not set the cursor");
          client.stop();
          // Legacy journal without page metadata: one page, then stop.
          const legacyJournal = read("hausmanhub_operation_journal_v1", "journal.json");
          if (legacyJournal.page) fail("legacy journal grew page metadata");
          const journalCalls = [];
          const result = await mod.readOperationJournal(
            async (method, path) => { journalCalls.push(path); return legacyJournal; },
            {},
          );
          if (result.pages !== 1 || journalCalls.length !== 1) {
            fail("legacy journal was paginated");
          }
          if (result.records.map((record) => record.sequence).join(",") !== "4,3,2,1") {
            fail("legacy journal records lost or reordered");
          }
          // Legacy meter fixture: head projection keeps at most 60 entries.
          const meter = read("hausmanhub_energy_meter_v1", "energy-meter.json");
          const history = Array.isArray(meter.history) ? meter.history : [];
          const projected = mod.headProjection(history, mod.MANUAL_READINGS_LIMIT);
          if (projected.length !== Math.min(history.length, 60)) fail("meter projection broken");
          if (mod.headProjection(null, 60).length !== 0) fail("garbage projection survived");
          if (mod.DASHBOARD_EVENTS_LIMIT !== 100 || mod.MANUAL_READINGS_LIMIT !== 60) {
            fail("head projection limits drifted");
          }
        """
        result = run_node_module(script, str(PAGINATION_JS), str(FIXTURES))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_new_fixtures_with_metadata(self) -> None:
        script = r"""
          import fs from "node:fs";
          import path from "node:path";
          const mod = await import(process.argv[1]);
          const fixtures = process.argv[2];
          const fail = (message) => { throw new Error(message); };
          const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
          const read = (...parts) => JSON.parse(fs.readFileSync(path.join(fixtures, ...parts), "utf8"));
          const dir = "hausmanhub_pagination_retention_v1";
          // Hello with retention metadata: session-only, stream id accepted,
          // replay metadata parsed but never rendered as a user event.
          const hello = read(dir, "event-stream-hello-retention.json");
          if (!hello.data || !hello.data.replay) fail("hello fixture lost replay metadata");
          if (hello.data.replay.max_events !== 128 || hello.data.replay.delivery_queue_limit !== 32) {
            fail("hello retention metadata drifted");
          }
          const delivered = [];
          const streamIds = [];
          const sources = [];
          const client = mod.createEventStreamClient({
            connect: () => { const source = { close() {} }; sources.push(source); return source; },
            onDomainEvent: (event) => { delivered.push(event.type); },
            onStreamId: (id) => { streamIds.push(id); },
          });
          client.start();
          sources[0].onmessage({ id: hello.id, data: JSON.stringify(hello) });
          await tick();
          if (delivered.length) fail("hello reached the user history");
          if (JSON.stringify(streamIds) !== '["stream-demo"]') fail("stream id not accepted");
          client.stop();
          // Energy history with page/retention metadata merges unchanged.
          const history = read(dir, "energy-history.json");
          if (!history.page || !history.retention) fail("energy fixture lost metadata");
          const merged = mod.mergeEnergyHistoryResponses([history]);
          if (merged.series.length !== 2) fail("energy fixture series lost");
          if (!merged.page || merged.page.maxWindowDays !== 31) fail("energy page metadata lost");
          const selection = merged.series.find((series) => series.scope === "selection");
          if (!selection || selection.points.length !== 2) fail("selection series broken");
          // Operation journal page with keyset metadata continues once and stops.
          const firstPage = read(dir, "operation-journal.json");
          if (!firstPage.page || firstPage.page.has_more !== true) fail("journal fixture lost page metadata");
          const secondPage = {
            ...firstPage,
            page: { ...firstPage.page, has_more: false, returned: 1, next_before_sequence: null },
            records: [{ sequence: 1, source: "device", correlation_id: "device-action-0001" }],
          };
          const calls = [];
          const result = await mod.readOperationJournal(async (method, path) => {
            calls.push(path);
            return calls.length === 1 ? firstPage : secondPage;
          }, { limit: 3 });
          if (result.records.map((record) => record.sequence).join(",") !== "4,3,2,1") {
            fail("fixture pagination repeated or lost a sequence");
          }
          if (!calls[1].includes("before_sequence=2")) fail("fixture cursor not continued");
        """
        result = run_node_module(script, str(PAGINATION_JS), str(FIXTURES))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_fetch_transport_sends_cursor_and_parses_frames(self) -> None:
        script = r"""
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const requests = [];
          const messages = [];
          const frame = (id, type, data) =>
            `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify({ id, type, data })}\n\n`;
          const payload = new TextEncoder().encode(
            `: comment\n\n${frame("evt-s-1", "hello", { stream_id: "s" })}${frame("evt-s-2", "heartbeat", { sequence: 1 })}`,
          );
          globalThis.fetch = async (url, options) => {
            requests.push({ url, headers: options.headers });
            return {
              ok: true,
              body: {
                getReader: () => {
                  let sent = false;
                  return {
                    read: async () => sent
                      ? { done: true }
                      : (sent = true, { value: payload, done: false }),
                    cancel: async () => {},
                  };
                },
              },
            };
          };
          await mod.fetchEventStream("/api/hausman_hub/v1/events", {
            token: "token-1",
            lastEventId: "evt-s-0",
            onMessage: (message) => { messages.push(message); },
          });
          if (requests.length !== 1) fail("transport sent more than one request");
          if (requests[0].headers["Last-Event-ID"] !== "evt-s-0") fail("cursor header lost");
          if (requests[0].headers.Authorization !== "Bearer token-1") fail("auth header lost");
          if (messages.length !== 2 || messages[0].id !== "evt-s-1" || messages[1].id !== "evt-s-2") {
            fail(`frames parsed wrong: ${JSON.stringify(messages)}`);
          }
          const parsed = JSON.parse(messages[0].data);
          if (parsed.type !== "hello" || parsed.data.stream_id !== "s") fail("hello frame corrupted");
        """
        result = run_node_module(script, str(PAGINATION_JS))
        self.assertEqual(0, result.returncode, result.stderr)


class FrontendPaginationWiringTest(unittest.TestCase):
    """Panel and energy wiring references the module without new commands."""

    def test_panel_and_energy_wiring(self) -> None:
        panel_source = PANEL_JS.read_text(encoding="utf-8")
        energy_source = ENERGY_JS.read_text(encoding="utf-8")
        pagination_source = PAGINATION_JS.read_text(encoding="utf-8")

        self.assertIn('from "./hausman-hub-pagination.js?v=', panel_source)
        self.assertIn("createEventStreamClient({", panel_source)
        self.assertIn("createFetchEventSource(EVENT_STREAM_PATH", panel_source)
        self.assertIn("this._startEventStream()", panel_source)
        self.assertIn("this._eventStreamClient.stop()", panel_source)
        self.assertIn('from "./hausman-hub-pagination.js?v=', energy_source)
        self.assertIn("splitEnergyWindows(start.getTime(), end.getTime())", energy_source)
        self.assertIn("mergeEnergyHistoryResponses(responses)", energy_source)

    def test_frontend_sends_no_physical_commands(self) -> None:
        pagination_source = PAGINATION_JS.read_text(encoding="utf-8")
        self.assertNotIn('"POST"', pagination_source)
        self.assertNotIn("callService", pagination_source)
        self.assertNotIn("turn_on", pagination_source)
        self.assertNotIn("turn_off", pagination_source)


if __name__ == "__main__":
    unittest.main()
