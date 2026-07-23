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

## 2026-07-23 - HausmanHub 1.17.2 panel readiness hotfix

- The administrator screenshot after installing 1.17.1 showed that the sidebar
  page now exists, but displayed the red generic data-unavailable banner.
- Root cause: the combined admin-panel route asked for the public climate
  snapshot even in the default disabled climate mode. The runtime raised
  `ClimateRuntimeUnavailable`, and the route converted this normal startup
  state into HTTP 503.
- The response contract is now `hausman-hub-admin-panel` version 2. A narrow
  `ClimateSnapshotUnavailable` condition returns HTTP 200 with truthful
  readiness and `snapshot: null`; the frontend renders no rooms, contours, or
  actions without that snapshot. Internal runtime failures still return 503.
- Regression coverage includes disabled mode, an actual managed runtime without
  an observable state view, an internal runtime fault, and a Node-executed DOM
  render proving that no room, contour, or button is created.
- The full staged release gate passes 622 tests plus HACS/package, version,
  naming, and repository-safety checks.
- Kimi failed before review with monthly-quota HTTP 403 in session
  `ses_0718c6a51ffeOEOqbCDUJoHS0M`; no Kimi PASS is claimed.
- A fallback review initially returned FAIL in OpenCode session
  `ses_0718c1edaffeElMNdo58oqRoi3` for an overbroad exception, unchanged
  response version, and shallow tests. All findings were fixed. The final
  direct read-only OpenAI fallback review returned PASS in session
  `ses_071864f04ffe3MwHXRFVF4Mm5X`.
- Release commit `1618e0b` was pushed to `origin/main`; GitHub Actions run
  `29998820030` passed.
- Stable release `v1.17.2` was published at
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.2. The remote
  tag resolves exactly to `1618e0b`; its manifest declares `1.17.2`, the
  combined panel contract is version 2, and the tagged frontend guards
  `snapshot: null`.
- No live Home Assistant write, update, or restart occurred. Next: refresh
  HACS, install `1.17.2`, restart Home Assistant, and hard-refresh the
  administrator browser.

## 2026-07-23 - Live diagnosis after installing 1.17.2

- Home Assistant Core is `2026.7.3`; the `hausman_hub` config entry is loaded
  and not disabled.
- HACS reports installed/latest `v1.17.2`. The live panel JavaScript returns
  HTTP 200 and has SHA-256
  `a936204bc586563d2ffaa4f91ae2ff2301736f16c09c5d7bd966d11918d412f0`,
  exactly matching tagged `v1.17.2`; it contains the `if (snapshot)` guard.
- 475 of 501 live states were recreated at 11:01-11:02 UTC, consistent with a
  Home Assistant restart. The persistent red banner is therefore a backend
  rejection, not an old browser asset.
- The only stored access token authenticates as non-admin with no groups.
  Admin panel/readiness and system-log requests are unauthorized; the exact
  local read-only summary also returns 403. This token cannot distinguish a
  browser request rejected by the local-address policy from a runtime 503.
- Final diagnosis needs a temporary administrator long-lived access token
  saved in the local project workspace rather than pasted into chat.
- A follow-up search by filename and credential keys across project and user
  configuration directories found no second HA access file. The only HA token
  file is `/home/ivsh/projects/УД-hasc/ha_read_access.json`; unrelated Codex
  and Figma credential stores were not used.
- The user should create the temporary token from the administrator account's
  Profile -> Security -> Long-lived access tokens page, save the one-time value
  locally as mode-600 `ha_admin_access.json` with `base_url` and
  `access_token`, and revoke it after the read-only diagnosis.
- The file was subsequently created with mode `600`, but the saved
  `access_token` value is only 9 characters. A safe authorization probe
  returned HTTP 400, so the value is a placeholder rather than a usable
  Home Assistant long-lived token. On the next retry the file kept mode `600`
  but contained an empty token (0 characters), so a validated hidden-input
  method was used.
- The final token authenticates as a non-system Home Assistant administrator
  and owner. WebSocket `get_panels` contains `hausman-hub` with
  `require_admin: true`.
- Direct requests through `http://172.30.0.92:8123` return HTTP 200 for both
  the combined panel and readiness routes. The panel contract is version 2,
  `snapshot` is null, and readiness is truthfully disabled with reason
  `bridge_disabled`.
