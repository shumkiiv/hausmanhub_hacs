import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const HARNESS = "/tests/visual/hausman-hub-panel-harness.html";
const FIXED_NOW = "2026-08-23T02:15:00.000Z";

const surfaces = [
  { name: "overview-light-wide", query: "section=overview&theme=light", width: 1440, height: 1200 },
  { name: "overview-dark-tablet", query: "section=overview&theme=dark", width: 900, height: 1000 },
  { name: "lighting-wide", query: "section=lighting&theme=dark", width: 1440, height: 1200 },
  { name: "climate-wide", query: "section=climate&screen=contour&theme=dark", width: 1440, height: 1400 },
  { name: "scenarios-wide", query: "section=scenarios&theme=dark", width: 1440, height: 900 },
  { name: "settings-narrow-light", query: "section=settings&settings=overview&theme=light", width: 640, height: 1000 },
  { name: "settings-power-tablet", query: "section=settings&settings=power&theme=dark", width: 900, height: 1000 },
];

// The interface was accepted before this gate existed. Keep its exact serious
// debt visible and stable, while blocking every new serious or critical issue.
const acceptedSeriousBaseline = {
  "overview-light-wide": { "aria-prohibited-attr": 1, "color-contrast": 1, "target-size": 4 },
  "overview-dark-tablet": {
    "aria-prohibited-attr": 1,
    "color-contrast": 1,
    "scrollable-region-focusable": 1,
    "target-size": 4,
  },
  "lighting-wide": { "nested-interactive": 3 },
  "climate-wide": {},
  "scenarios-wide": {},
  "settings-narrow-light": { "color-contrast": 5 },
  "settings-power-tablet": { "color-contrast": 1 },
};

async function openHarness(page, surface) {
  await page.setViewportSize({ width: surface.width, height: surface.height });
  await page.addInitScript((iso) => {
    const RealDate = Date;
    const fixed = RealDate.parse(iso);
    class FrozenDate extends RealDate {
      constructor(...args) {
        super(...(args.length ? args : [fixed]));
      }
      static now() {
        return fixed;
      }
    }
    FrozenDate.parse = RealDate.parse;
    FrozenDate.UTC = RealDate.UTC;
    window.Date = FrozenDate;
  }, FIXED_NOW);
  await page.goto(`${HARNESS}?${surface.query}`, { waitUntil: "networkidle" });
  const panel = page.locator("hausman-hub-panel");
  await expect(panel.locator("main")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessErrors)).toEqual([]);
  return panel;
}

for (const surface of surfaces) {
  test(`${surface.name} сохраняет утверждённый визуал`, async ({ page }) => {
    await openHarness(page, surface);
    await expect(page).toHaveScreenshot(`${surface.name}.png`, {
      fullPage: true,
    });
  });

  test(`${surface.name} не расширяет accessibility baseline`, async ({ page }) => {
    const panel = await openHarness(page, surface);
    const results = await new AxeBuilder({ page })
      .include("hausman-hub-panel")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
      .analyze();
    const blocking = results.violations.filter((violation) => violation.impact === "critical");
    const seriousSummary = Object.fromEntries(results.violations
      .filter((violation) => violation.impact === "serious")
      .map((violation) => [violation.id, violation.nodes.length]));
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
    expect(seriousSummary, JSON.stringify(results.violations, null, 2))
      .toEqual(acceptedSeriousBaseline[surface.name]);

    const overflow = await panel.evaluate((host) => {
      const root = host.shadowRoot;
      const shell = root && root.querySelector("main");
      return {
        host: Math.max(0, host.scrollWidth - host.clientWidth),
        shell: shell ? Math.max(0, shell.scrollWidth - shell.clientWidth) : -1,
      };
    });
    expect(overflow).toEqual({ host: 0, shell: 0 });
  });
}

test("клавиатура открывает основные разделы и показывает видимый фокус", async ({ page }) => {
  const panel = await openHarness(page, surfaces[0]);
  const firstNavigationButton = panel.locator("nav button").first();
  await firstNavigationButton.focus();
  await expect(firstNavigationButton).toBeFocused();
  await expect(firstNavigationButton).toHaveCSS("outline-style", /^(solid|auto)$/);
  await page.keyboard.press("Tab");
  const active = panel.locator(":focus");
  await expect(active).toBeVisible();
  await page.keyboard.press("Enter");
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessErrors)).toEqual([]);
});

