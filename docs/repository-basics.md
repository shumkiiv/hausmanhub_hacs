# Repository basics

Established on 2026-07-13 for the separate HausMan Hub HASC workstream.

## Repository identity

- GitHub repository: `shumkiiv/hausmanhub_hasc`.
- Visibility: private, following the private-first spike guardrail.
- License: MIT.
- Default branch: `main`.
- Home Assistant baseline: Core 2026.7.0 or newer.
- HACS metadata: deliberately deferred. Do not add `hacs.json` without a
  separate decision covering private testing or a public release.

## Allowed scope

The initial modes are read-only and shadow. Work may use only synthetic data
and local, read-only validation.

The first implementation work, when explicitly requested, is limited to:

1. synthetic fixtures and a static validator for the Common smart-home
   contract;
2. a shadow-evidence model;
3. a redacted diagnostics contract.

## Non-negotiable boundaries

- Do not modify or deploy the HausMan Hub Node-RED/Home Assistant runtime.
- Do not call live Home Assistant APIs, services, or physical devices.
- Do not own or change Climate, Automation, Common, or Smart Home Center
  policy.
- Do not add secrets, `.env` files, tokens, Node-RED flows, live entity IDs,
  service paths, physical command payloads, or deployment scripts.
- Keep HASC commits separate from climate fixes, Node-RED deployment, and
  Smart Home Center runtime work.

Proxy is possible only after separate owner approval and documented rollback.
Direct execution remains `direct_execution_blocked` until proven shadow parity,
a separate canary/rollback/authority decision, and owner signoff.

## Architecture sources

The runtime repository's architecture documents remain read-only sources. The
relevant set covers contour ownership, the Common contract, Automation
registry, Climate hardening, Smart Home Center facade, the HACS spike,
read-only contract tests, config/options flow, diagnostics and repairs, shadow
parity, and direct-execution authority guardrails.

Do not copy runtime configuration or sensitive examples from that repository.
