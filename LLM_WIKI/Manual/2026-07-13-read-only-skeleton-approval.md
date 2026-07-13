# Read-only skeleton approval

Date: 2026-07-13.

The owner explicitly approved option 2 from the read-only skeleton decision:
create a private `custom_components/hausman_hub/` skeleton.

The approved scope is deliberately narrow: config/options flow only for
`read-only` and `shadow`, allow-list diagnostics, and manual-only repair
guidance. It must not store live identifiers, call Home Assistant services,
touch Node-RED, create device entities, use HACS metadata, enable proxy, or
change direct execution from `direct_execution_blocked`.

The implementation follows the recorded Clean Architecture rule: pure domain
and application layers sit inside a thin Home Assistant adapter. Local tests
and a Kimi review of the final diff remain required before commit and push.
