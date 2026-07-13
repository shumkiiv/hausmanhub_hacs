# Synthetic shadow-evidence contract

This is a local static-fixture contract, not a claim that shadow parity has
been proven.

## Required posture

- `mode` is `read-only` or `shadow`.
- `direct_execution_status` is always `direct_execution_blocked`.
- `parity_status` is always `unresolved` while acceptance thresholds and owner
  approval remain placeholders.
- References are opaque synthetic labels, never raw Home Assistant IDs.
- Projection summaries contain routing meaning only; they never contain
  commands, payloads, service paths, or execution facts.

## Evidence shape

A record contains a redacted snapshot, reference-to-Common mappings, owner
review state, comparison summaries, documented mismatch categories, freshness
and confidence placeholders, redaction status, unresolved gaps, and a
read-only audit summary.

Supported mismatch categories are `mapping_mismatch`, `owner_mismatch`,
`stale_data`, `safety_class_mismatch`, `projection_mismatch`, `redaction_gap`,
and `unresolved_gap`.

The validator rejects a parity pass claim, direct-execution status, service
paths, command-oriented fields, secrets, and sensitive-route fields. Passing
the validator is schema consistency only; it never grants proxy or direct
execution authority.
