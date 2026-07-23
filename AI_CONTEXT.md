# HausmanHub AI Context

Last updated: 2026-07-23.

## Project state

- Product and Home Assistant integration name: **HausmanHub**. **HACS** is only
  the installation/update mechanism and is never the product name. The old
  temporary four-letter product label must not return to UI, contracts, docs,
  tests, or GitHub presentation.
- Repository: `shumkiiv/hausmanhub_hacs` (public, MIT, `main`).
- Local checkout remains at the legacy filesystem path
  `/home/ivsh/projects/hausmanhub_hasc`; this local folder name is not a public
  product identifier and must not be copied into documentation or URLs.
- Version 1.7.7 performs the one-time naming correction before the Android app
  has a live decoder: display/package name `HausmanHub`, repository
  `shumkiiv/hausmanhub_hacs`, and public contracts `hausman-hub-*`. HACS is
  documented only as the installer. The HA domain `hausman_hub`, config-entry
  unique ID, and entity unique IDs remain unchanged so existing installations
  upgrade in place. New suggested entity IDs no longer contain the old label;
  HA retains registry names for existing entities with the same unique IDs.
  A release check prevents the old public names from returning. The change
  passed 355 local tests and disposable Core 2026.6.4/2026.7.0. Kimi k2p7
  failed before review (`ses_08191e6cdffeZFO3U7kFPu1jNi`, `err_7c5c5f07`)
  and Kimi k3 timed out; the independent fallback review passed in OpenCode
  session `ses_0818e4910ffeNoKwAj0QEiB030`.
- Version 1.7.8 completes roadmap item 18. A local-admin POST at
  `/api/hausman_hub/v1/admin/climate-profiles` accepts an exact versioned list
  of day/night profiles for every configured room. It uses the full saved
  `setup_revision` as an optimistic lock and rejects stale forms with HTTP 409
  before persistence. The operation cannot change rooms, device bindings,
  contour mode, active profile, or temporary temperature. It performs one
  contour-store write and no bridge read or command. If the existing schedule
  is enabled, its last-applied marker is cleared so the normal next schedule
  check can apply the edited active profile when managed control is enabled;
  the strict receipt exposes that pending automatic effect explicitly and does
  not claim it in disabled or shadow mode. The final staged tree passed 361 local
  tests and disposable Home Assistant Core 2026.6.4/2026.7.0. The independent
  read-only OpenCode reviews passed in sessions
  `ses_0816d871effeB7Q1hFv1K1D6DN` and `ses_081692737ffevvtAdFcTaAETTR`
  with no substantial findings.
- Version 1.7.9 completes roadmap item 19. A local-admin POST at
  `/api/hausman_hub/v1/admin/climate-schedule` configures the exact day/night
  wall-clock times and either arms or disarms automatic profile switching.
  Arming requires an automatic contour, managed bridge mode, explicit consent,
  and a current full `setup_revision`; saving itself performs one contour-store
  write and no bridge read or command. Disarming remains deliberately available
  in disabled, shadow, and canary modes, while re-arming there is rejected.
  Changed times or disarming clear temporary overrides because their former
  schedule boundary is no longer valid; an unchanged armed schedule preserves
  them and the applied-period marker. The final staged tree passed 370 local
  tests and disposable Home Assistant Core 2026.6.4/2026.7.0. The independent
  read-only review passed after this canary rule was made explicit and tested in
  OpenCode session `ses_08147ba73ffe8CaWSkrw20gsOX`.
- Version 1.7.10 completes roadmap item 20. Contour settings application,
  scheduled profile application, temporary room temperature, and return to the
  active schedule now emit the same strict
  `hausman-hub-climate-control-receipt` v1. Its action block contains a stable
  action code plus Russian name and, only for room actions, the public room ID
  and effective target temperature. Status and bounded reasons each include a
  stable code and Russian explanation. No source/entity/device binding,
  backend command, service, fingerprint, or bridge address is exposed. Request
  idempotency now binds both desired-state fingerprint and exact action context.
  The final staged tree passed 373 local tests and disposable Home Assistant
  Core 2026.6.4/2026.7.0. Independent read-only review passed in OpenCode
  session `ses_08137d64cffe1UgIfPWWcyU09Q`.
- Version 1.8.0 completes roadmap item 21. HausmanHub packages a frozen,
  redacted reference suite derived read-only from working climate revision
  `0bf681c4278f14f1ad7808b5fe0726b199bcdccc`: 30 cases cover cooling,
  heating, humidity, policy priority, freshness, timing, device availability,
  execution guards, and explicit limitations; 31 protections preserve the
  decision and executor safety boundaries. Each case contains normalized
  observations, the expected decision, abstract device intents, blockers, and
  exact source-test provenance, never live entity/source/device IDs, service
  calls, or addresses. The packaged JSON is strict-schema validated, bound to
  source Git blobs, and locked by a reviewed SHA-256. Its mode is permanently
  `reference_only` with commands disabled. The source climate module and
  Android repository were not changed. Roadmap item 22 must build HausmanHub's
  internal observation model against this fixed corpus; it must not weaken or
  rewrite the reference to fit a new implementation. The final staged tree
  passed 378 local tests, HACS/package/boundary/Android checks, and disposable
  Home Assistant Core 2026.6.4/2026.7.0. Independent read-only review passed
  in OpenCode session `ses_0811f930fffeb7iYWFxEvPAST5`.
- Version 1.8.1 completes roadmap item 22. The pure
  `ClimateObservationSnapshot` boundary represents home, controller, room, and
  logical-device facts using only stable HausmanHub room/device IDs. It makes
  freshness, missing rooms, unavailable or missing devices, normalized
  activity, targets, window state, timing, and physical feedback explicit;
  contradictory, mutable, non-finite, out-of-range, or cross-room values fail
  validation. `build_climate_observation_snapshot` is the only adapter that
  consumes a registry's private `source_id`, solely to look up imported state;
  the resulting model has no source/entity IDs, endpoints, services,
  transports, or commands. The command-free native preview now consumes this
  model and resolves devices by stable HausmanHub ID. A separate reference
  adapter proves that all 30 immutable version-1.8.0 cases fit the same model
  without changing the frozen corpus. The source climate module and Android
  repository remain unchanged. Roadmap item 23 must calculate room temperature
  and humidity targets on this boundary without adding execution authority.
  The final staged tree passed 386 local tests, HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0. Independent
  read-only review passed in OpenCode session
  `ses_0810846a3ffe8paQjkDJAIZrfr`; its only non-blocking future-kind note was
  closed by making reference display-name coverage complete.
- Version 1.8.2 completes roadmap item 23. The pure climate-target layer
  resolves each contour room from its saved day/night profile, keeping the
  selected profile temperature separate from the effective temperature. An
  explicit temporary override replaces temperature only; humidity and strategy
  remain those of the active profile. Each result carries the internal
  observation's fresh/stale/unavailable status, but missing observations never
  erase the user's saved comfort configuration. Target snapshots contain only
  stable HausmanHub contour/room IDs and cannot select equipment, build an
  intent, call Home Assistant, or authorize execution. The runtime exposes a
  read-only seam for the configured contour and never posts from it. All 30
  frozen reference cases resolve the exact recorded target temperature and
  humidity. Roadmap item 24 must determine heating, cooling, and humidifying
  demand from these targets without adding execution authority. The source
  climate module and Android repository remain unchanged. The final staged
  tree passed 394 local tests, HACS/package/boundary/Android checks, and
  disposable Home Assistant Core 2026.6.4/2026.7.0. Independent read-only
  review passed in OpenCode session `ses_080f72f34ffeKIcMnGgNggCfaW`; all four
  nonblocking precision notes were closed before commit by tightening docs,
  covering stale data, avoiding evidence-ledger mutation, and ignoring retained
  cache in disabled mode.
- Version 1.8.3 completes roadmap item 24. The pure demand layer combines one
  internal observation with the resolved room target and reports heating,
  cooling, and humidifying as independent required/not-required/unavailable
  channels. New cooling uses the working core's exact inclusive 0.7 C start
  gap; heating retains the native preview's strict 0.5 C comfort band; and
  humidifying is required only when humidity is more than five points below
  target. Stale observations, missing values, and suspect temperature never
  become required demand, while temperature and humidity availability remain
  isolated. This is raw comfort demand only: season conflicts, running-device
  hysteresis, equipment policy, safety priority, intents, and commands are not
  part of the layer. The runtime reads observation, targets, and demand once
  without evidence mutation and ignores retained state when disabled. All 30
  frozen cases map to deterministic raw demand, including the exact 25.7/25.6
  cooling boundary and the dry-room humidity anchor. Roadmap item 25 must
  resolve heating/cooling conflicts without adding execution authority. The
  source climate module and Android repository remain unchanged. The final
  staged tree passed 402 local tests, HACS/package/boundary/Android checks, and
  disposable Home Assistant Core 2026.6.4/2026.7.0. Independent read-only
  review passed with no substantial findings in OpenCode session
  `ses_080dc1768ffekUBX1A0v30rMvR`.
- Version 1.8.4 completes roadmap item 25. A pure thermal-resolution layer
  combines raw heating/cooling demand with the observed home season,
  occupancy mode, and central-heating state. Away-safe-off has first priority,
  away-keep observes, and invalid thermal data is unavailable at home. Winter
  or explicitly active central heating blocks opposing cooling; summer blocks
  heating; an unknown season preserves current-core compatibility by allowing
  cooling but holding heating until a heating mode is known. The result is one
  immutable heating/cooling/hold/observe/safe-off/unavailable state with a
  stable reason. Humidity remains an independent raw demand. The layer has no
  equipment, HA entity, intent, service, command, or execution authority. The
  runtime derives observation, targets, demands, and resolution through one
  non-evidence-mutating read and ignores retained state while disabled. All 30
  frozen cases resolve deterministically, while device policy remains roadmap
  item 26. The source climate module and Android repository remain unchanged.
  The final staged tree passed 412 local tests, HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0. Independent
  read-only review passed with no substantial findings in OpenCode session
  `ses_080cb528cffeu1SP2f12E7oY4I`. Its nonblocking source-normalization note is
  carried into item 26: the future native adapter must explicitly map the old
  `hvacMode == heating` and active-heating facts into HausmanHub's normalized
  heating-mode observation before external-source removal.
- Version 1.8.5 completes roadmap item 26. A pure equipment-policy layer maps
  the resolved thermal direction only to thermal devices explicitly selected
  in each contour room. Generic air-conditioner profiles match the frozen
  core's soft/normal/aggressive setpoint, fan, and quiet choices. Radiator
  thermostats retain the frozen 19 C day, 17 C night, below -10 C cold
  adjustment, and above 18 C daytime heat-load adjustment, while unknown
  heating/period data fail closed to observation. Floor heating has an
  explicitly documented new HausmanHub rule because the frozen module had no
  complete floor policy. Unavailable devices clear all proposed settings;
  stale and mixed snapshots cannot create a setting. The immutable plans keep
  stable HausmanHub IDs only, expose `commands_enabled=False`, and contain no
  HA entity, private source, service, intent, command, or execution authority.
  Runtime derives the plan through one non-evidence-mutating read and never
  posts. The transitional Climate API still does not supply ordinary runtime
  observations with all home period/heating/weather facts, so live TRV plans
  remain observe-only until a native HA observation adapter supplies them;
  this limitation is explicit and must be closed before removing the external
  module. Item 27 owns running-device hysteresis, timing, and short-cycle
  protection. The source climate module and Android repository remain
  unchanged. The final staged tree passed 424 local tests,
  HACS/package/boundary/Android checks, and disposable Home Assistant Core
  2026.6.4/2026.7.0. Two read-only OpenCode/Kimi k3 attempts inspected the
  staged tree and frozen source but returned no terminal report, so neither is
  counted as PASS: `ses_080b2d8a4ffeFX0o9wFoBmaVH0` and
  `ses_080aa6566ffelElJG2iyoowP71`.
- Version 1.8.6 completes roadmap item 27. A pure stability layer applies the
  frozen working-core start boundary, running hysteresis, gradual 27 C/low-fan
  softening, hard-off override, minimum run/off windows, and humidifier
  hysteresis only to devices selected in each contour room. Default AC timing
  is 8 minutes running and 6 minutes off; confirmed fast cooling uses 5/8,
  confirmed slow cooling uses 10/5, and confirmed short cycles add at most two
  off minutes up to a 10-minute ceiling. Exact interval boundaries release the
  protection, while an active window exposes bounded remaining seconds.
  Confirmed weak cooling escalates only after the preserved day/night dwell,
  first from low to medium fan and then from 26 C to the room target; stale or
  unconfirmed physical feedback cannot authorize escalation. The
  humidifier thresholds are expressed relative to the configured target so
  the frozen 45 percent target gives 39/44 normally and 40/45 during active
  cooling or heat load at least 26 C. An unconfirmed-closed window selects
  humidifier off before missing humidity is considered. The immutable result
  rejects forged actions, thresholds, remaining times, contradictory inputs,
  differing observation timestamps, and mutable collections; it has stable
  HausmanHub IDs only and
  `commands_enabled=False`. Runtime derives the protected plan through one
  non-evidence-mutating read and never posts. All 30 frozen cases are
  deterministic, with exact anchors for timing and humidity. The transitional
  Climate API still does not supply physical transition timestamps, confirmed
  short-cycle history, or reliable window state; the policy accepts these
  facts but does not invent them. Native acquisition and restart restoration
  remain items 33 and 30, so this result must not be wired directly to an
  executor yet. Item 28 owns manual mode, final priority ordering, and safe
  stop. The source climate module and Android repository remain unchanged.
  A read-only OpenCode/Kimi k3 audit inspected the staged implementation in
  session `ses_0808c1139ffeGfZVhX1YG2h6Um` but was interrupted before a final
  top-level PASS/FAIL and is not counted as PASS. Its completed research branch
  correctly identified that deterministic execution alone was weaker than
  direct reference comparison and that observation provenance needed an
  explicit boundary. Before commit, HausmanHub therefore binds target and base
  equipment plans to the exact observation timestamp, rejects a timestamp
  mismatch, compares all 11 timing/cooling reference anchors directly with
  frozen expected action/setpoint/fan/quiet fields, and adds direct night-dwell,
  stale-feedback, heat-load-boundary, unknown-window, and forged-humidity tests.
  The final staged tree passed 441 local tests, HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0.
