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
