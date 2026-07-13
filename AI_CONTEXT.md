# HASC AI Context

Last updated: 2026-07-13.

## Project state

- Repository: `shumkiiv/hausmanhub_hasc` (private, MIT, `main`).
- Local checkout: `/home/ivsh/projects/hausmanhub_hasc`.
- Home Assistant baseline: Core 2026.7.0 or newer.
- HACS metadata and `custom_components/` are intentionally absent.
- No runtime, device, Node-RED, Home Assistant service, or live API work has
  been performed.
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

## Verification

Run `python3 -m unittest discover -s tests -v`. This validates only synthetic
schema data; it does not prove shadow parity or grant any authority.

## Next decision gate

Only after a separate approval decide whether to create a private read-only
`custom_components/hausman_hub/` skeleton. HACS metadata, proxy, and direct
execution remain out of scope.

See [repository basics](docs/repository-basics.md) and
[static validation](docs/static-validation.md),
[shadow evidence](docs/shadow-evidence-contract.md),
[diagnostics/repairs](docs/diagnostics-repairs-contract.md), and the
[foundation handoff](LLM_WIKI/Manual/2026-07-13-hasc-repository-foundation.md).
Engineering and review rules are in
[engineering standards](docs/engineering-standards.md).
