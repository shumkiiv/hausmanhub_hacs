# Kimi review: unsafe proxy-option repair

Date: 2026-07-16.

## Scope

Independent read-only review of the disposable lifecycle where a user-disabled
HASC entry has an unsafe saved `proxy` option, the user's activation is
rejected, and the exact safe options are then repaired manually.

## Result

Kimi session `ses_097cd5d5bffeoRXvJ4Qrc1ySoE` using
`kimi-for-coding/k2p7` returned **NO FINDINGS**.

It confirmed that this is the options branch of the existing safe-repair
helper: the repair changes options rather than main data, makes every
home-summary reader fail during the saved correction, and records exactly one
separate explicit reload. Only that reload may return exact safe data and
options, the direct-execution block, nine count sensors, fixed diagnostics,
and the guarded local page. No HASC service or device is created.

## Evidence

- 116 local synthetic and boundary checks passed.
- Disposable empty Home Assistant checks passed with 2026.6.4 and 2026.7.0.
- The additional lifecycle call uses the existing manual-repair path, then
  preserves the normal collision, removal, and final absence checks.
- The static check pins the proxy-option repair scenario and updates the
  expected generic lifecycle-call count from four to five.

The review and checks use only repository files, synthetic fixtures, and
temporary empty Home Assistant configurations. They do not connect to a real
home, Node-RED, devices, services, or a remote Home Assistant API.
