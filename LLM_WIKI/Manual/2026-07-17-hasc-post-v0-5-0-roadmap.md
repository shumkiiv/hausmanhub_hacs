# HASC roadmap after 0.5.0

Date: 2026-07-17.

## Product direction

HASC remains the single backend contract that Android should eventually know.
The existing climate-core remains the owner of automatic policy, cooldown,
authority, safety, physical feedback, and actual execution. The next HASC work
must make the 0.5.0 backend operable and observable before expanding physical
authority.

This roadmap covers HASC source only. Installing it in a live home, preparing a
registry with real identifiers, changing climate-core, changing Android, and
executing a physical canary require their own explicit authorization.

## Phase 1 — 0.5.1 operator readiness

1. Publish a machine-readable versioned schema for the public Android state,
   actions, action results, and fixed administrator registry/import payloads.
2. Add a multi-step local-admin setup flow for climate rooms and devices so a
   registry can be prepared without hand-writing JSON or exposing it to the
   tablet account.
3. Add a read-only bridge readiness check: reachable contract version,
   freshness, room/device counts, and coarse failure category. Never display or
   export the private target, source IDs, or entity IDs in diagnostics.
4. Add registry preview and reconciliation confirmation before atomic save.
   Import must remain read-only and must never auto-register or delete a device.
5. Add focused migration tests for an empty 0.5.0 Store and future registry
   versions.

Acceptance: after HACS installation an administrator can keep the bridge in
`disabled`, prepare a valid registry through HASC's own UI, and see a redacted
readiness result without a climate POST.

## Phase 2 — measurable shadow

1. Build an end-to-end disposable loopback Climate API fixture that exercises
   real Home Assistant auth, registry save, Android snapshot, every typed
   action, and proof that shadow performs zero POST requests.
2. Add redacted shadow counters for matched, missing, moved, stale, rejected,
   and translated intents. Do not retain private payloads or identifiers.
3. Expose normalized unavailable/rejected reasons in the public contract so
   Android can disable controls without learning backend details.
4. Define a shadow evidence window and an explicit readiness result for one
   candidate room.

Acceptance: all registered devices reconcile, public state remains stable, and
shadow evidence shows no unexpected translation or authority mismatch before a
canary can be selected.

## Phase 3 — command lifecycle

1. Generate HASC-side operation IDs and return typed receipts instead of only
   `submitted` or `shadow`.
2. Prevent duplicate or concurrent execution of the same room intent without
   duplicating climate-core policy.
3. Normalize accepted, pending, confirmed, rejected, timed-out, and unknown
   outcomes from climate-core and its physical feedback.
4. Add bounded per-room submission protection and audit only coarse operation
   status, never tokens, URLs, source IDs, entity IDs, or backend payloads.
5. Version the public contract compatibly so an older Android client fails
   closed instead of guessing a new response shape.

Acceptance: a repeated tablet request cannot create an accidental double
command, and the user can distinguish acceptance from physical confirmation.

## Phase 4 — one-room physical canary

1. Select one reconciled authority-ready room and the smallest initial action
   set, preferably target temperature plus room off.
2. Require fresh state, current authority, exact binding, capability, owner,
   scope, no in-flight conflict, and a confirmed rollback path before each
   execution.
3. Observe the existing climate-core result and physical feedback; do not let
   HASC infer success from HTTP acceptance alone.
4. Return to `disabled` on any mismatch and record only redacted evidence.

Acceptance: one separately authorized supervised canary is confirmed by the
existing climate contour, with immediate rollback demonstrated. This phase is
not authorized by publishing 0.5.0 or by this roadmap.

## Phase 5 — coverage and Android cutover

1. Promote AC commands first, then independently qualify TRV, humidifier, and
   floor-heating contracts only where climate-core advertises and owns their
   authority and confirmation path.
2. Add capability-driven Android screens only after the HASC public schema and
   command lifecycle are stable. Android work stays in its own repository and
   authorization scope.
3. Migrate the tablet to HASC-only room/device IDs; raw HA entities and
   climate-core source IDs must never enter the client.
4. Remove transitional direct client paths only after comparison and rollback
   evidence is complete.

## Next coding slice

After the 0.5.4 preflight release, the next HASC-only work should make the
same redacted readiness decision available as a versioned local-admin contract
with an installed JSON Schema and explicit freshness timestamp. This lets a
future operator surface consume one canonical result without gaining climate
authority. It must remain read-only, exclude private bindings, and never
create an authorization or activate canary. Physical execution still requires
a new explicit authorization naming one public room and exact actions.

## 0.5.1 implementation status

Implemented on 2026-07-17: installed JSON Schemas and fixtures; guided
room/device registry setup with preview and confirmation; redacted readiness;
real-auth disposable shadow with measured zero command POSTs; idempotent
request/operation IDs; typed receipts; observable confirmation; timeout; and
one-pending-operation-per-room protection. Release `v0.5.1` and the live HACS
update completed; the owner restarted Core and post-deployment verification
proved the operation route loaded while the live climate bridge remained
`disabled` with no target, canary room, or physical command.

## 0.5.2 implementation status

Implemented and released on 2026-07-17: a persisted rolling
24-hour shadow evidence window sampled at a five-minute minimum interval;
redacted matched/missing/moved/stale/rejected/translated counts; exact registry
fingerprint reset; a local-admin candidate query/response contract; guided
Home Assistant candidate-room evidence UX; and a fail-closed canary gate.
Readiness requires three matching samples plus successful shadow translation
of room target and room off. Runtime canary execution is limited to those two
initial actions. Release `v0.5.2` points to `f3ec8ad`; local/Core/Kimi/GitHub
gates passed and the owner completed the live HACS restart. Post-deploy
verification proved the new route loaded while the bridge remained `disabled`
without a target, physical command, or canary.

## 0.5.3 implementation status

Implemented and released on 2026-07-17: fresh read-only candidate
selection with opaque form tokens, native entity selectors, typed capability
inference, repeat-read drift rejection, automatic selected-room draft
population, and preservation of the existing preview plus separate atomic
confirmation. Disposable Core uses the real options flow for two candidates,
requires the exact fixture registry, and measures zero command POSTs. A formal
one-room rollout checklist is prepared but inactive. All 217 local tests,
release/file-safety checks, and disposable Core 2026.6.4/2026.7.0 lifecycles
passed. Kimi `kimi-for-coding/k2p7` returned PASS with no substantial findings
in session `ses_08e986dbaffe6gCgi4wPgxStqP`. Commit `eb05bce` was published as
the latest non-prerelease `v0.5.3` after successful GitHub Actions. HACS
installed it on the live Core 2026.6.4 home, the owner restarted Core, and the
new translation keys loaded. Live climate home/action stayed unavailable
because the bridge remained `disabled`; no physical command or canary ran.

## 0.5.4 implementation status

Implemented in the current release candidate on 2026-07-18: one saved-room
read-only preflight combines full registry reconciliation, redacted shadow
evidence, exact initial command scope, per-room pending-operation status, and
disabled rollback readiness in the real Home Assistant options flow. Only a
complete `shadow` result can be `ready_for_authorization`, while activation is
always false and separate owner authorization remains mandatory. The flow does
not save registry/options, enable canary, or execute command POST. Final local,
Core, and independent review gates passed: 224 local tests, disposable Core
2026.6.4/2026.7.0, and Kimi `kimi-for-coding/k2p7` session
`ses_08ca230b5ffe4LBnH7j2hMTROH` with PASS and no substantial findings.
Publication and disabled live-install gates remain.
