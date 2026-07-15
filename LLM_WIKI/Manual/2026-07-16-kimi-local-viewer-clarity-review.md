# Kimi review: local viewer wording

Date: 2026-07-16.

## Scope

Independent read-only review of two Russian user guides. The change explains
that HASC's ordinary nine count sensors and diagnostics work without a
separate user. A separate read-only user is optional and belongs only to a
person or program that chooses to view the extra local summary page.

## Result

Kimi session `ses_0986b7634ffe0CRQ4QmHmjsQiY` using
`kimi-for-coding/k2p7` returned **NO FINDINGS** after one wording clarification.

The final text accurately says that HASC does not receive or retain a password,
key, or Home Assistant connection address from that user. It also makes clear
that HASC checks the origin of an incoming request only to require the local
network and does not keep it. The review found no promise of new authority or
change to the read-only/shadow boundary.

## Evidence

- 112 local synthetic and boundary checks passed.
- The documentation states that the optional user is for a viewer, not for
  HASC, and that normal nine-count display and diagnostics need no separate
  user.
- The review cross-checked the wording against `local_summary.py` without
  connecting to a real Home Assistant.

The review and checks use only repository files. They do not connect to a real
home, Node-RED, devices, services, or remote Home Assistant API.
