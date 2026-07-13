# Engineering standards

These standards apply to every future HASC code change.

## Clean Code

- Keep names explicit, behavior-focused, and free of hidden execution intent.
- Keep functions and modules focused on one responsibility; prefer small,
  testable units over implicit cross-layer coupling.
- Make invalid and unsafe states explicit in data and validation results.
- Add or update local tests with every behavior change.
- Do not hide policy, authority, device actions, or side effects in utility
  code, fixtures, or configuration.

## Clean Architecture

- Dependencies point inward: domain contracts and rules must not depend on
  Home Assistant, Node-RED, transport, storage, or device APIs.
- Future adapters may translate external data into domain models, but may not
  become owners of Climate, Automation, Common, or Smart Home Center policy.
- Keep use cases separate from external-framework details and expose those
  details through explicit boundary interfaces.
- Tests and synthetic fixtures exercise the same contract boundaries without
  requiring a live runtime.
- The existing HASC safety boundary still applies: read-only/shadow first;
  no proxy or direct execution without the separately required approval.

## Mandatory Kimi code review

Every code change requires a Kimi review before it is considered complete or
pushed. The review must receive the intended scope, relevant architecture and
safety constraints, changed files, and local test results.

Address review findings with code or an explicit, documented reason. Record in
the final change report that Kimi reviewed the final diff and whether any
findings remain. A Kimi review is a quality gate; it does not grant authority
for proxy, device execution, runtime access, or any scope excluded by the
repository boundaries.

Documentation-only edits do not require Kimi review unless they accompany a
code change.
