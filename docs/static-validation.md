# Static validation scope

The files under `fixtures/`, `hasc_validation/`, and `tests/` are a local,
synthetic schema-check harness. They are not a Home Assistant integration,
runtime adapter, proxy, or deployment tool.

## Inputs

- Common inventory fixtures model rooms, devices, capabilities, safety classes,
  descriptors, contour membership, and read-only audit facts.
- Shadow fixtures model only redacted comparison evidence, policy placeholders,
  unresolved parity, and owner-review state.
- Diagnostics fixtures model only redacted snapshot sections and manual-only
  repair issue summaries.

All fixture identifiers are opaque synthetic labels. Fixtures must not contain
secrets, live identifiers, service paths, Node-RED flows, payloads, commands,
or deployment data.

The schema-specific boundaries are in the
[shadow-evidence contract](shadow-evidence-contract.md) and
[diagnostics/repairs contract](diagnostics-repairs-contract.md).

## Run locally

```sh
python3 -m unittest discover -s tests -v
python3 tools/validate_fixture.py common fixtures/common_contract/valid_minimal.json
python3 tools/validate_fixture.py shadow fixtures/shadow_evidence/valid_unresolved.json
python3 tools/validate_fixture.py diagnostics fixtures/diagnostics/valid_redacted.json
```

A pass means only that synthetic data satisfies these static invariants. It
does not prove shadow parity, grant proxy approval, or permit direct execution.
