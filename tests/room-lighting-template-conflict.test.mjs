import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const frontend = new URL("../custom_components/hausman_hub/frontend/", import.meta.url);
const taxonomySource = readFileSync(new URL("hausman-hub-error-taxonomy.js", frontend), "utf8");
const taxonomyUrl = `data:text/javascript;base64,${Buffer.from(taxonomySource).toString("base64")}`;
const editorSource = readFileSync(new URL("hausman-hub-room-lighting.js", frontend), "utf8");
const editorWithLocalImport = editorSource.replace(
  /from "\.\/hausman-hub-error-taxonomy\.js\?v=[^"]+"/,
  `from "${taxonomyUrl}"`,
);
assert.notEqual(editorWithLocalImport, editorSource, "editor taxonomy import must be found");
const editorUrl = `data:text/javascript;base64,${Buffer.from(
  `${editorWithLocalImport}\nexport const __test = { lightingState, applyTemplate, saveRoomLighting, refreshRoomLighting, renderSaveBar };`,
).toString("base64")}`;
const { __test: editor } = await import(editorUrl);

const copy = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));

function mockElement(tag, className, text) {
  return {
    tag, className, text, disabled: false, children: [], attributes: {},
    appendChild(child) { this.children.push(child); },
    addEventListener() {},
    setAttribute(name, value) { this.attributes[name] = value; },
  };
}

function findTestId(node, testId) {
  if (node.attributes["data-testid"] === testId) return node;
  for (const child of node.children) {
    const found = findTestId(child, testId);
    if (found) return found;
  }
  return null;
}

test("HH-016.f keeps stale template draft blocked after reapply failure", async () => {
  const server = {
    config: {
      roomId: "office", version: 3, updatedAt: 100, name: "Кабинет",
      devices: { light_targets: [{ id: "main", entityId: "light.old_binding" }] },
    },
    failApply: false,
    failGet: false,
    applyGate: null,
    calls: [],
  };
  const panel = {
    _render() {},
    _hass: {
      async callApi(method, path, payload) {
        server.calls.push({ method, path, payload: copy(payload) });
        if (method === "GET" && path.endsWith("/config")) {
          if (server.failGet) throw new Error("network unavailable");
          return copy(server.config);
        }
        if (method === "POST" && path.endsWith("/templates/apply")) {
          if (server.applyGate) await server.applyGate;
          if (server.failApply) throw new Error("template apply failed");
          return { ...copy(server.config), name: "Кабинет по шаблону", templateId: payload.templateId };
        }
        if (method === "PUT" && path.endsWith("/config")) {
          if (payload.version !== server.config.version) {
            const error = new Error("revision conflict");
            error.status = 409;
            error.body = {
              contract: { name: "hausman-hub-error", version: 1 },
              code: "revision_conflict",
              message: "Конфликт версии",
              details: { expectedRevision: payload.version, actualRevision: server.config.version },
            };
            throw error;
          }
          server.config = { ...copy(payload), version: payload.version + 1 };
          return copy(server.config);
        }
        return {};
      },
    },
  };
  const roomId = "office";
  const store = editor.lightingState(panel, roomId);
  store.loaded = true;
  store.config = copy(server.config);
  store.draft = copy(server.config);
  store.rerender = () => {};
  const saveButton = () => findTestId(
    editor.renderSaveBar(panel, roomId, store, { el: mockElement }),
    "room-lighting:save",
  );

  await editor.applyTemplate(panel, roomId, "day_profile");
  assert.equal(store.draft.version, 3);
  assert.equal(saveButton().disabled, false);

  server.config.version = 4;
  server.config.devices.light_targets[0].entityId = "light.new_binding";
  await editor.saveRoomLighting(panel, roomId);
  assert.equal(store.conflict, true);
  assert.equal(store.templateDraft, true);
  assert.equal(saveButton().disabled, true);

  server.failApply = true;
  let releaseApply;
  server.applyGate = new Promise((resolve) => { releaseApply = resolve; });
  const pendingReapply = editor.applyTemplate(panel, roomId, "day_profile");
  assert.equal(store.conflict, true);
  assert.equal(store.templateDraft, true);
  assert.equal(saveButton().disabled, true);
  releaseApply();
  await pendingReapply;
  server.applyGate = null;
  assert.equal(store.conflict, true);
  assert.equal(store.templateDraft, true);
  assert.equal(store.appliedTemplateId, "day_profile");
  assert.equal(store.draft.version, 3);
  assert.equal(store.draft.devices.light_targets[0].entityId, "light.old_binding");
  assert.equal(store.config.devices.light_targets[0].entityId, "light.new_binding");
  assert.equal(saveButton().disabled, true);
  const putsBeforeBlockedSave = server.calls.filter((call) => call.method === "PUT").length;
  await editor.saveRoomLighting(panel, roomId);
  assert.equal(server.calls.filter((call) => call.method === "PUT").length, putsBeforeBlockedSave);

  server.failGet = true;
  await editor.refreshRoomLighting(panel, roomId, true);
  assert.equal(store.conflict, true);
  assert.equal(store.templateDraft, true);
  assert.equal(saveButton().disabled, true);

  server.failGet = false;
  server.failApply = false;
  await editor.applyTemplate(panel, roomId, "day_profile");
  assert.equal(store.conflict, false);
  assert.equal(store.draft.version, 4);
  assert.equal(store.draft.devices.light_targets[0].entityId, "light.new_binding");
  assert.equal(saveButton().disabled, false);
  await editor.saveRoomLighting(panel, roomId);
  assert.equal(server.config.devices.light_targets[0].entityId, "light.new_binding");
});
