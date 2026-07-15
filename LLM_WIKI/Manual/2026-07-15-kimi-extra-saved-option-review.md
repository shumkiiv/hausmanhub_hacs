# Kimi review: extra saved mode field

Date: 2026-07-15.

## Scope

Only the disposable Home Assistant Core lifecycle check, its structural local
test, and its documentation changed. HASC runtime code, the fixed nine-count
boundary, and every execution boundary remain unchanged.

## What changed

The repeated temporary lifecycle for a bad saved mode choice now has one shared
helper and covers two separate invalid values:

1. a forbidden `proxy` choice;
2. an otherwise approved `shadow` choice with one extra synthetic
   `unmodelled` field.

For each value, the empty temporary Core must reject reload, remain closed
after restart, preserve the unrelated temporary external record, and show no
count state, page, device, service, or runtime data. Only restoring the exact
original approved choice can bring back the same nine count sensors, fixed
diagnostics, and authenticated GET-only page. The corrected setup must survive
its own empty restart before removal and one final empty absence check.

## Review outcome

Direct Kimi session `ses_099e5ad59ffeSYKw1XPbGWRGio`
(`kimi-for-coding/k2p7`) returned `NO FINDINGS`.

It confirmed the shared helper avoids copying the lifecycle, preserves the
exact nine-count boundary, keeps the collision fixture unchanged, and retains
the no-device, no-service, authenticated GET-only, and final-cleanup limits.

## Verification

- `python3 -m unittest discover -s tests -v` — 81 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, credential, live home connection, or
home data was used.
