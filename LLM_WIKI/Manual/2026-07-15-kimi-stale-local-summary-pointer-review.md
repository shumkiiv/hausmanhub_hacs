# Kimi review: stale local-summary pointer closure

Date: 2026-07-15.

## Scope

Version 0.3.10 closes a rare transition where the local nine-count page still
has an old in-memory HASC pointer after the HASC setup has stopped. The page
now requires exactly one saved HASC setup and confirms that Home Assistant
still reports that setup as loaded before it can ask for the aggregate summary.

The unit test keeps the old pointer, removes the synthetic setup only from the
loaded list, replaces the reader with a failing function, and requires the
unavailable response. The disposable Core check repeats the same idea after a
real ordinary unload in an empty temporary Home Assistant.

## Final result

Kimi session `ses_098ab6b96ffeNJeXXgnt0NUmnm` (model `k2p7`) reviewed the
final runtime, test, version, and user-document changes and returned
`NO FINDINGS`.

The review confirmed that the page fails closed before a home read, returns
the current saved entry rather than a stale object, and adds no device,
service, proxy, direct execution, remote connection, or control path.

## Verification

- `python3 -m unittest discover -s tests -v` — 110 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

All checks used synthetic data or disposable empty configurations. No real
Home Assistant, Node-RED, device, credential, or home data was used.
