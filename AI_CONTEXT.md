# HASC AI Context

Last updated: 2026-07-15.

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
- Before its first temporary restart, the same isolated lifecycle check also
  uses Home Assistant's ordinary user deactivation and reactivation path. While
  deactivated, the saved HASC setup is not loaded, its nine registry entries
  are marked disabled by that setup, and the guarded local page returns only an
  unavailable response with no count keys. Reactivation must restore the same
  nine enabled count sensors, safe diagnostics, and authenticated GET-only
  page, still with no device, service, proxy, or execution capability.
- One later temporary reinstallation is deliberately deactivated before it is
  removed. Its nine HASC registry records, temporary states, and guarded local
  page must then be cleared, while the unrelated temporary external record is
  preserved through the following empty restart.
- The first safe setup is also deactivated immediately before a temporary
  restart that replaces only the temporary HASC copy. It must stay disabled and
  not restore runtime data, count states, or the guarded page on its own.
  Explicit reactivation must restore only its existing nine safe count sensors,
  diagnostics, and authenticated GET-only page.
- The same disposable lifecycle now counts every local HASC page instead of
  merely finding the first one. An active safe setup must have exactly one
  guarded page; after an in-process deactivation or removal that one retained
  page must fail closed without counts; after a full temporary restart while
  disabled or removed, no such page may exist.
- Version 0.3.2 keeps a bad saved HASC setup closed. If its saved data violates
  the fixed safety contract, HASC rejects a reload and, after restart, removes
  only its own restored count states after Home Assistant finishes startup. It
  does not alter devices, services, other entities, Climate, or Automation.
- The same disposable lifecycle now corrects only its own deliberately bad
  saved data back to the exact original safe data, then starts one more empty
  Home Assistant while the corrected HASC setup remains installed. That restart
  must restore the same nine count-sensor names, fixed diagnostics, and the
  authenticated GET-only page with no devices or services. Only then is the
  temporary HASC setup removed and checked through a final empty restart.
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
- Kimi reviewed recovery after a corrected temporary saved setting with no
  findings. It confirmed the additional persistence restart, exact same
  nine-count sensor names, fixed diagnostics, GET-only local page, collision
  preservation, and clean removal. See the [corrected-settings recovery review
  note](LLM_WIKI/Manual/2026-07-15-kimi-corrected-settings-recovery-review.md).
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
