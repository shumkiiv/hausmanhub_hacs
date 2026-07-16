# HASC AI Context

Last updated: 2026-07-16.

## Project state

- Repository: `shumkiiv/hausmanhub_hasc` (public, MIT, `main`).
- Local checkout: `/home/ivsh/projects/hausmanhub_hasc`.
- Home Assistant baseline: Core 2026.6.4 or newer.
- A public `custom_components/hausman_hub/` read-only skeleton is approved
  and present. It may be added manually as an HACS custom repository; it is
  not in the public HACS catalog.
- The skeleton contains a local square `brand/icon.png`, so Home Assistant can
  show its original icon without relying on an external brand asset.
- A Russian safe-check guide is available at
  `docs/home-assistant-safe-check.md`. It guides HACS refresh, installation,
  visual confirmation, and the local aggregate diagnostic summary; it still
  explicitly excludes sharing diagnostics archives, configuration files, home
  addresses, credentials, names, identifiers, and device data.
- The optional local-viewer guide now ends with a simple choice: when the page
  is not needed, no action is required; when Home Assistant does not offer the
  exact read-only role, do not configure that optional page or edit internal
  files. Ordinary nine counts and diagnostics still work. Kimi found no issue
  in this wording or its focused local test; see the [local access guidance
  review](LLM_WIKI/Manual/2026-07-16-kimi-local-access-guidance-review.md).
- The Home Assistant setup screen uses plain labels: `Только чтение` and
  `Проверка без изменений` in Russian, with matching English labels. Its text
  no longer describes this public repository as private, and a local test
  guards all setup, options, error, and selector text.
- The skeleton passed isolated runtime smoke checks in Home Assistant Core
  2026.6.4 and 2026.7.0 on Python 3.14.3. They used disposable empty
  configurations only; no device, Node-RED, Home Assistant service, or live
  API work was performed. The smoke check also loads the installed diagnostics
  adapter and verifies its fixed redacted report after each approved mode
  change. It also reserves one HASC-like sensor name only in that temporary
  configuration, then proves that a new HASC setup keeps all nine count
  sensors, does not overwrite the occupied name, and leaves the other eight
  protected names unchanged. After HASC is removed, that temporary external
  record must still be unchanged. The same isolated check then creates and
  removes HASC once more, requiring the same nine sensors and the unchanged
  external record again. It requires no HASC device record and requires each of
  the nine HASC sensors to remain unattached to a device. It also requires Home
  Assistant to refuse a second HASC setup while keeping the existing setup
  unchanged and limited to its nine sensors. After each HASC removal, an
  authenticated temporary exact read-only user must receive only an unavailable
  response from the retained local summary route, with none of the nine counts.
  It also requires every removed HASC count state to be absent from the
  temporary state machine. After the final removal, a third empty Home
  Assistant instance uses the same temporary configuration and must not restore
  any HASC setup, object, state, runtime data, or local route; the unrelated
  temporary external record must still be unchanged. Only after that absence
  proof, the third instance creates a fresh `read-only` HASC setup with a new
  entry identifier, exactly nine count sensors, unchanged safe diagnostics,
  unchanged external record, and a newly authenticated local route. That fresh
  setup is removed too, its route immediately fails closed, and a fourth empty
  Home Assistant instance must again contain no HASC data while preserving the
  external record.
- Version 0.3.5 clears the current state values of only the nine HASC count
  sensors after a successful HASC unload. A deactivation therefore no longer
  leaves old aggregate values in memory; reactivation restores only the same
  nine counts. It does not alter a device, service, external state, or
  home-control boundary.
- Version 0.3.6 keeps the options screen safe even when old saved settings are
  broken: it shows the neutral `read-only` default instead of an unapproved
  saved mode, without repairing, saving, or otherwise changing that setting.
  It does not add a device, service, home-data path, or home-control boundary.
- Version 0.3.7 fails closed if a damaged saved configuration contains more
  than one HASC entry, including a user-deactivated one. If another saved
  entry appears while HASC is already working, it first closes the active
  summary and ordinarily unloads the existing HASC display before it clears
  only the captured HASC entries' stale count records. The retained local
  route then returns only unavailable, never counts. Both saved records remain
  for manual repair; HASC never chooses, deletes, or activates one
  automatically. A disposable Core lifecycle covers both an enabled pair and
  an enabled plus user-deactivated pair, before and after restart: after
  removal, a remaining enabled entry requires an explicit reload, while a
  remaining disabled entry requires explicit activation before it can recreate
  exactly nine safe counts. If every saved duplicate is already
  user-deactivated, Core does not start HASC at all, so no count state or page
  exists; its disabled registry rows remain until the owner repairs the saved
  pair.
- Version 0.3.8 closes diagnostics on the same boundary. It returns only the
  fixed unavailable status, without calling the local home-summary reader,
  unless exactly one saved HASC entry is currently loaded and safely
  configured. The isolated Core check covers ordinary unload, user
  deactivation before and after restart, removal through a stale object, and
  both malformed duplicate pairs. It patches the temporary diagnostics reader
  to fail if a closed report attempts to observe the home.
- Versions 0.3.8 and 0.3.9 keep diagnostics and the local summary page closed
  if they ever encounter a saved setting that is unsafe. Those defensive
  boundaries remain even though version 0.3.13 now closes the whole HASC
  display immediately after such a saved change.
- Version 0.3.10 also requires the authenticated local page to find exactly
  one saved HASC entry that Home Assistant still reports as loaded. A stale
  in-memory page pointer after an ordinary stop therefore returns only
  unavailable and does not read the nine-count summary. The disposable Core
  check deliberately restores that stale pointer only after the ordinary stop,
  replaces the reader with a failing function, and requires 503 with no count
  keys.
- Version 0.3.11 applies the same complete saved-setting check before the
  options screen chooses its visible default. A damaged main setting or mode
  option therefore shows neutral `read-only`, even if an isolated mode field
  says `shadow`. Opening that screen leaves both saved mappings unchanged;
  the disposable Core check covers every damaged main-setting and option
  variant before it closes the entry for manual repair.
- Version 0.3.12 validates the complete saved configuration before every
  scheduled nine-count refresh. Its coordinator boundary remains a second
  safety net if an unsafe setting somehow reaches a running display.
