# Kimi review: unsafe partial-repair lifecycle

Date: 2026-07-16.

## Scope

The disposable Home Assistant lifecycle now covers one meaningful combined
saved-settings failure: a user-disabled HASC entry attempts to unblock direct
execution in main data while its options request prohibited `proxy` mode.

After rejected user activation, correcting only the main data must not reload
or start HASC. The display, diagnostics, and local page stay closed. Only the
remaining safe options repair and one explicit reload restore the approved
nine-count surface.

## Review and correction

An independent read-only review found that the generic partial path could be
combined with an unsupported repeated-recovery sequence. The check now rejects
every repeated-recovery flag when partial repair is selected, rather than
silently attempting an incomplete later repair.

Kimi session `ses_0978d6954ffepGlzWrSJjFBLSO` using
`kimi-for-coding/k2p7` reviewed the corrected final diff and returned **NO
FINDINGS**. It confirmed that:

- the repeated-recovery guard is exact;
- single-error paths retain their prior behavior;
- both manual repair stages block home-summary reads;
- the final explicit reload retains nine counts, blocked direct execution,
  guarded diagnostics and local page, no service or device, collision
  protection, and final removal with restart;
- the combined case does not enable proxy, direct execution, or any control.

## Local evidence

- `python3 -m py_compile tools/check_home_assistant_core.py tests/test_read_only_skeleton.py`
- `python3 -m unittest discover -s tests -v` — 116 tests passed.
- temporary empty Home Assistant Core 2026.6.4 and 2026.7.0 checks passed.

No live Home Assistant, device, Node-RED, service, or remote API was used.
