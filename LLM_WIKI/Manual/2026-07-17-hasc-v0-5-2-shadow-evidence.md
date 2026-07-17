# HASC 0.5.2 measurable shadow evidence

Date: 2026-07-17.

## Decision

HASC may not infer canary readiness from one fresh snapshot or a successful
HTTP translation. It retains a bounded redacted evidence window and requires
an explicit room result before its climate command runtime can execute.
This is a HASC safety gate, not authority transfer from climate-core and not
authorization for a live physical canary.

## Window and redaction

- The rolling window is 24 hours and samples reconciliation no more often than
  once every five minutes.
- At most 288 observations and 512 intent results are retained in a separate
  Home Assistant Store record.
- Records contain timestamps, public HASC room IDs, approved action names, and
  only the categories matched, missing, moved, stale, rejected, translated.
- No Climate API origin, source ID, Home Assistant entity ID, backend payload,
  response body, token, device reading, or physical result is retained.
- A SHA-256 fingerprint binds the evidence to the complete validated registry.
  Any public or private registry change clears the window. Damaged, unordered,
  future, oversized, or version-mismatched storage fails closed.

## Candidate readiness

The local admin API and Home Assistant options wizard evaluate one public HASC
room ID. `ready` requires current freshness and authority, exact registered
bindings, support for `set_room_target` and `turn_room_off`, three spaced
matching observations, both shadow translations, and no candidate anomaly.
Otherwise the result is `collecting` or `blocked` with normalized reasons.

The Android role cannot call the evidence route. The response exposes no
private bindings. Switching options to canary is insufficient by itself:
commands remain disabled in the Android home contract and fail before POST
until the persisted result is ready. The first runtime canary set is further
limited to room target and room off; other typed intents remain shadow-only.

## Delivery boundary

The disposable Core harness must prove the route in disabled and shadow modes,
redaction, an incomplete single-sample result, and zero command POSTs. The live
deployment must keep the bridge disabled. Selecting a real room, enabling the
bridge, sending a physical command, or changing climate-core requires separate
explicit authorization.

## Live deployment closure

Release `v0.5.2` points to `f3ec8ad` and passed GitHub Actions. HACS installed
it on the live Core 2026.6.4 instance. After the owner restarted Core, the
update entity reported installed/latest `v0.5.2`; the new evidence route was
loaded and forbidden to the non-admin verification account. Climate home and
a deliberately unregistered action both failed closed as unavailable because
the saved bridge remained `disabled`. No target, physical command, or canary
was used.