- The Home Assistant system log has 17 current entries and none match
  HausmanHub. The release, panel registration, and runtime are therefore
  healthy.
- The persistent screenshot banner is the frontend's generic rendering of a
  rejected API call. Since the same admin call succeeds through the direct
  private address, the remaining cause is the intentional local-address guard
  rejecting a browser path through an external URL or reverse proxy. Test the
  panel through the direct private HA address.
- Revoke the temporary administrator token after the diagnosis.
- No live Home Assistant state was changed.

## 2026-07-23 - Full PC-to-HA browser-path verification

- The dedicated Windows diagnostic SSH key reaches the main PC as
  `IVSH-PC\IVSH` at `172.30.0.37`.
- The PC has a successful TCP route to `172.30.0.92:8123`. With the temporary
  admin token supplied only through encrypted SSH stdin, both the combined
  panel and readiness API return HTTP 200 from source `172.30.0.37`, with the
  same contract-v2 disabled result.
- Edge, Chrome, and Firefox histories contain no Home Assistant `:8123` or
  HausmanHub page. Their origin storage contains no private HA origin or
  `hassTokens` marker, there is no Home Assistant Windows app, and no active
  browser connection to HA exists. The supplied screenshot therefore did not
  come from a normal browser profile on this PC.
- An isolated real Chrome session on the private network was authenticated
  with the same admin token and opened the actual HA route `/hausman-hub`.
  The document title was `HausmanHub – Home Assistant`; the page HTML, panel
  JavaScript, and admin panel API all returned HTTP 200. There were no network
  failures, JavaScript exceptions, or generic red banner. The rendered page
  contained the expected `Управление климатом выключено` status.
- Version 1.17.2 and the clean authenticated frontend path are proven healthy.
  The remaining failing client is another device, profile, or stale/non-admin
  session. Supporting a truly external client would require an explicit
  decision to relax the intentional local-only read boundary.
- The isolated local and Windows temporary browser profiles and diagnostic
  scripts were removed. Existing browser profiles, HA state, and repository
  source were not changed.

## 2026-07-23 - HausmanHub 1.17.3 IPv6 mDNS release candidate

- The user's actual Edge window was opened at
  `http://homeassistant.local:8123/hausman-hub`. Live TCP inspection on the
  Windows PC showed Edge connecting from scoped IPv6 link-local
  `fe80::a532:7270:e063:7571%9` to Home Assistant at
  `fe80::1179:39cd:f44d:9939%9`.
- This explains the split result: direct RFC1918 IPv4 admin API requests
  returned HTTP 200, but `climate_api._is_local_address` rejected the normal
  mDNS browser path and the frontend rendered its generic red error banner.
- Version 1.17.3 allows IPv6 link-local only through the existing
  non-system-admin guard. Tablet routes keep the previous address boundary,
  and the separate fixed read-only local-summary route remains unchanged.
- Regression coverage accepts plain and scoped `fe80::/10` plus its upper
  boundary, rejects `fec0::1` and `2001:db8::1`, and proves a tablet request
  from scoped link-local still receives HTTP 403.
- The full staged release gate passes 623 tests plus fixture,
  Android-compatibility, version, naming, HACS-package, tracked-file, and
  staged-file safety checks.
- All three configured Kimi providers failed before review with billing-cycle
  quota HTTP 403 (`ses_070fcb1edffeoQc7sXq6i4fGlc`,
  `ses_070fc2698ffef461bP6l0ZS6ei`, and
  `ses_070fbe366ffe1kpx6b5uPMnKRT`), so no Kimi PASS is claimed. The final
  read-only OpenAI fallback review returned PASS with no substantial findings
  in OpenCode session `ses_070fb78a1ffe1nlEik803qO5E0`.
- Release commit `38cf6c5` was pushed to `origin/main`; GitHub Actions run
  `30009265197`, job `89212887371`, passed.
- Stable release `v1.17.3` was published at
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.3. The remote
  tag resolves exactly to `38cf6c5`, and the tagged manifest declares
  `1.17.3`.
- No live Home Assistant write, update, restart, or configuration change
  occurred. Next: refresh HACS, install 1.17.3, restart Home Assistant, and
  retest the same Edge `homeassistant.local` URL.
