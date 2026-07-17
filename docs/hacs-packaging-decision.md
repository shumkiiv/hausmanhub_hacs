# Decision record: HACS metadata and distribution

## Status

The first private-HACS decision proved infeasible: HACS cannot use private
GitHub repositories. On 2026-07-14 the owner explicitly approved a public
repository for manual HACS installation. The repository is not submitted to
the public HACS catalog.

## Facts already fixed

- The repository is public: `shumkiiv/hausmanhub_hasc`.
- The license is MIT and the supported baseline is Home Assistant Core 2026.6.4
  or newer.
- The `custom_components/hausman_hub/` observation modes remain limited to
  `read-only` and `shadow`.
- The nine approved diagnostic count sensors remain the default HASC entity
  surface.
- Version 0.4.0 adds one separately approved, off-by-default HASC switch for
  one selected `input_boolean`. It registers no HASC service, creates no
  device, and has no Node-RED, external, or physical-device execution path.
- Proxy and general device execution remain unapproved and blocked.

## Previous options

These were the original choices:

1. **Keep HACS metadata absent.** Continue private development and local
   verification without HACS distribution.
2. **Approve private HACS testing metadata.** This option is not available:
   HACS cannot use private GitHub repositories.
3. **Prepare a public distribution decision.** Do not publish anything yet;
   first define release, support, disclosure, and maintenance requirements in
   a separate owner decision.

## Chosen path

The owner approved a public repository that may be added manually in HACS as
an **Integration** custom repository. The implementation contains only this
root-level metadata:

```json
{
  "name": "HausMan Hub HASC",
  "homeassistant": "2026.6.4"
}
```

This decision does not install it into a live Home Assistant instance and does
not add it to the public HACS catalog.

## Requirements met for the chosen path

- The owner explicitly approved changing the GitHub repository to public.
- The public repository is added manually in HACS, not to its public catalog.
- The metadata contains no credentials, live identifiers, service paths,
  command payloads, deployment scripts, or runtime configuration.
- It does not expand the approved observation modes, add proxy, or lift
  general `direct_execution_blocked`. The helper-only canary is governed by
  its separate [control contract](canary-input-boolean-control.md).
- Repository verification remains local and read-only, with no live Home
  Assistant, Node-RED, device, or external API calls.
- The metadata change received Kimi review before commit and push.

## Implementation boundary after approval

This paragraph records the original 2026-07-14 metadata change. That
implementation was one isolated metadata-only change. It did not modify
the HASC integration's runtime behavior, alter Climate, Automation, Common,
or Smart Home Center policy. Later approved runtime versions, including the
0.4.0 helper canary, keep their own decision and review records.

## Explicitly still out of scope

- Public HACS catalog listing or support promise.
- Proxy approval, rollback procedure, or live source connection.
- Shadow-parity acceptance, physical-device canary, authority transfer, or
  general device execution.
