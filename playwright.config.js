import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  // The shared panel harness can be slow on a loaded CI runner: a panel-open
  // wait is bounded by PANEL_READY_TIMEOUT_MS (45s), so the per-test timeout
  // must be larger than 30s or the test aborts before its own bound.
  timeout: 120_000,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  // Two workers run fine on a developer machine, but the exhaustive
  // interaction audit opens a fresh document for every control. Running it in
  // parallel with the shared-state tests exhausts the CI runner and makes the
  // panel-open waits time out, so the release gate runs one worker.
  workers: process.env.CI ? 1 : 2,
  reporter: process.env.CI
    ? [["line"], ["html", { open: "never" }]]
    : [["line"]],
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      animations: "disabled",
      maxDiffPixelRatio: 0.001,
    },
  },
  use: {
    baseURL: "http://127.0.0.1:8765",
    colorScheme: "dark",
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}{ext}",
  webServer: {
    command: "python3 -m http.server 8765 --bind 127.0.0.1 >/dev/null 2>&1",
    url: "http://127.0.0.1:8765/tests/visual/hausman-hub-panel-harness.html",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
