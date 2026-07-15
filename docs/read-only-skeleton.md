# Read-only integration skeleton

Created on 2026-07-13 after explicit owner approval for the safe skeleton.
The original private-first choice is preserved in [the historical skeleton
decision](read-only-skeleton-decision.md). The current public, manual-HACS
installation rule is recorded in [the HACS packaging
decision](hacs-packaging-decision.md).

## What it does

The `custom_components/hausman_hub/` package provides only a small Home
Assistant-facing shell around framework-independent safety rules:

- a single config entry with a selector for `read-only` or `shadow`;
- an options flow that can change only between those same two modes;
- a diagnostics snapshot assembled from a strict allow-list, rather than from
  config-entry data;
- fixed manual guidance texts for review; they do not create issues or make
  changes.
- an original local brand icon for Home Assistant's interface; it is only an
  image and does not add a runtime capability.
- exactly nine diagnostic number sensors that show the already-approved
  aggregate summary. They share one local snapshot, do not count themselves,
  have no action, and expose no source name, identifier, reading, or history.
- one authenticated, local-network, GET-only view for the already-approved
  nine-count summary. It requires Home Assistant's exact built-in read-only
  group and has no command method or outgoing connection.

The inner `domain/` and `application/` layers use standard Python only. The
Home Assistant modules are thin adapters at the outer boundary.

## What it deliberately does not do

- It does not list, select, store, or expose real areas, devices, or entities.
- It does not use Home Assistant services, Node-RED, external APIs, or device
  commands.
- It does not create devices, buttons, control entities, `services.yaml`,
  repairs issues, or automatic fixes. Its only entities are the nine approved
  diagnostic count sensors.
- Its small `hacs.json` supports manual HACS installation from this public
  repository. It does not add the integration to the public HACS catalog or
  change its runtime behavior.
- `proxy` is absent and direct execution is always
  `direct_execution_blocked`.

## Local verification

Run the existing local test suite:

```sh
python3 -m unittest discover -s tests -v
```

This checks the pure safety rules, manifest, translations, diagnostics
allow-list, and absence of execution surfaces. It does not load Home Assistant
or access a live home.

The suite also includes an in-memory adapter test for config and options flow.
It supplies only the small Home Assistant form API surface used by this package
and checks the safe paths and the rejected `proxy` path. It is not a Home
Assistant runtime test and does not claim runtime compatibility.

Home Assistant Core 2026.6.4 requires Python 3.14.2 or newer, while this local
project environment uses Python 3.12. A real Core compatibility run therefore
remains a separate task in an isolated Python 3.14 environment, still without
a live home or device access.

Use the explicit smoke check only from such an isolated environment:

```sh
uv venv --python 3.14 /tmp/hasc-core
uv pip install --python /tmp/hasc-core/bin/python homeassistant==2026.6.4
/tmp/hasc-core/bin/python tools/check_home_assistant_core.py
```

The script creates a temporary empty Home Assistant configuration, copies the
local integration into it, and removes the temporary configuration afterwards.
It checks both approved initial modes, a safe change between those modes, a
real reload, the fixed redacted diagnostics report, clean removal, exactly nine
HASC diagnostic count sensors, and the absence of HASC services or devices.
It also starts a temporary loopback-only
Home Assistant server to prove that the local nine-count page rejects an
unsigned request and an administrator, accepts only the temporary read-only
test account, and rejects POST. An attempt to submit `proxy` through options
is required to be rejected before it can persist anything. It does not read any
real Home Assistant configuration, credentials, entities, or devices.

The same empty test removes one safe HASC setup completely and then creates a
new one in the other safe mode. This confirms that removing and installing
HASC again does not leave an old HASC object, service, or setting behind.
Before changing the first safe setup, the test also tries to create a second
one. Home Assistant must refuse it, keep the first setup unchanged, and retain
exactly its nine count sensors.

Before that removal, the test saves one safe HASC setup, stops the empty test
system, replaces its local HASC copy, and starts the empty system again. The
same safe choice must be present after the restart, HASC must retain exactly
its nine diagnostic count sensors and no service, and direct execution must
still be blocked. This checks
the safe persistence path for an HASC update without touching a real home.

The empty check also reserves one HASC-like internal sensor name before a new
safe setup. HASC must still create all nine count sensors under distinct,
HASC-prefixed names. This protects a new installation from being blocked by a
name that was already in use, without reading a real home.

For a manual check of an installed copy, see the Russian
[safe-check guide](home-assistant-safe-check.md). It asks only for Home
Assistant screens and explicitly excludes diagnostics archives and home data.