- Version 0.3.13 uses Home Assistant's standard saved-setting listener after
  the nine sensors and local page are safely registered. A permitted mode
  change reloads only the same HASC entry and takes effect immediately. An
  unsafe saved main setting or mode choice automatically unloads that HASC
  display, clears its nine count states and its HASC-only registry records, and
  rejects setup before any home-summary reader can run. The disposable Core
  check covers all five unsafe main-setting variants and both unsafe
  mode-choice variants, verifies the closed diagnostics and local page, and
  records exactly one reload of the same HASC entry for a normal safe mode
  change. Before each unsafe save, it replaces the sensor, diagnostics, and
  local-page home readers with a failure, so any read during the automatic
  closing interval fails the Core check. A saved entry that failed setup
  remains available for manual repair; because no running HASC remains to
  listen, its owner then explicitly reloads HASC after correcting it.
- The disposable Core lifecycle now changes one safe HASC setting twice:
  `read-only` to `shadow` and back to `read-only`. Each save must reload only
  that one HASC entry exactly once, retain exactly nine aggregate sensors and
  one authenticated GET-only local page, and preserve blocked direct
  execution. Every later stop, reactivation, and restart assertion expects the
  final `read-only` choice. Kimi found no remaining issue in the final review;
  see the [safe mode cycle review
  note](LLM_WIKI/Manual/2026-07-16-kimi-safe-mode-cycle-review.md).
- The disposable Core lifecycle also saves `shadow` while HASC is ordinarily
  stopped but still user-enabled. That save must neither reload HASC nor read
  a home summary, and its nine values, diagnostics, and local page stay
  closed. Only an explicit start restores the same nine sensors and safe
  `shadow` diagnostics. Kimi found no issue; see the [stopped safe-options
  review note](LLM_WIKI/Manual/2026-07-16-kimi-stopped-safe-options-review.md).
- The same disposable lifecycle also saves `read-only` while HASC is
  deliberately disabled by its user. It remains disabled and not loaded: no
  home summary is read, no reload occurs, and its nine values, diagnostics,
  and local page stay closed. Only the user's explicit activation restores the
  same nine sensors with the saved `read-only` mode. Kimi found no issue; see
  the [user-deactivated safe-options review
  note](LLM_WIKI/Manual/2026-07-16-kimi-user-deactivated-safe-options-review.md).
- After a full temporary Home Assistant restart, the same user-disabled HASC
  setup may also save `shadow` without starting itself. It still has no runtime
  data, page, or count values, and it cannot read a home summary or reload
  HASC. Only the user's explicit activation restores the same nine sensors in
  the newly saved `shadow` mode. Kimi found no issue; see the [disabled
  restart safe-options review
  note](LLM_WIKI/Manual/2026-07-16-kimi-disabled-restart-safe-options-review.md).
- A separate disposable check now gives a user-disabled HASC setup a deliberately
  unsafe saved `proxy` option and then attempts explicit user activation. Home
  Assistant rejects the activation, leaves HASC closed with a setup error, and
  keeps direct execution blocked. The broken option remains only for manual
  repair; no home summary is read and no count values, diagnostics, or local
  page become available. The check then removes the temporary setup and proves
  it stays absent after an empty restart. Kimi found no issue; see the [unsafe
  user-activation review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-user-activation-review.md).
- After that rejected proxy-mode activation, a separate disposable repair
  restores the exact safe options. The correction cannot read the home or
  start HASC by itself; only one explicit reload returns the same nine counts,
  fixed diagnostics, and guarded page with direct execution blocked. Kimi
  found no issue; see the [unsafe proxy-option repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-proxy-option-repair-review.md).
- The same user-activation and manual-repair safety path separately rejects an
  otherwise safe-looking `shadow` option with an extra unmodelled field. The
  exact safe options still require one explicit reload before the nine-count
  display returns. Kimi found no issue; see the [unsafe extra-field option
  repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-extra-field-option-repair-review.md).
- The same disposable activation check now separately uses damaged main data
  whose direct-execution marker says `allowed`. The user activation is still
  rejected before any home read; HASC stays in a setup-error state with no
  counts, diagnostics, local page, service, device, or execution surface. The
  deliberately bad data remains only for manual repair. Kimi found no issue;
  see the [unsafe direct-execution activation review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-activation-review.md).
- A user-disabled HASC entry whose main data lacks the required execution
  block follows the same safe manual-repair path. It cannot start or read the
  home during correction; one explicit reload restores the exact safe data,
  same nine counts, and direct-execution block. Kimi found no issue; see the
  [unsafe missing-execution-block repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-missing-execution-block-repair-review.md).
- A user-disabled HASC entry whose main data lacks the required safe mode also
  remains closed. Safe options cannot fill the missing main value; only a
  manual exact repair followed by one explicit reload restores the same nine
  counts. Kimi found no issue; see the [unsafe missing-mode repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-missing-mode-repair-review.md).
- A user-disabled HASC entry whose main data has an unknown extra field also
  remains closed. The entry needs a manual exact repair and one explicit
  reload before the same nine counts can return. Kimi found no issue; see the
  [unsafe extra-field data repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-extra-field-data-repair-review.md).
- A user-disabled HASC entry whose main data asks for prohibited proxy mode
  also remains closed. It can return only after a manual exact repair and one
  explicit reload, without enabling proxy. Kimi found no issue; see the
  [unsafe proxy-data repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-proxy-data-repair-review.md).
- A user-disabled HASC entry whose main data attempts to unblock direct
  execution remains closed even without an intervening Home Assistant restart.
  Manual exact repair and one explicit reload restore only the same nine
  counts with direct execution still blocked. Kimi found no issue; see the
  [unsafe direct-execution repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repair-review.md).
- A user-disabled HASC entry with both an unblocked direct-execution marker
  and a prohibited proxy option remains closed after only one part is repaired.
  It cannot reload or read the home until the remaining part is repaired and
  the owner explicitly reloads HASC. Repeated partial recovery is explicitly
  rejected. Kimi found no issue after an independent review found and closed
  that edge case; see the [unsafe partial-repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-partial-repair-review.md).
- That unsafe direct-execution activation check also has a separate full
  temporary restart between saving the bad data and the user's activation
  attempt. The saved setup remains user-disabled and unloaded with no runtime
  data or local page after the restart; activation is still rejected and the
  damaged data stays for manual repair. Kimi found no issue; see the [unsafe
  direct-execution restart review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-restart-review.md).
- After that rejected activation, a separate temporary recovery check restores
  the exact original safe data and explicitly reloads HASC. It returns only
  the same nine safe counts, fixed diagnostics, and guarded local page with
  direct execution blocked; it creates no service or device. The saved repair
  itself cannot read the home or start HASC before the explicit reload. Kimi
  found no issue; see the [unsafe direct-execution recovery review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-recovery-review.md).