- Version 1.8.7 completes roadmap item 28. A pure final policy layer preserves
  the frozen priority ladder: away, safety lockout, freshness guard,
  forced-auto-only, manual, auto, then direct-device requests as an external
  last fallback which is not admitted to the internal plan. Manual mode and a
  room-scoped manual request produce observation with no automatic device
  plans; forced automation rejects that request and keeps the automatic plan.
  Away-safe-off, open or unknown windows, missing temperature, and explicit
  cooling/heating denial produce a selected-device-only safe-stop result.
  Running or unknown AC/humidifier/floor activity needs a safe stop, confirmed
  stopped devices suppress the redundant stop, unavailable devices remain
  explicit, and radiator thermostats stay observe-only rather than receiving
  an invented safety setpoint. Stale state, suspect temperature, and stale
  delayed work observe with an empty device plan. All 30 frozen cases match
  expected policy, room action, and ordered blockers exactly. Control requests
  and execution guards are scoped to one stable room id. The immutable result
  rejects forged output, mutable collections, mixed device plans, and
  observation-time mismatches; it has `commands_enabled=False` and no HA
  entity, service, transport, or private source binding. Runtime derives the
  final policy from one non-evidence-mutating read and never posts. Item 29
  owns failure isolation between rooms and devices. The source climate module
  and Android repository remain unchanged. The final staged tree passed 454
  local tests, HACS/package/boundary/Android checks, and disposable Home
  Assistant Core 2026.6.4/2026.7.0.
- Version 1.8.8 completes roadmap item 29. The full strict climate pipeline now
  runs independently per configured room. A missing room input, no retained
  device, or a bounded local calculation violation produces a failed result
  only for that room and does not erase neighbouring policies. A configured
  device absent from the observation is removed only from that room's effective
  calculation and reported by stable HausmanHub id; `missing` and `unavailable`
  placeholders remain explicit while healthy devices in the same room keep
  their plans. Each immutable room result is `ready`, `degraded`, `unavailable`,
  or `failed` with fixed ordered reasons. The snapshot rejects forged states,
  mutable ids, mixed observation times, and private bindings; it always has
  `commands_enabled=False`. Runtime obtains one observation without evidence
  mutation or POST. Item 30 owns restoration of state and protective delays
  after restart. The source climate module and Android repository remain
   unchanged. The final staged tree passed 463 local tests,
   HACS/package/boundary/Android checks, and disposable Home Assistant Core
   2026.6.4/2026.7.0.
- Version 1.8.9 completes roadmap item 30. Confirmed climate transition facts
  now survive a Home Assistant restart. For every configured air conditioner a
  versioned per-entry Home Assistant store persists only the normalized phase,
  the last confirmed start and stop times, and the bounded confirmed
  short-cycle count, keyed by stable HausmanHub ids with no private bindings,
  sources, services, or command authority. On startup the memory is reconciled
  against the current registry: unbound or moved devices are dropped and
  future-dated memory after a clock change is reset. After a restart with
  retained memory the protection rearms once conservatively from fresh
  observations and then continues normally, so protective delays are not
  silently restarted from zero. A storage failure fails the climate
  calculation closed with no partial state and no commands;
  `commands_enabled` remains `False`. The source climate module and Android
  repository remain unchanged. The independent Kimi review passed after one
  fix iteration that made the stored version check strictly typed. The final
  staged tree passed 471 local tests, the HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0.
- Version 1.9.0 completes roadmap item 31. A strict command-free comparison
  layer now states, for every configured room and selected device, whether the
  observed state of the working climate module agrees with the native
  HausmanHub plan. Each room and device is `aligned`, `diverged`, or
  `not_comparable` with a fixed ordered reason list: stale observation,
  missing room policy, unavailable room data, manual observe, planned observe,
  unobserved or unavailable device, unknown activity, unobserved settings,
  activity mismatch, or settings mismatch. A stale observation short-circuits
  all rooms; manual mode and deliberate observe are honestly not comparable;
  an already stopped device needs no repeated stop. The comparison uses only
  stable HausmanHub ids and approved codes, always has
  `commands_enabled=False`, and the runtime accessor reads one observation
  without writes or POSTs. The source climate module and Android repository
  remain unchanged. The final staged tree passed 485 local tests, the
  HACS/package/boundary/Android checks, and disposable Home Assistant Core
  2026.6.4/2026.7.0.
- Version 1.9.1 completes roadmap item 32. Decision comparison is now proven
  on all 30 frozen reference scenarios: for each case the module's frozen
  decision is expressed as its post-decision observed state and the comparison
  verdict is locked in an exact table. 19 scenarios align exactly; 8 are
  honestly not comparable (manual mode, deliberate observe, stale data, and
  the thermostat activity the module never exposes); 3 execution-guard
  scenarios confirm a bounded fan-stage divergence — the frozen module
  escalates to medium while the ported stability layer does not escalate
  without confirmed feedback and elapsed run time. The divergence is frozen
  as the expected verdict, not hidden. Automatic rooms with no room-level
  action (for example a thermostat-only adjustment) now compare per-device
  plans instead of a blanket not-comparable. The comparison still creates no
  commands, carries no private bindings, and always has
  `commands_enabled=False`. The source climate module and Android repository
  remain unchanged. The final staged tree passed 491 local tests, the
  HACS/package/boundary/Android checks, and disposable Home Assistant Core
  2026.6.4/2026.7.0.
- Version 1.9.2 completes roadmap item 33. Strict Home Assistant device
  adapters now translate each proven final device plan into an exact call
  list from a closed service whitelist: `climate.set_hvac_mode` (cool/heat/
  off), `climate.set_temperature`, `climate.set_fan_mode`, and humidifier
  power. Calls name one validated registry control entity and bounded values
  (temperature 10–35, humidity 0–100, approved modes only); arbitrary fields
  are impossible. Translation stops honestly with bounded limits: missing
  control endpoint, missing capability, unsupported action, observe, hold,
  nothing to translate, or the quiet setting that has no strict call. A
  missing fan capability blocks the whole device translation rather than
  silently dropping part of the plan. This is translation only:
  `commands_enabled` is always `False` and nothing is executed. The source
  climate module and Android repository remain unchanged. The final staged
  tree passed 504 local tests, the HACS/package/boundary/Android checks, and
  disposable Home Assistant Core 2026.6.4/2026.7.0. The independent Kimi
  review initially stopped the set (FAIL) for an inconsistent floor-heating
  hvac call and a missing `HVAC_MODE` capability requirement; one fix
  iteration closed both with new tests. The same iteration fixed the
  floor-heating policy: in the heating season it now yields a strict
  set-temperature action instead of tripping the final-plan invariant and
  failing the room (no frozen reference case covers floor heating, so frozen
  parity is untouched). HVAC-mode calls now require the declared `HVAC_MODE`
  capability.
- Version 1.9.3 completes roadmap item 34. HausmanHub can now physically
  control the climate itself — exactly one explicitly configured trial room
  and only with every guard agreeing. A one-minute tick requires CANARY
  bridge mode, an automatic contour, a fresh observation, a ready room, a
  decisive comparison, trial-scoped devices with HausmanHub control
  endpoints, and a complete translation. It acts only when the native plan
  diverges from the observed state; alignment is honestly skipped as
  up-to-date, uncertainty denies without a single call. Execution uses only
  the strict adapter whitelist in order and stops at the first error. The
  redacted receipt keeps only the stable room id, a bounded status
  (applied/up_to_date/denied/failed), bounded reasons, and call counts. The
  enforced execution boundary now allows HA service calls only in the trial
  executor module and the legacy canary switch; the skeleton test locks
  this. The operator must remove the trial room from the external module's
  rooms to avoid double control. The source climate module and Android
  repository remain unchanged. The final staged tree passed 513 local tests,
  the HACS/package/boundary/Android checks, and disposable Home Assistant
  Core   2026.6.4/2026.7.0.
- Version 1.17.0 local release candidate complete (settings page rework, user request
  2026-07-23: best-practice design, reorganize, push, release). Plan:
  `.omo/plans/2026-07-23-settings-page-home-environment.md`. Scope:
  expose item 38 home signals in the options UI (previously only raw
  JSON editor) and convert navigation selects to native HA menus.
   `config_flow.py` has a `home_environment` options step with three optional
   entity pickers and two required box NumberSelectors. Both heating lockout
   thresholds use the registry range -40..60 °C and require `low < high`.
   `ClimateRuntime.async_update_home_environment()` replaces only the home
   block under the runtime lock, preserving concurrent room/device changes.
   The initial and advanced screens use `async_show_menu`; translations, unit
   tests, Core smoke navigation, README, manifest 1.17.0, changelog, and HACS
   validation are updated. `check_local_release.py` passed through a temporary
   Git index with 618 tests. Disposable Core checks were skipped because their
   expected Python environments are absent. A manual Home Assistant test archive
   was built from the
   current working tree at
   `/home/ivsh/projects/УД-hasc/releases/HausmanHub-1.17.0-test.zip`.
   It contains only `custom_components/hausman_hub`, includes the sidebar panel,
   passes `unzip -t`, and has SHA-256
   `82f5f8d4a5dc43d642be3d6e4fa9339970ff91e30478890fcb78053153f56b45`.
   The release code was committed as `909ae3d`, pushed to `origin/main`, and
   published as the latest GitHub Release `v1.17.0`:
   https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.0. The remote
   tag resolves exactly to `909ae3d`; its manifest declares `1.17.0` and the
   sidebar panel asset is present. The only GitHub check job concluded
   successfully:
   https://github.com/shumkiiv/hausmanhub_hacs/actions/runs/29991423859/job/89154943431.
   No live Home Assistant action occurred. Next: refresh the custom repository
   in HACS, install `1.17.0`, restart Home Assistant, and provision disposable
   Core environments for the deferred smoke check.
   Post-install read-only diagnostics against Home Assistant Core 2026.7.3
   confirmed that `hausman_hub` is loaded, the panel JavaScript returns HTTP
   200 and exactly matches the `v1.17.0` Git blob (SHA-256
   `4f796a24e4147a73ee3673a6568401dc0425feeafe61aad5b7cdb37acdb59a3f`), and
   the admin panel API exists (HTTP 403 for the intentionally read-only
   diagnostic token). WebSocket `get_panels` succeeds but omits
   `hausman-hub` for that non-admin user. This proved only that the static asset
   and authorization boundary were active; the later administrator screenshot
   proved that the panel itself had never been registered. No live state was
   changed.
- Version 1.17.1 is the sidebar registration hotfix. The administrator's
  sidebar editor screenshot proved that `hausman-hub` was absent rather than
  merely hidden. Home Assistant Core 2026.7.3 defines
  `panel_custom.async_register_panel` as an async function, but HausmanHub
  called it without `await`; the static asset registered first, explaining the
  live HTTP 200, while the panel coroutine never executed. `panel.py` now
  awaits registration, and the panel tests use an async mock so the former bug
  fails deterministically. The final staged package passed 618 local tests,
  HACS/package checks, version checks, and repository-safety checks. All three
  configured Kimi profiles stopped before review with the same monthly-quota
  HTTP 403 (`ses_071c8bf52ffeCp7AD0sQb6RwH1`,
  `ses_071c86d9cffe2MGwrMXqnx7l3d`, and
  `ses_071c827f4ffeQHSh5t41p627GO`); this is not a Kimi PASS. The direct
  read-only OpenAI fallback review passed with no substantial findings in
  OpenCode session `ses_071c619cfffeCD4QTUh6eD5vsI`. Publication is pending.
- Version 1.16.0 completes roadmap item 38. Windows, presence,
  outdoor temperature, and sensor quality now shape climate decisions.
  An open or unreadable configured window hard-locks its room into
  safe-off on the next tick, bypassing stability debounce, while an
  unbound window stays neutral; suspect or unknown room temperature
  also forces safe-off with computed targets kept for diagnostics.
  Absence applies a soft setback of minus two degrees heating and plus
  two degrees cooling, clamped to limits and never inverting demand,
  only in automatic modes and never on temporary or manual targets;
  unreadable presence changes no target, and auto-humidifying is
  denied for both absent and unknown presence. A weather lockout
  blocks automatic heating from 18 degrees outdoor and re-enables it
  from 16, keeping the previous explicit home-level permission inside
  the band (first observation there fails closed); cooling is
  untouched, manual and temporary targets bypass it, and both
  thresholds are registry options (16/18 defaults). Central heating
  now distinguishes unbound (neutral) from bound-but-off-or-unavailable
  (hydraulic radiators blocked and driven to safe-off); electric
  floors, heat pumps, and air conditioners are unaffected. The frozen
  reference suite was re-fingerprinted for the suspect-temperature and
  central-heating behavior changes, and 26 new acceptance tests cover
  the setback, lockout hysteresis, gates, and force safe-off
  semantics. Independent review returned FAIL (activity-based
  hysteresis was not a sound latch; away-setback formula was
  questioned); one fix iteration replaced the activity latch with
  explicit home-level weather-heating state, and a probe confirmed the
  away-setback formula matches its ±2 invariant on the exact
  questioned cases. The final tree passed 618 local tests, the full
  release gate, and disposable Core 2026.6.4 and 2026.7.0.
  Remaining roadmap: 39 (per-room schedules and profiles), 40
  (standalone climate release), then 41-50 (HausmanHub 2.0 platform).
- Version 1.15.0 completes roadmap item 37. A safe migration wizard
  imports existing legacy climate settings into an empty native
  contour: a one-shot GET-only read of the old API with private-address
  validation (address never persisted), an explicit per-device entity
  mapping confirmed from the native HA catalog (ordinal form tokens,
  skip only for passive sensors), and one atomic write with a stored
  receipt. Rooms and day/night comfort profiles are copied (targets
  into both), `auto`/`forced_auto_only` become `managed`, `manual`
  becomes `disabled`; schedule, live state, authority, history, and the
  API address are never moved. The safe rollback removes exactly the
  migrated setup only when the full registry+contour fingerprint still
  matches, and is blocked after any manual change. The confirm step
  rebuilds the draft against a fresh catalog before writing, and the
  receipt is saved before the setup write with compensation on failure.
  Independent review returned FAIL (rollback compared only ID sets,
  orphaned import on receipt failure, stale catalog at confirm, unbounded
  GET, loose receipt schema, skip on active devices, raw private ids in
  form fields); one fix iteration resolved all seven with regression
  tests, and the follow-up self-review confirmed the exact-fingerprint
  rollback and receipt-first ordering. The final tree passed 591 local
  tests, the full release gate, and disposable Core 2026.6.4 and
  2026.7.0 with a real migration import plus rollback on a blank Core.
  Remaining roadmap: 38 (windows/presence/outdoor/sensor quality), 39
  (per-room schedules and profiles), 40 (standalone climate release),
  then 41-50 (HausmanHub 2.0 platform).