test("выделение активной страницы Hero не обрезается полосой комнат", async ({ page }) => {
  const panel = await openHarness(page, surfaces[0]);
  const activeGeometry = () => panel.evaluate((host) => {
    const root = host.shadowRoot;
    const strip = root.querySelector(".overview-canon-room-strip");
    const active = strip?.querySelector('[aria-current="page"]');
    const stripRect = strip?.getBoundingClientRect();
    const activeRect = active?.getBoundingClientRect();
    return {
      leftInset: activeRect && stripRect ? activeRect.left - stripRect.left : -1,
      rightInset: activeRect && stripRect ? stripRect.right - activeRect.right : -1,
    };
  });
  expect((await activeGeometry()).leftInset).toBeGreaterThanOrEqual(4);
  const lastPage = panel.locator(".overview-canon-room-strip button").last();
  await lastPage.click();
  await expect(lastPage).toHaveAttribute("aria-current", "page");
  await expect.poll(async () => (await activeGeometry()).rightInset).toBeGreaterThanOrEqual(4);
});

test("AI-компоновщик сценария открывается и предлагает устройство по @", async ({ page }) => {
  const panel = await openHarness(page, surfaces.find((surface) => surface.name === "scenarios-wide"));
  const root = panel.locator(":scope");
  await root.getByRole("button", { name: "Создать с Hausman AI" }).click();
  const dialog = root.getByRole("dialog", { name: "Создать с Hausman AI" });
  await expect(dialog).toBeVisible();
  const prompt = dialog.getByLabel("Опишите сценарий");
  await prompt.fill("Когда @");
  await expect(dialog.locator(".scenario-ai-suggestion").first()).toBeVisible();
  await expect(dialog.getByText("Токен Home Assistant", { exact: false })).toBeVisible();
});

test("встроенный редактор Node-RED проверяет и сохраняет function без команд", async ({ page }) => {
  const panel = await openHarness(page, {
    query: "section=scenarios&theme=dark&nodeRedEditor=1&openScenario=Тестовый%20алгоритм",
    width: 1440,
    height: 900,
  });
  const scenarioDialog = panel.getByRole("dialog", { name: "Редактор сценария" });
  await expect(scenarioDialog).toBeVisible();
  await scenarioDialog.getByRole("button", { name: "Редактировать алгоритм в Hausman" }).click();
  const sourceDialog = panel.getByRole("dialog", { name: "Алгоритм Node-RED" });
  await expect(sourceDialog).toBeVisible();
  const editor = sourceDialog.getByLabel("Исходник function Node-RED");
  await expect(editor).toHaveValue(/HAUSMAN_MANAGED_SCENARIO node_red_test/);
  await editor.fill(`${await editor.inputValue()}\n// browser gate`);
  await sourceDialog.getByRole("button", { name: "Проверить и сохранить" }).click();
  await expect(sourceDialog.getByText("Проверено.", { exact: true })).toBeVisible();
  await expect(sourceDialog.getByText("команд отправлено: нет", { exact: false })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__hausmanHubHarnessCalls
    .filter((call) => call.method === "PUT" && call.path.endsWith("/source/node_red_test"))
    .map((call) => call.payload.validateOnly))).toEqual([false]);
});

test("каталог показывает способ редактирования и запуска и фильтрует Node-RED", async ({ page }) => {
  const panel = await openHarness(page, {
    query: "section=scenarios&theme=dark&nodeRedEditor=1",
    width: 1440,
    height: 900,
  });
  await panel.getByRole("button", { name: "Node-RED", exact: true }).click();
  const cards = panel.locator(".scenario-library-card:visible");
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("Тестовый алгоритм Node-RED");
  await expect(cards.first()).toContainText("Node-RED · код");
  await expect(cards.first()).toContainText("Ручной запуск");
  await expect(cards.first().locator(".scenario-library-badge ha-icon")).toHaveCount(2);
});
