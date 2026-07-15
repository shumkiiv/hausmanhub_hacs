# Kimi review: remove a disabled HASC setup after restart

Date: 2026-07-15.

## Scope

This change extends the disposable Home Assistant lifecycle check. A
collision-aware safe HASC reinstallation is disabled by the user, saved through
an empty temporary restart, and removed only by the following empty Home
Assistant instance.

## What the check proves

The restarted instance finds the same saved entry and the same nine disabled
temporary registry records, including the HASC name adjusted for the temporary
external collision. It has no count states, runtime data, or local page. The
test removes that preserved disabled entry, keeps the external temporary record
unchanged, and then uses one more empty restart to require HASC's complete
absence.

## Review outcome

Final direct Kimi model `k2p7` review session
`ses_09934c203ffeb6eGnRwmfWFYaq` returned `NO FINDINGS`.

The review confirmed the lifecycle order, dynamic handling of the nine
temporary names, preservation of the external collision fixture, the
formatting-independent static ordering check, and the unchanged read-only /
shadow boundary.

## Verification

- `python3 -m compileall -q custom_components tools tests` — passed.
- `python3 -m unittest discover -s tests -v` — 92 passed.
- `tools/check_home_assistant_core.py` — passed in disposable empty Core
  2026.6.4 and 2026.7.0 environments on Python 3.14.3.

No real Home Assistant, Node-RED, device, credential, home data, deploy, or
live API was used.
