# Kimi review: historical skeleton decision correction

Date: 2026-07-15.

## Scope

Only documentation and its local guard changed. The old private-first skeleton
decision is now clearly historical and links to the current public manual-HACS
decision. The packaging decision now correctly says that HASC has exactly nine
diagnostic count sensors, while still having no device, command, or control
surface.

## Review outcome

Kimi's first read-only review found one minor issue: the new document guard was
too dependent on exact wording. The guard was changed to check the current
public/manual-HACS facts, the cross-links between the documents, and the
absence of old active installation instructions in current documents.

Kimi's final read-only review returned `NO FINDINGS`.

## Verification

- `python3 -m unittest discover -s tests -v` — 66 passed.
- `python3 tools/check_local_release.py` — passed.

No Home Assistant, Node-RED, device, or live home connection was used.
