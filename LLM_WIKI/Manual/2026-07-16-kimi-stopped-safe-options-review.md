# Kimi review: stopped safe options

Date: 2026-07-16.

## Scope

Independent read-only review of the disposable Home Assistant Core check for
changing HASC from `read-only` to `shadow` while HASC is ordinarily stopped
but still enabled for a later explicit start.

## Result

Kimi session `ses_09831b38effe4RTIpR9gwBA7BZ` using
`kimi-for-coding/k2p7` returned **NO FINDINGS**.

It confirmed that the new check records no HASC reload, makes every HASC
home-summary reader fail if called, keeps the saved option shape fixed, keeps
direct execution blocked, and leaves the stopped HASC entry user-enabled.
The nine count values, diagnostics, and local page remain closed until an
explicit start. That start restores the existing nine count sensors and the
safe `shadow` mode only.

## Evidence

- 115 local synthetic and boundary checks passed.
- The disposable empty Home Assistant Core check passed with 2026.6.4 and
  2026.7.0.
- During the stopped save, the Core check requires zero calls to HASC reload
  and a `NOT_LOADED` HASC entry.
- No integration runtime file, HACS metadata, device command, service,
  proxy, direct execution path, secret, live identifier, or real-home access
  was added.

The review and checks use only repository files, synthetic fixtures, and
temporary empty Home Assistant configurations. They do not connect to a real
home, Node-RED, devices, services, or a remote Home Assistant API.
