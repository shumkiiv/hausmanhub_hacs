# Kimi review: unsafe proxy-data repair

Date: 2026-07-16.

## Scope

The disposable Home Assistant lifecycle now separately covers a
user-disabled HASC entry whose saved main data requests the prohibited
`proxy` mode. Explicit activation must fail closed. A manual exact repair must
remain closed until one explicit reload restores only the approved nine-count
surface.

## Independent review

Kimi session `ses_097aa895affe3KxlEsre7vKGc9` using
`kimi-for-coding/k2p7` returned **NO FINDINGS**.

It confirmed that:

- the existing generic lifecycle helper separately covers proxy in main data,
  rather than the already covered options value;
- the check does not enable proxy, a service, a device, or home control;
- repair blocks home-summary reads until the explicit reload;
- the helper retains the nine counts, blocked direct execution, guarded
  diagnostics and local page, collision protection, and final removal with
  restart;
- the static scenario count and marker changed only for this case.

## Local evidence

- `python3 -m py_compile tools/check_home_assistant_core.py tests/test_read_only_skeleton.py`
- `python3 -m unittest discover -s tests -v` — 116 tests passed.
- temporary empty Home Assistant Core 2026.6.4 and 2026.7.0 checks passed.

No live Home Assistant, device, Node-RED, service, or remote API was used.
