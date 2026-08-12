# Engineering standards

These standards apply to every future HausmanHub code change.

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
- Read-only/shadow remain the observation modes. Version 0.4.0 permits only
  the separately approved, opt-in single-`input_boolean` canary. No proxy,
  physical-device domain, or general execution may be added without another
  explicit owner decision and a device-specific rollback plan.

## Code review: Codex final self-review

Every code change requires a final Codex self-review. Codex reviews the final
current diff before commit, push, release, deployment, or publication. The
self-review includes the staged diff, relevant tests, and the full local
release gate.

Review findings must be addressed or explicitly documented. Record the scope,
checks and remaining risks in the final report. Self-review does not grant new
runtime authority and does not relax proxy, device execution or repository
boundaries.
