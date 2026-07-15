# Kimi review: unload state cleanup

Date: 2026-07-15.

## Scope

Version 0.3.5 closes a display-cleanup gap found in the disposable Core check.
After HASC was turned off, its nine registry entries were disabled but their
old aggregate state values remained in memory. A successful HASC unload now
clears only the state values belonging to that same HASC setup.

## What the check proves

The updated checks show that HASC:

- removes its nine current state values immediately after a successful user
  deactivation;
- retains its disabled registry entries, so it does not delete registry data
  while clearing displayed values;
- does not change the unrelated synthetic external state or registry entry;
- restores only the same nine aggregate counts after activation; and
- adds no device, service, proxy, command, or real-home access.

## Review outcome

Kimi session `ses_0998ece1dffeLPhwrXAMZog4IQ` (model `k2p7`) returned
`NO FINDINGS` for the runtime change. Its follow-up review also returned
`NO FINDINGS` after the test was strengthened to prove that the HASC registry
entry is retained and no registry record is removed.

## Verification

- `python3 -m compileall -q custom_components tools tests` — passed.
- `python3 -m unittest discover -s tests -v` — 84 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, credential, or home data was used.
