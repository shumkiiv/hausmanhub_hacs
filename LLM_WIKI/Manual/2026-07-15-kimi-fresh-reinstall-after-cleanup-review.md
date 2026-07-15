# Kimi review: fresh reinstall after cleanup

Date: 2026-07-15.

## Scope

Only the disposable empty Home Assistant Core check, its local source guard,
and its documentation changed. The integration package did not change.

## What changed

After the third empty Home Assistant instance proves that removed HASC is
absent, the same instance creates a new `read-only` HASC setup. The test
requires a new internal entry identifier, exactly nine allowed count sensors,
the fixed safe diagnostics report, the unchanged external temporary record,
and the guarded authenticated local page. A distinct temporary user-name
prefix keeps this check separate from earlier disposable users.

## Review outcome

Kimi returned `NO FINDINGS`. It confirmed the absence-before-reinstall order,
the temporary-user separation, count-only validation without printing values,
external-record preservation, and the unchanged no-control boundary.

## Verification

- `python3 -m unittest discover -s tests -v` — 72 passed.
- `python3 tools/check_local_release.py` — passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, or live home connection was used.
