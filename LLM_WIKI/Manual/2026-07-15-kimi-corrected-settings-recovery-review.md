# Kimi review: recovery after corrected saved settings

Date: 2026-07-15.

## Scope

Only the isolated Home Assistant Core lifecycle check, its structural local
test, and its documentation changed. HASC runtime code, the nine-count data
boundary, and every execution boundary remain unchanged.

## What changed

The disposable lifecycle now proves the full safe-recovery sequence:

1. a temporary HASC entry receives deliberately unsafe saved data;
2. it fails closed through a temporary restart;
3. the same temporary entry receives its original approved data again and
   reloads safely;
4. one further empty temporary restart keeps that corrected entry installed
   and proves that its approved data and options, the same nine sensor names,
   fixed diagnostics, and authenticated GET-only page return safely;
5. the corrected entry is removed and one last empty restart proves that HASC
   has no remaining setup, state, device, service, runtime data, or page.

An earlier independent local audit found that the first version had no restart
between correction and removal. The current change adds that restart before
this final review.

## Review outcome

Direct Kimi session `ses_09a050a9effeu0eOEyWqjpAzzp`
(`kimi-for-coding/k2p7`) returned `NO FINDINGS`.

It confirmed that all deliberate changes occur only in the disposable
temporary configuration, that recovered data and options persist, and that the
same exact nine aggregate sensors remain the only HASC entities. It also
confirmed the collision fixture, no-device/no-service boundary, GET-only
authenticated page, and final removal checks.

## Verification

- `python3 -m unittest discover -s tests -v` — 80 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

No real Home Assistant, Node-RED, device, credential, live home connection, or
home data was used.
