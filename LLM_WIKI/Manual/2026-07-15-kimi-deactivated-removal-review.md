# Kimi review: removal after deactivation

Date: 2026-07-15.

## Scope

Only the disposable empty Home Assistant Core lifecycle check, its local
source guard, and its documentation changed. The HASC integration package did
not change.

## What changed

One repeat installation in the empty Core check is now deactivated through the
ordinary Home Assistant user path before removal.

- While deactivated, exactly nine HASC count records must be disabled and the
  guarded local count page must answer only that it is unavailable.
- Removal must then clear HASC's own count records and temporary states, keep
  the local page unavailable, and leave the unrelated temporary external record
  unchanged.
- The existing empty restart check also includes this removed setup, so it must
  not return later.

## Review outcome

Kimi session `ses_09a41f468ffekZV3uIIYZm1rCO` returned `NO FINDINGS`. It found
no safety, correctness, or documentation issue. It specifically confirmed that
the change remains limited to a disposable empty Core and does not add a
runtime capability, real-home access, device control, service, proxy, or
execution surface.

## Verification

- `python3 -m unittest discover -s tests -v` — 75 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, credential, or live home connection
was used.
