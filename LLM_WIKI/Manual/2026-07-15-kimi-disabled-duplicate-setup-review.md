# Kimi review: duplicate setup while HASC is disabled after restart

Date: 2026-07-15.

## Scope

This change extends the disposable Home Assistant lifecycle check. A safe HASC
setup is deliberately disabled by the user, the empty test instance is
restarted, and the still-saved disabled setup is then protected from a second
setup attempt.

## What the check proves

Home Assistant keeps exactly one saved HASC setup. A rejected second setup
preserves its data, options, disabled state, and unloaded state. The existing
nine records stay disabled without count values or an active local page. Only
the normal explicit activation can restore the same nine safe counts,
diagnostics, and authenticated GET-only page.

## Review outcome

Final direct Kimi model `k2p7` review session
`ses_099408cc5ffeaETB7ntDzrRvbP` returned `NO FINDINGS`.

The review confirmed the disabled-state parameter, the order of the temporary
lifecycle, compatibility with Core 2026.6.4 and 2026.7.0, and the unchanged
read-only boundary.

## Verification

- `python3 -m compileall -q custom_components tools tests` — passed.
- `python3 -m unittest discover -s tests -v` — 92 passed.
- `tools/check_home_assistant_core.py` — passed in disposable empty Core
  2026.6.4 and 2026.7.0 environments on Python 3.14.3.

No real Home Assistant, Node-RED, device, credential, home data, deploy, or
live API was used.
