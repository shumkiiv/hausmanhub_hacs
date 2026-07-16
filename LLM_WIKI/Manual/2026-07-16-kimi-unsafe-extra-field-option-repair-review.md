# Kimi review: unsafe extra-field option repair

Date: 2026-07-16.

## Scope

Independent read-only review of the disposable lifecycle where a user-disabled
HASC entry has a saved `shadow` option plus an unmodelled extra field, the
user's activation is rejected, and the exact safe options are repaired
manually.

## Result

Kimi session `ses_097c6e679ffe9K7Sj41CvC0U15` using
`kimi-for-coding/k2p7` returned **NO FINDINGS**.

It confirmed that the extra-field case uses the options repair branch rather
than the main-data or proxy path. All home-summary readers fail during the
saved correction, so only the one separate explicit reload may return exact
safe options, the direct-execution block, nine count sensors, fixed
diagnostics, and the guarded local page. No HASC service or device is created.

## Evidence

- 116 local synthetic and boundary checks passed.
- Disposable empty Home Assistant checks passed with 2026.6.4 and 2026.7.0.
- The static checks preserve the five invalid-main-data scenarios and increase
  only the user-activation scenario count from five to six.
- The existing collision, removal, and final absence restart checks remain
  part of the lifecycle.

The review and checks use only repository files, synthetic fixtures, and
temporary empty Home Assistant configurations. They do not connect to a real
home, Node-RED, devices, services, or a remote Home Assistant API.
