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
- The `custom_components/hausman_hub/` read-only skeleton is limited to
  `read-only` and `shadow`.
- It has no service, entity, device, Node-RED, or execution surface.
- Proxy and direct execution remain unapproved and blocked.

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
- It does not expand the approved modes, add proxy, or lift
  `direct_execution_blocked`.
- Repository verification remains local and read-only, with no live Home
  Assistant, Node-RED, device, or external API calls.
- The metadata change received Kimi review before commit and push.

## Implementation boundary after approval

The implementation is one isolated metadata-only change. It must not modify
the HASC integration's runtime behavior, alter Climate, Automation, Common,
or Smart Home Center policy, or be combined with another feature. Before it
is committed, re-run the local tests, inspect staged files for prohibited
data, record the Kimi review, and push the dedicated commit only after all
checks pass.

## Explicitly still out of scope

- Public release, marketplace listing, or support promise.
- Proxy approval, rollback procedure, or live source connection.
- Shadow-parity acceptance, canary, authority transfer, or direct execution.
