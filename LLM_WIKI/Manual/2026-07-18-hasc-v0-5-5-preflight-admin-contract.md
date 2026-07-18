# HASC 0.5.5 local-admin canary preflight contract

Date: 2026-07-18.

## Decision

The canonical saved-room preflight may be exposed through one fixed local-admin
POST route so future operator tooling can consume exactly the same decision as
the Home Assistant options flow. This is a read-only observation surface, not
an authorization or activation endpoint.

## Boundary

- Route: `POST /api/hausman_hub/v1/admin/climate-canary-preflight`.
- Request: exactly one public HASC `room_id`; extra/private fields fail closed.
- Access: a non-system local administrator only. The ordinary tablet account
  is forbidden before runtime processing.
- Response: preflight contract v1 with only public room ID, coarse status,
  counts, fixed action labels, operation/rollback state, and freshness times.
- Freshness: `checked_at`, optional `state_generated_at` and
  `state_valid_until`, plus strict `state_fresh`. State is valid for at most
  five minutes with the existing one-minute future-skew tolerance. A stale or
  too-future result adds `preflight_state_not_fresh` and cannot be ready.
- The response schema fixes `activation.allowed=false` and
  `separate_authorization_required=true`.
- The route uses `Cache-Control: no-store`, does not save options/registry,
  does not enable canary, and never performs a command POST.

Two strict JSON Schemas and compatible fixtures ship in the HACS package for
the query and response. Private origin, source/entity IDs, backend payloads,
operation IDs, tokens, and real home data are excluded.

## Verification boundary

Pure tests cover ready and expired freshness, disabled zero-fetch behavior,
malformed evidence, schema compatibility, and a structurally forbidden
activation flag. Adapter and disposable Core checks cover exact POST routing,
local-admin/tablet separation, no-store, redaction, explicit timestamps, and
measured zero command POST in shadow. Physical canary activation remains a
separate owner-authorized phase.

The final staged package passed 226 local tests, the complete
release/file-safety checks, and disposable Home Assistant Core 2026.6.4 and
2026.7.0 lifecycles. Kimi `kimi-for-coding/k2p7` session
`ses_08b9a95d1ffe9AVm46wQzzPqZQ` completed a read-only review with PASS and no
substantial findings. Publication and disabled live deployment remain pending.
