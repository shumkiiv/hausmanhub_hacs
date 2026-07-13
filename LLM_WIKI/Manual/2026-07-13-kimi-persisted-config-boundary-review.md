# Kimi review: persisted configuration exact-key boundary

Date: 2026-07-13.

## Scope

Read-only review of the added test in `tests/test_read_only_skeleton.py`. The
test covers the framework-independent configuration boundary only; it does not
start Home Assistant or interact with a home, device, Node-RED, service, or
network API.

## Review and remediation

The initial review found no blocking issue. Its test-quality suggestions were
addressed before the final review:

- the name now says it covers representative extra top-level fields, rather
  than claiming an exhaustive field list;
- options explicitly reject `direct_execution_status` as an extra field;
- a case with multiple unexpected option fields is covered;
- the credential example remains covered by the existing options test, so it
  is not duplicated.

## Final Kimi result

The Kimi-backed final review reported:

- Blocking findings: none.
- Non-blocking findings: none.

The final review session was `ses_0a3a2b95cffeiRgdNgMdUzSree`. It did not
modify repository files.

## Verification

- `python3 -m compileall -q custom_components hasc_validation tools tests`
- `python3 -m unittest discover -s tests -v` — 22 passed
- isolated Home Assistant Core 2026.7.0 smoke check — passed
- `git diff --check`

The exact-key rule rejects unmodelled top-level entry-data and options fields;
it does not copy values into diagnostics or grant runtime authority.
