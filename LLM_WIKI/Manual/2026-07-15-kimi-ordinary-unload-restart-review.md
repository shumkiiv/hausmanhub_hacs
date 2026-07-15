# Kimi review: ordinary unload and full restart

Date: 2026-07-15.

## Scope

This change adds a disposable Home Assistant lifecycle check for a safe HASC
setup that is still enabled but has been stopped in the ordinary way. The empty
Home Assistant is then fully stopped and started again. HASC must return by
itself with the same safe setup.

## What the check proves

Before the temporary restart, the check confirms that the saved setup and its
nine enabled count records remain while current count values and the guarded
page fail closed. After the new empty Home Assistant starts, it requires the
same entry data and mode choice, exactly the same nine count sensors, fixed
redacted diagnostics, one authenticated GET-only page, no device or service,
and the unchanged direct-execution block. It separately retains the existing
proof that a user-deactivated HASC setup stays inactive across a restart.

## Review outcome

Kimi model `k2p7`, supervised through OpenCode in session
`ses_0995d6b03ffeAfuFbBc8SqIxDj`, returned `NO FINDINGS`.

The review checked the stop/restart order, the distinction from user
deactivation, the exact nine-count limit, the retained control prohibitions,
and the static test style. It made no file changes.

## Verification

- `python3 -m compileall -q custom_components tools tests` — passed.
- `python3 -m unittest discover -s tests -v` — 90 passed.
- `tools/check_home_assistant_core.py` — passed in disposable empty Core
  2026.6.4 and 2026.7.0 environments on Python 3.14.3.

No real Home Assistant, Node-RED, device, credential, home data, deploy, or
live API was used.