- The same disposable recovery then deliberately receives the unsafe
  direct-execution marker once more. The restored saved-setting guard closes
  HASC again before any home read: it clears all nine counts, diagnostics, and
  the local page, while retaining the bad saved value for a future manual
  repair. Kimi found no issue; see the [unsafe direct-execution repeat-closure
  review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-closure-review.md).
- A second exact safe manual repair after that repeat closure also remains
  closed until one separate explicit reload. It cannot read the home or
  restart HASC while the saved value is being corrected; the explicit reload
  restores only the same nine counts and safe display. Kimi found no issue;
  see the [unsafe direct-execution repeat-repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-repair-review.md).
- A full empty restart after that second repair preserves only the exact safe
  HASC entry and its same nine counts, fixed diagnostics, and guarded page.
  The direct-execution block remains saved and no control surface appears.
  Kimi found no issue; see the [unsafe direct-execution repeat-repair restart
  review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-repair-restart-review.md).
- After that restart, another unsafe direct-execution marker still causes an
  immediate closure before any home read. The restarted guard clears all nine
  counts, diagnostics, and the local page while retaining the bad saved value
  only for manual repair. Kimi found no issue; see the [unsafe direct-
  execution repeat-repair restart-closure review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-repair-restart-closure-review.md).
- Kimi independently reviewed the automatic saved-setting reload and closure.
  Its first review requested an explicit no-read check during the closing
  interval; the follow-up review found no remaining issues. See the
  [automatic settings reload review
  note](LLM_WIKI/Manual/2026-07-16-kimi-automatic-settings-reload-review.md).
- Kimi independently reviewed the live count-refresh closure with no
  findings. See the [live count-refresh review
  note](LLM_WIKI/Manual/2026-07-15-kimi-live-summary-refresh-review.md).
- Kimi independently reviewed the closed diagnostics change with no findings.
  See the [closed diagnostics review
  note](LLM_WIKI/Manual/2026-07-15-kimi-closed-diagnostics-review.md).
- Kimi independently reviewed this before-reload diagnostics closure with no
  findings. See the [invalid saved-settings diagnostics review
  note](LLM_WIKI/Manual/2026-07-15-kimi-invalid-settings-diagnostics-review.md).
- Kimi independently reviewed the local summary before-reload closure with no
  findings. See the [local summary unsafe-settings review
  note](LLM_WIKI/Manual/2026-07-15-kimi-local-summary-unsafe-settings-review.md).
- Kimi independently reviewed the stale local-summary pointer closure with no
  findings. See the [stale local-summary pointer review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stale-local-summary-pointer-review.md).
- Kimi independently reviewed the options-screen closure for damaged saved
  settings with no findings. It confirmed that the selected default now uses
  the complete saved configuration, keeps manual repair possible, and neither
  writes nor expands HASC's read-only/shadow boundary. See the [damaged
  options-screen review
  note](LLM_WIKI/Manual/2026-07-15-kimi-damaged-options-screen-review.md).
- Kimi independently reviewed the final live and restart duplicate-entry
  closure with no findings. See the [live duplicate fail-closed review
  note](LLM_WIKI/Manual/2026-07-15-kimi-live-duplicate-fail-closed-review.md).
- The local HASC adapter check also covers a failed ordinary unload with one
  saved HASC setup. In that case it keeps the current safe display intact
  rather than partly clearing its values or local page while Home Assistant
  still has HASC loaded. This is separate from the damaged multi-entry case,
  which must close the display.
- The disposable Core lifecycle separately unloads and starts one safe,
  still-user-enabled HASC setup. In the gap, its saved setup and nine enabled
  records remain but all count states and the guarded page fail closed; starting
  the same setup restores only the same nine safe counts, diagnostics, and
  GET-only page. This runs in a temporary empty configuration only.
- The same disposable lifecycle also ordinary-unloads that still-user-enabled
  setup, fully stops the temporary Home Assistant, then starts a new empty
  instance. The setup must auto-load with the same data, safe mode, nine count
  sensors, fixed diagnostics, and authenticated GET-only page. It remains
  user-enabled, direct execution stays blocked, and it creates no device or
  service. This is separate from a user's deliberate deactivation, which must
  remain inactive across a restart.
- After that automatic recovery, the same temporary user-enabled setup is
  ordinarily stopped once more and removed before it starts again. While
  stopped, its saved settings and nine enabled registry records must remain
  intact but all values, diagnostics, and the guarded page must stay closed.
  Removal must clear its records and values, keep the temporary external
  similar-name record unchanged, and remain absent after the following empty
  restart.
- A later temporary reinstallation is also ordinarily stopped before the user
  deactivates it. The stop retains its safe settings and nine enabled but
  value-free records; deactivation marks those same records disabled and closes
  diagnostics and the local page. The same setup is then immediately
  reactivated before a restart: it must restore the unchanged settings, the
  same nine safe counts, diagnostics, and authenticated GET-only page without
  changing the external temporary record. It is deactivated once more before
  the existing restart-and-removal check.
- Kimi independently reviewed that ordinary-stop/deactivate/reactivate path
  with no findings. See the [ordinary-stop reactivation review
  note](LLM_WIKI/Manual/2026-07-15-kimi-ordinary-stop-reactivation-review.md).
- While that user-enabled setup is ordinarily stopped before its temporary
  restart, the same lifecycle tries to add HASC again. Home Assistant must
  refuse the duplicate, retain exactly one still-enabled saved setup and its
  nine unloaded count records, and keep values and the guarded page closed.
  It creates no extra sensor, device, service, or control path.
- Both HASC setup forms now have an isolated input-boundary check: even if a
  form receives invented extra fields beside a safe mode, it persists only the
  fixed approved data shape. This is local test coverage only and adds no
  runtime authority.
- Before its first temporary restart, the same isolated lifecycle check also
  uses Home Assistant's ordinary user deactivation and reactivation path. While
  deactivated, the saved HASC setup is not loaded, its nine registry entries
  are marked disabled by that setup, their temporary state values are absent,
  and the guarded local page returns only an unavailable response with no count
  keys. Reactivation must restore the same nine enabled count sensors, safe
  diagnostics, and authenticated GET-only page, still with no device, service,
  proxy, or execution capability.
- One later temporary reinstallation is deliberately deactivated, persisted
  through an empty restart, and then removed. Its nine HASC registry records,
  temporary states, and guarded local page must stay cleared through the
  following empty restart, while the unrelated temporary external record is
  preserved.
