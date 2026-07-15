# Kimi review: local summary closure after removal

Date: 2026-07-15.

## Scope

Only the disposable empty Home Assistant Core check, its local source guard,
and its documentation changed. The integration package did not change.

## What changed

The local nine-count route remains registered so a later safe HASC setup can
reuse it. After each HASC removal, the empty check now creates an exact
read-only temporary user, uses an authenticated loopback request, and requires
the route to return only an unavailable response. The response must not include
any of the nine approved count keys.

## Review outcome

Kimi returned `NO FINDINGS`. It confirmed the active-entry clear check, the
fail-closed HTTP check, temporary-user isolation, client cleanup, and the
unchanged no-control boundary.

## Verification

- `python3 -m unittest discover -s tests -v` — 69 passed.
- `python3 tools/check_local_release.py` — passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, or live home connection was used.
