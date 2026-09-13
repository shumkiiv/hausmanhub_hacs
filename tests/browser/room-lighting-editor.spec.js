import { expect, test } from "@playwright/test";

/* Room lighting settings editor acceptance spec.
 *
 * The room card still opens the room devices; the new "Настроить" action opens
 * the full-size editor. The spec proves three invariants of the rework:
 *   - a failed save and a 409 keep the local draft;
 *   - a 409 resynchronizes the server version onto the carried draft;
 *   - technical identifiers stay inside the collapsed block.
 */

const HARNESS = "/tests/visual/hausman-hub-panel-harness.html";

async function openLighting(page) {
  await page.goto(`${HARNESS}?section=lighting&theme=dark`, { waitUntil: "domcontentloaded" });
  const panel = page.locator("hausman-hub-panel");
  await expect(panel.locator("main")).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessErrors)).toEqual([]);
  return panel;
}

async function openEditor(page, panel) {
  const shell = panel.locator(".lighting-room-card-shell").filter({ hasText: "Кабинет" }).first();
  await expect(shell).toBeVisible();
  const configure = shell.locator(".lighting-room-configure");
  await expect(configure).toHaveCount(1);
  await configure.click();
  const screen = panel.locator('[data-testid="room-lighting-settings"]');
  await expect(screen).toBeVisible();
  await expect(screen.locator('[data-testid="room-lighting:save-state"]')).toContainText("Изменений нет");
  return screen;
}

async function setHarness(page, patch) {
  await page.evaluate((values) => {
    const host = document.querySelector("hausman-hub-panel");
    Object.assign(host._roomLightingHarness, values);
  }, patch);
}

test("карточка комнаты открывает устройства, а «Настроить» открывает редактор", async ({ page }) => {
  const panel = await openLighting(page);
  const card = panel.locator(".lighting-room-card").filter({ hasText: "Кабинет" }).first();
  await card.click();
  const sheet = panel.locator(".lighting-room-sheet");
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText("Люстра кабинет");
  await sheet.locator(".lighting-room-sheet-close").click();
  await expect(sheet).toHaveCount(0);

  const screen = await openEditor(page, panel);
  await expect(screen.locator(".rle-nav-item")).toHaveCount(7);
  await expect(screen.locator('[data-testid="room-lighting:name"]')).toHaveValue("Кабинет");
  for (const key of ["overview", "lights", "inputs", "periods", "conditions", "manual-away", "review-save"]) {
    await expect(screen.locator(`[data-testid="room-lighting:section:${key}"]`)).toBeVisible();
  }
});

test("черновик переживает неудачное сохранение и не объявляется сохранённым", async ({ page }) => {
  const panel = await openLighting(page);
  const screen = await openEditor(page, panel);
  const name = screen.locator('[data-testid="room-lighting:name"]');
  await name.fill("Кабинет · черновик");
  await expect(screen.locator('[data-testid="room-lighting:save-state"]')).toContainText("Не сохранено");

  await setHarness(page, { failPut: true });
  await screen.locator('[data-testid="room-lighting:save"]').click();
  await expect(screen.locator('[data-testid="room-lighting:error"]')).toBeVisible();
  await expect(name).toHaveValue("Кабинет · черновик");
  await expect(screen.locator('[data-testid="room-lighting:save-state"]')).toContainText("Не сохранено");
  await expect(screen.locator('[data-testid="room-lighting:notice"]')).toHaveCount(0);
  await setHarness(page, { failPut: false });
});

test("конфликт версии переносит локальные правки и обновляет версию", async ({ page }) => {
  const panel = await openLighting(page);
  const screen = await openEditor(page, panel);
  const name = screen.locator('[data-testid="room-lighting:name"]');
  await name.fill("Кабинет · локальная правка");

  await page.evaluate(() => {
    const harness = document.querySelector("hausman-hub-panel")._roomLightingHarness;
    harness.conflictOnPut = true;
    harness.config.version = 4;
    harness.config.name = "Кабинет с другого клиента";
  });
  await screen.locator('[data-testid="room-lighting:save"]').click();

  const conflict = screen.locator('[data-testid="room-lighting:conflict"]');
  await expect(conflict).toBeVisible();
  await expect(conflict).toContainText("Ничего не сохранено");
  await expect(name).toHaveValue("Кабинет · локальная правка");
  await expect(screen.locator('[data-testid="room-lighting:notice"]')).toHaveCount(0);

  const technical = screen.locator('[data-testid="room-lighting:technical-overview"]');
  const version = technical.locator('[data-testid="room-lighting:version"]');
  await expect(version).not.toBeVisible();
  await technical.locator("summary").click();
  await expect(version).toBeVisible();
  await expect(version).toHaveText("4");

  await setHarness(page, { conflictOnPut: false });
  await screen.locator('[data-testid="room-lighting:save"]').click();
  await expect(screen.locator('[data-testid="room-lighting:notice"]')).toContainText("Сохранено");
  await expect(conflict).toHaveCount(0);
});

test("технические идентификаторы видны только внутри раскрытого блока", async ({ page }) => {
  const panel = await openLighting(page);
  const screen = await openEditor(page, panel);
  await screen.locator('[data-testid="room-lighting:section:lights"]').click();
  const lights = screen.locator('.rle-section[data-section="lights"]');
  await expect(lights).toBeVisible();

  const hasVisibleEntityId = () => lights.locator("input:visible").evaluateAll(
    (inputs) => inputs.some((input) => input.value === "light.office_chandelier"),
  );
  expect(await hasVisibleEntityId()).toBe(false);

  const technical = lights.locator("details.rle-technical").first();
  await expect(technical).toBeVisible();
  await technical.locator("summary").click();
  expect(await hasVisibleEntityId()).toBe(true);
});
