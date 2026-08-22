from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "custom_components" / "hausman_hub" / "frontend"
DEVICE_FEATURES_JS = FRONTEND_DIR / "hausman-hub-device-features.js"
PANEL_JS = FRONTEND_DIR / "hausman-hub-panel.js"
FIXTURE_JSON = (
    ROOT
    / "fixtures"
    / "hausmanhub_device_feature_matrix_v1"
    / "document.json"
)
READ_ONLY_TYPES = (
    "alarm_control_panel", "binary_sensor", "camera", "select", "sensor", "sun",
)


def run_node_module(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("node", "--input-type=module", "-e", script, *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


class FrontendDeviceFeatureMatrixTest(unittest.TestCase):
    """The pinned frontend matrix stays in lockstep with the golden fixture."""

    def test_vendored_fixture_matches_release_counters(self) -> None:
        fixture = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(
            {"name": "hausman-hub-device-feature-matrix", "version": 1},
            fixture["contract"],
        )
        self.assertEqual(1, fixture["apiMajorVersion"])
        self.assertEqual(
            {
                "semantics": "upper_bound",
                "runtimeActionSource": "scenario_catalog",
                "unknownTypePolicy": "read_only",
                "unknownControlPolicy": "hidden",
                "clientMaySynthesizeActions": False,
            },
            fixture["authority"],
        )
        types = fixture["deviceTypes"]
        controls = [control for entry in types for control in entry["controls"]]
        bindings = [action for control in controls for action in control["actionIds"]]
        self.assertEqual(19, len(types))
        self.assertEqual(28, len(controls))
        self.assertEqual(45, len(bindings))
        self.assertEqual(29, len(set(bindings)))
        read_only = [entry["type"] for entry in types if entry["readOnly"]]
        self.assertEqual(sorted(READ_ONLY_TYPES), sorted(read_only))
        for entry in types:
            if entry["readOnly"]:
                self.assertEqual([], entry["controls"], entry["type"])
            for control in entry["controls"]:
                self.assertIs(True, control["receiptRequired"], control["id"])

    def test_pinned_snapshot_matches_vendored_fixture(self) -> None:
        script = r"""
          import fs from "node:fs";
          import assert from "node:assert";
          const mod = await import(process.argv[1]);
          const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const snapshot = JSON.parse(JSON.stringify(mod.DEVICE_FEATURE_MATRIX_SNAPSHOT));
          assert.deepStrictEqual(snapshot, fixture, "snapshot drift");
          const stats = mod.matrixStats(mod.DEVICE_FEATURE_MATRIX_SNAPSHOT);
          assert.deepStrictEqual(stats, {
            deviceTypes: 19, controlGroups: 28, actionBindings: 45,
            uniqueActionIds: 29, readOnlyTypes: 6,
          });
        """
        result = run_node_module(script, str(DEVICE_FEATURES_JS), str(FIXTURE_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_fixture_validates_and_read_only_types_have_no_controls(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          if (!mod.validateDeviceFeatureMatrix(fixture)) fail("golden fixture rejected");
          const expected = [
            "alarm_control_panel", "binary_sensor", "button", "camera", "climate",
            "cover", "fan", "humidifier", "light", "lock", "media_player", "number",
            "select", "sensor", "sun", "switch", "vacuum", "valve", "water_heater",
          ];
          const actual = fixture.deviceTypes.map((entry) => entry.type);
          if (JSON.stringify(actual) !== JSON.stringify(expected)) fail("type inventory drift");
          for (const entry of fixture.deviceTypes) {
            if (entry.readOnly) {
              if (mod.isCommandable(fixture, entry.type)) fail(`read-only ${entry.type} is commandable`);
              if (mod.controlsFor(fixture, entry.type, ["turn_on", "press"]).length) {
                fail(`read-only ${entry.type} exposes controls`);
              }
            } else if (!mod.isCommandable(fixture, entry.type)) {
              fail(`commandable ${entry.type} is not commandable`);
            }
          }
        """
        result = run_node_module(script, str(DEVICE_FEATURES_JS), str(FIXTURE_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_unknown_type_and_unknown_control_fail_closed(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          for (const type of ["made_up", "", null, undefined, 42]) {
            if (mod.isCommandable(fixture, type)) fail(`unknown type ${type} is commandable`);
            if (mod.controlsFor(fixture, type, ["turn_on"]).length) {
              fail(`unknown type ${type} exposes controls`);
            }
            const actions = mod.filterCatalogActions(fixture, type, [
              { action_id: "turn_on", title: "Включить", allowed_fields: [] },
            ]);
            if (actions.length) fail(`unknown type ${type} keeps actions`);
          }
          const filtered = mod.filterCatalogActions(fixture, "light", [
            { action_id: "turn_on", title: "Включить", allowed_fields: [] },
            { action_id: "self_destruct", title: "Самоуничтожение", allowed_fields: [] },
          ]);
          if (filtered.length !== 1 || filtered[0].action_id !== "turn_on") {
            fail("unknown control was not hidden");
          }
          const controls = mod.controlsFor(fixture, "light", ["turn_on"]);
          if (controls.length !== 1 || controls[0].id !== "power") fail("partial intersection lost power");
          if (JSON.stringify(controls[0].actionIds) !== JSON.stringify(["turn_on"])) {
            fail("intersection did not narrow control actionIds");
          }
        """
        result = run_node_module(script, str(DEVICE_FEATURES_JS), str(FIXTURE_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_actions_are_intersection_with_runtime_catalog(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const full = mod.controlsFor(fixture, "vacuum", ["start", "pause", "stop", "return_home"]);
          if (full.length !== 1 || full[0].actionIds.length !== 4) fail("full catalog lost actions");
          const empty = mod.controlsFor(fixture, "vacuum", ["dock_now"]);
          if (empty.length) fail("empty intersection did not hide the control");
          const none = mod.controlsFor(fixture, "vacuum", []);
          if (none.length) fail("empty runtime catalog kept controls");
          const runtime = [
            { action_id: "start", title: "Старт", allowed_fields: [] },
            { action_id: "stop", title: "Стоп", allowed_fields: [] },
          ];
          const filtered = mod.filterCatalogActions(fixture, "vacuum", runtime);
          if (filtered.length !== 2) fail("runtime catalog actions were replaced by matrix");
          const matrixOnly = mod.filterCatalogActions(fixture, "vacuum", []);
          if (matrixOnly.length) fail("client synthesized actions from the matrix alone");
          const allowed = mod.allowedActionIds(fixture, "climate", ["turn_on", "set_temperature"]);
          if (!allowed.has("turn_on") || !allowed.has("set_temperature") || allowed.has("set_hvac_mode")) {
            fail("allowedActionIds did not intersect");
          }
        """
        result = run_node_module(script, str(DEVICE_FEATURES_JS), str(FIXTURE_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_bounds_and_options_follow_value_source(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const control = (type, id) => fixture.deviceTypes
            .find((entry) => entry.type === type).controls.find((item) => item.id === id);
          const brightness = control("light", "brightness");
          const contractBounds = mod.resolveControlBounds(brightness, { minimum: 5, maximum: 50 });
          if (!contractBounds || contractBounds.minimum !== 0 || contractBounds.maximum !== 255) {
            fail("contract_bounds did not come from the matrix");
          }
          const position = control("cover", "position");
          if (mod.resolveControlBounds(position, null).maximum !== 100) {
            fail("contract_bounds depended on runtime");
          }
          const temperature = control("climate", "temperature");
          const runtimeBounds = mod.resolveControlBounds(temperature, { minimum: 16, maximum: 30 });
          if (!runtimeBounds || runtimeBounds.minimum !== 16 || runtimeBounds.maximum !== 30) {
            fail("runtime_bounds did not come from catalog properties");
          }
          for (const bad of [null, undefined, {}, { minimum: 30, maximum: 16 }, { minimum: "16", maximum: 30 }]) {
            if (mod.resolveControlBounds(temperature, bad) !== null) {
              fail(`invalid runtime bounds accepted: ${JSON.stringify(bad)}`);
            }
          }
          if (mod.resolveControlBounds(control("light", "power"), { minimum: 0, maximum: 1 }) !== null) {
            fail("value-less control accepted bounds");
          }
          const hvac = control("climate", "hvac_mode");
          const options = mod.resolveControlOptions(hvac, [
            { value: "cool", label: "Охлаждение" }, { value: "heat", label: "Обогрев" },
            { value: "cool", label: "Дубль" }, { value: "", label: "Пусто" }, { value: null },
          ]);
          if (options.length !== 2 || options[0].value !== "cool" || options[1].label !== "Обогрев") {
            fail("runtime_options did not come from catalog properties");
          }
          if (mod.resolveControlOptions(hvac, null).length) fail("missing runtime options survived");
          if (mod.resolveControlOptions(brightness, ["a"]).length) fail("numeric control accepted options");
        """
        result = run_node_module(script, str(DEVICE_FEATURES_JS), str(FIXTURE_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_missing_metadata_disables_fetch_without_network_call(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const fail = (message) => { throw new Error(message); };
          const calls = [];
          const hass = { callApi: async (method, path) => { calls.push({ method, path }); return fixture; } };
          for (const capabilities of [null, undefined, {}, { contract: { name: "other", version: 1 } },
              { contract: { name: "hausman-hub-capabilities", version: 1 }, capabilities: {} },
              { contract: { name: "hausman-hub-capabilities", version: 1 },
                capabilities: { device_actions: { available: false } } }]) {
            const resolved = await mod.loadDeviceFeatureMatrix(hass, capabilities);
            if (resolved.declared !== false || resolved.source !== "snapshot") {
              fail("missing metadata did not fall back to the pinned snapshot");
            }
            if (!mod.validateDeviceFeatureMatrix(resolved.matrix)) fail("fallback matrix invalid");
          }
          if (calls.length) fail("missing metadata still called the network");
          if (mod.featureMatrixCapability(null) !== null) fail("garbage capabilities resolved");
          const declared = {
            contract: { name: "hausman-hub-capabilities", version: 1 },
            capabilities: { device_actions: {
              available: true,
              feature_matrix_path: "/api/hausman_hub/v1/device-features",
              feature_matrix_method: "GET",
              feature_matrix_contract: { name: "hausman-hub-device-feature-matrix", version: 1 },
            } },
          };
          const capability = mod.featureMatrixCapability(declared);
          if (!capability || capability.apiPath !== "hausman_hub/v1/device-features") {
            fail("declared capability did not resolve");
          }
          for (const bad of [
            { ...declared, capabilities: { device_actions: {
              ...declared.capabilities.device_actions, feature_matrix_method: "POST" } } },
            { ...declared, capabilities: { device_actions: {
              ...declared.capabilities.device_actions, feature_matrix_path: "/api/hausman_hub/v1/admin/panel" } } },
            { ...declared, capabilities: { device_actions: {
              ...declared.capabilities.device_actions,
              feature_matrix_contract: { name: "hausman-hub-device-feature-matrix", version: 2 } } } },
          ]) {
            calls.length = 0;
            const resolved = await mod.loadDeviceFeatureMatrix(hass, bad);
            if (resolved.declared !== false || calls.length) {
              fail("tampered metadata reached the network");
            }
          }
        """
        result = run_node_module(script, str(DEVICE_FEATURES_JS), str(FIXTURE_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_endpoint_matrix_used_and_invalid_response_fails_closed(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const fail = (message) => { throw new Error(message); };
          const capabilities = {
            contract: { name: "hausman-hub-capabilities", version: 1 },
            capabilities: { device_actions: {
              available: true,
              feature_matrix_path: "/api/hausman_hub/v1/device-features",
              feature_matrix_method: "GET",
              feature_matrix_contract: { name: "hausman-hub-device-feature-matrix", version: 1 },
            } },
          };
          const calls = [];
          const hass = (behavior) => ({ callApi: async (method, path) => {
            calls.push({ method, path });
            return behavior();
          } });
          const ok = await mod.loadDeviceFeatureMatrix(hass(() => fixture), capabilities);
          if (ok.source !== "endpoint" || ok.declared !== true) fail("endpoint matrix not used");
          if (!mod.validateDeviceFeatureMatrix(ok.matrix)) fail("endpoint matrix invalid");
          if (calls.length !== 1 || calls[0].method !== "GET"
              || calls[0].path !== "hausman_hub/v1/device-features") {
            fail("endpoint call contract drift: " + JSON.stringify(calls));
          }
          for (const behavior of [
            () => ({ hello: "world" }),
            () => null,
            () => { throw new Error("offline"); },
          ]) {
            const resolved = await mod.loadDeviceFeatureMatrix(hass(behavior), capabilities);
            if (resolved.source !== "snapshot" || resolved.declared !== true) {
              fail("invalid endpoint response did not fail closed to the snapshot");
            }
            if (JSON.stringify(resolved.matrix) !== JSON.stringify(mod.DEVICE_FEATURE_MATRIX_SNAPSHOT)) {
              fail("fallback is not the pinned snapshot");
            }
          }
        """
        result = run_node_module(script, str(DEVICE_FEATURES_JS), str(FIXTURE_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_semantic_mutations_fail_closed(self) -> None:
        script = r"""
          import fs from "node:fs";
          const mod = await import(process.argv[1]);
          const fail = (message) => { throw new Error(message); };
          const fixture = () => JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
          const light = (doc) => doc.deviceTypes.find((entry) => entry.type === "light");
          const mutations = [
            ["authority semantics", (doc) => { doc.authority.semantics = "exact"; }],
            ["authority runtime source", (doc) => { doc.authority.runtimeActionSource = "matrix"; }],
            ["authority unknown type", (doc) => { doc.authority.unknownTypePolicy = "hidden"; }],
            ["authority unknown control", (doc) => { doc.authority.unknownControlPolicy = "shown"; }],
            ["authority synthesis", (doc) => { doc.authority.clientMaySynthesizeActions = true; }],
            ["authority extra key", (doc) => { doc.authority.extra = true; }],
            ["contract name", (doc) => { doc.contract.name = "other"; }],
            ["contract version", (doc) => { doc.contract.version = 2; }],
            ["api major", (doc) => { doc.apiMajorVersion = 2; }],
            ["extra top key", (doc) => { doc.extra = []; }],
            ["missing deviceTypes", (doc) => { delete doc.deviceTypes; }],
            ["too few types", (doc) => { doc.deviceTypes = doc.deviceTypes.slice(0, 18); }],
            ["duplicate types", (doc) => { doc.deviceTypes.push(JSON.parse(JSON.stringify(doc.deviceTypes[0]))); }],
            ["receipt not required", (doc) => { light(doc).controls[0].receiptRequired = false; }],
            ["read-only with controls", (doc) => {
              const entry = doc.deviceTypes.find((item) => item.type === "sensor");
              entry.controls = [JSON.parse(JSON.stringify(light(doc).controls[0]))];
            }],
            ["commandable without controls", (doc) => { light(doc).controls = []; }],
            ["unknown category", (doc) => { light(doc).category = "garage"; }],
            ["unknown kind", (doc) => { light(doc).controls[0].kind = "dial"; }],
            ["empty actionIds", (doc) => { light(doc).controls[0].actionIds = []; }],
            ["duplicate actionIds", (doc) => {
              light(doc).controls[0].actionIds = ["turn_on", "turn_on"];
            }],
            ["bad action id", (doc) => { light(doc).controls[0].actionIds = ["Turn On"]; }],
            ["none with bounds", (doc) => { light(doc).controls[0].minimum = 0; }],
            ["number with runtime_options", (doc) => {
              light(doc).controls[1].valueSource = "runtime_options";
            }],
            ["string with contract bounds", (doc) => {
              const hvac = doc.deviceTypes.find((entry) => entry.type === "climate").controls[2];
              hvac.valueSource = "contract_bounds";
            }],
            ["contract_bounds without maximum", (doc) => {
              delete light(doc).controls[1].maximum;
            }],
            ["runtime_bounds with bounds", (doc) => {
              const temp = doc.deviceTypes.find((entry) => entry.type === "climate").controls[1];
              temp.minimum = 16;
            }],
          ];
          for (const [name, mutate] of mutations) {
            const doc = fixture();
            mutate(doc);
            if (mod.validateDeviceFeatureMatrix(doc)) fail(`mutation accepted: ${name}`);
            const normalized = mod.normalizeDeviceFeatureMatrix(doc);
            if (!mod.validateDeviceFeatureMatrix(normalized)) fail(`fail-closed matrix invalid: ${name}`);
            if (JSON.stringify(normalized) !== JSON.stringify(mod.DEVICE_FEATURE_MATRIX_SNAPSHOT)) {
              fail(`fail-closed matrix is not the pinned snapshot: ${name}`);
            }
          }
          for (const raw of [null, undefined, 42, "matrix", [], { deviceTypes: [] }]) {
            if (mod.validateDeviceFeatureMatrix(raw)) fail("garbage validated");
            if (!mod.validateDeviceFeatureMatrix(mod.normalizeDeviceFeatureMatrix(raw))) {
              fail("garbage did not fail closed");
            }
          }
        """
        result = run_node_module(script, str(DEVICE_FEATURES_JS), str(FIXTURE_JSON))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_panel_wiring_uses_device_features_module(self) -> None:
        panel_source = PANEL_JS.read_text(encoding="utf-8")

        self.assertIn('from "./hausman-hub-device-features.js?v=', panel_source)
        self.assertIn("loadDeviceFeatureMatrix(", panel_source)
        self.assertIn("filterCatalogActions(", panel_source)
        self.assertIn('"hausman_hub/v1/capabilities"', panel_source)


if __name__ == "__main__":
    unittest.main()
