import { expect, test } from "@playwright/test";

const HARNESS = process.env.HMH_HARNESS_URL || "/tests/visual/hausman-hub-panel-harness.html";

async function openSettings(page) {
  await page.goto(`${HARNESS}?section=settings&settings=light-protection&theme=dark`, { waitUntil: "networkidle" });
  const panel = page.locator("hausman-hub-panel");
  await expect(panel.locator("main")).toBeVisible();
  await panel.evaluate(async (host) => {
    host._capabilities = await host._hass.callApi("GET", "hausman_hub/v1/capabilities");
    host._lightProtection = { state: "Active", snapshot: structuredClone(host._manualLightProtectionHarness), error: "" };
    host._render();
  });
  await expect(panel.getByTestId("settings-manual-light-protection")).toBeVisible();
  return panel;
}

test("редактор сохраняет только явно выбранные поля override", async ({ page }) => {
  const panel = await openSettings(page);
  const root = panel.locator(":scope");
  await root.getByTestId("manual-light-protection:scope").selectOption("room");
  await root.getByTestId("manual-light-protection:room-id").fill("shower");
  await root.getByTestId("manual-light-protection:override-enabled").check();
  await root.getByTestId("manual-light-protection:save").click();
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "PUT").at(-1)?.payload.settings.roomOverrides.shower))
    .toEqual({ enabled: true });
});

test("release posts the server protection revision and returns to server state", async ({ page }) => {
  const panel = await openSettings(page);
  await panel.getByRole("button", { name: /Разрешить автоматику:/ }).click();
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "POST").at(-1)?.payload))
    .toMatchObject({ roomId: "shower", profileId: "shower-main", expectedProtectionRevision: 1 });
  await expect(panel.getByText("Активной защиты нет.")).toBeVisible();
});

test("состояние wire snapshot не получает клиентских полей", async ({ page }) => {
  const panel = await openSettings(page);
  const clean = await panel.evaluate(async (host) => {
    const snapshot = structuredClone(host._manualLightProtectionHarness);
    await host._load();
    return { snapshot, rendered: host._lightProtection.snapshot };
  });
  expect(clean.rendered).toEqual(clean.snapshot);
});

test("семь safety-состояний не открывают команды без готового подтверждения", async ({ page }) => {
  const panel = await openSettings(page);
  const states = [["Loading", "Загрузка"], ["Ready", "Готово"], ["Active", "Защита активна"], ["ReadyToRelease", "Можно снять защиту"], ["Unavailable", "Недоступно"], ["Conflict", "Конфликт"], ["Failure", "Ошибка"]];
  for (const [state, text] of states) {
    await panel.evaluate((host, value) => { host._lightProtection = { state: value, snapshot: structuredClone(host._manualLightProtectionHarness), error: "Проверка" }; host._render(); }, state);
    await expect(panel.getByText(text, { exact: true })).toBeVisible();
    const enabledRelease = panel.getByTestId("manual-light-protection:release");
    if (["Loading", "Unavailable", "Conflict", "Failure"].includes(state)) await expect(enabledRelease).toHaveCount(0);
  }
});

test("повреждённый snapshot и неверная квитанция оставляют панель в fail-closed состоянии", async ({ page }) => {
  const panel = await openSettings(page);
  await panel.evaluate(async (host) => {
    const callApi = host._hass.callApi;
    host._hass.callApi = async (method, path, payload) => method === "GET" && path === "hausman_hub/v1/lighting/manual-off-protection"
      ? { contract: { name: "hausman-hub-manual-light-off-protection", version: 1 }, revision: 1, updatedAt: "2026-09-03T12:00:00Z", settings: {}, protections: [] }
      : callApi(method, path, payload);
    await host._load();
  });
  await expect(panel.getByText("Ошибка", { exact: true })).toBeVisible();
  await expect(panel.getByTestId("manual-light-protection:release")).toHaveCount(0);
  await panel.evaluate((host) => { host._lightProtection = { state: "Active", snapshot: structuredClone(host._manualLightProtectionHarness), error: "" }; const callApi = host._hass.callApi; host._hass.callApi = async (method, path, payload) => method === "POST" ? { contract: { name: "hausman-hub-manual-light-off-protection-command-receipt", version: 1 }, requestId: payload.requestId, operation: "manual_release", accepted: true, confirmed: true, status: "confirmed", revision: 2, protection: { ...host._manualLightProtectionHarness.protections[0], revision: 2, state: "released" }, extra: true } : callApi(method, path, payload); host._render(); });
  await panel.getByTestId("manual-light-protection:release").click();
  await expect(panel.getByText("Ошибка", { exact: true })).toBeVisible();
});

