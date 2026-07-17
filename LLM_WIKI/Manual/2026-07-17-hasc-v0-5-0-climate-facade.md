# HASC 0.5.0 climate facade decision

Date: 2026-07-17.

## Owner intent

The owner described the intended product explicitly: climate devices are
configured in HASC, HASC understands their type and abilities, and an Android
tablet controls the home through HASC. The owner asked to implement every
planned point and continue without stopping.

This authorizes the local HASC implementation and its source push after
verification. It does not silently authorize live-home deployment or changing
the current climate and Android repositories. Those repositories were read
only to confirm their contracts.

## Architecture decision

Android must eventually know only the authenticated HASC API. HASC owns:

- stable rooms and logical device identities;
- private binding of one logical device to one or more Home Assistant entities;
- device kind, user-facing capabilities, control owner, and rollout scope;
- aggregation of current climate state for Android;
- exact validation and translation of typed Android intents;
- local user/admin authorization and registry persistence.

The current climate-core / Smart Home Center remains the policy and execution
engine. It owns auto/manual behavior, targets, target strategy, cooldown,
safety blockers, authority readiness, physical feedback, planning, and actual
device execution. HASC must never implement a second climate policy engine or
become a generic HA/Node-RED proxy.

## Implemented contracts

The registry supports air conditioners, radiator thermostats, humidifiers,
floor heating, temperature sensors, and humidity sensors. Private endpoints
have explicit roles. Passive sensors cannot acquire a control endpoint or
leave observed scope. Controllable devices require exact minimum capabilities
and one control endpoint.

The existing `hausman-climate` v1 state is imported through a bounded strict
reader. Import produces candidates and reconciliation only; it does not mutate
the registry. Unknown command types, duplicate IDs, stale/future state, wrong
room references, and an unsupported contract fail closed.

The public Android snapshot contains registered HASC IDs, names, kinds,
capabilities, availability, state, room measurements/targets/mode/authority,
and a redacted reconciliation summary. It never contains source or entity IDs.
Private candidates and full registry replacement are separate local-admin
operations. Registry replacement is rejected in active `canary`; bindings may
change only in `disabled` or `shadow`.

Typed actions cover:

- room target, mode, minimum target, target strategy, and room off;
- per-device power, target temperature, target humidity, HVAC mode, and fan
  mode;
- exact mappings for AC, TRV, humidifier, and floor-heating contracts already
  understood by climate-core.

## Rollout and rollback

The climate bridge is separate from HASC's legacy observation mode:

- `disabled` stores no target and no canary room and performs no climate I/O;
- `shadow` reads and translates but never posts;
- `canary` may post for one exact room only after current freshness,
  reconciliation, authority, capability, owner, and scope checks.

The target is restricted to a literal loopback/RFC1918/ULA HTTP(S) origin.
Only `/endpoint/climate/api/v1/state` and
`/endpoint/climate/api/v1/command` are used. Redirects and arbitrary paths,
hosts, credentials, queries, payloads, services, entity IDs, and command types
are rejected. Returning to `disabled` drops target and canary room as the
immediate HASC rollback.

## Current delivery boundary

HASC 0.5.0 provides a locally testable backend vertical slice: registry,
state facade, shadow actions, typed canary bridge, role separation, and
rollback. It is not proof of a live physical command. A real house must first
configure a synthetic-free registry, pass shadow comparison, select one
authority-ready room, and then perform a separately supervised canary. Android
can be switched to these HASC routes only after that backend contract is
deployed and observed.

## Verification evidence

The final staged package passed on 2026-07-17:

- `python tools/check_local_release.py`: 191 tests plus contract fixtures,
  version increase, HACS package, tracked-file safety, and staged-file safety;
- disposable Home Assistant Core 2026.6.4 and 2026.7.0 lifecycles on Python
  3.14.3, including actual loopback HTTP authentication for the four fixed
  climate routes in the disabled rollback state;
- independent read-only Kimi review with model `kimi-for-coding/k2p7`, OpenCode
  session `ses_09070e1c2ffeeTgDvZ3A3kiLUu`: no substantial findings.

The reviewer changed no file or index entry and performed no commit, push,
deployment, live Home Assistant request, or device access. The verified source
was committed as `5ac09c5` and pushed to `origin/main`. That source push made
no deployment. On the owner's later explicit request, the current verified
tree `cc04029` was tagged and published as the non-prerelease latest GitHub
Release [`v0.5.0`](https://github.com/shumkiiv/hausmanhub_hasc/releases/tag/v0.5.0).
The tag resolves exactly to `cc04029`, and both GitHub source archives returned
HTTP 200 after publication. A real registry, one-room physical canary, and
Android cutover remain later explicit steps.