- Version 1.14.0 completes roadmap item 36 entirely. The external
  climate module is retired: `ClimateControlMode` is now only
  `disabled` or fully native `managed`; legacy shadow/canary entries
  migrate once via `async_migrate_entry` (config entry version 2) to
  disabled with contours switched to `ContourMode.OBSERVE`, saved
  bridge target and canary room fields removed, and the old evidence
  store key deleted best-effort. The legacy routes
  (`/actions`, `/operations`, admin shadow-evidence and
  canary-preflight) now answer 404, and all bridge code is deleted
  (`climate_bridge.py`, `climate_commands.py`, `climate_evidence.py`,
  `climate_evidence_storage.py`, `climate_canary_preflight.py`,
  `android_climate.py`, `climate_operations.py`, and the
  `climate_import.py` parser, whose dataclasses live in the neutral
  `application/climate_discovery.py` while the parser itself survives
  only as `tests/climate_bridge_fixture.py`). Bridge-bound devices
  without a CONTROL endpoint are quarantined: excluded from
  observation, projections, ownership, trial, and apply, and surfaced
  as the `needs_reimport` readiness reason. The Android room-control
  block honestly advertises no executable action with bounded reasons.
  Independent review returned FAIL (migration not persisted, duplicate
  panel views, parser left in production); one fix iteration resolved
  all three and the follow-up passed. The final tree passed 578 local
  tests, the full release gate, and disposable Core 2026.6.4 and
  2026.7.0 with a managed end-to-end scenario that performs zero
  bridge reads. Remaining roadmap: 37 (safe migration wizard for
  existing settings), 38 (windows/presence/outdoor/sensor quality),
  39 (per-room schedules and profiles), 40 (standalone climate
  release), then 41-50 (HausmanHub 2.0 platform).
- Version 1.13.0 completes roadmap item 36 sub-step 36f3. Startup in
  MANAGED and DISABLED never reads the external module; the bridge
  client is constructed only for SHADOW and CANARY (their evidence
  purpose), and `_require_client` now enforces that in every path, so
  legacy shadow-evidence, canary-preflight, and canary-action routes
  cannot touch the bridge outside those modes. The bridge target is
  optional for MANAGED (a legacy saved target is accepted but unused)
  and required for SHADOW/CANARY. The contour wizard tries native
  discovery first and falls back to the one-time bridge address form
  only when native discovery is unavailable; saving a contour without
  a bridge target is allowed. Disabled-mode admin wizards observe
  natively (explicit discovery) while the disabled control pipeline
  keeps its no-observe gate. Review returned FAIL (disabled wizard
  fell back to the bridge form; `_require_client` allowed managed
  bridge contact); one fix iteration resolved both with poison
  regressions and the follow-up passed. The final tree passed 630
  local tests, the full release gate, and disposable Core 2026.6.4 and
  2026.7.0. Remaining: 36g retires shadow/canary, the legacy actions
  route, and the bridge itself.
- Version 1.12.0 completes roadmap item 36 sub-step 36f2. All climate
  setup wizards (setup options, current setup, contour draft
  create/validate/save, registry import snapshot) now build their
  discovery snapshot from the native Home Assistant catalog in every
  mode; the bridge is never touched, locked by poison tests. Unassigned
  entities are honestly roomless: the contour wizard assigns them on
  the room step, the import wizard asks for a room, and saving requires
  an explicit assignment. New devices receive a fresh private
  `hausmanhub-native-<entity_id>` source id (never the entity id) plus
  control/observation endpoints, so a saved contour runs natively at
  once; bound devices keep their private source id and endpoints
  through re-imports. Draft save stays forbidden in CANARY and is
  atomic elsewhere. Review returned FAIL (endpoint loss on re-import,
  duplicate room assignment, multi-room assignment dead-end, loose
  override parameters); one fix iteration resolved all four with
  regression tests and the follow-up passed. The final tree passed 624
  local tests, the full release gate, and disposable Core 2026.6.4 and
  2026.7.0 with a migrated end-to-end wizard scenario. Remaining
  sub-steps: 36f3 (startup and bridge lifecycle semantics) and 36g
  (shadow/canary retirement with the legacy actions route).
- Version 1.11.0 completes roadmap item 36 sub-step 36f1 (the Oracle
  split of 36f is 36f1 native discovery, 36f2 wizard cutover, 36f3
  mode/bridge lifecycle). The new pure
  `application/climate_native_setup.py` enumerates climate-relevant Home
  Assistant entities (climate, humidifier, temperature/humidity
  sensors) through `HomeAssistantClimateStateView.entity_catalog()` and
  builds the existing `ClimateImportSnapshot` wizard shape natively:
  rooms come from the registry plus native observation, bound devices
  keep their private `source_id` (matched via `endpoints[].entity_id`,
  all endpoints excluded from unbound candidates), unbound entities
  become candidates with `source_id = entity_id` and the locked
  unassigned sentinel `room_id = ""`. Classification is conservative
  (domain + device_class + supported_features intersected with the
  strict vocabulary). Identity option A was locked: the setup payload
  keeps its current contract version, and a native candidate's
  `source_id` never migrates into the private registry `source_id`.
  Wizard cutover (including accepting unassigned candidates with an
  explicit room choice and allowing draft save in MANAGED with atomic
  rebuild) is 36f2; startup/bridge lifecycle is 36f3. Independent
  review returned FAIL (multi-endpoint duplication, negative
  supported_features, empty-state availability, wizard-chain test
  gaps); one fix iteration resolved all four and the follow-up passed.
  The final tree passed 615 local tests and the full release gate.
- Version 1.10.0 completes roadmap item 37. HausmanHub now has its own
  admin page in the Home Assistant sidebar (`panel_custom` registration,
  `require_admin=True`, `config_panel_domain` for the settings gear).
  The plain-JS webcomponent
  `custom_components/hausman_hub/frontend/hausman-hub-panel.js` (no
  build step, no external URLs, Russian UI, 30-second polling) reads a
  combined admin payload and offers the everyday actions: apply saved
  contour settings and set/clear per-room temporary temperature. Three
  new admin-gated routes serve it:
  `GET /api/hausman_hub/v1/admin/panel`,
  `POST .../admin/panel/apply`, `POST .../admin/panel/temporary-temperature`;
  tablet and read-only users get 403, malformed bodies get 400, and
  unexpected exceptions propagate instead of masquerading as 503.
  Registration in `panel.py` is idempotent (static paths once per
  server lifetime under a separate `hass.data` key, panel skipped when
  already present) and the panel is removed on entry unload. Registry
  and setup editing deliberately stay in the config-flow wizards.
  Independent review initially returned FAIL (non-idempotent
  registration, exception mapping, missing lifecycle tests); one fix
  iteration resolved all three and the follow-up passed. The final
  tree passed 610 local tests, the full release gate, and disposable
  Home Assistant Core 2026.6.4 and 2026.7.0 including the new routes.
- Version 1.9.10 completes roadmap item 36 sub-step 36e2. In MANAGED
  mode all five read projections (Android public snapshot, contours
  snapshot, apply preview, readiness, administrator snapshot) are
  served by the native builders from 1.9.9; the runtime never touches
  the bridge for them, locked by poison-bridge acceptance tests
  (`NativeProjectionSwitchTest`: zero bridge calls in managed and
  disabled, fail-closed mapping without a state view, shadow still
  reads the bridge deliberately). SHADOW and CANARY projections keep
  bridge reads because their purpose is migration evidence and canary
  comparison (36g). DISABLED keeps its no-observe behavior. The apply
  preview now reports the native strict HA plan call count instead of
  legacy bridge command counts (three legacy tests updated to the new
  number). Presentation helpers moved to the neutral
  `application/android_climate_values.py` shared by both builders,
  resolving the 36e1 review finding. Independent review passed with
  three LOW findings: the module docstring was updated, the
  no-state-view fail-closed test was added, and a shadow/canary
  projection matrix remains assigned to 36g. The final tree passed 601
  local tests and the full release gate. Remaining bridge usage:
  startup refresh (36f), registry import and setup/discovery wizards
  (36f), shadow evidence and the legacy canary route (36g).
- Version 1.9.9 completes roadmap item 36 sub-step 36e1. The new pure
  module `application/climate_native_projections.py` builds the five
  external projection payloads (Android tablet contract v12, contour
  snapshot, contour apply preview, readiness, administrator snapshot)
  from the native `ClimateObservationSnapshot` and the version-2
  registry only, with no bridge contact possible by construction.
  Production consumers are NOT switched yet; that is sub-step 36e2,
  which will also need a neutral home for the presentation helpers the
  module currently imports from `android_climate.py` (review finding,
  deliberately deferred). Golden and parity tests
  (`tests/test_climate_native_projections.py`) lock byte-identical
  payloads against the legacy builders for the same physical situation
  plus the documented semantic differences: native reconciliation
  covers configured devices only (bridge-only devices no longer count
  as unregistered), the native apply preview reports the real strict
  HA plan call count instead of legacy bridge command counts, and
  integral floats versus JSON ints are normalized in comparison.
  Independent review initially returned FAIL; one fix iteration added
  readiness hardening (full room-observation coverage and device
  availability required), room-mismatch checks in the contour room
  status, an authority gate for settings apply availability, a positive
  CANARY control-gate baseline with single-mutation closures, and
  serialization stability goldens. The follow-up review passed. The
  final tree passed 599 local tests, the HACS/package/boundary/Android
  checks, and the staged-version check; the disposable Core smoke was
  skipped because no runtime path changed. The external module still
  serves the registry import and setup/discovery wizards (36f) and the
  shadow evidence and legacy canary route (36g).
- Version 1.9.8 completes roadmap item 36 sub-step 36d. Settings
  application no longer uses the external Climate API: manual contour
  apply, scheduled day/night switching, temporary temperature, and return
  to schedule run the native chain "persist desired contour state → native
  HA observation → native plan → all-or-nothing scope preflight → strict HA
  calls through the single strict executor → bounded (about two seconds)
  observation verification". The pure planner lives in
  `application/climate_application.py` with models in
  `climate_application_models.py`; the trial executor boundary is
  generalized to `ClimateStrictHaCallExecutor`, and the skeleton still
  finds HA service calls only in `switch.py` and `climate_ha_executor.py`.
  Apply and schedule require every active contour room fully managed and
  ready (one blocked room cancels every call); temporary temperature checks
  only its own room. Disabled, shadow, and canary reject before reading
  native state. Persistence order: apply writes nothing, schedule saves
  profiles and the period marker first (a denied transition is not retried
  by the timer), temporary set/clear saves the override change first.
  Receipt v1 is unchanged: confirmed/pending/partial/unavailable with
  bounded reasons; a duplicate request id only re-observes and may promote
  to confirmed. The independent one-minute managed controller stays
  unchanged and may later reapply a divergent plan. The external module
  still serves the Android public snapshot, apply preview, readiness,
  setup wizards, shadow evidence, and the legacy canary `/actions` route
  (sub-steps 36e-36g). The final tree passed 571 local tests; release
  gate, disposable Core checks, and independent review are recorded in
  the final report of the 36d session.
- Version 1.9.7 completes roadmap item 36 sub-step 36c. The whole internal
  climate pipeline (preview, targets, demands, resolutions, equipment,
  stability, policy, isolation, comparison, call translation, trial and
  managed rooms) now reads only the native Home Assistant observation from
  1.9.6; the external Climate API is no longer touched by the internal
  contour. The new `HomeAssistantClimateStateView` boundary exposes bounded
  immutable states with a strict attribute whitelist; a broken state source
  fails the observation closed with no bridge fallback and no cross-system
  fact mixing. Disabled mode still does not observe. Comparison now checks
  the native plan against actual HA state: alignment suppresses redundant
  calls, divergence permits action, incomparability denies. The external
  module still serves the Android public snapshot, settings application,
  readiness, and setup wizards (sub-steps 36d-36f). The final staged tree
  passed 549 local tests, the HACS/package/boundary/Android checks, and
  disposable Home Assistant Core 2026.6.4/2026.7.0. The independent
  read-only review initially stopped the staged tree (FAIL): an absent
  native state view still fell back to the external bridge in the preview
  and shared observation paths, and preview/managed coverage was missing.
  One fix iteration removed the fallback (no state view now yields an
  unavailable observation, never a bridge read), added poison-bridge
  preview/managed tests, and migrated 22 legacy tests to the native
  observation path. The follow-up review passed in OpenCode session
  `ses_07cd1c3ffffeM9Isuzx4UKkMk7`; the suite now has 551 local tests.
- Version 1.9.6 completes roadmap item 36 sub-step 36b. The pure
  `application/climate_ha_observations.py` adapter builds the internal
  observation snapshot directly from Home Assistant states through the
  version-2 registry bindings: room temperature/humidity from passive
  sensor endpoints (with a climate-entity `current_temperature` fallback),
  window from the room binding, mode and observed targets from the local
  contour, device activity from state plus `hvac_action`, AC transitions
  and short-cycle counts from restart protection memory, outdoor
  temperature (also feeding the heat-load rule), presence, and central
  heating from the home bindings. The day period comes from the local
  contour schedule; season stays honestly unknown. Missing, unavailable,
  stale, or non-numeric values stay unknown and never become permissive.
  The adapter consumes an abstract `ClimateHaStateView` and imports no
  Home Assistant code; the HA wrapper arrives with the runtime switch in
  36c. Execution behavior is unchanged. The final staged tree passed 539
  local tests, the HACS/package/boundary/Android checks, and disposable
  Home Assistant Core 2026.6.4/2026.7.0.
- Version 1.9.5 completes roadmap item 36 sub-step 36a. The climate registry
  moves to schema version 2 with HausmanHub's own Home Assistant observation
  bindings: a room may hold an optional window binary sensor, a passive
  sensor may hold one observation endpoint strictly matching its kind, and a
  new home environment block holds optional outdoor-temperature, presence,
  and central-heating entities with strict domain validation. Stored version
  1 registries migrate once to version 2 with every new binding absent, so
  an old configuration never becomes permissive. Execution behavior is
  unchanged: observation and commands still use the external Climate API
  path; sub-steps 36b-36g (native observation adapter, runtime switch, local
  desired-state application, native projections, bridge-independent control
  mode, poisoned-bridge acceptance) are recorded in the roadmap. The final
  staged tree passed 527 local tests, the HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0.
- Version 1.9.4 completes roadmap item 35. Ownership now expands one verified
  room at a time. A strict promotion operation moves one room to HausmanHub
  management only with every guard agreeing: the room is in the contour,
  bridge mode is `canary` or `managed`, the contour is automatic, the
  observation is fresh, the room is ready, and the comparison is aligned
  (verified parity). Every room device must already hold a HausmanHub control
  endpoint; a partially transferred room is denied. Promotion is atomic: the
  registry is saved whole, a storage failure keeps the previous registry and
  yields an honest failure receipt, and re-promotion answers
  already-managed. Managed rooms run on the same one-minute guard chain as
  the trial room: act only on divergence, skip on alignment, deny on
  uncertainty, execute only the strict whitelist with fail-closed order.
  Ownership receipts are redacted (stable room id, bounded status and
  reasons, device counts). The source climate module and Android repository
  remain unchanged. The final staged tree passed 518 local tests, the
  HACS/package/boundary/Android checks, and disposable Home Assistant Core
  2026.6.4/2026.7.0. The same iteration aligned contour binding validation
  with the trial design: CANARY-scoped active devices now count as
  engine-managed alongside MANAGED, so a trial room no longer starts with a
  binding error. Passive temperature and humidity sensors legitimately stay
  observed: they neither block their room's promotion nor need a control
  endpoint.
