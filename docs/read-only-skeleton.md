# Read-only integration skeleton

Created on 2026-07-13 after explicit owner approval for the safe skeleton
described in [the decision record](read-only-skeleton-decision.md).

## What it does

The `custom_components/hausman_hub/` package provides only a small Home
Assistant-facing shell around framework-independent safety rules:

- a single config entry with a selector for `read-only` or `shadow`;
- an options flow that can change only between those same two modes;
- a diagnostics snapshot assembled from a strict allow-list, rather than from
  config-entry data;
- fixed manual guidance texts for review; they do not create issues or make
  changes.

The inner `domain/` and `application/` layers use standard Python only. The
Home Assistant modules are thin adapters at the outer boundary.

## What it deliberately does not do

- It does not list, select, store, or expose real areas, devices, or entities.
- It does not use Home Assistant services, Node-RED, external APIs, or device
  commands.
- It does not create entities, platforms, `services.yaml`, repairs issues, or
  automatic fixes.
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

Home Assistant Core 2026.7.0 requires Python 3.14.2 or newer, while this local
project environment uses Python 3.12. A real Core compatibility run therefore
remains a separate task in an isolated Python 3.14 environment, still without
a live home or device access.

Use the explicit smoke check only from such an isolated environment:

```sh
uv venv --python 3.14 /tmp/hasc-core
uv pip install --python /tmp/hasc-core/bin/python homeassistant==2026.7.0
/tmp/hasc-core/bin/python tools/check_home_assistant_core.py
```

The script creates a temporary empty Home Assistant configuration, copies the
local integration into it, and removes the temporary configuration afterwards.
It checks both approved initial modes, a safe change between those modes, a
real reload, clean removal, and the absence of HASC services and entities. An
attempt to submit `proxy` through options is required to be rejected before it
can persist anything. It does not read any real Home Assistant configuration,
credentials, entities, or devices.
