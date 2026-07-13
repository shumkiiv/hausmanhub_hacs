# HASC AI Context

Last updated: 2026-07-13.

## Project state

- Repository: `shumkiiv/hausmanhub_hasc` (private, MIT, `main`).
- Local checkout: `/home/ivsh/projects/hausmanhub_hasc`.
- Home Assistant baseline: Core 2026.7.0 or newer.
- A private `custom_components/hausman_hub/` read-only skeleton is approved
  and present. HACS metadata remains intentionally absent.
- The skeleton passed an isolated runtime smoke check in Home Assistant Core
  2026.7.0 on Python 3.14.3. It used a disposable empty configuration only;
  no device, Node-RED, Home Assistant service, or live API work was performed.
- Synthetic Common-contract fixtures, static validators, synthetic shadow
  evidence, and redacted diagnostics/repairs fixtures are present. They use
  Python's standard library and local JSON only.

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
- Kimi reviewed the persisted-config exact-key boundary tests and confirmed the
  final version with no remaining findings. See the [persisted-config review
  note](LLM_WIKI/Manual/2026-07-13-kimi-persisted-config-boundary-review.md).
- Kimi reviewed the diagnostics allow-list structure test with no findings. See
  the [diagnostics review note](LLM_WIKI/Manual/2026-07-13-kimi-diagnostics-allow-list-review.md).

## Verification

Run `python3 -m unittest discover -s tests -v`. This validates only synthetic
schema data and the in-memory config/options adapter boundary; it does not
prove shadow parity, grant any authority, or load Home Assistant. Core 2026.7.0
requires Python 3.14.2 or newer. The isolated Core smoke check is documented
in `docs/read-only-skeleton.md`; it proves only the safe integration shell can
load and unload in an empty local Core, never shadow parity or authority.

## Next decision gate

The read-only skeleton is limited to the two approved modes and local,
synthetic verification. HACS metadata, proxy, and direct execution remain out
of scope.

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
