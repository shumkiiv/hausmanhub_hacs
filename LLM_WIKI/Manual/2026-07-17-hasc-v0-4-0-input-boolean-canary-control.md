# HASC 0.4.0 input-boolean control canary

Date: 2026-07-17.

## Owner direction

The owner asked HASC development to move toward working control. The first
implementation deliberately interprets this as approval for a reversible
virtual-helper canary, not as authority over physical devices, Climate,
Automation, Common, Smart Home Center, proxy, Node-RED, or a device protocol.

## Implemented boundary

- Existing observation modes remain `read-only` and `shadow`.
- Canary control is off by default.
- The options flow can arm exactly one target in the `input_boolean` domain.
- An armed setup creates exactly one HASC switch without a device record.
- Every action revalidates the single saved HASC entry, exact options, armed
  flag, matching target, target state, and fixed on/off service availability.
- HASC registers no service and makes no outgoing connection.
- Disarming discards the saved target and removes only the HASC canary switch
  and registry row. It leaves the owner's helper unchanged.
- Diagnostics expose only the enabled boolean and fixed
  `single_input_boolean` scope, never the selected runtime identifier.

An automation attached to the helper may react to its state. Manual canary
instructions therefore require a new helper with no automation, scene, script,
or device attachment. A physical control domain requires a later explicit
target, canary, stop, rollback, authority, and owner decision.

## Verification

- 153 local unit tests passed after the documentation and package checks were
  brought into the final change.
- Disposable Home Assistant Core 2026.6.4 passed.
- Disposable Home Assistant Core 2026.7.0 passed.
- Both Core checks created only a temporary helper, exercised on/off through
  the HASC switch, disarmed the canary, and required the HASC switch and saved
  target to disappear while the helper remained off.
- No live Home Assistant, credential, home identifier, automation, service
  target, physical device, commit, push, release, or deployment was used.
- The repository-mandated Kimi review inspected the final staged control,
  lifecycle, validation, diagnostics, tests, and documentation diff and
  reported `KIMI_REVIEW_RESULT: NO FINDINGS`.

The local 0.4.0 change is verified and review-complete. It remains uncommitted,
unpushed, unreleased, undeployed, and unpublished until the owner explicitly
requests one of those actions.
