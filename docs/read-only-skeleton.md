# Private read-only skeleton

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
- It does not add `hacs.json` or otherwise make an HACS packaging decision.
- `proxy` is absent and direct execution is always
  `direct_execution_blocked`.

## Local verification

Run the existing local test suite:

```sh
python3 -m unittest discover -s tests -v
```

This checks the pure safety rules, manifest, translations, diagnostics
allow-list, and absence of execution surfaces. It does not load Home Assistant
or access a live home. A separate Home Assistant environment and an explicit
approval would be needed before compatibility testing in that runtime.
