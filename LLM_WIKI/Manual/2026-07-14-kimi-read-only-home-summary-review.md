# Kimi review: read-only aggregate home summary

Date: 2026-07-14.

## Scope

HASC 0.1.1 introduced a diagnostics-only `home_summary`. Version 0.1.2
extends the same approved boundary to nine aggregate counts: areas, devices,
entities, sensors, and five availability categories. A disabled registry entry
is counted separately before its state is read; `not_reported` now means an
enabled entry has no current state. The summary must not expose names,
identifiers, readings, history, network details, credentials, Home Assistant
services, Node-RED, proxy, or direct execution.

## Review sequence

1. Review session `ses_0a0237e6bffeVNZZs3GFmw7cR0` found one blocker: the first
   adapter version materialised a full `entity_id -> state` map. It also noted
   several small quality improvements.
2. The map was removed. The adapter now reads one entity state at a time and
   immediately converts it to `available`, `unavailable`, `unknown`, or
   `not_reported`. Tests added synthetic private-looking identifiers and a
   reading solely to prove that the result contains counts only.
3. Review session `ses_0a0182bb6ffelmFr4b76aYhPDW` confirmed the blocker was
   closed and reported no blockers. Its small follow-up notes were applied:
   empty state is unknown, empty registry coverage was added, counters use one
   pass, the static diagnostics fixture now models `home_summary`, and this
   context was updated.
4. The first final-review session became stuck without an answer and was not
   used as evidence. A fresh independent read-only review session
   `ses_0a00125a5ffe7RQGT5dXnRHTJC` checked the final diff and reported no
   blocking or non-blocking issues.
5. Version 0.1.2 added the separate disabled-entry aggregate. An independent
   read-only review found no code or safety defect; it identified only stale
   v0.1.1/eight-count wording in `AI_CONTEXT.md`. The wording was corrected
   before the final Kimi review and commit.
6. Final Kimi-backed review session `ses_09fd18e74ffeNgFi3adtWU6l3I` checked
   the complete v0.1.2 diff after the context correction. It reported no
   blockers, important issues, or minor issues.

## Final verdict

The reviewers found no identity or data-leak path and no execution surface.
Domain and application layers remain independent of Home Assistant; the outer
adapter only reads local registry/state information and passes already-reduced
categories inward. For a disabled entry it does not read the current state.
The final Kimi-backed review also confirmed the fixed nine-count diagnostics
shape and the updated project context.

## Checks before commit

- `python3 -m compileall -q custom_components hasc_validation tools tests`
- `python3 -m unittest discover -s tests -v` — 33 tests passed.
- JSON validation for `manifest.json` and `hacs.json`.
- `python3 tools/validate_fixture.py diagnostics fixtures/diagnostics/valid_redacted.json`.
- `tools/check_home_assistant_core.py` with isolated Core 2026.6.4 and
  2026.7.0 — both passed.
- `git diff --check` passed.

All checks used local synthetic or disposable configurations. No live Home
Assistant, Node-RED, service, device, proxy, or direct-execution action was
used.
