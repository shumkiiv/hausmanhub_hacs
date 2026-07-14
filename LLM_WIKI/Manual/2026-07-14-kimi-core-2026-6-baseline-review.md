# Kimi review: Core 2026.6.4 baseline

Date: 2026-07-14.

## Scope

Read-only review of the change that lowers the HACS and documented minimum
from Home Assistant Core 2026.7.0 to 2026.6.4 after an isolated real-Core
lifecycle check passed on both 2026.6.4 and 2026.7.0.

## Final Kimi result

- Blocking findings: none.
- Non-blocking finding: the review prompt said two tests were renamed, while
  this staged diff renames one. The other rename was already committed in
  `b344743`; the current test behavior is unchanged. No source change is
  needed.

Kimi confirmed that `hacs.json` and its exact-shape test both use `2026.6.4`,
the safe-mode and execution boundaries remain unchanged, and no sensitive or
live-runtime surface was added.

Review session: `ses_0a10fded5ffeYrNlyrL9QKnPZ8`. The reviewer did not modify
repository files.

## Verification

- `python3 -m compileall -q custom_components hasc_validation tools tests`
- `python3 -m unittest discover -s tests -v` — 24 passed
- `python3 -m json.tool hacs.json`
- isolated Core lifecycle check passed on 2026.6.4 and 2026.7.0
- `git diff --cached --check`
