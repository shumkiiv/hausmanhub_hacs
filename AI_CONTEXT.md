# HASC AI Context

Last updated: 2026-07-14.

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
  change.
- Synthetic Common-contract fixtures, static validators, synthetic shadow
  evidence, and redacted diagnostics/repairs fixtures are present. They use
  Python's standard library and local JSON only.
- Version 0.1.2 adds one explicitly approved local read-only observation:
  `home_summary` in diagnostics. It contains exactly nine aggregate counts:
  areas, devices, entities, sensors, and available/unavailable/unknown/not
  reported/disabled entities. Disabled entries are counted separately before
  the adapter reads a state; `not_reported` therefore means an enabled entry
  has no current state. The adapter reduces each permitted local fact
  immediately to a category; it exports no name, identifier, reading, history,
  address, secret, or raw state. It creates no entities or services and does
  not call Home Assistant services.
- On 2026-07-14, an owner-performed local v0.1.2 diagnostics check confirmed
  the exact nine-count shape and all required safe-mode markers. Its aggregate
  values and the diagnostics file were inspected only and were not copied into
  this repository or this context.

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
  data on 2026-07-14. That approval is limited to the v0.1.2 aggregate
  `home_summary`, including a separate disabled-entry count; it does not grant
  remote assistant access, proxy, direct execution,
  Common/Climate/Automation ownership, or permission to save live home data
  in this repository.
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

## Verification

Run `python3 -m unittest discover -s tests -v`. The suite validates synthetic
schema data, in-memory form/observation adapters, and strict count-only
diagnostics boundaries; it does not prove shadow parity or grant authority.
The isolated Core lifecycle check is documented in `docs/read-only-skeleton.md`;
on 2026-07-14 it passed with the aggregate summary on Core 2026.6.4 and
2026.7.0 using disposable configurations only. It proves neither live-home
behaviour nor execution authority.

## Next decision gate

The read-only skeleton is limited to the two approved modes, local synthetic
verification, and the narrowly approved aggregate diagnostics summary. The
next safe live step is for the owner to update HASC through HACS and inspect
the local `home_summary`; it must not be committed or sent whole to chat.
Public HACS catalog listing, proxy, and direct execution remain out of scope.

The public custom-HACS decision and its narrow implementation boundary are
recorded in the [HACS packaging decision record](docs/hacs-packaging-decision.md).

The required explicit choice is documented in
[the read-only skeleton decision record](docs/read-only-skeleton-decision.md).
Its implementation boundary is documented in
[the read-only skeleton guide](docs/read-only-skeleton.md).

See [repository basics](docs/repository-basics.md) and
[static validation](docs/static-validation.md),
[shadow evidence](docs/shadow-evidence-contract.md),
[diagnostics/repairs](docs/diagnostics-repairs-contract.md), and the
[foundation handoff](LLM_WIKI/Manual/2026-07-13-hasc-repository-foundation.md).
Engineering and review rules are in
[engineering standards](docs/engineering-standards.md).
