# HASC AI Context

Last updated: 2026-07-13.

## Project state

- Repository: `shumkiiv/hausmanhub_hasc` (private, MIT, `main`).
- Local checkout: `/home/ivsh/projects/hausmanhub_hasc`.
- Home Assistant baseline: Core 2026.7.0 or newer.
- HACS metadata and `custom_components/` are intentionally absent.
- No runtime, device, Node-RED, Home Assistant service, or live API work has
  been performed.

## Durable decisions

- HASC is a separate repository and has no authority over the existing
  HausMan Hub runtime.
- Initial modes are read-only and shadow only.
- Proxy requires separate owner approval and rollback notes.
- Direct execution remains blocked pending proven shadow parity, a separate
  canary/rollback/authority decision, and owner signoff.
- Do not commit secrets, live identifiers, flow snapshots, service paths,
  command payloads, or deployment scripts.

## Next safe step

On explicit request, create synthetic read-only fixtures and a static
Common-contract validator. Run local read-only tests only.

See [repository basics](docs/repository-basics.md) and
[foundation handoff](LLM_WIKI/Manual/2026-07-13-hasc-repository-foundation.md).
