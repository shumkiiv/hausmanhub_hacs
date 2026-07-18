# HASC 0.5.6 Android room-control projection

Date: 2026-07-18.

## Decision

The public tablet home contract advances explicitly from v1 to v2. Every
registered room receives one coarse `control` projection containing an enabled
flag, the first supported room actions, and normalized blocked reasons. The old
v1 home schema remains installed so an older client can identify its known
shape and fail closed on version 2 instead of guessing added fields.

## Boundary

- The first projected actions remain exactly `set_room_target` and
  `turn_room_off`, matching the evidence-qualified physical-canary scope.
- `enabled=true` requires canary mode for that exact public room, fresh state,
  one exact controlled AC binding, authority readiness, an available imported
  device, both advertised backend command types, ready persisted evidence, and
  no pending room operation.
- The public reason vocabulary is fixed to bridge disabled, shadow only, room
  not selected, stale state, registry mismatch, authority not ready, device
  unavailable, actions unsupported, evidence not ready, and operation pending.
- No source/entity ID, private bridge origin, backend payload, operation ID, or
  real-home data enters the projection.
- Command planning now independently rejects an imported device marked
  unavailable, so the published button state cannot be stricter than a still
  executable direct request.

## Compatibility and rollout

The existing local endpoint path stays fixed while its `contract.version`
becomes 2. A strict v2 JSON Schema and synthetic fixture ship beside the
retained v1 schema. Shadow exposes supported actions but always returns
`enabled=false` with `shadow_only`; physical execution is not authorized by
this contract change. Live deployment must finish with the bridge `disabled`.

Android repository work, real registry configuration, bridge activation, and
physical climate commands remain outside this HASC-only slice.

## Verification

The final staged package passed 229 local tests, the complete
release/package/file-safety checks, and disposable Home Assistant Core
2026.6.4 and 2026.7.0 lifecycles with measured zero shadow command POSTs. Kimi
`kimi-for-coding/k2p7` session `ses_08b7a860affeOVomxNvxlvfWbi` returned PASS
with no substantial findings. Its non-blocking note about individually named
blocked-reason branches was closed with a table-driven fail-closed test, and
the follow-up review also returned PASS. Publication and disabled live
deployment remain pending.
