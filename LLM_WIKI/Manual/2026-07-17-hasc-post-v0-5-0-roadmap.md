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

The 0.5.8 worktree implements the typed public target-temperature constraints
planned after 0.5.6. The next HASC-only slice should add stable public display
metadata for the two first room actions and their fields, so Android can render
Russian labels and explanations from a versioned contract without learning
backend details. This remains contract preparation, not Android repository
work or physical authorization. Physical execution still requires a new
explicit authorization naming one public room and exact actions.

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

Implemented and released on 2026-07-18: one saved-room
read-only preflight combines full registry reconciliation, redacted shadow
evidence, exact initial command scope, per-room pending-operation status, and
disabled rollback readiness in the real Home Assistant options flow. Only a
complete `shadow` result can be `ready_for_authorization`, while activation is
always false and separate owner authorization remains mandatory. The flow does
not save registry/options, enable canary, or execute command POST. Final local,
Core, and independent review gates passed: 224 local tests, disposable Core
2026.6.4/2026.7.0, and Kimi `kimi-for-coding/k2p7` session
`ses_08ca230b5ffe4LBnH7j2hMTROH` with PASS and no substantial findings. Commit
`2435c7f` was published as the latest stable `v0.5.4` after successful GitHub
Actions. HACS installed it on the live Core 2026.6.4 home, the owner restarted
Core, and the new preflight translation keys loaded. Climate home/action
remained unavailable because the bridge stayed `disabled`; no physical
command or canary ran.

## 0.5.5 implementation status

Implemented in the current worktree on 2026-07-18: one fixed local-admin POST
returns the same redacted saved-room preflight as options, including explicit
state generation and expiration timestamps. Expired state fails closed even
against ready historical evidence. Strict query/response schemas bring the
installed climate schema set to twelve. The tablet role is forbidden, disabled
performs no bridge GET, shadow performs no command POST, and activation remains
structurally false. The final staged package passed 226 local tests, the full
release/file-safety checks, and disposable Core 2026.6.4/2026.7.0. Kimi
`kimi-for-coding/k2p7` session `ses_08b9a95d1ffe9AVm46wQzzPqZQ` returned PASS
with no substantial findings. Commit `23aa3f8` was published as the latest
stable `v0.5.5` after successful GitHub Actions. HACS installed it on the live
Core 2026.6.4 home, the owner restarted Core, and the new admin preflight route
loaded while remaining forbidden to the non-admin verification account.
Climate home/action stayed unavailable because the bridge remained `disabled`;
no physical command or canary ran.

## 0.5.6 implementation status

Implemented in the current worktree on 2026-07-18: Android home contract v2
adds per-room `control.enabled`, the exact target/off action list, and a closed
normalized blocked-reason vocabulary. Availability follows the same canary,
freshness, binding, authority, imported-device availability, evidence, and
pending-operation gates as runtime. The prior home v1 schema remains packaged;
a strict v2 schema and synthetic fixture define the new response. The command
planner also rejects unavailable imported devices. Android repository changes,
live registry configuration, bridge activation, and physical commands remain
outside this slice. The final staged package passed 229 local tests, the full
release/package/file-safety checks, disposable Core 2026.6.4/2026.7.0, and
Kimi `kimi-for-coding/k2p7` session `ses_08b7a860affeOVomxNvxlvfWbi` with PASS
after a test-coverage follow-up. Publication and disabled live-deployment gates
remain.

## 0.5.7 implementation status

Implemented and released on 2026-07-18: the Home Assistant operator interface,
errors, result summaries, entity names, README, workflow labels, and GitHub
About description use plain Russian text instead of mixed internal English
codes. The release changed no climate contract or authority. All 231 local
tests, disposable Core 2026.6.4/2026.7.0, Kimi review, and GitHub Actions
passed. Commit `979c4c5` was published as stable `v0.5.7` and installed through
HACS with the live climate bridge left disabled.

## 0.5.8 implementation status

Implemented in the current worktree on 2026-07-18: Android home contract v3
adds per-room `action_inputs`. The target-temperature field declares type,
required status, minimum 18 °C, maximum 28 °C, step 0.5 °C, and unit. These
values come from the same constants as command validation. Metadata is omitted
when the target action is not advertised. The v1 and v2 home schemas remain
packaged; strict v3 schema and fixture cover the new boundary. Android code,
live registry configuration, bridge activation, and physical commands remain
outside this slice. All 232 local tests, the release/package/file-safety
checks, and disposable Core 2026.6.4/2026.7.0 passed with measured zero climate
command POSTs. Kimi `kimi-for-coding/k2p7` session
`ses_08b312059ffedrMEVGxBLevcNI` returned PASS with no substantial findings.
Publication gates remain.
