# Kimi review: missing main mode

Date: 2026-07-15.

## Scope

Version 0.3.4 closes a saved-settings gap. Previously, main saved data with
only the direct-execution block could still load if the separate safe options
contained `{"mode": "shadow"}`. Main saved data now requires both its fixed
fields before options are considered. Empty options remain valid for a complete
safe main setting.

## What the check proves

The test coverage now shows that HASC:

- rejects the incomplete main setting even when its separate safe option says
  `shadow`;
- creates no HASC sensor or local page for that rejected setting and clears
  only its own old records;
- stays closed through a temporary empty restart with no HASC state, record,
  device, service, runtime data, or local page; and
- restores only the same nine aggregate counts after the exact main setting is
  returned while the safe `shadow` option remains in place.

## Review outcome

Kimi session `ses_099969147ffe5iDTrZSfViZJKz` (model `k2p7`) returned
`NO FINDINGS`. It confirmed the exact main-setting rule, the still-valid empty
options path, the safe `shadow` lifecycle coverage, the version update, and
the absence of any device control, service call, proxy, direct execution, or
real-home access.

## Verification

- `python3 -m compileall -q custom_components tools tests` — passed.
- `python3 -m unittest discover -s tests -v` — 83 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, credential, or home data was used.