- The first safe setup is also deactivated immediately before a temporary
  restart that replaces only the temporary HASC copy. It must stay disabled and
  not restore runtime data, count states, or the guarded page on its own.
  Explicit reactivation must restore only its existing nine safe count sensors,
  diagnostics, and authenticated GET-only page.
- While that saved setup remains user-deactivated after the temporary restart,
  the lifecycle tries to add HASC again. Home Assistant must refuse the
  duplicate, retain exactly one disabled saved setup and its nine disabled
  records, and keep runtime data, count values, and the guarded page closed
  until the owner explicitly activates the same setup.
- The same disposable lifecycle now counts every local HASC page instead of
  merely finding the first one. An active safe setup must have exactly one
  guarded page; after an in-process deactivation or removal that one retained
  page must fail closed without counts; after a full temporary restart while
  disabled or removed, no such page may exist.
- Version 0.3.4 requires both fixed fields in saved HASC main data. Even a
  safe `shadow` mode in the separate options cannot fill in a missing main
  mode, so an incomplete saved setup stays closed until its exact data is
  restored. This does not add any home-control feature.
- Version 0.3.3 keeps a bad saved HASC setup closed. If its saved data violates
  the fixed safety contract, HASC rejects a reload and removes only its own
  restored count states and stale HASC records, both after startup and during a
  running-system reload. Its delayed startup cleanup is explicitly scheduled
  on Home Assistant's main loop, and the local test fake rejects an unmarked
  startup callback. It does not alter devices, services, other entities,
  Climate, or Automation.
- The same disposable Core lifecycle now checks five deliberately invalid main
  saved settings separately: an unsafe mode, a false unblocked-execution
  marker, a missing required execution block, a missing required mode, and an
  otherwise safe main setting with one extra synthetic field. Each must close
  through reload and restart, recover only after the exact safe data is
  restored, and keep the unrelated temporary record unchanged.
- The same disposable lifecycle now corrects only its own deliberately bad
  saved data back to the exact original safe data, then starts one more empty
  Home Assistant while the corrected HASC setup remains installed. That restart
  must restore the same nine count-sensor names, fixed diagnostics, and the
  authenticated GET-only page with no devices or services. Only then is the
  temporary HASC setup removed and checked through a final empty restart.
- The same disposable lifecycle separately covers two bad saved mode choices
  in HASC options: a temporary `proxy` choice and an otherwise safe `shadow`
  choice with one extra synthetic field. Each rejects reload and remains closed
  after restart; restoring the exact original safe choice must preserve the
  same nine count-sensor names, safe diagnostics, and GET-only page through its
  own empty restart before removal. The check keeps no data beyond its
  temporary fixtures.
- Synthetic Common-contract fixtures, static validators, synthetic shadow
  evidence, and redacted diagnostics/repairs fixtures are present. They use
  Python's standard library and local JSON only.
- Version 0.3.1 retains one explicitly approved local read-only observation:
  `home_summary` in diagnostics. It contains exactly nine aggregate counts:
  areas, devices, entities, sensors, and available/unavailable/unknown/not
  reported/disabled entities. Disabled entries are counted separately before
  the adapter reads a state; `not_reported` therefore means an enabled entry
  has no current state. The adapter reduces each permitted local fact
  immediately to a category; it exports no name, identifier, reading, history,
  address, secret, or raw state. Version 0.3.1 shows the same fixed payload as
  exactly nine HASC diagnostic number sensors. They share one redacted local
  snapshot, exclude HASC's own sensors from the house totals, create no HASC
  device or service, and do not call Home Assistant services.
- The owner explicitly approved a local count-only access path on 2026-07-14.
  It may expose the same fixed nine counts only after Home Assistant
  authentication, an exact built-in read-only user group, and a local-network
  origin check. It must have GET only, no outgoing connection, no token
  storage, no raw data, and no external or device-control capability. See the
  [local-access decision](LLM_WIKI/Manual/2026-07-14-local-read-only-access-decision.md).
- The Russian guides now make clear that ordinary HASC counts and diagnostics
  need no extra user. The optional local account belongs only to a viewer;
  HASC never receives or stores its password, key, or Home Assistant
  connection address, and only checks an incoming request origin momentarily.
  Kimi reviewed that clarification with no findings. See the [local viewer
  wording review](LLM_WIKI/Manual/2026-07-16-kimi-local-viewer-clarity-review.md).
- On 2026-07-14, an owner-performed local v0.1.2 diagnostics check confirmed
  the exact nine-count shape and all required safe-mode markers. Its aggregate
  values and the diagnostics file were inspected only and were not copied into
  this repository or this context.
- On 2026-07-14, the owner separately approved Codex direct local Home
  Assistant observation through a dedicated local non-administrator account.
  This is outside HASC's runtime boundary: Codex sends GET only, keeps the
  credential outside GitHub and chat, and does not retain raw home data. The
  access account is not a technical read-only role, so the no-command rule is
  an operating constraint. See the [direct local observation decision](LLM_WIKI/Manual/2026-07-14-direct-local-read-observation-decision.md).

## Durable decisions

- HASC is a separate repository and has no authority over the existing
  HausMan Hub runtime.
- Initial modes are read-only and shadow only.
- Proxy requires separate owner approval and rollback notes.
- Direct execution remains blocked pending proven shadow parity, a separate
  canary/rollback/authority decision, and owner signoff.
- Do not commit secrets, live identifiers, flow snapshots, service paths,
  command payloads, or deployment scripts.
- Every future code change follows Clean Code and Clean Architecture and must
  receive Kimi review before it is considered complete or pushed.
- The owner approved a public GitHub repository on 2026-07-14 because HACS
  cannot use a private GitHub repository. This permits only the minimal root
  `hacs.json` and manual HACS custom-repository installation. It does not
  approve inclusion in the public HACS catalog, live testing, proxy, or direct
  execution.
- The owner also explicitly approved local, read-only HASC access to home
  data on 2026-07-14. That approval is limited to the v0.2.0 aggregate
  `home_summary`, including a separate disabled-entry count and the guarded
  local count-only path; it does not grant remote assistant access, proxy,
  direct execution,
  Common/Climate/Automation ownership, or permission to save live home data
  in this repository.
- The owner later approved a separate, local Codex read-observation path after
  the Home Assistant UI did not offer the exact `system-read-only` role. It
  does not relax HASC's own strict route guard or grant HASC any device
  authority; see the direct local observation decision above.
- On 2026-07-15 the owner explicitly approved showing only the existing nine
  aggregate HASC counts in Home Assistant. This authorizes exactly nine
  diagnostic number sensors, not devices, controls, new home data, proxy, or
  execution. The decision is recorded in
  [the summary-display decision](docs/read-only-home-summary-display-decision.md).
