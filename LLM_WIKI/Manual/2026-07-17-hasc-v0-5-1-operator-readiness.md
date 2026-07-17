# HASC 0.5.1 operator readiness and operation receipts

Date: 2026-07-17.

## Decision

HASC 0.5.1 turns the 0.5.0 climate facade into an operator-visible workflow
without expanding physical authority. The existing climate-core remains the
only policy/execution owner. The live deployment must finish with the HASC
climate bridge in `disabled`; a physical one-room canary remains separately
authorized work.

## Implemented contract

- The local Home Assistant options flow can build a registry room by room and
  typed device by typed device. The common path requires no handwritten JSON.
  A complete JSON editor remains an advanced fallback.
- The flow holds an unsaved draft, runs read-only preview/reconciliation, and
  performs atomic replacement only after a separate boolean confirmation.
- Eight JSON Schema 2020-12 files are installed under
  `custom_components/hausman_hub/contracts/v1/`, with sanitized compatibility
  fixtures under `fixtures/hasc_climate_v1/`.
- Admin readiness and preview expose only mode, freshness, bounded counts,
  match status, and normalized reason codes. Target origins, source IDs, and
  entity IDs are excluded.
- Android actions require a stable `request_id`. HASC creates a random 128-bit
  opaque operation ID and retains at most 256 in-memory receipts per loaded
  runtime. Identical retries return the same receipt without I/O; reuse for a
  different intent is rejected.
- An explicit negative climate-core response becomes a terminal redacted
  `rejected` receipt. Transport ambiguity remains unavailable instead of being
  misreported as a physical rejection.
- Canary idempotency is reserved before network I/O. An ambiguous POST keeps a
  pending record, so a same-request retry cannot submit a second command.
- Evicted request IDs enter a bounded fail-closed filter for the loaded runtime,
  preventing receipt eviction from enabling a second POST.
- Shadow returns `accepted` without a command POST. Canary returns `pending`
  after backend HTTP acceptance. A later state read may produce `confirmed`
  only for explicitly observable room results; otherwise the receipt times out.
  Unknown or evicted operation IDs return a redacted `unknown` receipt.
- Room-off confirmation follows the exact private source selected by the
  command plan; another already-idle device in the room cannot confirm it.
- One room may have only one pending HASC canary submission. This is submission
  protection, not a replacement for climate-core policy or cooldown.

## Verification boundary

The disposable Home Assistant Core harness uses real local Home Assistant
authentication plus a temporary loopback Climate API. It verifies registry
preview/save, readiness, Android state, action retry, receipt lookup, read-only
state GETs, and a measured zero Climate API command POST count in shadow. It
then restores `disabled` and removes only its temporary registry fixture.

No real credential, home identifier, live target, Android repository, Node-RED
flow, climate-core source, or physical device is part of this implementation.