- Workspace boundary: this thread may change only HausmanHub and its integration
  wrapper. The Android application is developed separately in
  `/home/ivsh/projects/УД-android`; it may be inspected only read-only for
  contract compatibility. Never edit, format, generate files, build, commit,
  push, or otherwise mutate that directory or its repository from this thread.
- The existing climate contour/module is also strictly read-only for this
  thread: never edit its source, Node-RED flows, configuration, repository, or
  live runtime. The current bridge may call only its fixed Climate API. The
  final product must reimplement the proven climate behavior and device
  adapters entirely inside HausmanHub, verify parity without double commands, and
  then remove the external module as an installation requirement.
- Home Assistant baseline: Core 2026.6.4 or newer.
- Version 1.0.0 established the product as a platform of automatic contours.
  Climate is the first contour. The ordinary Russian options flow chooses
  several rooms/devices; old registry/bridge/native-preview and helper-canary
  tools are hidden under advanced settings.
- The current 1.6 climate contour deliberately reuses the existing
  `hausman-climate` algorithm and executor while the public HausmanHub surface is
  stabilized. This is a migration bridge, not the final architecture. Roadmap
  points 21–40 capture the behavior, build the internal engine and strict Home
  Assistant device adapters, compare both implementations, transfer control
  room by room, and finally remove the external API dependency. Private
  registry plus public contour storage already save atomically.
- Public `GET /api/hausman_hub/v1/contours` returns strict
  `hausman-hub-contours` v1 state without source/entity IDs. Automatic status
  requires fresh engine state, auto mode, authority, device availability, and
  matching targets. Version 1.0.0 sends no climate POST and does not sync
  parameters into the engine; mismatches are explicit `attention`. See the
  [1.0.0 contour decision](LLM_WIKI/Manual/2026-07-18-hausmanhub-v1-0-0-universal-contours.md).
- Version 1.1.0 adds the first normal contour-settings execution path while
  keeping the existing `hausman-climate` algorithm and executor. A saved
  automatic contour uses a distinct `managed` bridge mode; legacy `shadow`
  remains strictly no-POST and legacy one-room `canary` remains separate.
  Explicit confirmation can apply only typed room strategy, temperature, and
  automatic mode, in that order. A bounded in-memory idempotency ledger
  reserves the request before the first POST, never resubmits ambiguous or
  duplicate requests, and rereads Climate API state before reporting
  confirmation. Room humidity is declared unsupported for apply because the
  current engine has no shared room-humidity command. Contour contract v2 adds
  observed strategy and apply capability; local tablet preview/apply routes
  expose no private binding or backend payload. See the
  [1.1.0 apply decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-1-0-confirmed-contour-apply.md).
- Version 1.2.0 replaced the shared
  multi-room comfort fields with one short parameters step per selected room.
  Each room stores its own validated temperature, humidity, and strategy using
  the existing contour registry and Android contour v2 shapes, so no persisted
  data migration or contract bump is needed. Editing preselects only a fully
  validated saved contour and uses its saved values even when current engine
  targets differ. Every selected room must have a selected device; exact
  per-room keys prevent incomplete or hidden inputs. The review screen lists
  public room names and their targets. Setup/save remain zero-command; the
  separate confirmed 1.1 apply path is unchanged, including unsupported
  humidity. See the
  [1.2.0 room-parameters decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-2-0-room-parameters.md).
- Version 1.3.0 gave every contour room
  exact `day` and `night` comfort bundles and an approved active profile.
  Existing v1 contour storage is migrated once to storage v2 by copying the
  former targets into both profiles with `day` active, so installation or
  migration changes no effective target and sends no command. The ordinary
  Russian options flow separately configures both profiles, selects one
  profile for all rooms, and then reuses the existing explicit apply preview
  and confirmation. Configuring or selecting a profile only atomically saves
  HausmanHub state; only the apply step may call the existing `hausman-climate`
  executor. Ordinary contour editing updates the active bundle and preserves
  the inactive bundle. Public contour contract v3 exposes active/day/night
  comfort values without private bindings. See the
  [1.3.0 profile decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-3-0-day-night-profiles.md).
- Version 1.4.0 established the first options page and
  ordinary climate workflow use plain Russian labels, with `strings.json` and
  the English-locale fallback intentionally mirroring Russian so a locale
  mismatch cannot produce a half-English UI. The visible sections are
  Climate, Home information, and Diagnostics/maintenance. Contour/device
  internals remain stable but are hidden from the ordinary language.
  An explicitly confirmed local-time schedule can now switch every room
  between the saved day/night profiles. A one-minute HA clock adapter is
  always registered; the runtime performs no bridge I/O unless the schedule
  is enabled, the contour is automatic, the bridge is managed, and the desired
  profile differs. It atomically persists the new active profile before
  reusing the same typed, idempotent contour executor. It never retries on the
  next minute because the selected profile is already persisted. Storage v3
  migrates v1/v2 with a disabled 07:00/23:00 schedule. Tablet contour contract
  v4 exposes only enabled/day/night times. See the
  [1.4.0 schedule decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-4-0-russian-schedule.md).
- Version 1.5.0 is the published HausmanHub release. One room may receive a
  temporary 18–28 °C target in 0.5 °C steps while an automatic schedule is
  armed for the current local-time period. The override is stored separately
  from the saved day/night bundles, persists before the first POST, and is
  applied only through the existing typed `hausman-climate` executor for the
  selected room. It clears on the next day/night transition or through a
  separate confirmed early-return action. Ambiguous command results are never
  automatically reposted. Storage v4 migrates v1–v3 with no override; public
  contour contract v5 and the strict local tablet temporary-temperature route
  expose no private bindings. See the
  [1.5.0 temporary-temperature decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-5-0-temporary-temperature.md).
- The 1.5.0 release candidate passed 289 local tests, isolated Home Assistant
  2026.6.4 and 2026.7.0 checks, and a final read-only Kimi review with no
  significant findings (session `ses_084f948c2ffee4C3vSqj22zKaT`).
- Version 1.6.0 completed the first HausmanHub-only roadmap item. It adds
  `GET /api/hausman_hub/v1/capabilities`, a local-tablet discovery
  response containing only installed HausmanHub features, public paths, and contract
  versions. It is independent of current climate command readiness and exposes
  no home data, private binding, or climate-module address.
- Version 1.6.1 completed the second HausmanHub-only roadmap item. It advances
  `hausman-hub-home` to v5 and embeds the
  public contour projection in the same response as live rooms and devices.
  Both projections use one imported Climate API snapshot; the legacy
  `/contours` route remains available. Android and the climate module are not
  changed. The final staged review passed after fixture reachability was made
  explicit (Kimi session `ses_084b63f0bffeaYv70SAOrV4Jqu`).
- Version 1.6.2 completed the third HausmanHub-only roadmap item. Public home and
  contour contracts v6 carry one immutable Russian `display_names` catalog.
  Private engine room modes and arbitrary device states are normalized to a
  bounded set of HausmanHub codes before projection; unknown external text is never
  echoed to the tablet. The catalog also covers every bounded room-control and
  contour reason, and the schema allow-lists all device capability codes. The
  final read-only Kimi review passed after those completeness checks were added
  (session `ses_0849e5c55ffesSAtzPiPLoPqe2`).
- Version 1.6.3 completed the fourth HausmanHub-only roadmap item. Home contract v7
  gives every registered room an explicit factual `actual` block with current,
  stale, or unavailable data status, temperature, humidity, and normalized
  engine mode. Missing source data stays null/unknown; legacy flat fields remain
  temporarily for Android compatibility. The final read-only Kimi review passed
  (session `ses_084881905ffeftJ53HfmP6RXTu`).
- Version 1.6.4 completed the fifth HausmanHub-only roadmap item. Home contract v8
  separates the imported engine `active_target` from HausmanHub `saved_profiles` for
  day and night. An unconfigured contour keeps all saved profile values null
  instead of copying the current engine target. The final read-only Kimi review
  passed (session `ses_0847a2b90ffe5Z4brgW45Gy2m2`).
- Version 1.6.5 completed the sixth HausmanHub-only roadmap item. Home contract v9
  and contour contract v7 expose the exact next real local schedule transition
  and the exact end of an active temporary temperature. Production projection
  uses Home Assistant local time, and the schedule calculation follows real UTC
  minutes across daylight-saving changes. The final read-only Kimi review
  passed (session `ses_0824c7fa7ffe02CSROzGL3CO5h`).
- Version 1.6.6 completed the seventh HausmanHub-only roadmap item. Home contract v10
  adds `allowed_actions` to every room. Existing `actions` remain the device's
  supported controls, while `allowed_actions` contains only commands executable
  now for that exact room. Runtime and schema both require aggregate
  `commands_enabled` to match whether at least one room has an allowed action.
  Both configured OpenCode review profiles failed before review with token
  refresh `401`; the final Codex audit found no remaining issue after adding the
  strict aggregate-to-room schema relation.
- Version 1.6.7 completed the eighth HausmanHub-only roadmap item. Home contract v11
  adds `action_availability` for every advertised room action. Each entry has an
  exact allowed flag and bounded blocked-reason codes whose Russian labels are
  supplied by the existing `display_names` catalog. The schema requires action,
  permission, and reason lists to stay consistent. OpenCode review again failed
  before reading the change because token refresh returned `401`; the final
  Codex audit found no remaining issue.
- Version 1.6.8 completed the ninth HausmanHub-only roadmap item. Home contract v12
  adds a deterministic JSON-safe integer `state_revision` over all public home
  content except `generated_at`. Equal public content keeps the same revision;
  any visible state, configuration, or permission change produces a new opaque
  value. Clients compare equality only; the value is not monotonic. OpenCode
  review again stopped before reading the change because token refresh returned
  `401`; the final Codex audit found no remaining issue.
- Version 1.6.9 completed the tenth HausmanHub-only roadmap item. A repository-local
  compatibility check decodes the v12 home fixture into the scalar and
  collection types audited read-only in the existing Android `HomeRoom`,
  `HomeDevice`, and `HomeAction` models. It also constructs strict HausmanHub action
  requests, enforces Android `Long` and exact JSON-number limits, device-domain
  mappings, and Russian blocked-reason labels. The check reads and changes only
  HausmanHub files in CI. It proves model-level compatibility, not that the current
  Android application already has a live HausmanHub v12 network decoder. The final
  staged tree passed 314 local tests and both supported Home Assistant Core
  checks. OpenCode stopped before review with token-refresh `401` in session
  `ses_08227e465ffekdVIgv90Up8d7b`; the final Codex audit added exact coverage
  between all HausmanHub device kinds and Android domain mappings and found no
  remaining issue.
- Version 1.7.0 completed the eleventh HausmanHub-only roadmap item. The new strict
  `hausman-hub-climate-rooms` v1 contract projects the union of discovered and
  configured rooms using only stable HausmanHub IDs. It sorts deterministically,
  preserves the configured HausmanHub name, disables all selection for stale data,
  and keeps a configured-but-missing room visible and unselectable. Fixed
  Russian status labels ship in the payload. The contract contains no bridge
  origin, source device ID, entity ID, or command. It is intentionally an
  application contract only in this point; the administrative draft HTTP route
  belongs to roadmap item 14. The final staged tree passed 319 local tests and
  both supported Home Assistant Core checks. OpenCode stopped before review
  with token-refresh `401` in session `ses_0821dbbf9ffe3R4Ym2RCx1eFzu`; the
  final Codex audit added a schema rule forbidding stale per-room status inside
  a current snapshot and found no remaining issue.
- Version 1.7.1 completed the twelfth HausmanHub-only roadmap item. The strict
  `hausman-hub-climate-device-candidates` v1 contract projects discovered and
  configured devices without source IDs, entity IDs, backend commands, or
  bridge details. It carries bounded HausmanHub kind codes with Russian names,
  response-local `candidate_0001` references, and an opaque JSON-safe snapshot
  revision that changes when private candidate bindings change. Freshness,
  current availability, already-configured, unsupported, missing-source, and
  registry-mismatch states fail closed. Configured-but-missing devices remain
  visible. This is still an application contract only; item 14 will expose the
  administrative draft route. The candidate revision ignores read time alone
  but changes with private binding or candidate state. The final staged tree
  passed 326 local tests and both supported Home Assistant Core checks.
  OpenCode stopped before review with token-refresh `401` in session
  `ses_082149289ffetmVMUsPAlvHXps`; the final Codex audit corrected unavailable
  configured-device status and timestamp-only revision churn and found no
  remaining issue.
- Version 1.7.2 completed the thirteenth HausmanHub-only roadmap item. The new strict
  `hausman-hub-climate-room-suggestions` v1 contract links response-local
  candidate references to rooms only through the explicit fresh source room
  relation. It never guesses from device names and never assigns or saves.
  Every suggestion requires confirmation and has fixed Russian confidence and
  reason labels. Stale data removes all room suggestions; missing sources and
  registry mismatches remain suggestion-free; unavailable or unsupported
  devices may explain their detected room but cannot be accepted. The format
  shares the candidate snapshot revision and remains internal until the item
  14 administrative draft route. The final staged tree passed 331 local tests
  and both supported Home Assistant Core checks. OpenCode stopped before review
  with token-refresh `401` in session `ses_0820c85d8ffesZEl7NOXnUq5NF`; the
  final Codex audit strengthened schema relations among status, reason,
  confidence, suggested room, and acceptance and found no remaining issue.
- Version 1.7.3 completed the fourteenth HausmanHub-only roadmap item. One fixed
  local-admin route, `/api/hausman_hub/v1/admin/climate-drafts`, now exposes
  strict setup choices through GET and creates a deterministic unsaved draft
  through POST. The request binds response-local candidate references to an
  exact JSON-safe snapshot revision, rejects changed or stale discovery data,
  validates per-room comfort ranges and detected device kinds, and never
  exposes source or entity IDs. GET and POST perform only a bridge state read:
  they save neither registry nor contours, send no commands, and do not even
  advance in-memory shadow-readiness evidence. The response explicitly keeps
  `save_allowed` false and `validation_required` true for item 15. The final
  staged tree passed 340 local tests, the HACS/package/boundary checks, Android
  model compatibility, and Home Assistant Core 2026.6.4 and 2026.7.0. The
  independent Kimi review could not start because provider session
  `ses_081f59898ffeL2TSbbZKMf8fYg` returned `Unexpected server error`
  (`err_26c09fac`). The final Codex audit added the missing GET surface needed
  to obtain candidate references and prevented setup reads from changing
  shadow evidence; it found no remaining issue.
