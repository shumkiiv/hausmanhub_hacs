# Kimi review: one HASC setup check

Date: 2026-07-15.

## Scope

Only the isolated Home Assistant Core check, its local source guard, and its
documentation changed. The HASC integration did not change. The check uses an
empty temporary Home Assistant configuration only.

## What changed

After the first safe HASC setup, the test tries a second safe setup. Home
Assistant must refuse it because HASC allows only one setup. The check then
requires the first setup to remain loaded and unchanged, with exactly its nine
approved count sensors.

## Review outcome

Kimi found no issues. It confirmed that `single_instance_allowed` is the
expected Home Assistant result when a manifest permits only one setup. The
change adds no HASC capability, no home data, no control, and no real Home
Assistant connection.

## Verification

- `python3 -m unittest discover -s tests -v` — 65 passed.
- `python3 tools/check_local_release.py` — passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.
