# HASC 0.5.4 one-room climate canary preflight

Date: 2026-07-18.

## Decision

HASC may present one complete operator preflight for a saved public room, but
the preflight is evidence only. It must not create authority, change rollout
mode, select a live canary room, or send a climate command.

## Contract boundary

- The room selector is built from the persisted HASC climate registry, never
  from an unsaved options draft.
- In `shadow`, HASC performs a fresh Climate API state GET and reconciles the
  complete registry. In `disabled`, no bridge read is attempted.
- The redacted result combines matched/missing/moved/unregistered counts,
  shadow status and counters, the exact initial action scope
  `set_room_target` plus `turn_room_off`, per-room pending state, and readiness
  to return to `disabled`.
- Only exact reconciliation, ready shadow evidence, qualified scope, no
  pending operation, and ready rollback can produce
  `ready_for_authorization=true`.
- `activation.allowed` is always false and
  `separate_authorization_required` is always true. The flow only closes back
  to setup; it cannot save options, save the registry, enable canary, or post a
  command.
- Private Climate API origin, source/entity IDs, command/backend payloads,
  operation IDs, and real device data are excluded from the result.

Malformed internal evidence, an unknown room, an inconsistent readiness
status, unsupported actions, missing state, reconciliation drift, active
canary mode, or a pending operation fails closed.

## Verification and rollout boundary

Pure and form-adapter tests cover ready, disabled, pending, corrupted evidence,
saved-versus-draft room selection, redaction, zero option/registry saves, and
zero bridge execution. The disposable Home Assistant Core lifecycle opens the
real options flow and keeps its measured command POST count at zero. The
prepared release passed 224 local tests and the complete release/file-safety
checks, then passed disposable Core 2026.6.4 and 2026.7.0. Kimi model
`kimi-for-coding/k2p7` returned PASS with no substantial findings from the
read-only staged review session `ses_08ca230b5ffe4LBnH7j2hMTROH`. Remaining
gates are GitHub Actions, exact release tag, and a live HACS install ending
with the bridge still `disabled`.

Publication or installation of 0.5.4 is not authorization for physical climate
control. A live one-room canary still requires a new explicit owner decision
naming the public room and allowed actions.
