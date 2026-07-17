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

## Code review: Kimi and a temporary alternative

Every code change requires an independent review. For every code change, Kimi
must review the final current diff before it is considered complete or before a
commit, push, release, deployment, or publication. The reviewer must receive
the intended scope, relevant architecture and safety constraints, changed
files, and local test results.

If Kimi is temporarily unavailable, for example because of a provider error or
a quota limit, use a different independent reviewer for the final local diff.
Record who reviewed it, what was checked, and whether findings remain. This
alternative review lets every change already permitted by the HASC boundaries
continue safely, including code, tests, documentation, and local checks or
fixes. It does not authorize a commit, push, release, deployment, publication,
or new authority.

Address review findings with code or an explicit, documented reason. Record in
the final change report which review was completed and whether any findings
remain. Neither type of review grants authority for proxy, device execution,
runtime access, or any scope excluded by the repository boundaries.

Documentation-only edits that are not part of a code change do not require
Kimi review. This narrow exception never applies to a mixed diff: when code is
present, the final Kimi gate above applies to the entire diff.
