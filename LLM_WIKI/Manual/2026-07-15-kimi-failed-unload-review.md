# Kimi review: failed unload check

Date: 2026-07-15.

## Scope

This test-only change checks the other half of version 0.3.5's unload rule.
If Home Assistant reports that it could not unload HASC, HASC must not partly
clear its own displayed values or local page while the integration remains
loaded.

## What the check proves

The isolated local adapter test creates one safe HASC setup and one temporary
HASC-owned count state, then makes the platform unload return `False`. It
requires that the current count state, HASC registry entry, and authenticated
local page remain available. The separate synthetic external state remains
distinct throughout.

## Review outcome

Kimi session `ses_0998a6268ffeGjQVIbc3pPhvfL` (model `k2p7`) returned
`NO FINDINGS`. It confirmed that the configurable fake does not weaken the
existing successful-unload tests and introduces no device control, service,
network, or real-home access.

## Verification

- `python3 -m compileall -q custom_components tools tests` — passed.
- `python3 -m unittest discover -s tests -v` — 85 passed.
- `python3 tools/check_local_release.py` — passed.

The change does not alter the integration package or HACS metadata, so it does
not create a new HACS version. No real Home Assistant, Node-RED, device,
credential, or home data was used.
