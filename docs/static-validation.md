# Static validation scope

The files under `fixtures/`, `hasc_validation/`, and `tests/` are a local,
synthetic schema-check harness. They are not a Home Assistant integration,
runtime adapter, proxy, or deployment tool.

## Inputs

- Common inventory fixtures model rooms, devices, capabilities, safety classes,
  descriptors, contour membership, and read-only audit facts.
- Shadow fixtures model only redacted comparison evidence, policy placeholders,
  unresolved parity, and owner-review state.
- Diagnostics fixtures model only redacted snapshot sections, manual-only
  repair issue summaries, and the fixed nine-number aggregate home summary.
  They never model names, identifiers, readings, or history.

All fixture identifiers are opaque synthetic labels. Fixtures must not contain
secrets, live identifiers, service paths, Node-RED flows, payloads, commands,
or deployment data.

The schema-specific boundaries are in the
[shadow-evidence contract](shadow-evidence-contract.md) and
[diagnostics/repairs contract](diagnostics-repairs-contract.md).

## Run locally

After preparing files for the next commit, the shortest complete local check
is:

```sh
python3 tools/check_local_release.py
```

It runs the same fixed local checks below: tests, the three synthetic fixture
checks, a local Git-only version check for HACS-visible changes, a prepared
HACS-package check, and the safety checks for published and prepared files.
The package check makes sure the fixed installation surface is complete:
metadata, the integration entry files, both translations, the local icon,
license, release notes, and the approved manifest shape. It reads Git-index
blobs and modes only. It does not start Home Assistant or contact a home. An
automatic test locks down that command list: it must not contain a network
address, Home Assistant, `curl`, or `wget`, and every named Python file must
exist inside this repository.

The individual commands remain available when one result needs closer review:

```sh
python3 -m unittest discover -s tests -v
python3 tools/validate_fixture.py common fixtures/common_contract/valid_minimal.json
python3 tools/validate_fixture.py shadow fixtures/shadow_evidence/valid_unresolved.json
python3 tools/validate_fixture.py diagnostics fixtures/diagnostics/valid_redacted.json
python3 tools/check_staged_release_version.py
python3 tools/check_hacs_package.py
python3 tools/check_repository_boundary.py
python3 tools/check_repository_boundary.py --staged
```

A pass means only that synthetic data satisfies these static invariants. It
does not prove shadow parity, grant proxy approval, or permit direct execution.
