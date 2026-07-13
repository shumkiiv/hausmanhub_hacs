# Decision record: private read-only integration skeleton

## Status

Pending explicit owner approval. This document creates no integration code,
`custom_components/`, manifest, config flow, HACS metadata, runtime access, or
Home Assistant service call.

## Decision to make

Choose one of the following paths for `hausman_hub`:

1. **Defer the skeleton.** Keep the repository limited to synthetic contract,
   shadow-evidence, and diagnostics schema validation.
2. **Approve a private read-only skeleton.** Add the smallest possible
   Home Assistant-facing package only to host safe config/options flow,
   selectors, redacted diagnostics, and manual-only repairs in `read-only` and
   `shadow` modes.

## Preconditions already met

- Repository basics, synthetic contracts, local validation, and redacted
  diagnostics fixtures are in place.
- The static harness received a Kimi review and one remediation iteration.
- No runtime integration, HACS metadata, proxy, or direct execution exists.

## Required approval details

An approval for option 2 must confirm all of the following:

- The skeleton remains private and does not add `hacs.json`.
- The initial modes are only `read-only` and `shadow`; `proxy` is excluded.
- `direct_execution_status` remains `direct_execution_blocked`.
- The integration has no device authority and does not call Home Assistant
  services, Node-RED, physical devices, or external APIs.
- Config/options choices map only redacted/safe references to the Common
  contract and never change Climate, Automation, Common, or Smart Home Center
  policy.
- Verification is local and synthetic only until another owner decision.

## If option 2 is approved

The implementation must use Clean Architecture:

- framework-independent domain contracts and validation at the center;
- application use cases for read-only mapping and shadow projection;
- a thin Home Assistant adapter at the outer boundary;
- no owner-policy logic in adapters, config flow, diagnostics, or repairs;
- local tests and Kimi review of the final diff before every push.

The resulting skeleton must be a separate commit from all existing static
validation work. It must not change the decision gates for HACS metadata,
proxy, canary, rollback, or direct execution.

## Explicitly not decided here

- Public release or HACS metadata.
- Proxy approval or rollback notes.
- Shadow parity acceptance thresholds or owner signoff.
- Canary, emergency stop, authority transfer, or direct execution.