test("все восемь полей доступны на global, room и profile уровне", async ({ page }) => {
  const panel = await openSettings(page);
  const root = panel.locator(":scope");
  const fields = ["enabled", "minimumIntervalSeconds", "releaseMode", "stableAbsenceSeconds", "extendOnRepeatedManualOff", "noSensorFallback", "protectedScope", "allowManualRelease"];
  for (const scope of ["global", "room", "profile"]) {
    await root.getByTestId("manual-light-protection:scope").selectOption(scope);
    if (scope !== "global") await root.getByTestId("manual-light-protection:room-id").fill("shower");
    if (scope === "profile") await root.getByTestId("manual-light-protection:profile-id").fill("shower-main");
    for (const field of fields) {
      await expect(root.getByTestId(`manual-light-protection:override-${field}`)).toBeVisible();
      await expect(root.getByTestId(`manual-light-protection:field-${field}`)).toBeVisible();
    }
  }
});

test("нулевой остаток перечитывает snapshot без release, reconnect перечитывает его сразу", async ({ page }) => {
  const panel = await openSettings(page);
  await panel.evaluate(async (host) => {
    host._manualLightProtectionHarness.protections[0].remainingMinimumSeconds = 0;
    window.__hausmanHubHarnessCalls = [];
    await host._load();
  });
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "GET" && call.path === "hausman_hub/v1/lighting/manual-off-protection").length)).toBe(2);
  await page.waitForTimeout(100);
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "GET" && call.path === "hausman_hub/v1/lighting/manual-off-protection").length)).toBe(2);
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "POST").length)).toBe(0);
  await panel.evaluate((host) => { window.__hausmanHubHarnessCalls = []; host.disconnectedCallback(); host.connectedCallback(); });
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "GET" && call.path === "hausman_hub/v1/lighting/manual-off-protection").length)).toBe(1);
  await page.waitForTimeout(100);
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "GET" && call.path === "hausman_hub/v1/lighting/manual-off-protection").length)).toBe(1);
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "POST").length)).toBe(0);
});

test("profile override and reset affect only the selected profile", async ({ page }) => {
  const panel = await openSettings(page);
  const root = panel.locator(":scope");
  await root.getByTestId("manual-light-protection:scope").selectOption("profile");
  await root.getByTestId("manual-light-protection:room-id").fill("shower");
  await root.getByTestId("manual-light-protection:profile-id").fill("shower-main");
  await root.getByTestId("manual-light-protection:override-minimumIntervalSeconds").check();
  await root.getByTestId("manual-light-protection:field-minimumIntervalSeconds").fill("900");
  await root.getByTestId("manual-light-protection:save").click();
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "PUT").at(-1)?.payload.settings.profileOverrides["shower-main"]))
    .toEqual({ minimumIntervalSeconds: 900 });
  await root.getByTestId("manual-light-protection:scope").selectOption("profile");
  await root.getByTestId("manual-light-protection:room-id").fill("shower");
  await root.getByTestId("manual-light-protection:profile-id").fill("shower-main");
  await root.getByTestId("manual-light-protection:reset-inheritance").click();
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls.filter((call) => call.method === "PUT").at(-1)?.payload.settings.profileOverrides["shower-main"]))
    .toBeUndefined();
});
