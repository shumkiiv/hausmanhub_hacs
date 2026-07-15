# Kimi review: missing execution block

Date: 2026-07-15.

## Scope

The disposable Core lifecycle gained one more invalid main saved setting. It
contains only `{"mode": "read-only"}` and therefore lacks the mandatory
saved block that keeps direct execution forbidden. This is a test and
documentation change only: HASC runtime code and its approved nine-count
surface did not change.

## What the check proves

For the missing-block case, the empty test configuration must show that HASC:

- closes immediately after reload;
- stays closed through a restart with no HASC state, record, device, service,
  runtime data, or local page;
- returns only the same nine aggregate counts after the exact safe setting is
  restored; and
- leaves the unrelated temporary external record unchanged throughout.

## Review outcome

Kimi session `ses_099a33962ffefX57ywxPl4nz76` (model `k2p7`) returned
`NO FINDINGS`. It confirmed that the missing mandatory block is rejected by
the existing fixed settings check, the lifecycle scenario runs in order, and
the check adds no home access, device control, service call, or execution path.

## Verification

- `python3 -m compileall -q custom_components tools tests` — passed.
- `python3 -m unittest discover -s tests -v` — 81 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, credential, or home data was used.
