# Kimi review: no-device runtime check

Date: 2026-07-15.

## Scope

Only local tests, the isolated Home Assistant Core smoke check, and its
documentation changed. HASC itself did not change. The Core check starts an
empty temporary Home Assistant configuration and removes it afterward.

## What changed

For every safe HASC setup, the isolated check now requires:

- no device that belongs to the HASC setup;
- exactly nine HASC count sensors; and
- no device attachment on any of those nine sensors.

This protects the promise that HASC shows nine general numbers only, rather
than creating a new device in Home Assistant.

## Review outcome

Kimi found no issues. It confirmed that the Home Assistant registry calls are
compatible with Core 2026.6.4 and 2026.7.0, and that the change introduces no
new HASC capability, home data, control, or real Home Assistant connection.

## Verification

- `python3 -m unittest discover -s tests -v` — 64 passed.
- `python3 tools/check_local_release.py` — passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.
