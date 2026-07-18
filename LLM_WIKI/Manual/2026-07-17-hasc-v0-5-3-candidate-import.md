# HASC 0.5.3 explicit climate candidate import

Date: 2026-07-17.

## Decision

The normal registry workflow may populate a draft from the existing Climate
API only after an explicit candidate choice. It must remove manual private-ID
copying without becoming automatic discovery ownership or changing the
physical execution boundary.

## Wizard boundary

- HASC performs a fresh read-only Climate API state GET.
- The selector exposes only room/device labels and ephemeral values such as
  `candidate_001`. Source IDs are held only in the in-memory options flow.
- The operator provides a stable public HASC device ID and name, chooses one
  suggested kind, rollout scope/owner, and selects the control entity with the
  native Home Assistant entity selector.
- HASC maps advertised backend command types to the smallest typed capability
  set required for that kind. Unsupported kinds, missing minimums, wrong entity
  domains, duplicates, and incoherent scope/owner combinations fail closed.
- On submit HASC repeats the read-only GET and requires the selected candidate
  and imported room to be unchanged. Caller-supplied extra source data is
  ignored.
- Only the selected room/device is appended to the draft. Preview and a second
  explicit confirmation remain mandatory before Store replacement.

The wizard never imports every candidate, deletes an existing entry, saves on
selection, sends a command POST, exposes private bindings to Android, or turns
on canary mode.

## Verification and rollout boundary

Pure tests cover capability inference, public redaction, stale/unknown/drift,
duplicates, suggested kinds, and entity domains. The form-adapter test proves
opaque selector values, ignored injected source ID, no early Store save, exact
private binding after confirmation, stable interpretation of an already shown
opaque token, and zero bridge execution. All 217 local tests plus the
release/file-safety checks passed. Disposable Core 2026.6.4 and 2026.7.0 both
pass the real two-candidate options flow, exact registry comparison, and zero
command POST assertion. Kimi `kimi-for-coding/k2p7` completed the final
read-only staged review in session `ses_08e986dbaffe6gCgi4wPgxStqP` with PASS
and no substantial findings. Commit `eb05bce` was pushed and published as the
latest non-prerelease release `v0.5.3` after successful GitHub Actions. HACS
installed it on the live Core 2026.6.4 home; after the owner restart,
installed/latest both reported `v0.5.3`, the new translation keys loaded, and
climate home/action remained unavailable because the bridge was `disabled`.
No physical command or canary was attempted.

The companion [one-room checklist](../../docs/climate-canary-rollout-checklist.md)
is documentation only. It does not authorize live shadow configuration or a
physical canary. Live deployment of 0.5.3 ended in `disabled`.
