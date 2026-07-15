# Kimi review: unsafe direct-execution recovery

Date: 2026-07-16.

## Scope

Independent read-only review of the disposable lifecycle that first rejects a
user activation from saved `direct_execution_status: allowed` data after a
temporary restart, then restores the exact safe data and explicitly reloads
HASC.

## Result

Kimi session `ses_097f467d0ffeHa7ulS02SMcF2f` using
`kimi-for-coding/k2p7` returned **NO FINDINGS**.

It confirmed that the saved repair runs while all home-summary readers fail,
so it cannot silently read the home or start HASC. Only the following explicit
reload may restore HASC, and it must restore the exact safe data and options,
`direct_execution_blocked`, one loaded entry, the same nine count sensors,
fixed diagnostics, and the guarded GET-only local page. No HASC service or
device is created.

## Evidence

- 116 local synthetic and boundary checks passed.
- The disposable empty Home Assistant check passed with 2026.6.4 and
  2026.7.0.
- The repair requires exactly one explicit reload after the saved correction.
  It keeps the user activation choice, restores the direct-execution block,
  and checks the authenticated local page before removal.
- Removal closes the retained page and diagnostics, then a fresh empty restart
  proves the temporary HASC entry remains absent while the unrelated temporary
  collision record is unchanged.

The review and checks use only repository files, synthetic fixtures, and
temporary empty Home Assistant configurations. They do not connect to a real
home, Node-RED, devices, services, or a remote Home Assistant API.
