# Kimi review: invalid persisted mode option

Date: 2026-07-15.

## Scope

Only the disposable Home Assistant Core lifecycle check, its structural local
test, and its documentation changed. HASC runtime code, the fixed nine-count
boundary, and all execution boundaries remain unchanged.

## What changed

The disposable lifecycle now separately covers a bad saved mode choice in
HASC's options:

1. a temporary safe entry selects the approved `shadow` choice;
2. only its temporary saved option is replaced with `proxy`;
3. reload and a temporary restart must keep HASC closed;
4. restoring the original safe option must recover the same nine sensor names,
   fixed diagnostics, and authenticated GET-only page;
5. another empty restart must preserve that corrected option before removal,
   followed by one final absence check.

The shared invalid-entry assertion now also requires the saved option value to
remain exactly the value that was deliberately tested. The recovery helper uses
the saved safe option when it determines the expected diagnostic mode.

## Review outcome

Direct Kimi session `ses_099fbb843ffein724qmmFEcTzJ`
(`kimi-for-coding/k2p7`) returned `NO FINDINGS`.

It confirmed the Core API use, exact nine-count boundary, preserved collision
fixture, no-device/no-service boundary, authenticated GET-only page, rejected
POST, and cleanup through the final empty restart.

## Verification

- `python3 -m unittest discover -s tests -v` — 81 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, credential, live home connection, or
home data was used.
