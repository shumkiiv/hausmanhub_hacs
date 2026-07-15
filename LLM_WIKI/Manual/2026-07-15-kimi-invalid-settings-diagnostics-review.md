# Kimi review: diagnostics after invalid saved settings

Date: 2026-07-15.

## Scope

The isolated Home Assistant Core check now asks for diagnostics immediately
after it saves deliberately invalid HASC main settings or a deliberately
invalid saved mode choice, and before it explicitly reloads HASC. The entry is
still loaded at this point, but its effective configuration is unsafe.

The check covers five invalid main-settings variants and two invalid
mode-choice variants. It temporarily replaces the local home-summary reader
with a function that fails if anything tries to read the home, then requires
the fixed unavailable diagnostic answer.

## Final result

Kimi session `ses_098e2ca5effewpvJEjH1M3wDe7` (review agent `k2p7`) returned
`NO FINDINGS`.

The review confirmed the two new checks run after their settings are saved and
before an explicit reload. In Core 2026.6.4 and 2026.7.0, updating the saved
entry does not unload it here, and HASC has no update listener, so diagnostics
uses the invalid-configuration safety gate rather than only the unloaded gate.

## Verification

- `python3 -m unittest discover -s tests -v` — 105 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

Every check used disposable empty configurations or synthetic data. No real
Home Assistant, Node-RED, device, credential, or home data was accessed.
