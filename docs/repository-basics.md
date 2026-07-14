# Repository basics

Established on 2026-07-13 for the separate HausMan Hub HASC workstream.

## Repository identity

- GitHub repository: `shumkiiv/hausmanhub_hasc`.
- Visibility: public, explicitly approved by the owner on 2026-07-14 so HACS
  can read the repository. It is not listed in the public HACS catalog.
- License: MIT.
- Default branch: `main`.
- Home Assistant baseline: Core 2026.7.0 or newer.
- HACS metadata: approved for manual custom-repository installation on
  2026-07-14. It does not approve inclusion in the public HACS catalog.

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

## Engineering quality gate

All future code follows Clean Code and Clean Architecture: dependencies point
inward to framework-independent domain contracts, and external adapters do not
own domain policy. Every code change requires Kimi review before completion or
push; review findings must be addressed or explicitly documented. This quality
gate does not relax any runtime, authority, or execution boundary.