- Version 1.7.4 completed the fifteenth HausmanHub-only roadmap item. A local-admin
  POST at `/api/hausman_hub/v1/admin/climate-drafts/validate` accepts the exact
  draft response, re-creates it against one fresh discovery snapshot, rejects
  stale candidate revisions and any material draft change, and resolves
  private source bindings only after those checks. Deep validation preserves
  an explicitly selected suggested device kind, requires a controllable device
  in every room, and verifies that imported capabilities can construct the
  existing HausmanHub registry and contour model. Its strict Russian result is either
  `ready` with future `save_allowed`, or `blocked` with bounded issue codes;
  `command_allowed` is always false. Validation performs no persistence,
  command, or shadow-evidence update. Setup bodies have a separate 256 KiB
  bound while ordinary commands remain at 16 KiB. The final staged tree passed
  346 local tests, package/boundary/Android checks, and Home Assistant Core
  2026.6.4 and 2026.7.0. Kimi provider session
  `ses_081e7a57fffegiNPU9QW3CfaTQ` failed before review with server reference
  `err_6718dd9d`. The final Codex audit strengthened blocked-result schema
  consistency and explicit request-size regression coverage and found no
  remaining issue.
- Version 1.7.5 completed the sixteenth HausmanHub-only roadmap item. A local-admin
  POST at `/api/hausman_hub/v1/admin/climate-drafts/save` refreshes discovery,
  deeply revalidates the exact unchanged draft, resolves private device
  bindings only after validation, and builds the existing HausmanHub climate
  registry and `existing_climate_core` contour model under one runtime lock.
  Registry, contour, and shadow-evidence stores use the existing
  rollback-protected setup transaction: a failed later write restores the
  prior working configuration, while rollback failure remains explicitly
  unavailable instead of reporting success. The strict private-id-free receipt
  says `saved`, `commands_sent: false`, and `restart_required: false`. The route
  is local-admin-only, has the separate 256 KiB setup limit, sends no device
  command, and maps a stale snapshot to HTTP 409 without persistence. The final
  staged tree passed 349 local tests, package/boundary/Android checks, and Home
  Assistant Core 2026.6.4 and 2026.7.0. Kimi provider session
  `ses_081d77549ffe5piZVGFmOgJuGd` failed before review with server reference
  `err_4169f40f`. The final Codex audit added direct stale-save and
  blocked-draft regression checks and found no remaining issue.
- Version 1.7.6 completed the seventeenth HausmanHub-only roadmap item. A local-admin
  GET at `/api/hausman_hub/v1/admin/climate-drafts/current` projects the exact
  saved climate contour into a strict private-id-free editor model. It keeps
  per-room day and night profiles, active profile, temporary temperature,
  schedule, mode, and assigned device kinds separate, so transient engine or
  override values cannot replace saved comfort settings. `setup_revision`
  fingerprints the complete stored registry and contour while
  `snapshot_revision` fingerprints current device bindings. Missing, stale,
  unavailable, or mismatched devices remain visible but set
  `editing_allowed: false` with a fixed Russian reason; an absent contour is an
  explicit `not_configured` result. Reading refreshes discovery without
  persistence, commands, or shadow-evidence changes. The final staged tree
  passed 352 local tests, package/boundary/Android checks, and Home Assistant
  Core 2026.6.4 and 2026.7.0. Kimi provider session
  `ses_081c8eff3ffe1X2SVwve701Amw` failed before review with server reference
  `err_9d1c65ec`. The final Codex audit bound every issue code to its exact
  Russian message and correct global/device scope and found no remaining issue.
- The final architecture was clarified on 2026-07-20: HausmanHub must ultimately
  contain the complete currently working climate algorithm. During migration,
  the existing module remains read-only and serves as a behavior oracle through
  its fixed API. After parity, HausmanHub must work from its own selected Home
  Assistant devices and the separate climate module must no longer be required.
  Progress is tracked in the
  [50-item HausmanHub roadmap](LLM_WIKI/Manual/2026-07-19-hausmanhub-50-point-roadmap.md).
- Version 0.4.0 was committed as `2e8cda3` and pushed to `origin/main` after
  its 153 tests, disposable Core 2026.6.4/2026.7.0 checks, and final Kimi
  review passed. This source push did not create a tag, release, HACS
  publication, deployment, or live-home change. The boundary is recorded in
  the [0.4.0 canary note](LLM_WIKI/Manual/2026-07-17-hausmanhub-v0-4-0-input-boolean-canary-control.md).
- Version 0.5.0 implements the first complete climate facade in
  HausmanHub. It adds a versioned logical Device Registry for rooms, ACs, TRVs,
  humidifiers, floor heating, sensors, private endpoint roles, capabilities,
  control owner, and observed/canary/managed scope. Import from the current
  `hausman-climate` v1 state is read-only and never auto-registers a device.
- The Android-facing HausmanHub contract exposes only stable HausmanHub IDs and provides
  local authenticated state/actions routes. Separate local-admin routes expose
  private import candidates and atomically replace the registry. Android never
  receives raw HA entity IDs, Climate API source IDs, Node-RED details, vendor
  transport, or backend command payloads.
- The climate bridge has `disabled`, `shadow`, and one-room `canary` stages.
  It accepts only a literal private HTTP(S) origin and two fixed Climate API
  v1 paths. Shadow translates but never posts. Canary requires a fresh state,
  exact room/binding, current climate-core authority readiness, configured
  capability, climate-core ownership, and canary/managed device scope before
  POST. The current climate-core remains responsible for auto/manual policy,
  cooldown, safety, authority, physical feedback, and actual execution.
- Typed HausmanHub intents now cover room target/mode/minimum/strategy/off and
  device power/temperature/humidity/HVAC/fan contracts for AC, TRV,
  humidifier, and floor-heating kinds. No generic proxy, caller-provided
  service, private source/entity ID, backend type, arbitrary URL, or payload is
  accepted. The architecture and rollout are in
  [the climate guide](docs/climate-control-architecture.md) and the durable
  [0.5.0 decision](LLM_WIKI/Manual/2026-07-17-hausmanhub-v0-5-0-climate-facade.md).
- Version 0.5.0 was committed as `5ac09c5` and pushed to `origin/main` after
  it passed 191 local tests, the HACS/version/repository
  safety checks, and disposable Home Assistant Core 2026.6.4 and 2026.7.0
  lifecycles on Python 3.14.3. The Core check also exercised all four climate
  routes through real loopback HTTP authentication in the disabled rollback
  state. Kimi model `kimi-for-coding/k2p7` completed the final read-only staged
  review in session `ses_09070e1c2ffeeTgDvZ3A3kiLUu` with no substantial
  findings. The verified `cc04029` tree was published as the non-prerelease
  latest GitHub Release `v0.5.0`; its tag resolves to that exact commit and
  both GitHub source archives were reachable. Publication did not deploy HausmanHub
  to a live home, enable either canary, or modify the Android repository.
- Version 0.5.1 implements the first operator-ready HausmanHub climate workflow.
  Home Assistant options now contain a guided local-admin draft for rooms and
  typed devices, a separate preview/reconciliation step, and explicit atomic
  save confirmation. An advanced JSON editor remains optional. Eight JSON
  Schema v1 files ship inside the integration for the Android and admin
  contracts.
- Android climate actions in 0.5.1 require a bounded public `request_id` and
  return a bounded versioned receipt with an opaque HausmanHub `operation_id`.
  Identical retries return the same receipt without another GET or POST;
  conflicting reuse is rejected. Canary HTTP acceptance is only `pending`,
  an explicit negative backend answer is terminal `rejected`, and transport
  ambiguity remains unavailable. HTTP acceptance is never physical success.
  Only an observable later state can become
  `confirmed`; a room cannot have two pending HausmanHub canary submissions.
- The disposable Core check now includes a temporary loopback Climate API and
  real Home Assistant owner/tablet authentication. It previews and saves a
  synthetic registry, reads the Android home contract, retries a shadow
  action, queries its receipt, and asserts a measured zero command POST count
  before restoring `disabled` and removing only the temporary registry.
- HausmanHub 0.5.1 was published from `494ae94` as the non-prerelease GitHub Release
  `v0.5.1` after 204 local tests, disposable Core 2026.6.4/2026.7.0 checks,
  successful GitHub Actions, and a final Kimi review with no findings. HACS
  installed it on the live Core 2026.6.4 home and the owner completed the
  required restart. Post-restart evidence showed installed/latest `v0.5.1`,
  the new operation contract v1 loaded, an unknown receipt fully redacted, and
  the admin readiness route closed to the non-admin verification account. The
  live climate bridge remained `disabled`, with no target or canary room; the
  fail-closed action check returned unavailable before execution. No physical
  climate canary or device command was run.
- Version 0.5.2 persists a
  redacted rolling 24-hour evidence window with a five-minute sample interval,
  bounded matched/missing/moved/stale observations and rejected/translated
  intents. The window stores only timestamps, public HausmanHub room IDs, and
  approved action labels; it is bound to the exact registry fingerprint and
  resets on any registry change. Private origin, source/entity identifiers,
  payloads, backend responses, and tokens never enter its Store or API shape.
- A local administrator can evaluate one candidate room through the guided
  options flow or the versioned climate-shadow-evidence route. `ready` requires
  fresh exact registered bindings, current climate-core authority, three
  spaced matching samples, successful shadow translation of room target and
  room off, and no candidate anomaly. A configured canary remains fail-closed
  before POST until this persisted result is ready, and 0.5.2 limits the first
  executable climate canary scope to those two room actions. This code does
  not authorize or activate a live physical canary; live deployment must keep
  the climate bridge `disabled`. The durable decision is in
  [the 0.5.2 evidence note](LLM_WIKI/Manual/2026-07-17-hausmanhub-v0-5-2-shadow-evidence.md).
- HausmanHub 0.5.2 was committed as `f3ec8ad`, passed 212 local tests, disposable
  Core 2026.6.4/2026.7.0 checks, two final Kimi reviews, GitHub Actions, and was
  published as the non-prerelease release `v0.5.2`. HACS installed it on the
  live Core 2026.6.4 home. After the owner restarted Core, installed/latest
  both reported `v0.5.2`, the new shadow-evidence admin route was present and
  correctly forbidden to the non-admin verification account, and climate
  home/action remained unavailable because the bridge was still `disabled`.
  No physical command or canary was attempted.
- Version 0.5.3 added the HausmanHub-only operator-import workflow. The options
  wizard obtains fresh Climate API candidates read-only and exposes only
  ephemeral `candidate_NNN` selector values plus device/room labels. The
  private source ID is neither displayed nor accepted from the form. The
  operator supplies a public HausmanHub ID and chooses a control entity with Home
  Assistant's native selector; HausmanHub re-reads the snapshot, rejects drift, and
  infers only capabilities supported by the candidate's typed command list.
  The selected room/device remain in an unsaved draft until the existing
  preview and separate atomic confirmation. It never auto-imports another
  candidate, deletes a registry record, or sends a Climate API command POST.
  The package passed 217 local tests plus release/file-safety checks
  and the full options-flow lifecycle on disposable Core 2026.6.4 and
  2026.7.0 with an exact two-device registry and zero command POSTs. Kimi model
  `kimi-for-coding/k2p7` completed the final read-only staged review in session
  `ses_08e986dbaffe6gCgi4wPgxStqP` with no substantial findings. Commit
  `eb05bce` was pushed and published as the non-prerelease latest release
  `v0.5.3` after successful GitHub Actions. HACS installed it on the live
  Core 2026.6.4 home; after the owner restarted Core, installed/latest both
  reported `v0.5.3` and the new candidate-import translation keys were loaded.
  Climate home/action still returned unavailable because the live bridge
  remained `disabled`; no physical command or canary was attempted.
  A non-activating supervised one-room checklist is documented in
  [the rollout checklist](docs/climate-canary-rollout-checklist.md).
- Version 0.5.4 adds the HausmanHub-only one-room preflight. Its guided options
  flow selects one room strictly from the saved registry and combines exact
  reconciliation, redacted shadow evidence, the fixed `set_room_target` plus
  `turn_room_off` scope, per-room pending state, and disabled rollback
  readiness. Only complete evidence in `shadow` can produce
  `ready_for_authorization`; the result always keeps
  `activation.allowed=false` and requires separate owner authorization. It
  performs no climate command POST, does not enable canary, and does not save
  options or registry. The prepared package passed 224 local tests, the full
  release/file-safety checks, and disposable Core 2026.6.4/2026.7.0. Kimi
  model `kimi-for-coding/k2p7` completed the final read-only staged review in
  session `ses_08ca230b5ffe4LBnH7j2hMTROH` with PASS and no substantial
  findings. Commit `2435c7f` was pushed and published as the latest stable
  release `v0.5.4` after successful GitHub Actions. HACS installed it on the
  live Core 2026.6.4 home; after the owner restart, installed/latest both
  reported `v0.5.4`, the new preflight steps and fields were loaded, and
  climate home/action remained unavailable because the bridge stayed
  `disabled`. No physical command or canary was attempted. The
  implementation decision is recorded in
  [the 0.5.4 preflight note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-4-canary-preflight.md).
- Version 0.5.5 exposed the canonical
  saved-room preflight through one local-admin-only POST route and adds
  explicit checked/generated/valid-until freshness timestamps. Expired state
  blocks readiness independently of saved evidence. Two installed JSON
  Schemas define the exact query and response; activation remains structurally
  false, the tablet role is forbidden, and no options, registry, canary mode,
  or command POST can be changed. The final staged package passed 226 local
  tests, the full release/file-safety checks, and disposable Core
  2026.6.4/2026.7.0. Kimi model `kimi-for-coding/k2p7` completed the read-only
  staged review in session `ses_08b9a95d1ffe9AVm46wQzzPqZQ` with PASS and no
  substantial findings. Commit `23aa3f8` was pushed and published as the
  latest stable release `v0.5.5` after successful GitHub Actions. HACS
  installed it on the live Core 2026.6.4 home; after the owner restart,
  installed/latest both reported `v0.5.5`, the new admin preflight route was
  present and forbidden to the non-admin verification account, and climate
  home/action remained unavailable because the bridge stayed `disabled`. No
  physical command or canary was attempted. The decision is recorded in
  [the 0.5.5 contract note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-5-preflight-admin-contract.md).