- Version `0.3.1` has a public GitHub release at
  https://github.com/shumkiiv/hausmanhub_hasc/releases/tag/v0.3.1. It keeps the
  approved nine diagnostic count sensors only. New installations use a HASC
  prefix for their internal names; an existing Home Assistant registry keeps
  the same names through its unchanged permanent keys.
- On 2026-07-15, after the owner updated and restarted Home Assistant, a direct
  local Codex check used only GET requests and HTTP status codes. It confirmed
  that Home Assistant responded, HASC's guarded read-only path was active, and
  all nine approved HASC count sensors were present. No count value, raw home
  payload, name, identifier, credential, or other home data was printed or
  stored.
- A local repository safety check now scans Git-tracked files or exactly the
  staged files before publication. It reads file blobs only from Git's index,
  so it never follows a working-tree symbolic link outside the repository. It
  detects common runtime/backup file names and credential-shaped data, but is
  an additional guard rather than a substitute for a human check. It has no
  Home Assistant, Node-RED, device, or network access.
- The local publication command also verifies the complete manual-HACS package
  from Git-index blobs and modes: approved metadata and manifest, entry files,
  both translations, the local icon, license, and release notes. It rejects a
  missing or linked required file, unapproved metadata or manifest field, bad
  JSON, mismatched translation shape, invalid icon, or missing version note.
  It remains local-only and does not change the HASC runtime or home authority.
- Kimi independently reviewed the local HACS-package check with no findings.
  See the [HACS-package check review
  note](LLM_WIKI/Manual/2026-07-15-kimi-hacs-package-check-review.md).
- The supported baseline was lowered to Core 2026.6.4 after the isolated
  lifecycle check passed on that exact version. See the [2026.6.4 compatibility
  note](LLM_WIKI/Manual/2026-07-14-core-2026-6-4-compatibility.md).
- Kimi reviewed the 2026.6.4 baseline change. Its only non-blocking note was
  a prompt wording mismatch about a test rename; the final code has no related
  defect. See the [2026.6.4 baseline review
  note](LLM_WIKI/Manual/2026-07-14-kimi-core-2026-6-baseline-review.md).
- Kimi reviewed the local brand icon change with no findings. See the [brand
  icon review note](LLM_WIKI/Manual/2026-07-14-kimi-local-brand-icon-review.md).
- Kimi reviewed the isolated diagnostics smoke-check extension and the manual
  safe-check guide with no findings. See the [safe Home Assistant check review
  note](LLM_WIKI/Manual/2026-07-14-kimi-safe-home-assistant-check-review.md).
- Kimi reviewed the safe-mode language change after one review-fix pass and
  found no final issues. See the [safe-mode language review
  note](LLM_WIKI/Manual/2026-07-14-kimi-safe-mode-language-review.md).
- Kimi reviewed the initial HACS metadata change with no findings before the
  private-HACS limitation was discovered. Its historical review note is
  [here](LLM_WIKI/Manual/2026-07-14-kimi-private-hacs-metadata-review.md).
- Kimi reviewed the correction for the public HACS custom-repository path. It
  found three outdated phrases, which were corrected; the final review had no
  findings. See the [public HACS correction review
  note](LLM_WIKI/Manual/2026-07-14-kimi-public-hacs-correction-review.md).
- Kimi baseline/review-fix pass found no blocking safety or correctness issue
  in the static harness. The follow-up tightened mismatch validation, made
  negative tests assert their intended reason, and covered the CLI failure path.
- Kimi reviewed the approved read-only skeleton. It identified no blocking
  safety issue; a type-hint compatibility question was checked against the
  official Home Assistant 2026.7.0 source and is compatible. See the detailed
  [skeleton review note](LLM_WIKI/Manual/2026-07-13-kimi-read-only-skeleton-review.md).
- Kimi reviewed the isolated config/options-flow adapter test twice: first it
  identified two test-isolation gaps, then confirmed the corrections with no
  remaining findings. See the [adapter review note](LLM_WIKI/Manual/2026-07-13-kimi-config-flow-adapter-review.md).
- Kimi reviewed the isolated real-Core smoke check, then confirmed its
  remediations with no remaining findings. See the [Core smoke-check review
  note](LLM_WIKI/Manual/2026-07-13-kimi-home-assistant-core-smoke-check-review.md).
- Kimi reviewed the expanded real-Core lifecycle check, including both safe
  modes, rejected unsafe options, reload, and removal, with no remaining
  findings. See the [expanded Core lifecycle review
  note](LLM_WIKI/Manual/2026-07-13-kimi-expanded-core-lifecycle-review.md).
- Kimi reviewed the persisted-config exact-key boundary tests and confirmed the
  final version with no remaining findings. See the [persisted-config review
  note](LLM_WIKI/Manual/2026-07-13-kimi-persisted-config-boundary-review.md).
- Kimi reviewed the diagnostics allow-list structure test with no findings. See
  the [diagnostics review note](LLM_WIKI/Manual/2026-07-13-kimi-diagnostics-allow-list-review.md).
- Kimi reviewed the fixed manual-repair category contract with no findings. See
  the [repairs review note](LLM_WIKI/Manual/2026-07-13-kimi-manual-repairs-contract-review.md).
- Kimi-backed review of the v0.1.1 aggregate home summary first found an
  in-memory full-state map; that map was removed. The final independent review
  found no blocking or non-blocking issues. See the [aggregate-summary review
  note](LLM_WIKI/Manual/2026-07-14-kimi-read-only-home-summary-review.md).
- Kimi-backed final review of v0.1.2 found no blockers, important issues, or
  minor issues. It confirmed the separate disabled-entry count, the
  state-read order, the strict nine-count boundary, and the updated context.
  See the same [aggregate-summary review
  note](LLM_WIKI/Manual/2026-07-14-kimi-read-only-home-summary-review.md).
- Kimi reviewed the guarded local nine-count access path with no findings. It
  confirmed the fixed response shape, exact read-only role, local-source and
  GET-only guards, fail-closed behaviour, and clean architecture boundary. See
  the [local access review note](LLM_WIKI/Manual/2026-07-14-kimi-local-summary-access-review.md).
- Kimi's first review of the repository safety check found an unsafe
  working-tree symbolic-link read and an over-broad flow-file name check. Both
  were corrected and covered by tests. The final direct Kimi review found no
  remaining issues; see the [repository safety review
  note](LLM_WIKI/Manual/2026-07-14-kimi-repository-safety-check-review.md).
