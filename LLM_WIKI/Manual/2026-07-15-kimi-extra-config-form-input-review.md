# Kimi review: extra config-form input

Date: 2026-07-15.

## Scope

This test-only change gives both HASC setup screens an isolated check for
invented extra input. It does not change HASC runtime code, package metadata,
or the approved read-only boundary.

## What the check proves

The initial setup screen receives a safe mode together with an invented
execution marker and another invented field. It may save only the exact fixed
safe main setting. The mode-change screen receives the same input and may save
only the approved mode. The check uses local in-memory fakes only.

## Review outcome

Kimi session `ses_099839071ffe9HXVDzww4q3f4n` (model `k2p7`) returned
`NO FINDINGS`. It confirmed that the test matches the existing discard rule,
does not weaken runtime behaviour, and introduces no device control, service,
network, real Home Assistant, or home-data access.

## Verification

- `python3 -m compileall -q custom_components tools tests` — passed.
- `python3 -m unittest discover -s tests -v` — 86 passed.
- `python3 tools/check_local_release.py` — passed against the staged change.

The change does not alter the integration package or HACS metadata, so it does
not create a new HACS version. No real Home Assistant, Node-RED, device,
credential, or home data was used.
