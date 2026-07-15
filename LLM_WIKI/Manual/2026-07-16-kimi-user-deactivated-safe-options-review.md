# Kimi review: user-deactivated safe options

Date: 2026-07-16.

## Scope

Independent read-only review of the disposable Home Assistant Core check for
changing HASC from `shadow` to `read-only` while its user has deliberately
disabled it, before that user explicitly activates it again.

## Result

Kimi session `ses_09826c507ffebXjbkdEMS9Aoud` using
`kimi-for-coding/k2p7` returned **NO FINDINGS**.

It confirmed that the new check keeps HASC user-disabled and not loaded,
records no reload, and makes each HASC home-summary reader fail if called.
The nine count values, diagnostics, and local page remain closed until the
user explicitly activates HASC. That activation preserves the saved
`read-only` mode and restores only the existing nine count sensors.

## Evidence

- 115 local synthetic and boundary checks passed.
- The disposable empty Home Assistant Core check passed with 2026.6.4 and
  2026.7.0.
- During the user-disabled save, the Core check requires zero calls to HASC
  reload, a `NOT_LOADED` HASC entry, and `disabled_by=USER`.
- No integration runtime file, HACS metadata, device command, service,
  proxy, direct execution path, secret, live identifier, or real-home access
  was added.

The review and checks use only repository files, synthetic fixtures, and
temporary empty Home Assistant configurations. They do not connect to a real
home, Node-RED, devices, services, or a remote Home Assistant API.
