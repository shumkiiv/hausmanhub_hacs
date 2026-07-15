# Kimi review: closed fresh-reinstall cycle

Date: 2026-07-15.

## Scope

Only the disposable empty Home Assistant Core check, its local source guards,
and its documentation changed. The integration package did not change.

## What changed

The fresh `read-only` HASC setup created after cleanup is now removed too. Its
retained local page must immediately return only an unavailable response with
no count keys, and the external temporary record must remain unchanged. A
fourth empty Home Assistant instance using the same temporary configuration
then requires HASC to remain fully absent.

## Review outcome

Kimi returned `NO FINDINGS`. It confirmed the lifecycle order, cleanup of
objects, states, and route, safe temporary read-only user handling, no count
value output, external-record preservation, and the unchanged no-control
boundary.

## Verification

- `python3 -m unittest discover -s tests -v` — 73 passed.
- `python3 tools/check_local_release.py` — passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, or live home connection was used.
