# Home Assistant Core 2026.6.4 compatibility

Date: 2026-07-14.

## Reason

The owner's installed Home Assistant version is Core 2026.6.4. The previous
HACS minimum of 2026.7.0 blocked installation even though the integration has
no feature tied to that newer release.

## Local evidence

In a disposable Python 3.14.3 environment, the repository's real-Core
lifecycle check passed with `homeassistant==2026.6.4`:

```sh
/tmp/hasc-core-2026.6.4/bin/python tools/check_home_assistant_core.py
```

The check used an empty temporary Home Assistant configuration. It did not
read or call a live Home Assistant, Node-RED, device, service, or external API.

## Approved narrow change

Lower only the stated HACS and documentation baseline to Core 2026.6.4. Keep
the same read-only and shadow modes, blocked direct execution, and all other
safety boundaries.
