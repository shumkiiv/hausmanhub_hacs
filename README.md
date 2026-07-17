# HausMan Hub HASC

Safety-first Home Assistant custom integration for HausMan Hub.

## Current status

This repository contains a public Home Assistant integration under
`custom_components/hausman_hub/`. It always creates nine diagnostic number
sensors from the approved aggregate summary. Version 0.5.3 adds explicit
read-only candidate selection with opaque form tokens and native Home
Assistant entity selectors, so an operator can prepare a registry without
copying private IDs. It builds on the persistent redacted shadow-evidence
window, guided readiness, installed JSON Schemas, zero-POST shadow acceptance,
and idempotent operation receipts.
These features build on the private logical climate-device
registry, a local Android facade, read-only import of
the existing Climate API, shadow validation, and a one-room typed climate
canary. The existing climate-core remains the policy and execution owner.

- Visibility: public.
- License: MIT.
- Supported Home Assistant baseline: Core 2026.6.4 or newer.
- HACS: available as a manually added custom repository; it is not in the
  public HACS catalog.
- Allowed initial modes: read-only and shadow only.

## Hard boundaries

This repository does not own Climate policy, Automation policy, the Common
smart-home contract, or Smart Home Center decisions. It must not deploy
Node-RED, contain secrets, live entity identifiers, flow snapshots, physical
command payloads, or deployment scripts. Climate commands may leave HASC only
through two fixed Climate API v1 paths after typed validation. The client can
never provide a service, entity ID, private source ID, arbitrary URL, or
backend command payload.

General proxy execution remains blocked. The climate path is separately off by
default, runs read-only in `shadow`, and may execute only for one explicitly
selected authority-ready canary room after its persisted shadow evidence is
ready. The first climate canary scope is limited to room target temperature
and room off. Returning it to `disabled` removes its target and room settings.
The legacy single-`input_boolean` canary remains separate.

## Current safe scope

1. Keep synthetic Common-contract and shadow-evidence checks local.
2. Keep diagnostics redacted and limited to an explicit allow-list.
3. Provide an approved local-only home summary: aggregate counts for areas,
   devices, entities, sensors, and availability, including a separate count
   for disabled entities. It contains no names, identifiers, readings,
   history, addresses, or secrets.
4. In version 0.3.1, show that same fixed summary as exactly nine diagnostic
   number sensors in Home Assistant. They do not create a device, accept an
   action, or add any new home data.
5. In version 0.2.0, provide that same fixed summary through one authenticated
   local GET-only view for a dedicated Home Assistant read-only user. It has
   no command method, external access, or token storage.
6. Let the owner close or restore only that optional local view in HASC's
   settings. Closing it leaves the same nine diagnostic numbers and redacted
   diagnostics in place; it does not add a device action or any home control.
7. Let the owner keep the established five-minute refresh for those same nine
   numbers or slow it to 15 or 30 minutes. No faster choice, new data, entity,
   route, service, device, command, or authority is added.
8. Show only the effective validated HASC mode, optional-page choice, and
   five-, 15-, or 30-minute refresh choice in the existing redacted
   diagnostics. Raw entry data and options are never copied into the report.
9. In version 0.4.0, let the owner explicitly arm one HASC switch for one
   `input_boolean`. Every command revalidates the single HASC entry and exact
   target. Other domains are rejected, and disarming removes the saved target
   and HASC switch. Diagnostics show only the fixed canary scope and enabled
   flag, never the selected identifier. See the [canary control
   contract](docs/canary-input-boolean-control.md).
10. In version 0.5.0, let a local administrator bind climate devices into a
    private logical registry, serve a private-id-free Android snapshot, and
    validate typed climate intents in shadow. A one-room canary can post only
    after fresh state, authority, capability, room, owner, and scope checks.
    See the [climate architecture](docs/climate-control-architecture.md).
11. In version 0.5.1, prepare that registry through guided Home Assistant
    options, preview and reconcile before atomic save, publish installed JSON
    Schemas, and return idempotent operation receipts. Disposable real-auth
    shadow coverage proves that this path performs zero command POSTs.
12. In version 0.5.2, retain a bounded redacted 24-hour shadow window, show one
    room's `collecting`, `blocked`, or `ready` result in the guided options
    flow, and keep canary execution closed until three spaced exact samples and
    both initial room actions have been translated without anomalies.
13. In version 0.5.3, select fresh Climate API candidates through opaque
    non-private form tokens, choose their control entities with Home
    Assistant's native selector, infer only advertised typed capabilities, and
    keep the result in an unsaved draft until preview and separate atomic
    confirmation. See the [inactive one-room rollout
    checklist](docs/climate-canary-rollout-checklist.md).

See [repository basics](docs/repository-basics.md) and
[AI context](AI_CONTEXT.md) before changing the repository.

## Local validation

The repository uses standard-library Python checks over synthetic fixtures
only. After preparing a commit, run `python3 tools/check_local_release.py` for
the one-command local check. See [static validation](docs/static-validation.md)
for its safety boundary and the individual commands.

If a change touches HASC itself or its HACS setup file, the same command also
requires a higher integration version. This prevents an HACS-visible change
from being published under an old version number.

The same local command checks the prepared Git package needed for manual HACS
installation: the approved metadata, integration entry files, translations,
local icon, license, and release notes. It reads local Git data only and does
not contact HACS or Home Assistant.

Before publishing, run the local [repository safety check](docs/repository-safety-check.md).
It looks only for accidentally added credentials and runtime files; it does
not connect to the home.

All future code follows the [engineering standards](docs/engineering-standards.md).
Every code change needs independent review. Kimi must review the final current
diff before the change is considered complete or before a commit, push,
release, deployment, or publication. If Kimi is temporarily unavailable,
another independent review may support every change permitted by the HASC
boundaries, including code, tests, documentation, and local checks or fixes.
It does not authorize a commit, push, release, deployment, publication, or new
authority. Documentation-only edits do not require Kimi only when the change
contains no code; the final Kimi gate applies to a mixed diff.

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
   settings. Choose only `read-only` or `shadow` for the base observation mode.

Installation does not grant physical-device control. Both canaries are off by
default. Keep the climate bridge `disabled` until its private registry is
prepared, then use `shadow` before selecting one canary room. The exact rollout
is documented in the [climate guide](docs/climate-control-architecture.md).

For the short, safe Home Assistant check after installation, see the Russian
[safe-check guide](docs/home-assistant-safe-check.md).

## Updates through HACS

Published versions make it easier for HACS to show what can be updated. After
HACS refreshes the repository information, choose the latest published version
in its update screen, then restart Home Assistant. The observation modes remain
`read-only` and `shadow`; helper-canary control must be armed separately.

See the short Russian [version history](CHANGELOG.md) for the changes in each
published version.

For maintainers, the Russian [release checklist](docs/hacs-release-checklist.md)
explains when an HACS update is needed and how to publish it safely.