- Kimi reviewed the isolated safe-update check. It found no issues: the check
  restarts only a disposable empty Home Assistant after replacing the local
  test copy, then requires the safe choice, the execution block, and the
  absence of HASC objects to survive. See the [safe-update review
  note](LLM_WIKI/Manual/2026-07-14-kimi-safe-update-persistence-review.md).
- Kimi reviewed the one-command local publication check with no findings. It
  confirmed the command runs only local tests, synthetic fixtures, and the
  existing Git-file safety checks, stopping at the first failed check. See the
  [local publication-check review
  note](LLM_WIKI/Manual/2026-07-14-kimi-local-release-check-review.md).
- Kimi reviewed the added command-list guard with no findings. It confirmed
  that the local publication command's fixed list cannot acquire a network
  address, Home Assistant, `curl`, or `wget` without making the local test
  fail. See the [no-home-target review
  note](LLM_WIKI/Manual/2026-07-14-kimi-local-release-no-home-target-review.md).
- Kimi re-reviewed the manifest/version-history test after its first review
  session did not return a final report. The final review found no issues; see
  the [version-history review
  note](LLM_WIKI/Manual/2026-07-14-kimi-version-history-review.md).
- Kimi reviewed the GitHub local-quality workflow before publication and found
  no safety or boundary issues. See the [GitHub local-quality review
  note](LLM_WIKI/Manual/2026-07-14-kimi-github-local-quality-review.md).
- Kimi reviewed the staged-release-version guard after a first independent
  review identified an omitted file-type change. The guard and its test were
  corrected; Kimi's final review found no remaining issues. See the
  [staged-release-version review
  note](LLM_WIKI/Manual/2026-07-15-kimi-staged-release-version-review.md).
- Kimi reviewed the v0.3.0 nine-count display. Its short fallback review raised
  five questions; checking the complete staged code showed that the first,
  second, third, and fifth came from the deliberately shortened excerpt, while
  the fourth is the intended no-change refresh behavior. No capability or data
  boundary was expanded. See the [nine-count display review
  note](LLM_WIKI/Manual/2026-07-15-kimi-nine-count-display-review.md).
- Kimi reviewed the v0.3.1 protected-name and upgrade-preservation change. The
  first review suggested an explicit legacy-update check; it was added. The
  final review found no blocking or non-blocking issue. See the
  [v0.3.1 review note](LLM_WIKI/Manual/2026-07-15-kimi-v0-3-1-review.md).
- Kimi reviewed the isolated occupied-name check twice. The first pass noted
  that the test should not depend on Home Assistant's exact suffix choice;
  the check now requires only a different protected HASC name and exact names
  for the other eight sensors. The final review found no issues. See the
  [occupied-name review note](LLM_WIKI/Manual/2026-07-15-kimi-occupied-name-check-review.md).
- Kimi reviewed the no-device runtime check with no findings. It confirmed
  that the isolated check requires both an empty HASC device list and no
  device attachment for each of the nine sensors. See the [no-device review
  note](LLM_WIKI/Manual/2026-07-15-kimi-no-device-check-review.md).
- Kimi reviewed the real-Core one-setup check with no findings. It confirmed
  that `single_instance_allowed` is the Home Assistant result for a second
  attempt when the manifest permits only one HASC setup. See the [one-setup
  review note](LLM_WIKI/Manual/2026-07-15-kimi-one-setup-check-review.md).
- Kimi reviewed the isolated external-name cleanup check with no findings. It
  confirmed that after HASC removal, the temporary external entry still has the
  same identity and no HASC or device ownership. See the [external-cleanup
  review note](LLM_WIKI/Manual/2026-07-15-kimi-external-collision-cleanup-review.md).
- Kimi reviewed the isolated repeat-install check with no findings. It
  confirmed that the second safe setup creates the same nine count sensors,
  keeps the external entry unchanged, and removes cleanly. See the
  [repeat-install review
  note](LLM_WIKI/Manual/2026-07-15-kimi-repeat-install-after-cleanup-review.md).
- Kimi reviewed the isolated local-summary closure check with no findings. It
  confirmed that a retained route has no active entry after every removal and
  returns an unavailable response without count data to a temporary local
  read-only user. See the [local-summary closure review
  note](LLM_WIKI/Manual/2026-07-15-kimi-local-summary-closed-after-removal-review.md).
- Kimi reviewed the isolated state-cleanup check with no findings. It confirmed
  that the test remembers only HASC's temporary internal state names before
  removal, then rejects any state left afterward without reading or printing a
  count value. See the [state-cleanup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-state-cleanup-after-removal-review.md).
- Kimi reviewed the isolated final-restart cleanup check with no findings. It
  confirmed that a third empty Home Assistant instance keeps HASC absent after
  removal while preserving the unrelated external record, without HTTP or home
  access. See the [final-restart cleanup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-final-restart-cleanup-review.md).
- Kimi reviewed the isolated fresh-reinstall check with no findings. It
  confirmed that the third instance proves absence before creating a new
  read-only setup, keeps the external record unchanged, and reuses only a
  distinct temporary user name for the guarded local route. See the
  [fresh-reinstall review
  note](LLM_WIKI/Manual/2026-07-15-kimi-fresh-reinstall-after-cleanup-review.md).
- Kimi reviewed the isolated closed fresh-reinstall cycle with no findings. It
  confirmed that the fresh setup is removed, its route fails closed without
  count data, and a fourth empty Home Assistant instance remains HASC-free
  while the external record survives. See the [closed-cycle review
  note](LLM_WIKI/Manual/2026-07-15-kimi-closed-fresh-reinstall-cycle-review.md).
- Kimi reviewed the ordinary deactivation/reactivation lifecycle check with no
  findings. It confirmed that deactivation marks only HASC's nine temporary
  count entries disabled and closes the guarded page, while reactivation
  restores only the same safe observation surface. See the [deactivation
  review note](LLM_WIKI/Manual/2026-07-15-kimi-deactivation-reactivation-review.md).
- Kimi reviewed the removal of a deactivated temporary HASC setup with no
  findings. It confirmed that the test closes the page before removal, clears
  only HASC's own temporary records, and preserves the unrelated external
  record. See the [deactivated-removal review
  note](LLM_WIKI/Manual/2026-07-15-kimi-deactivated-removal-review.md).
- Kimi reviewed the persisted-deactivation check with no findings. It confirmed
  that a temporary restart/update cannot silently reactivate HASC or restore
  its page or state values, while explicit reactivation remains limited to the
  same nine safe counts. See the [deactivation-persistence review
  note](LLM_WIKI/Manual/2026-07-15-kimi-deactivation-persistence-review.md).
