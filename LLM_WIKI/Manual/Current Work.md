# Current Work

## 2026-07-23 - HausmanHub 1.17.0 local release candidate

- Completed the Home Environment settings step and native options menus.
- Oracle review found atomic update, threshold range, and coverage gaps.
  One safe fix iteration added a runtime-locked home update and aligned the
  form with the registry's -40..60 °C range.
- `python3 tools/check_local_release.py` passed through a temporary Git index;
  its full suite reports 618 successful tests.
- Built the manual Home Assistant test archive
  `/home/ivsh/projects/УД-hasc/releases/HausmanHub-1.17.0-test.zip` from the
  current working tree. It contains only `custom_components/hausman_hub`,
  includes `frontend/hausman-hub-panel.js`, declares version `1.17.0`, and
  passes `unzip -t`.
- Archive SHA-256:
  `82f5f8d4a5dc43d642be3d6e4fa9339970ff91e30478890fcb78053153f56b45`.
- Disposable Home Assistant Core checks remain blocked because the expected
  Python environments under `/tmp/hausmanhub-core-2026.6.4` and `2026.7.0`
  are absent.
- Release code commit `909ae3d` was pushed to `origin/main`.
- The latest GitHub Release is `v1.17.0`:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.0.
  Its remote tag resolves exactly to `909ae3d`, the manifest declares `1.17.0`,
  and the sidebar panel asset is present.
- The only GitHub check job concluded successfully:
  https://github.com/shumkiiv/hausmanhub_hacs/actions/runs/29991423859/job/89154943431.
- No live Home Assistant action occurred. Next: refresh the custom repository
  in HACS, install `1.17.0`, restart Home Assistant, and run the deferred
  disposable Core smoke check when its environments are provisioned.

## 2026-07-23 - Sidebar panel diagnosis

- A read-only check against Home Assistant Core 2026.7.3 confirmed that the
  `hausman_hub` component is loaded.
- The live panel JavaScript returns HTTP 200 and exactly matches the
  `v1.17.0` Git blob (SHA-256
  `4f796a24e4147a73ee3673a6568401dc0425feeafe61aad5b7cdb37acdb59a3f`).
- The admin panel API exists and correctly returns HTTP 403 to the read-only
  diagnostic token.
- WebSocket `get_panels` succeeds but omits `hausman-hub` for the read-only
  user, matching the intentional `require_admin=True` registration.
- This initially proved only that the installation asset and authorization
  boundary were active. The later administrator screenshot proved that the
  panel itself had never been registered. No live Home Assistant state was
  changed.

## 2026-07-23 - HausmanHub 1.17.1 sidebar hotfix

- The administrator's sidebar editor screenshot proved that `hausman-hub` was
  absent, not hidden.
- Home Assistant Core 2026.7.3 defines
  `panel_custom.async_register_panel` as an async function. HausmanHub called
  it without `await`, so the static file route registered but the panel
  coroutine never ran.
- `panel.py` now awaits registration. The test double is async as well, making
  the old bug fail deterministically.
- The final staged package passed 618 local tests, HACS/package checks, version
  checks, and repository-safety checks.
- All three configured Kimi profiles failed before review with the same
  monthly-quota HTTP 403 (`ses_071c8bf52ffeCp7AD0sQb6RwH1`,
  `ses_071c86d9cffe2MGwrMXqnx7l3d`, and
  `ses_071c827f4ffeQHSh5t41p627GO`), so no Kimi PASS is claimed.
- The direct read-only OpenAI fallback review returned PASS with no substantial
  findings in OpenCode session `ses_071c619cfffeCD4QTUh6eD5vsI`.
- Release commit `d0efbd4` was pushed to `origin/main`; GitHub Actions run
  `29994143599` passed.
- Stable release `v1.17.1` was published at
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.1. The remote
  tag resolves exactly to `d0efbd4`, its manifest version is `1.17.1`, and the
  tagged `panel.py` awaits `async_register_panel`.
- No live Home Assistant write, update, or restart occurred. Next: refresh the
  custom repository in HACS, install `1.17.1`, restart Home Assistant, and
  hard-refresh the administrator browser.