- Version 0.5.6 published the tablet home contract v2. It
  explicitly v2 and adds one public `control` result per room: whether commands
  are enabled, the evidence-qualified target/off actions, and a closed set of
  normalized blocked reasons. It derives availability from the same canary,
  freshness, binding, authority, device availability, evidence, and pending
  gates used by runtime. The old home v1 schema remains installed; a new strict
  v2 schema and synthetic fixture define the added shape. Command planning now
  also rejects a device marked unavailable. Android code, live registry,
  bridge activation, and physical commands remain out of scope. The final
  staged package passed 229 local tests, release/package/file-safety checks,
  and disposable Core 2026.6.4/2026.7.0. Kimi
  `kimi-for-coding/k2p7` session `ses_08b7a860affeOVomxNvxlvfWbi` completed
  the staged review and follow-up with PASS and no substantial findings.
  Commit `b62f1d7` was pushed, passed GitHub Actions, and was published as the
  latest stable release `v0.5.6`. HACS installed it on the live home while the
  climate bridge and action path stayed closed; the owner restart remained
  pending when the next development slice began. The decision is recorded in
  [the 0.5.6 room-control note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-6-android-room-control.md).
- Version 0.5.7 replaced mixed Russian
  and internal English operator text with plain Russian names, descriptions,
  errors, statuses, reasons, actions, and room names. Fixed selectors now pass
  string values plus translation keys instead of explicit English labels that
  could override frontend translations. Unknown result codes stay hidden.
  The repository README, GitHub workflow labels, and public GitHub About
  description are Russian. This slice changed no climate contract or authority
  and deployed with the bridge disabled. It passed 231 local tests,
  disposable Core 2026.6.4/2026.7.0, final Kimi review, and GitHub Actions.
  Commit `979c4c5` was published as latest stable release `v0.5.7`; HACS
  installed it on the live home without configuring a registry or enabling
  the bridge. See the
  [0.5.7 Russian interface note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-7-russian-interface.md).
- Version 0.5.8 was released on 2026-07-18. Android home contract v3
  keeps the v2 room action and blocked-reason shape and adds `action_inputs`.
  The target-temperature input is numeric and required, with an exact public
  range of 18–28 °C and a 0.5 °C step. The command validator and public
  projection use the same constants; an unsupported target action does not
  advertise input metadata. Strict v3 schema/fixture are added while v1/v2
  remain packaged. This is contract preparation only: it does not change the
  Android repository, live registry, bridge state, or physical authority. The
  prepared package passed 232 local tests, release/package/file-safety checks,
  and disposable Core 2026.6.4/2026.7.0 with zero climate command POSTs. Kimi
  model `kimi-for-coding/k2p7` completed the final read-only staged review in
  session `ses_08b312059ffedrMEVGxBLevcNI` with PASS and no substantial
  findings. See
  the [0.5.8 input-contract note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-8-action-inputs.md).
- Version 0.5.9 was published and installed through HACS. Android home contract v4
  adds `action_presentations` for the two initial room actions. Each advertised
  action has fixed Russian title and description; the target-temperature field
  has its own title and explanation; room off requires user confirmation while
  target adjustment does not. Presentation keys must exactly follow advertised
  actions, and strict v4 schema/fixture enforce the copy and confirmation rule.
  Earlier v1-v3 home schemas remain packaged. This is still client-contract
  preparation only: it changes no Android repository, live registry, bridge
  state, or physical authority. All 232 local tests, the release/package/file
  safety checks, and disposable Core 2026.6.4/2026.7.0 passed with measured
  zero climate command POSTs. Kimi model `kimi-for-coding/k2p7` completed the
  final read-only staged review in session `ses_08a6b28e4ffeLp6u9BYpGw1F4O`
  with PASS and no substantial findings. See the
  [0.5.9 action presentation note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-9-action-presentations.md).
- Version 0.5.10 was published and installed through HACS. The single nine-field
  options form is replaced by a one-choice settings menu with four separate
  areas: rooms/devices, climate-controller connection, aggregate information,
  and a clearly non-climate service switch test. The connection flow asks for
  its mode first, then an address only for check/trial modes, and a room only
  for one-room trial control. Saving one area preserves the other validated
  areas; choosing disabled still removes the private address and room. Russian
  labels and repository instructions describe the resulting user path in
  ordinary language. This changes no Android contract or runtime authority,
  keeps the live bridge disabled, and sends no physical commands. The 233
  local tests and disposable Core 2026.6.4/2026.7.0 checks pass. Kimi model
  `kimi-for-coding/k2p7` completed the final staged read-only review in session
  `ses_08a36c03bffeXybMbvHK4IPj8g` with PASS and no substantial findings.
  Commit, publication, and HACS installation completed; the owner has not yet
  confirmed the post-install Home Assistant restart. See the
  [0.5.10 settings-menu note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-10-simple-settings-menu.md).
- Version 0.6.0 started moving climate
  policy into HausmanHub. A validated one-room policy stores temperature and humidity
  targets. A pure decision engine uses fresh transitional Climate API state,
  fixed ±0.5 °C/±5% deadbands, registered device kinds, and availability to
  report heating, cooling, humidifying, hold, stale, or unavailable. A fifth
  Russian options area previews that decision and requires separate target
  confirmation. Execution is structurally `preview_only` with commands always
  false; a disabled bridge performs no state I/O. Existing installations
  default to the disabled native policy. Existing climate-core remains the
  transitional observation/execution adapter while native HausmanHub observation,
  planning, cooldown, manual override, and later separately authorized
  execution are developed. All 244 local tests, the release checks, and the
  disposable Core 2026.6.4/2026.7.0 checks pass. The staged implementation and
  the final fail-closed delta received Kimi PASS reviews with no substantial
  findings. Commit `a765cc7` was pushed, release `v0.6.0` was published, and
  HACS reports installed/latest `v0.6.0`; the owner still needs to restart Home
  Assistant before using the new fifth settings area. See the
  [0.6.0 native preview note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-6-0-native-climate-preview.md).
- The owner clarified the end product after 0.6.0: HausmanHub is a platform of
  autonomous contours, not a technical climate bridge or a collection of
  manual device controls. A user adds a contour, assigns rooms, observations,
  and actuator devices, sets comfort parameters and safety limits, then HausmanHub
  continuously owns its decisions and operation. Climate is the first contour;
  later contours reuse a shared device registry, lifecycle, status, override,
  and conflict model. Transitional climate-core, shadow, canary, private
  bindings, and migration details must move out of the ordinary user path.
  The 0.6.0 one-room preview is an internal foundation, not the target UX. See
  the [contour-platform product direction](LLM_WIKI/Manual/2026-07-18-hausmanhub-contour-platform-direction.md).
- Further HausmanHub-only development is tracked in the
  [post-0.5 roadmap](LLM_WIKI/Manual/2026-07-17-hausmanhub-post-v0-5-0-roadmap.md):
  the operator registry, formal Android contract, measurable shadow, command
  receipts, confirmation, and non-activating one-room preflight now exist.
  Physical canary execution remains a separate explicitly authorized phase.
- A public `custom_components/hausman_hub/` observation foundation with the
  local 0.4.0 helper-canary addition is present. It may be added manually as an
  HACS custom repository; it is not in the public HACS catalog.
- The skeleton contains a local square `brand/icon.png`, so Home Assistant can
  show its original icon without relying on an external brand asset.
- A Russian safe-check guide is available at
  `docs/home-assistant-safe-check.md`. It guides HACS refresh, installation,
  visual confirmation, the local aggregate diagnostic summary, and the
  isolated helper-canary check; it still
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
  change. It also reserves one HausmanHub-like sensor name only in that temporary
  configuration, then proves that a new HausmanHub setup keeps all nine count
  sensors, does not overwrite the occupied name, and leaves the other eight
  protected names unchanged. After HausmanHub is removed, that temporary external
  record must still be unchanged. The same isolated check then creates and
  removes HausmanHub once more, requiring the same nine sensors and the unchanged
  external record again. It requires no HausmanHub device record and requires each of
  the nine HausmanHub sensors to remain unattached to a device. It also requires Home
  Assistant to refuse a second HausmanHub setup while keeping the existing setup
  unchanged and limited to its nine sensors. After each HausmanHub removal, an
  authenticated temporary exact read-only user must receive only an unavailable
  response from the retained local summary route, with none of the nine counts.
  It also requires every removed HausmanHub count state to be absent from the
  temporary state machine. After the final removal, a third empty Home
  Assistant instance uses the same temporary configuration and must not restore
  any HausmanHub setup, object, state, runtime data, or local route; the unrelated
  temporary external record must still be unchanged. Only after that absence
  proof, the third instance creates a fresh `read-only` HausmanHub setup with a new
  entry identifier, exactly nine count sensors, unchanged safe diagnostics,
  unchanged external record, and a newly authenticated local route. That fresh
  setup is removed too, its route immediately fails closed, and a fourth empty
  Home Assistant instance must again contain no HausmanHub data while preserving the
  external record.
- Version 0.3.5 clears the current state values of only the nine HausmanHub count
  sensors after a successful HausmanHub unload. A deactivation therefore no longer
  leaves old aggregate values in memory; reactivation restores only the same
  nine counts. It does not alter a device, service, external state, or
  home-control boundary.
- Version 0.3.6 keeps the options screen safe even when old saved settings are
  broken: it shows the neutral `read-only` default instead of an unapproved
  saved mode, without repairing, saving, or otherwise changing that setting.
  It does not add a device, service, home-data path, or home-control boundary.
- Version 0.3.7 fails closed if a damaged saved configuration contains more
  than one HausmanHub entry, including a user-deactivated one. If another saved
  entry appears while HausmanHub is already working, it first closes the active
  summary and ordinarily unloads the existing HausmanHub display before it clears
  only the captured HausmanHub entries' stale count records. The retained local
  route then returns only unavailable, never counts. Both saved records remain
  for manual repair; HausmanHub never chooses, deletes, or activates one
  automatically. A disposable Core lifecycle covers both an enabled pair and
  an enabled plus user-deactivated pair, before and after restart: after
  removal, a remaining enabled entry requires an explicit reload, while a
  remaining disabled entry requires explicit activation before it can recreate
  exactly nine safe counts. If every saved duplicate is already
  user-deactivated, Core does not start HausmanHub at all, so no count state or page
  exists; its disabled registry rows remain until the owner repairs the saved
  pair.
- Version 0.3.8 closes diagnostics on the same boundary. It returns only the
  fixed unavailable status, without calling the local home-summary reader,
  unless exactly one saved HausmanHub entry is currently loaded and safely
  configured. The isolated Core check covers ordinary unload, user
  deactivation before and after restart, removal through a stale object, and
  both malformed duplicate pairs. It patches the temporary diagnostics reader
  to fail if a closed report attempts to observe the home.
- Versions 0.3.8 and 0.3.9 keep diagnostics and the local summary page closed
  if they ever encounter a saved setting that is unsafe. Those defensive
  boundaries remain even though version 0.3.13 now closes the whole HausmanHub
  display immediately after such a saved change.
- Version 0.3.10 also requires the authenticated local page to find exactly
  one saved HausmanHub entry that Home Assistant still reports as loaded. A stale
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
  change reloads only the same HausmanHub entry and takes effect immediately. An
  unsafe saved main setting or mode choice automatically unloads that HausmanHub
  display, clears its nine count states and its HausmanHub-only registry records, and
  rejects setup before any home-summary reader can run. The disposable Core
  check covers all five unsafe main-setting variants and both unsafe
  mode-choice variants, verifies the closed diagnostics and local page, and
  records exactly one reload of the same HausmanHub entry for a normal safe mode
  change. Before each unsafe save, it replaces the sensor, diagnostics, and
  local-page home readers with a failure, so any read during the automatic
  closing interval fails the Core check. A saved entry that failed setup
  remains available for manual repair; because no running HausmanHub remains to
  listen, its owner then explicitly reloads HausmanHub after correcting it.
- The disposable Core lifecycle now changes one safe HausmanHub setting twice:
  `read-only` to `shadow` and back to `read-only`. Each save must reload only
  that one HausmanHub entry exactly once, retain exactly nine aggregate sensors and
  one authenticated GET-only local page, and preserve blocked direct
  execution. Every later stop, reactivation, and restart assertion expects the
  final `read-only` choice. Kimi found no remaining issue in the final review;
  see the [safe mode cycle review
  note](LLM_WIKI/Manual/2026-07-16-kimi-safe-mode-cycle-review.md).
- The disposable Core lifecycle also saves `shadow` while HausmanHub is ordinarily
  stopped but still user-enabled. That save must neither reload HausmanHub nor read
  a home summary, and its nine values, diagnostics, and local page stay
  closed. Only an explicit start restores the same nine sensors and safe
  `shadow` diagnostics. Kimi found no issue; see the [stopped safe-options
  review note](LLM_WIKI/Manual/2026-07-16-kimi-stopped-safe-options-review.md).
- The same disposable lifecycle also saves `read-only` while HausmanHub is
  deliberately disabled by its user. It remains disabled and not loaded: no
  home summary is read, no reload occurs, and its nine values, diagnostics,
  and local page stay closed. Only the user's explicit activation restores the
  same nine sensors with the saved `read-only` mode. Kimi found no issue; see
  the [user-deactivated safe-options review
  note](LLM_WIKI/Manual/2026-07-16-kimi-user-deactivated-safe-options-review.md).
- After a full temporary Home Assistant restart, the same user-disabled HausmanHub
  setup may also save `shadow` without starting itself. It still has no runtime
  data, page, or count values, and it cannot read a home summary or reload
  HausmanHub. Only the user's explicit activation restores the same nine sensors in
  the newly saved `shadow` mode. Kimi found no issue; see the [disabled
  restart safe-options review
  note](LLM_WIKI/Manual/2026-07-16-kimi-disabled-restart-safe-options-review.md).
- A separate disposable check now gives a user-disabled HausmanHub setup a deliberately
  unsafe saved `proxy` option and then attempts explicit user activation. Home
  Assistant rejects the activation, leaves HausmanHub closed with a setup error, and
  keeps direct execution blocked. The broken option remains only for manual
  repair; no home summary is read and no count values, diagnostics, or local
  page become available. The check then removes the temporary setup and proves
  it stays absent after an empty restart. Kimi found no issue; see the [unsafe
  user-activation review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-user-activation-review.md).
- After that rejected proxy-mode activation, a separate disposable repair
  restores the exact safe options. The correction cannot read the home or
  start HausmanHub by itself; only one explicit reload returns the same nine counts,
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
  rejected before any home read; HausmanHub stays in a setup-error state with no
  counts, diagnostics, local page, service, device, or execution surface. The
  deliberately bad data remains only for manual repair. Kimi found no issue;
  see the [unsafe direct-execution activation review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-activation-review.md).
- A user-disabled HausmanHub entry whose main data lacks the required execution
  block follows the same safe manual-repair path. It cannot start or read the
  home during correction; one explicit reload restores the exact safe data,
  same nine counts, and direct-execution block. Kimi found no issue; see the
  [unsafe missing-execution-block repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-missing-execution-block-repair-review.md).
- A user-disabled HausmanHub entry whose main data lacks the required safe mode also
  remains closed. Safe options cannot fill the missing main value; only a
  manual exact repair followed by one explicit reload restores the same nine
  counts. Kimi found no issue; see the [unsafe missing-mode repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-missing-mode-repair-review.md).