- Kimi reviewed the local-page uniqueness check with no findings. It confirmed
  that an active HASC requires exactly one page, while the retained in-process
  page remains safely unavailable after deactivation or removal and no page
  returns after a full empty restart. See the [local-page uniqueness review
  note](LLM_WIKI/Manual/2026-07-15-kimi-local-summary-route-uniqueness-review.md).
- Kimi reviewed the invalid-saved-settings fail-closed fix with no findings. It
  confirmed that HASC clears only its own restored state placeholders after
  startup, immediately clears them on a reload, and does not touch a device,
  service, external entity, or home-control boundary. See the [invalid-settings
  review note](LLM_WIKI/Manual/2026-07-15-kimi-invalid-persisted-settings-review.md).
- The v0.3.3 Kimi review cycle first found stale HASC registry records and a
  startup callback that needed the Home Assistant loop-safety marker. Both
  were corrected, with a local test that rejects an unmarked callback. The
  final focused Kimi review found no issues; see the [invalid-record cleanup
  review note](LLM_WIKI/Manual/2026-07-15-kimi-invalid-record-cleanup-review.md).
- Kimi reviewed the isolated lifecycle for an extra saved main-data field with
  no findings. It confirmed the third deliberately bad main setting closes
  through reload and restart, restores only the same nine counts after exact
  correction, and never touches the external temporary record. See the
  [extra-main-data review note](LLM_WIKI/Manual/2026-07-15-kimi-extra-saved-main-data-review.md).
- Kimi reviewed the lifecycle for a saved main setting with its mandatory
  execution block missing, with no findings. It confirmed the fourth bad main
  setting closes through reload and restart, restores only the same nine counts
  after exact correction, and never touches the external temporary record. See
  the [missing-execution-block review
  note](LLM_WIKI/Manual/2026-07-15-kimi-missing-execution-block-review.md).
- Kimi reviewed the v0.3.4 correction for a missing main mode with a safe
  `shadow` option, with no findings. It confirmed that complete main saved data
  is now required, empty options still work for a complete setting, and the
  rejected setup cannot create a page, sensor, device, service, or execution
  path. See the [missing-main-mode review
  note](LLM_WIKI/Manual/2026-07-15-kimi-missing-main-mode-review.md).
- Kimi reviewed the v0.3.5 cleanup of HASC state values after a successful
  unload, with no findings. It confirmed that HASC removes only its own nine
  displayed values, keeps its registry records, preserves an external state,
  and restores only the same nine counts after reactivation. See the
  [unload-state-cleanup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-unload-state-cleanup-review.md).
- Kimi reviewed the test-only failed-unload case with no findings. It confirmed
  that a failed platform unload leaves the current safe display intact rather
  than partly clearing state or the local page, with no new home access or
  control. See the [failed-unload review
  note](LLM_WIKI/Manual/2026-07-15-kimi-failed-unload-review.md).
- Kimi reviewed the separate ordinary Core unload/setup check with no findings.
  It confirmed that it keeps the user-enabled lifecycle distinct from user
  deactivation, preserves the fixed safety boundary, and uses a temporary
  empty Home Assistant only. See the [ordinary unload/setup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-ordinary-unload-setup-review.md).
- Kimi reviewed the ordinary-unload full-restart recovery check with no
  findings. It confirmed that an enabled HASC setup auto-loads after the next
  empty Home Assistant starts, while preserving exactly the same nine counts,
  fixed diagnostics, GET-only local page, and all control prohibitions. See the
  [ordinary unload/restart review
  note](LLM_WIKI/Manual/2026-07-15-kimi-ordinary-unload-restart-review.md).
- Kimi reviewed removal of an ordinarily stopped, still-user-enabled HASC
  setup with no findings. It confirmed that the temporary test keeps the same
  nine-count and no-control boundary, closes both read paths before and after
  removal, preserves an unrelated similar-name record, and uses no real home.
  See the [ordinary stopped-removal review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stopped-removal-review.md).
- Kimi reviewed user deactivation after an ordinary HASC stop with no findings.
  It confirmed that the disposable lifecycle distinguishes this state from an
  active deactivation, preserves the nine-count/no-control boundary, and
  carries the disabled state through restart and removal. See the [ordinary
  stopped-deactivation review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stopped-deactivation-review.md).
- Kimi reviewed the duplicate-setup guard while HASC is ordinarily stopped.
  Its first pass found a test that depended on exact source formatting; the
  check now uses semantic markers and order instead. The final direct Kimi
  review found no issues. See the [stopped duplicate-setup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stopped-duplicate-setup-review.md).
- Kimi reviewed the duplicate-setup guard while a saved HASC setup stays
  user-deactivated after restart, with no findings. It confirmed that the
  rejected second setup preserves the disabled state and that explicit
  activation restores only the same nine safe counts. See the [disabled
  duplicate-setup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-disabled-duplicate-setup-review.md).
- Kimi reviewed removal of a saved user-deactivated HASC setup after an empty
  restart, with no findings. It confirmed the same collision-aware nine
  disabled records survive until removal and that the following restart remains
  HASC-free. See the [disabled removal-after-restart review
  note](LLM_WIKI/Manual/2026-07-15-kimi-disabled-removal-after-restart-review.md).
- Kimi reviewed the isolated extra-input boundary check for both HASC setup
  forms with no findings. It confirmed that the test preserves the fixed safe
  saved shape and adds no runtime, device, service, network, or home-data
  access. See the [extra config-form-input review
  note](LLM_WIKI/Manual/2026-07-15-kimi-extra-config-form-input-review.md).
- Kimi reviewed the version 0.3.6 safe options-form default. Its first pass
  requested an explicit test for the still-approved `shadow` default; after
  that test was added, the final review found no issues. See the [safe
  options-default review
  note](LLM_WIKI/Manual/2026-07-15-kimi-safe-options-default-review.md).
- Kimi reviewed recovery after a corrected temporary saved setting with no
  findings. It confirmed the additional persistence restart, exact same
  nine-count sensor names, fixed diagnostics, GET-only local page, collision
  preservation, and clean removal. See the [corrected-settings recovery review
  note](LLM_WIKI/Manual/2026-07-15-kimi-corrected-settings-recovery-review.md).
- Kimi reviewed the bad saved mode-option lifecycle with no findings. It
  confirmed option persistence, Core compatibility, the exact nine-count
  boundary, collision preservation, GET-only local access, and final cleanup.
  See the [invalid-options review
  note](LLM_WIKI/Manual/2026-07-15-kimi-invalid-persisted-options-review.md).
