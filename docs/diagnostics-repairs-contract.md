# Synthetic diagnostics and repairs contract

This contract models only a redacted diagnostics export and manual-only repair
issue summary. It is not `diagnostics.py`, `repairs.py`, a Home Assistant
config entry, or an issue-registry implementation.

## Safe diagnostics sections

The fixture model includes entry summary, selected redacted references, Common
mapping, owner contours, the safety model, unresolved shadow parity, repair
summary, and a redaction report. `direct_execution_status` is always
`direct_execution_blocked`.

Secrets, credentials, raw identifiers, internal routes, payloads, physical
commands, flow data, and execution facts are rejected before a fixture can be
treated as valid diagnostics data.

## Manual-only repairs

The allowed categories and fixed severities are:

| Category | Severity |
| --- | --- |
| `missing_references` | `warning` |
| `unsafe_mode` | `error` |
| `unresolved_owner_contour` | `error` |
| `stale_parity` | `warning` |
| `redaction_failure` | `critical` |

The only lifecycle values are `open`, `visible`, `acknowledged`,
`resolved_manually`, `dismissed`, and `reopened`. Guidance is a status for a
manual review, never an action or auto-fix. A blocked redaction report requires
a visible critical `redaction_failure` issue.
