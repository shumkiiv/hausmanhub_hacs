# HausMan Hub HASC

Read-only foundation for a future Home Assistant custom integration for
HausMan Hub.

## Current status

This repository contains a public, read-only Home Assistant skeleton under
`custom_components/hausman_hub/`. It contains no device-control runtime,
entity platform, service definition, or outgoing connection.

- Visibility: public.
- License: MIT.
- Supported Home Assistant baseline: Core 2026.6.4 or newer.
- HACS: available as a manually added custom repository; it is not in the
  public HACS catalog.
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

## Current safe scope

1. Keep synthetic Common-contract and shadow-evidence checks local.
2. Keep diagnostics redacted and limited to an explicit allow-list.
3. Provide an approved local-only home summary: aggregate counts for areas,
   devices, entities, sensors, and availability, including a separate count
   for disabled entities. It contains no names, identifiers, readings,
   history, addresses, or secrets.
4. In version 0.2.0, provide that same fixed summary through one authenticated
   local GET-only view for a dedicated Home Assistant read-only user. It has
   no command method, external access, or token storage.

See [repository basics](docs/repository-basics.md) and
[AI context](AI_CONTEXT.md) before changing the repository.

## Local validation

The repository uses standard-library Python checks over synthetic fixtures
only. After preparing a commit, run `python3 tools/check_local_release.py` for
the one-command local check. See [static validation](docs/static-validation.md)
for its safety boundary and the individual commands.

Before publishing, run the local [repository safety check](docs/repository-safety-check.md).
It looks only for accidentally added credentials and runtime files; it does
not connect to the home.

All future code follows the [engineering standards](docs/engineering-standards.md),
including mandatory Kimi review before completion or push.

See [the read-only skeleton](docs/read-only-skeleton.md) and the Russian
[safe home summary](docs/read-only-home-summary.md) for the exact safety
boundary. The separate [local-access guide](docs/read-only-local-access.md)
explains the additional nine-count-only access boundary.

## Automatic GitHub check

Every change to `main`, and every proposed change, runs the same fixed local
check on a temporary GitHub computer. It can only read this repository's
files. It has no Home Assistant address, no home data, no saved credentials,
and no deployment or device-control step.

For safe public contributions, see the Russian [contribution guide](CONTRIBUTING.md).

## Installation through HACS

This is not a public HACS listing. Add this GitHub repository manually as a
custom HACS repository.

1. In HACS, open the menu in the top-right corner and choose **Custom
   repositories**.
2. Add `https://github.com/shumkiiv/hausmanhub_hasc` and choose the
   **Integration** type.
3. Find **HausMan Hub HASC** in HACS and install it.
4. Restart Home Assistant, then add **HausMan Hub HASC** from its integration
   settings. Choose only `read-only` or `shadow`.

Installation does not grant device control. It must not be used to call
services, send commands, or enable `proxy` or direct execution.

For the short, safe Home Assistant check after installation, see the Russian
[safe-check guide](docs/home-assistant-safe-check.md).

## Updates through HACS

Published versions make it easier for HACS to show what can be updated. After
HACS refreshes the repository information, choose the latest published version
in its update screen, then restart Home Assistant. An update does not grant
HASC device control; the only available modes remain `read-only` and `shadow`.

See the short Russian [version history](CHANGELOG.md) for the changes in each
published version.
