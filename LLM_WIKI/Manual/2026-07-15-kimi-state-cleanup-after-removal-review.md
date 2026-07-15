# Kimi review: state cleanup after removal

Date: 2026-07-15.

## Scope

Only the disposable empty Home Assistant Core check, its local source guard,
and its documentation changed. The integration package did not change.

## What changed

Before each safe HASC removal, the empty check remembers only the internal
state names belonging to that HASC entry. After removal, it requires every one
of those states to be absent. It never reads or prints a count value.

## Review outcome

Kimi returned `NO FINDINGS`. It confirmed that the snapshot happens before
removal, all current removal paths use the same check, failures do not expose
state contents, and the no-control boundary is unchanged.

## Verification

- `python3 -m unittest discover -s tests -v` — 70 passed.
- `python3 tools/check_local_release.py` — passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, or live home connection was used.
