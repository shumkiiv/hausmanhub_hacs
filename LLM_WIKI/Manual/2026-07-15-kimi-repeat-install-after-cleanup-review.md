# Kimi review: repeat install after collision cleanup

Date: 2026-07-15.

## Scope

Only the disposable empty Home Assistant Core check, its local source guard,
and its documentation changed. The integration package did not change.

## What changed

After a safe HASC setup is removed in the empty test system, the test creates
another safe `read-only` setup. It requires exactly nine HASC count sensors,
the unchanged external collision record, clean removal of the second setup,
and the same unchanged external record after that removal.

## Review outcome

Kimi returned `NO FINDINGS`. It confirmed that the scenario waits for setup and
removal, does not depend on a specific collision suffix, uses the existing
nine-count and external-record guards, and adds no HASC runtime capability,
home data, control, or network access.

## Verification

- `python3 -m unittest discover -s tests -v` — 68 passed.
- `python3 tools/check_local_release.py` — passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, or live home connection was used.
