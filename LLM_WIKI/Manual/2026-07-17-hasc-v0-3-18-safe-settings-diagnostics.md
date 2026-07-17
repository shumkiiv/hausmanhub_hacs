# HASC 0.3.18 safe settings diagnostics

Date: 2026-07-17.

## Pre-change checkpoint

Version 0.3.17 was complete locally but intentionally not committed or
published. Before starting 0.3.18, the tracked `git diff HEAD --binary` had
SHA-256 `17574ba17e187c8cdd1a5f90094900b4dcbd4c4abe762e923fff60d3e963d624`.
The index was empty. The existing untracked files were only the three manual
LLM Wiki review notes for the 0.3.15–0.3.17 work.

This logical checkpoint records the boundary without creating a commit,
staging files, pushing, releasing, deploying, or accessing a live Home
Assistant.

## Approved implementation scope

Version 0.3.18 may add only the already-validated effective HASC settings to
the existing redacted diagnostics `entry_summary`:

- the existing safe mode;
- whether the optional local nine-count page is enabled;
- the exact `5m`, `15m`, or `30m` summary refresh interval.

It must never copy raw entry data or options. An inactive, ambiguous, or
unsafe entry must retain the exact fixed unavailable diagnostics response and
must not read the home. The change may not add a count, home datum, entity,
route, service, device, command, proxy, execution path, automatic repair, or
authority. The nine sensors and the request-time local GET page remain
unchanged.

## Status

Implementation, tests, documentation, and disposable Core checks are complete.
The bounded Kimi `k2p7` review returned `NO FINDINGS`. No commit or publication
is authorized by this note.

## Implemented contract

`diagnostics_snapshot_for_configuration()` now adds only
`local_summary_enabled` and `summary_update_interval` beside the existing
validated `mode` and fixed `single_config_entry` value. The Home Assistant
adapter still validates the one loaded entry before collecting the aggregate
home summary. The fixed unavailable response remains exactly
`{"diagnostics_status": "unavailable"}`.

The unit suite checks the exact entry-summary key set, safe defaults, and
effective option values. The disposable Core harness derives its expected
diagnostics values from the saved safe options and explicitly checks the
legacy empty-options restart before any options are saved.

## Verification

- `python -m unittest discover -s tests -v`: 144 tests passed.
- `python tools/check_local_release.py`: passed.
- Home Assistant Core 2026.6.4 disposable empty-config check: passed.
- Home Assistant Core 2026.7.0 disposable empty-config check: passed.
- No live Home Assistant, home data, device, service, or command was used.

## Independent review

The first attempt to re-review the complete accumulated 0.3.15–0.3.18 working
tree reached the Kimi agent step limit without a verdict. Versions 0.3.15–0.3.17
already had their own completed Kimi reviews, so a fresh bounded Kimi review
was requested only for the 0.3.18 delta and its immediate
validated-configuration and Home Assistant diagnostics interfaces. Its Kimi
child payload was empty twice, and a fallback OpenCode review returned
`NO FINDINGS`. A first direct continuation was discarded because it had
resumed under GPT-5.6-Sol rather than Kimi. The completed Kimi child session
`ses_091268e8fffetxhY0JeyJn120Y` was then resumed with
`kimi-for-coding/k2p7` explicitly pinned and returned
`KIMI_REVIEW_RESULT: NO FINDINGS`. Every review was read-only and made no file,
live-home, commit, push, release, deployment, or publication change.