- A user-disabled HausmanHub entry whose main data has an unknown extra field also
  remains closed. The entry needs a manual exact repair and one explicit
  reload before the same nine counts can return. Kimi found no issue; see the
  [unsafe extra-field data repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-extra-field-data-repair-review.md).
- A user-disabled HausmanHub entry whose main data asks for prohibited proxy mode
  also remains closed. It can return only after a manual exact repair and one
  explicit reload, without enabling proxy. Kimi found no issue; see the
  [unsafe proxy-data repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-proxy-data-repair-review.md).
- A user-disabled HausmanHub entry whose main data attempts to unblock direct
  execution remains closed even without an intervening Home Assistant restart.
  Manual exact repair and one explicit reload restore only the same nine
  counts with direct execution still blocked. Kimi found no issue; see the
  [unsafe direct-execution repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repair-review.md).
- A user-disabled HausmanHub entry with both an unblocked direct-execution marker
  and a prohibited proxy option remains closed after only one part is repaired.
  It cannot reload or read the home until the remaining part is repaired and
  the owner explicitly reloads HausmanHub. Repeated partial recovery is explicitly
  rejected. Kimi found no issue after an independent review found and closed
  that edge case; see the [unsafe partial-repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-partial-repair-review.md).
- The disposable local-page check also confirms that an existing temporary
  local token loses access immediately when its user changes from Home
  Assistant's exact read-only group to the ordinary group. That request must
  return only an access refusal, without any of the nine counts or a home
  summary read. Kimi found no issue; see the [local access-revocation review
  note](LLM_WIKI/Manual/2026-07-16-kimi-local-access-revocation-review.md).
- Version 0.3.14 tells browsers not to store every JSON response generated by
  HausmanHub's local nine-count page: the allowed summary, HausmanHub's access refusal,
  and HausmanHub's unavailable response. It deliberately does not alter `401`,
  `405`, stopping, or other responses created by Home Assistant outside that
  page. Empty Core 2026.6.4 and 2026.7.0 checks confirm the header on the
  approved and closed paths. Kimi found no issue; see the [local no-store
  review note](LLM_WIKI/Manual/2026-07-16-kimi-local-summary-no-store-review.md).
- On 2026-07-17 the owner explicitly authorized a push. The accumulated
  0.3.15–0.3.18 work was committed as `a032303` and pushed to `origin/main`.
  This was a source push only: no tag, GitHub Release, HACS release
  publication, deployment, or live-home change was performed.
- Work included in version 0.3.18 closes the unspecified local
  origins 0.0.0.0, ::, and IPv4-mapped ::ffff:0.0.0.0 before any nine-count
  read. The same nine approved rows now have only fixed ordinary visual icons;
  the disposable Core check proves the icon for each row without adding data or
  an action. It also proves in disposable Core 2026.6.4 and 2026.7.0 that the
  guarded page accepts only GET: HEAD, POST, PUT, PATCH, DELETE, and TRACE
  return 405; CONNECT does not reach the route and returns 404; OPTIONS returns
  Home Assistant's safe 403 before any home read. Its one fixed address has no
  alternate URL: even the same path with a trailing slash or added query data
  is a closed 404 before a home read and without count names. The real route
  registration must contain GET plus only that safe, closed Home Assistant
  OPTIONS response. The local Core check also requires every rejected method
  response, guest response, and administrator response to omit all nine count
  names. The combined working tree passed 139 local tests and both disposable
  Core checks. The later mixed-diff Kimi review cycle is recorded with version
  0.3.16 below.
- Work included in version 0.3.18 adds only the exact boolean
  `local_summary_enabled` option. It lets the owner close or restore the
  already-approved optional local nine-count page without adding a URL, data,
  command, service, device, proxy, or execution right. With the page closed,
  the existing nine HausmanHub count rows and fixed diagnostics intentionally remain
  available and may refresh the same approved aggregates; a request to the
  closed old page itself fails before it can read them. After a full temporary
  Home Assistant restart while closed, neither HausmanHub page runtime data nor its
  route is registered. Strings, numbers, and other truth-like values are
  rejected. The disposable lifecycle now also changes this boolean while HausmanHub
  is ordinarily stopped, user-disabled, and user-disabled after a restart. Each
  save must leave HausmanHub `NOT_LOADED`, record no reload, and fail immediately if
  any HausmanHub home-summary reader runs. Only the following explicit setup or user
  activation may apply the saved page choice; the after-restart case performs a
  real `True` to `False` change and then keeps the page runtime and route absent
  through activation, ordinary unload, another restart, and removal. The final
  local diff passed 139 fast tests, the complete local release check, and
  disposable Core 2026.6.4 and 2026.7.0 checks. A first temporary fallback
  review found two test-only weaknesses: broad source-string assertions and no
  after-restart boolean change. Both were corrected, and the final independent
  OpenCode fallback review found no remaining issue; see the [inactive local
  page options review](LLM_WIKI/Manual/2026-07-17-opencode-inactive-local-page-options-review.md).
  After the provider quota renewed, Kimi reviewed the complete mixed diff and
  raised one potential frontend-serialization risk for the strict boolean
  selector. Both supported Core versions already serialize the inherited type
  as the native `boolean`; the contract is now explicit, the unit adapter test
  guards it, and the disposable Core harness checks the real serialized form.
  Both Core checks, all 139 fast tests, and the complete local release check
  passed again. The Kimi follow-up found no remaining issue; see the [0.3.15
  and 0.3.16 Kimi review cycle](LLM_WIKI/Manual/2026-07-17-kimi-v0-3-15-v0-3-16-review.md).
  These local reviews do not themselves authorize a commit, push, release,
  deployment, or publication.
- Work included in version 0.3.18 adds only one fixed refresh choice
  for the same nine diagnostic count sensors: the established `5m` default or
  the slower `15m` and `30m` choices. Exact validation rejects faster,
  arbitrary, numeric, and missing submitted values. Old entries whose options
  do not contain the new field still use `5m`; saved entry data is unchanged.
  The one coordinator shared by all nine rows receives the selected interval.
  No new count, data, entity, route, service, device, command, proxy, execution
  path, or authority is added, and the optional authenticated local GET page
  remains immediate per request. The disposable lifecycle covers active
  changes, a real legacy empty-options restart, ordinary unload/restart,
  stopped and user-disabled saves without reload or home reads, and later
  explicit activation. Fast tests, the complete local release check, and Core
  2026.6.4/2026.7.0 results are recorded with the [0.3.17 Kimi review
  cycle](LLM_WIKI/Manual/2026-07-17-kimi-v0-3-17-summary-interval-review.md).
  No review authorizes a commit, push, release, deployment, publication, or
  live-home change.
- Version 0.3.18 adds only the effective validated
  HausmanHub settings to the existing redacted diagnostics `entry_summary`: safe
  mode, the optional local-page boolean, and the exact `5m`, `15m`, or `30m`
  nine-count refresh choice. It never copies raw entry data or options. Legacy
  empty options report the safe enabled-page and `5m` defaults. Unsafe,
  inactive, removed, and ambiguous setups still return only the fixed
  unavailable response before any home-summary read. No count, home datum,
  entity, route, service, device, command, proxy, execution path, automatic
  repair, or authority is added. All 144 fast tests, the complete local
  release check, and disposable Core 2026.6.4/2026.7.0 checks passed. The
  implementation boundary and verification record are in the [0.3.18 safe
  settings diagnostics note](LLM_WIKI/Manual/2026-07-17-hausmanhub-v0-3-18-safe-settings-diagnostics.md).
  A bounded Kimi `k2p7` review of the 0.3.18 delta returned `NO FINDINGS` after
  its completed child session was resumed with the Kimi model explicitly
  pinned. The review itself did not authorize a commit, push, release,
  deployment, publication, or live-home change; the later source push was
  explicitly authorized by the owner.
- The same accumulated version now accepts the local nine-count page only from
  loopback, RFC 1918 IPv4, unique-local IPv6, or an IPv4-mapped form of the
  same approved IPv4 range. Test, link-local, carrier-grade, public, and other
  special addresses fail closed before the summary reader runs. The local fast
  and disposable Core checks cover both exact range boundaries and those
  refusals without using a live home.
- The same local page now also fails closed when its approved nine-count reader
  unexpectedly raises: it returns only the fixed unavailable response, with no
  partial count or error detail. Fast and disposable Core checks use a failing
  temporary reader to prove this without accessing a live home.
- That unsafe direct-execution activation check also has a separate full
  temporary restart between saving the bad data and the user's activation
  attempt. The saved setup remains user-disabled and unloaded with no runtime
  data or local page after the restart; activation is still rejected and the
  damaged data stays for manual repair. Kimi found no issue; see the [unsafe
  direct-execution restart review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-restart-review.md).
- After that rejected activation, a separate temporary recovery check restores
  the exact original safe data and explicitly reloads HausmanHub. It returns only
  the same nine safe counts, fixed diagnostics, and guarded local page with
  direct execution blocked; it creates no service or device. The saved repair
  itself cannot read the home or start HausmanHub before the explicit reload. Kimi
  found no issue; see the [unsafe direct-execution recovery review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-recovery-review.md).
- The same disposable recovery then deliberately receives the unsafe
  direct-execution marker once more. The restored saved-setting guard closes
  HausmanHub again before any home read: it clears all nine counts, diagnostics, and
  the local page, while retaining the bad saved value for a future manual
  repair. Kimi found no issue; see the [unsafe direct-execution repeat-closure
  review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-closure-review.md).
- A second exact safe manual repair after that repeat closure also remains
  closed until one separate explicit reload. It cannot read the home or
  restart HausmanHub while the saved value is being corrected; the explicit reload
  restores only the same nine counts and safe display. Kimi found no issue;
  see the [unsafe direct-execution repeat-repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-repair-review.md).
- A full empty restart after that second repair preserves only the exact safe
  HausmanHub entry and its same nine counts, fixed diagnostics, and guarded page.
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
  writes nor expands HausmanHub's read-only/shadow boundary. See the [damaged
  options-screen review
  note](LLM_WIKI/Manual/2026-07-15-kimi-damaged-options-screen-review.md).
- Kimi independently reviewed the final live and restart duplicate-entry
  closure with no findings. See the [live duplicate fail-closed review
  note](LLM_WIKI/Manual/2026-07-15-kimi-live-duplicate-fail-closed-review.md).
- The local HausmanHub adapter check also covers a failed ordinary unload with one
  saved HausmanHub setup. In that case it keeps the current safe display intact
  rather than partly clearing its values or local page while Home Assistant
  still has HausmanHub loaded. This is separate from the damaged multi-entry case,
  which must close the display.
- The disposable Core lifecycle separately unloads and starts one safe,
  still-user-enabled HausmanHub setup. In the gap, its saved setup and nine enabled
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
  restart, the same lifecycle tries to add HausmanHub again. Home Assistant must
  refuse the duplicate, retain exactly one still-enabled saved setup and its
  nine unloaded count records, and keep values and the guarded page closed.
  It creates no extra sensor, device, service, or control path.
- Both HausmanHub setup forms now have an isolated input-boundary check: even if a
  form receives invented extra fields beside a safe mode, it persists only the
  fixed approved data shape. This is local test coverage only and adds no
  runtime authority.
- Before its first temporary restart, the same isolated lifecycle check also
  uses Home Assistant's ordinary user deactivation and reactivation path. While
  deactivated, the saved HausmanHub setup is not loaded, its nine registry entries
  are marked disabled by that setup, their temporary state values are absent,
  and the guarded local page returns only an unavailable response with no count
  keys. Reactivation must restore the same nine enabled count sensors, safe
  diagnostics, and authenticated GET-only page, still with no device, service,
  proxy, or execution capability.
- One later temporary reinstallation is deliberately deactivated, persisted
  through an empty restart, and then removed. Its nine HausmanHub registry records,
  temporary states, and guarded local page must stay cleared through the
  following empty restart, while the unrelated temporary external record is
  preserved.
- The first safe setup is also deactivated immediately before a temporary
  restart that replaces only the temporary HausmanHub copy. It must stay disabled and
  not restore runtime data, count states, or the guarded page on its own.
  Explicit reactivation must restore only its existing nine safe count sensors,
  diagnostics, and authenticated GET-only page.
- While that saved setup remains user-deactivated after the temporary restart,
  the lifecycle tries to add HausmanHub again. Home Assistant must refuse the
  duplicate, retain exactly one disabled saved setup and its nine disabled
  records, and keep runtime data, count values, and the guarded page closed
  until the owner explicitly activates the same setup.
- The same disposable lifecycle now counts every local HausmanHub page instead of
  merely finding the first one. An active safe setup must have exactly one
  guarded page; after an in-process deactivation or removal that one retained
  page must fail closed without counts; after a full temporary restart while
  disabled or removed, no such page may exist.
- Version 0.3.4 requires both fixed fields in saved HausmanHub main data. Even a
  safe `shadow` mode in the separate options cannot fill in a missing main
  mode, so an incomplete saved setup stays closed until its exact data is
  restored. This does not add any home-control feature.
- Version 0.3.3 keeps a bad saved HausmanHub setup closed. If its saved data violates
  the fixed safety contract, HausmanHub rejects a reload and removes only its own
  restored count states and stale HausmanHub records, both after startup and during a
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
  Home Assistant while the corrected HausmanHub setup remains installed. That restart
  must restore the same nine count-sensor names, fixed diagnostics, and the
  authenticated GET-only page with no devices or services. Only then is the
  temporary HausmanHub setup removed and checked through a final empty restart.
- The same disposable lifecycle separately covers two bad saved mode choices
  in HausmanHub options: a temporary `proxy` choice and an otherwise safe `shadow`
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
  exactly nine HausmanHub diagnostic number sensors. They share one redacted local
  snapshot, exclude HausmanHub's own sensors from the house totals, create no HausmanHub
  device or service, and do not call Home Assistant services.
- The owner explicitly approved a local count-only access path on 2026-07-14.
  It may expose the same fixed nine counts only after Home Assistant
  authentication, an exact built-in read-only user group, and a local-network
  origin check. It must have GET only, no outgoing connection, no token
  storage, no raw data, and no external or device-control capability. See the
  [local-access decision](LLM_WIKI/Manual/2026-07-14-local-read-only-access-decision.md).
- The Russian guides now make clear that ordinary HausmanHub counts and diagnostics
  need no extra user. The optional local account belongs only to a viewer;
  HausmanHub never receives or stores its password, key, or Home Assistant
  connection address, and only checks an incoming request origin momentarily.
  Kimi reviewed that clarification with no findings. See the [local viewer
  wording review](LLM_WIKI/Manual/2026-07-16-kimi-local-viewer-clarity-review.md).
