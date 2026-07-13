# HausMan Hub HASC

Private-first foundation for a future Home Assistant custom integration for
HausMan Hub.

## Current status

This repository intentionally contains no Home Assistant runtime integration,
`custom_components/`, or `hacs.json`.

- Visibility: private.
- License: MIT.
- Supported Home Assistant baseline: Core 2026.7.0 or newer.
- HACS metadata: deferred until a separate owner decision.
- Allowed initial modes: read-only and shadow only.

## Hard boundaries

This repository does not own Climate policy, Automation policy, the Common
smart-home contract, or Smart Home Center decisions. It must not deploy
Node-RED, invoke Home Assistant services, hold device authority, or contain
secrets, live entity identifiers, flow snapshots, service paths, command
payloads, or deployment scripts.

Proxy requires a separate owner approval and rollback notes. Direct execution
is blocked until proven shadow parity, a separate canary/rollback/authority
decision, and owner signoff.

## Next safe work

1. Add synthetic read-only fixtures and a static Common-contract validator.
2. Add a shadow-evidence model and redacted diagnostics contract.
3. Separately decide whether a private read-only
   `custom_components/hausman_hub/` skeleton is needed.

See [repository basics](docs/repository-basics.md) and
[AI context](AI_CONTEXT.md) before changing the repository.

## Local validation

The repository uses standard-library Python checks over synthetic fixtures
only. See [static validation](docs/static-validation.md) for the local command
and its safety boundary.

All future code follows the [engineering standards](docs/engineering-standards.md),
including mandatory Kimi review before completion or push.
