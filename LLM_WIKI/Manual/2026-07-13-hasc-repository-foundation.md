# HASC repository foundation

Date: 2026-07-13.

## Decision

The user authorized a new GitHub repository named `hausmanhub_hasc` for the
HausMan Hub HASC workstream. It was created as `shumkiiv/hausmanhub_hasc` and
cloned to `/home/ivsh/projects/hausmanhub_hasc`.

The repository is private, uses the MIT license, supports Home Assistant Core
2026.7.0 or newer, and has no HACS metadata. `custom_components/` is also
intentionally absent.

## Safety posture

Initial work is limited to read-only and shadow modes with synthetic data and
local read-only tests. HASC has no authority over Climate, Automation, Common
policy, Smart Home Center decisions, Node-RED, Home Assistant services, or
devices.

Proxy needs separate owner approval and rollback notes. Direct execution stays
blocked until shadow parity is proven and a separate canary, rollback, and
authority decision receives owner signoff.

## Next action

The next safe implementation is a synthetic Common-contract fixture set and a
static validator. It must not use the existing runtime, live IDs, secrets,
flow snapshots, service paths, command payloads, or deploy scripts.