- On 2026-07-14, an owner-performed local v0.1.2 diagnostics check confirmed
  the exact nine-count shape and all required safe-mode markers. Its aggregate
  values and the diagnostics file were inspected only and were not copied into
  this repository or this context.
- On 2026-07-14, the owner separately approved Codex direct local Home
  Assistant observation through a dedicated local non-administrator account.
  This is outside HausmanHub's runtime boundary: Codex sends GET only, keeps the
  credential outside GitHub and chat, and does not retain raw home data. The
  access account is not a technical read-only role, so the no-command rule is
  an operating constraint. See the [direct local observation decision](LLM_WIKI/Manual/2026-07-14-direct-local-read-observation-decision.md).

## Durable decisions

- HausmanHub is a separate repository and has no authority over the existing
  HausmanHub runtime.
- Initial modes are read-only and shadow only.
- Proxy requires separate owner approval and rollback notes.
- On 2026-07-17 the owner asked development to move toward working control.
  This authorizes the local 0.4.0 single-`input_boolean` canary only. It does
  not authorize a live deployment, a physical device, another service domain,
  multiple targets, Climate/Automation/Common ownership, proxy, or Node-RED.
- General physical-device execution remains blocked pending proven shadow
  parity, a device-specific canary/stop/rollback/authority decision, and owner
  signoff. The virtual-helper canary is not a physical authority transfer.
- Do not commit secrets, live identifiers, flow snapshots, device-specific
  service paths, physical command payloads, or deployment scripts.
- Every code change needs independent review. It follows Clean Code and Clean
  Architecture. Kimi must review the final current diff before the
  change is considered complete or before a commit, push, release, deployment,
  or publication. If Kimi is temporarily unavailable, another
  independent review may support every change permitted by the HausmanHub boundaries,
  including code, tests, documentation, and local checks or fixes. It must be
  recorded. It does not authorize a commit, push, release, deployment,
  publication, or new authority. Documentation-only edits do not require Kimi
  only when the change contains no code; the final Kimi gate applies to a mixed
  diff.
- The owner approved a public GitHub repository on 2026-07-14 because HACS
  cannot use a private GitHub repository. This permits only the minimal root
  `hacs.json` and manual HACS custom-repository installation. It does not
  approve inclusion in the public HACS catalog, live testing, proxy, or direct
  execution.
- The owner also explicitly approved local, read-only HausmanHub access to home
  data on 2026-07-14. That approval is limited to the v0.2.0 aggregate
  `home_summary`, including a separate disabled-entry count and the guarded
  local count-only path; it does not grant remote assistant access, proxy,
  direct execution,
  Common/Climate/Automation ownership, or permission to save live home data
  in this repository.
- The owner later approved a separate, local Codex read-observation path after
  the Home Assistant UI did not offer the exact `system-read-only` role. It
  does not relax HausmanHub's own strict route guard or grant HausmanHub any device
  authority; see the direct local observation decision above.
- On 2026-07-15 the owner explicitly approved showing only the existing nine
  aggregate HausmanHub counts in Home Assistant. This authorizes exactly nine
  diagnostic number sensors, not devices, controls, new home data, proxy, or
  execution. The decision is recorded in
  [the summary-display decision](docs/read-only-home-summary-display-decision.md).
- Version `0.3.1` has a public GitHub release at
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v0.3.1. It keeps the
  approved nine diagnostic count sensors only. New installations use a HausmanHub
  prefix for their internal names; an existing Home Assistant registry keeps
  the same names through its unchanged permanent keys.
- On 2026-07-15, after the owner updated and restarted Home Assistant, a direct
  local Codex check used only GET requests and HTTP status codes. It confirmed
  that Home Assistant responded, HausmanHub's guarded read-only path was active, and
  all nine approved HausmanHub count sensors were present. No count value, raw home
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
  It remains local-only and does not change the HausmanHub runtime or home authority.
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
  absence of HausmanHub objects to survive. See the [safe-update review
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
  the check now requires only a different protected HausmanHub name and exact names
  for the other eight sensors. The final review found no issues. See the
  [occupied-name review note](LLM_WIKI/Manual/2026-07-15-kimi-occupied-name-check-review.md).
- Kimi reviewed the no-device runtime check with no findings. It confirmed
  that the isolated check requires both an empty HausmanHub device list and no
  device attachment for each of the nine sensors. See the [no-device review
  note](LLM_WIKI/Manual/2026-07-15-kimi-no-device-check-review.md).
- Kimi reviewed the real-Core one-setup check with no findings. It confirmed
  that `single_instance_allowed` is the Home Assistant result for a second
  attempt when the manifest permits only one HausmanHub setup. See the [one-setup
  review note](LLM_WIKI/Manual/2026-07-15-kimi-one-setup-check-review.md).
- Kimi reviewed the isolated external-name cleanup check with no findings. It
  confirmed that after HausmanHub removal, the temporary external entry still has the
  same identity and no HausmanHub or device ownership. See the [external-cleanup
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
  that the test remembers only HausmanHub's temporary internal state names before
  removal, then rejects any state left afterward without reading or printing a
  count value. See the [state-cleanup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-state-cleanup-after-removal-review.md).
- Kimi reviewed the isolated final-restart cleanup check with no findings. It
  confirmed that a third empty Home Assistant instance keeps HausmanHub absent after
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
  count data, and a fourth empty Home Assistant instance remains HausmanHub-free
  while the external record survives. See the [closed-cycle review
  note](LLM_WIKI/Manual/2026-07-15-kimi-closed-fresh-reinstall-cycle-review.md).
- Kimi reviewed the ordinary deactivation/reactivation lifecycle check with no
  findings. It confirmed that deactivation marks only HausmanHub's nine temporary
  count entries disabled and closes the guarded page, while reactivation
  restores only the same safe observation surface. See the [deactivation
  review note](LLM_WIKI/Manual/2026-07-15-kimi-deactivation-reactivation-review.md).
- Kimi reviewed the removal of a deactivated temporary HausmanHub setup with no
  findings. It confirmed that the test closes the page before removal, clears
  only HausmanHub's own temporary records, and preserves the unrelated external
  record. See the [deactivated-removal review
  note](LLM_WIKI/Manual/2026-07-15-kimi-deactivated-removal-review.md).
- Kimi reviewed the persisted-deactivation check with no findings. It confirmed
  that a temporary restart/update cannot silently reactivate HausmanHub or restore
  its page or state values, while explicit reactivation remains limited to the
  same nine safe counts. See the [deactivation-persistence review
  note](LLM_WIKI/Manual/2026-07-15-kimi-deactivation-persistence-review.md).
- Kimi reviewed the local-page uniqueness check with no findings. It confirmed
  that an active HausmanHub requires exactly one page, while the retained in-process
  page remains safely unavailable after deactivation or removal and no page
  returns after a full empty restart. See the [local-page uniqueness review
  note](LLM_WIKI/Manual/2026-07-15-kimi-local-summary-route-uniqueness-review.md).
- Kimi reviewed the invalid-saved-settings fail-closed fix with no findings. It
  confirmed that HausmanHub clears only its own restored state placeholders after
  startup, immediately clears them on a reload, and does not touch a device,
  service, external entity, or home-control boundary. See the [invalid-settings
  review note](LLM_WIKI/Manual/2026-07-15-kimi-invalid-persisted-settings-review.md).
- The v0.3.3 Kimi review cycle first found stale HausmanHub registry records and a
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
- Kimi reviewed the v0.3.5 cleanup of HausmanHub state values after a successful
  unload, with no findings. It confirmed that HausmanHub removes only its own nine
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
  findings. It confirmed that an enabled HausmanHub setup auto-loads after the next
  empty Home Assistant starts, while preserving exactly the same nine counts,
  fixed diagnostics, GET-only local page, and all control prohibitions. See the
  [ordinary unload/restart review
  note](LLM_WIKI/Manual/2026-07-15-kimi-ordinary-unload-restart-review.md).
- Kimi reviewed removal of an ordinarily stopped, still-user-enabled HausmanHub
  setup with no findings. It confirmed that the temporary test keeps the same
  nine-count and no-control boundary, closes both read paths before and after
  removal, preserves an unrelated similar-name record, and uses no real home.
  See the [ordinary stopped-removal review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stopped-removal-review.md).
- Kimi reviewed user deactivation after an ordinary HausmanHub stop with no findings.
  It confirmed that the disposable lifecycle distinguishes this state from an
  active deactivation, preserves the nine-count/no-control boundary, and
  carries the disabled state through restart and removal. See the [ordinary
  stopped-deactivation review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stopped-deactivation-review.md).
- Kimi reviewed the duplicate-setup guard while HausmanHub is ordinarily stopped.
  Its first pass found a test that depended on exact source formatting; the
  check now uses semantic markers and order instead. The final direct Kimi
  review found no issues. See the [stopped duplicate-setup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stopped-duplicate-setup-review.md).
- Kimi reviewed the duplicate-setup guard while a saved HausmanHub setup stays
  user-deactivated after restart, with no findings. It confirmed that the
  rejected second setup preserves the disabled state and that explicit
  activation restores only the same nine safe counts. See the [disabled
  duplicate-setup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-disabled-duplicate-setup-review.md).
- Kimi reviewed removal of a saved user-deactivated HausmanHub setup after an empty
  restart, with no findings. It confirmed the same collision-aware nine
  disabled records survive until removal and that the following restart remains
  HausmanHub-free. See the [disabled removal-after-restart review
  note](LLM_WIKI/Manual/2026-07-15-kimi-disabled-removal-after-restart-review.md).
- Kimi reviewed the isolated extra-input boundary check for both HausmanHub setup
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
  exact settings shape closes HausmanHub through reload and restart, and that only the
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
temporary v0.3.0-style registry, replaces only the temporary HausmanHub copy, and
requires the old names to survive while a new entry receives the protected
v0.3.1 names. Before that new entry, the check reserves one protected-looking
name only in the disposable registry and requires the occupied name to remain
external while all nine HausmanHub sensors still appear. After HausmanHub removal, it
requires that external record to remain unchanged. It then creates and removes
another safe HausmanHub setup, requiring its nine sensors and the same external
record again. After each removal it sends one authenticated loopback GET from a
temporary exact read-only user to the retained local-summary route, requires an
unavailable response, and rejects any returned count key. It proves neither
live-home behaviour nor execution authority. It also records the temporary
HausmanHub state names before each removal and requires all of those states to be
absent afterward, without reading their values. It also requires no HausmanHub device
registry entry and no device attachment for each HausmanHub sensor. It also tries a
second safe setup and requires Home Assistant to refuse it while preserving the
original nine-sensor setup. After the final removal it starts a third empty
Home Assistant instance with the same temporary configuration and requires no
HausmanHub entry, entity, device, service, state, runtime data, or local route to
return, while the unrelated temporary external record remains unchanged.
Only after that absence proof, it creates a fresh `read-only` HausmanHub setup in the
same third instance. The new setup must have a new entry identifier, exactly
nine count sensors, the fixed safe diagnostics report, the unchanged external
record, and the guarded authenticated local route.
That fresh setup is then removed, its route must immediately fail closed
without count data, and a fourth empty Home Assistant instance must contain no
HausmanHub data while the external record remains unchanged.

Before its first restart, the check also deactivates the saved safe setup
through Home Assistant's normal user path. The setup must become unloaded, its
nine registry entries must be marked disabled by that setup, and the guarded
local route must return only an unavailable response without count keys. After
reactivation, it must restore the same nine enabled count sensors, safe
diagnostics, and the authenticated GET-only route without any device, service,
proxy, or execution capability.

One later temporary reinstallation is deactivated before removal. The check
then requires removal to clear its nine HausmanHub records, temporary states, and
guarded page, while preserving the unrelated temporary external record through
the next empty restart.

Before the earlier temporary update restart, the first safe setup is also
deactivated. The restarted empty Home Assistant must keep it disabled, with no
HausmanHub runtime data, count state, or guarded page. Only explicit reactivation
may restore the existing nine safe count sensors, diagnostics, and GET-only
page.

Throughout that temporary lifecycle, the check counts every local HausmanHub page.
An active setup must have exactly one. After a deactivation or removal in the
same temporary process, that one retained page must fail closed without counts;
after a full temporary restart while HausmanHub is disabled or removed, no page may
return.

The same disposable Core check writes one deliberately unsafe saved HausmanHub mode,
rejects an immediate reload, then restarts. It requires no HausmanHub runtime data,
service, device, page, or count state to return. HausmanHub clears only the restored
states belonging to that invalid HausmanHub entry after Home Assistant startup; it
does not change other entities or any device-control surface.

Separately, direct local Codex observation passed a harmless availability
check, a version-only check, and a count-only current-state check on
2026-07-14. It used no command or mutating request, retained no raw home data,
and does not validate or expand HausmanHub runtime authority.

Before publishing, run `python3 tools/check_local_release.py` after staging
the intended files. It runs the local tests, synthetic fixture checks, and the
Git-file safety checks as one fixed list. It also requires a higher integration
version if a staged change touches HausmanHub itself or `hacs.json`. It does not
inspect a live home or grant any authority.

The repository also runs that same fixed command in GitHub after a change to
`main` or a proposed change. Its workflow has only `contents: read`, disables
stored checkout credentials, and has no Home Assistant target, home data, or
deployment step.
Its first GitHub run completed successfully on 2026-07-14 for commit
`a75f78b`; the recorded run is
https://github.com/shumkiiv/hausmanhub_hacs/actions/runs/29352007883.
Public contribution guidance and a pull-request safety checklist are present.
They require the local check, Kimi review for code, and an explicit statement
that no home data or control capability is being introduced.
A Russian release checklist records the safe order for a real HACS update:
version, version history, local check, Kimi review, GitHub check, published
release, HACS refresh, and Home Assistant restart. Documentation-only and
test-only changes do not need a new HACS version.

## Next decision gate

The active 50-item roadmap changes only HausmanHub. Android is already developed in a
separate read-only repository; HausmanHub must provide stable contracts for it without
editing or building the application here. The existing climate module is also
read-only and remains the execution engine through its current fixed API. The
first 1.6 milestone is API discovery and a combined climate projection; a
readable decision journal, a continuous HausmanHub dispatcher, and further contour
types follow. Generic proxying, arbitrary device execution, changes to the
climate module, and unsupervised live deployment remain out of scope.

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
[foundation handoff](LLM_WIKI/Manual/2026-07-13-hausmanhub-repository-foundation.md).
Engineering and review rules are in
[engineering standards](docs/engineering-standards.md).

<!-- llm-wiki-sync:start -->
## LLM Wiki

- Obsidian/context index: `LLM_WIKI/00_Index.md`.
- Latest generated context: `LLM_WIKI/Context.md`.
- Last sync: 2026-07-23T12:09:17+03:00.
<!-- llm-wiki-sync:end -->