- Kimi reviewed the shared lifecycle for both an unsafe saved mode and a
  safe-looking mode with an extra field, with no findings. It confirmed the
  exact settings shape closes HASC through reload and restart, and that only the
  original safe choice restores the same nine counts before cleanup. See the
  [extra-option review
  note](LLM_WIKI/Manual/2026-07-15-kimi-extra-saved-option-review.md).
- The old private-first skeleton decision is now clearly marked historical and
  points to the current public manual-HACS decision. Kimi first asked for a
  less brittle document guard; after that correction, its final review found no
  issues. See the [historical-decision review
  note](LLM_WIKI/Manual/2026-07-15-kimi-historical-skeleton-decision-review.md).

## Verification

Run `python3 -m unittest discover -s tests -v`. The suite validates synthetic
schema data, in-memory form/observation adapters, and strict count-only
diagnostics boundaries; it does not prove shadow parity or grant authority.
The isolated Core lifecycle check is documented in `docs/read-only-skeleton.md`;
on 2026-07-15 it passed with the aggregate summary, exactly nine diagnostic
count sensors, and guarded authenticated loopback route on Core 2026.6.4 and
2026.7.0 using disposable configurations only. It also now starts from a
temporary v0.3.0-style registry, replaces only the temporary HASC copy, and
requires the old names to survive while a new entry receives the protected
v0.3.1 names. Before that new entry, the check reserves one protected-looking
name only in the disposable registry and requires the occupied name to remain
external while all nine HASC sensors still appear. After HASC removal, it
requires that external record to remain unchanged. It then creates and removes
another safe HASC setup, requiring its nine sensors and the same external
record again. After each removal it sends one authenticated loopback GET from a
temporary exact read-only user to the retained local-summary route, requires an
unavailable response, and rejects any returned count key. It proves neither
live-home behaviour nor execution authority. It also records the temporary
HASC state names before each removal and requires all of those states to be
absent afterward, without reading their values. It also requires no HASC device
registry entry and no device attachment for each HASC sensor. It also tries a
second safe setup and requires Home Assistant to refuse it while preserving the
original nine-sensor setup. After the final removal it starts a third empty
Home Assistant instance with the same temporary configuration and requires no
HASC entry, entity, device, service, state, runtime data, or local route to
return, while the unrelated temporary external record remains unchanged.
Only after that absence proof, it creates a fresh `read-only` HASC setup in the
same third instance. The new setup must have a new entry identifier, exactly
nine count sensors, the fixed safe diagnostics report, the unchanged external
record, and the guarded authenticated local route.
That fresh setup is then removed, its route must immediately fail closed
without count data, and a fourth empty Home Assistant instance must contain no
HASC data while the external record remains unchanged.

Before its first restart, the check also deactivates the saved safe setup
through Home Assistant's normal user path. The setup must become unloaded, its
nine registry entries must be marked disabled by that setup, and the guarded
local route must return only an unavailable response without count keys. After
reactivation, it must restore the same nine enabled count sensors, safe
diagnostics, and the authenticated GET-only route without any device, service,
proxy, or execution capability.

One later temporary reinstallation is deactivated before removal. The check
then requires removal to clear its nine HASC records, temporary states, and
guarded page, while preserving the unrelated temporary external record through
the next empty restart.

Before the earlier temporary update restart, the first safe setup is also
deactivated. The restarted empty Home Assistant must keep it disabled, with no
HASC runtime data, count state, or guarded page. Only explicit reactivation
may restore the existing nine safe count sensors, diagnostics, and GET-only
page.

Throughout that temporary lifecycle, the check counts every local HASC page.
An active setup must have exactly one. After a deactivation or removal in the
same temporary process, that one retained page must fail closed without counts;
after a full temporary restart while HASC is disabled or removed, no page may
return.

The same disposable Core check writes one deliberately unsafe saved HASC mode,
rejects an immediate reload, then restarts. It requires no HASC runtime data,
service, device, page, or count state to return. HASC clears only the restored
states belonging to that invalid HASC entry after Home Assistant startup; it
does not change other entities or any device-control surface.

Separately, direct local Codex observation passed a harmless availability
check, a version-only check, and a count-only current-state check on
2026-07-14. It used no command or mutating request, retained no raw home data,
and does not validate or expand HASC runtime authority.

Before publishing, run `python3 tools/check_local_release.py` after staging
the intended files. It runs the local tests, synthetic fixture checks, and the
Git-file safety checks as one fixed list. It also requires a higher integration
version if a staged change touches HASC itself or `hacs.json`. It does not
inspect a live home or grant any authority.

The repository also runs that same fixed command in GitHub after a change to
`main` or a proposed change. Its workflow has only `contents: read`, disables
stored checkout credentials, and has no Home Assistant target, home data, or
deployment step.
Its first GitHub run completed successfully on 2026-07-14 for commit
`a75f78b`; the recorded run is
https://github.com/shumkiiv/hausmanhub_hasc/actions/runs/29352007883.
Public contribution guidance and a pull-request safety checklist are present.
They require the local check, Kimi review for code, and an explicit statement
that no home data or control capability is being introduced.
A Russian release checklist records the safe order for a real HACS update:
version, version history, local check, Kimi review, GitHub check, published
release, HACS refresh, and Home Assistant restart. Documentation-only and
test-only changes do not need a new HACS version.

## Next decision gate

The read-only skeleton is limited to the two approved modes, local synthetic
verification, the narrowly approved aggregate diagnostics summary, exactly nine
diagnostic count sensors, and the guarded local count-only path. The stricter
HASC route still requires the exact Home Assistant read-only account; the
separately approved Codex observation path does not bypass it. Its credential
stays outside the repository and chat. Public HACS catalog listing, proxy, and
direct execution remain out of scope.

The current public manual-HACS decision and its narrow implementation boundary
are recorded in the [HACS packaging decision
record](docs/hacs-packaging-decision.md). The original private-first skeleton
choice is preserved in [the historical skeleton decision
record](docs/read-only-skeleton-decision.md); it is not the current installation
instruction. The skeleton's implementation boundary is documented in
[the read-only skeleton guide](docs/read-only-skeleton.md).

See [repository basics](docs/repository-basics.md) and
[static validation](docs/static-validation.md),
[shadow evidence](docs/shadow-evidence-contract.md),
[diagnostics/repairs](docs/diagnostics-repairs-contract.md), and the
[foundation handoff](LLM_WIKI/Manual/2026-07-13-hasc-repository-foundation.md).
Engineering and review rules are in
[engineering standards](docs/engineering-standards.md).
