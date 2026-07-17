# HASC 0.3.17 fixed summary interval review

Date: 2026-07-17.

## Scope

Unpublished local version 0.3.17 adds one safe option for the existing nine
aggregate count sensors. The exact choices are `5m`, `15m`, and `30m`; the
legacy/default value remains `5m`. No faster choice exists. The option changes
only the timer of the existing shared count coordinator. It adds no data,
entity, route, service, device, command, proxy, execution path, or authority.
The optional authenticated local GET page remains request-time and independent
of this timer.

## Implementation

- The framework-independent configuration contract validates the exact three
  strings and defaults old options to `5m` without changing saved entry data.
- The Home Assistant options form uses a translated fixed select selector.
- The sensor adapter maps the validated choice to 5, 15, or 30 minutes and
  passes it to the one coordinator shared by the same nine diagnostic sensors.
- Active setting changes reload only the HASC entry. When HASC is ordinarily
  stopped or user-disabled, the same option saves without a reload or a home
  summary read and applies only on the later explicit setup or activation.

## Verification

- The complete fast suite passed with 142 tests before the review fixes.
- `python tools/check_local_release.py` passed before the review fixes.
- Disposable Home Assistant Core 2026.6.4 and 2026.7.0 both passed before the
  review fixes. They verified the real selector serialization, rejection of
  `1m`, one HASC-only reload, the shared coordinator interval, inactive saves,
  restarts, and the unchanged local-page boundary.
- After the review fixes, the focused 80 configuration/skeleton tests passed,
  both disposable Core 2026.6.4 and 2026.7.0 passed again, the complete fast
  suite passed with 143 tests, and `python tools/check_local_release.py` passed.

No live Home Assistant, device, credential, external address, or real home
data was used.

## Kimi review cycle

- Production/config-flow review: child Kimi session
  `ses_09166b4a6ffe1RhM4u1Fi9gN90` returned `NO FINDINGS` for the 0.3.17
  domain, application, form, coordinator, translations, version note, and new
  unit-test delta.
- Core-harness review: child Kimi session
  `ses_0916363d2ffeuRUPA2GmZYawCC` found three test-only gaps. Some restart
  branches did not explicitly assert the runtime coordinator interval, and the
  fast source guards were too broad.
- The gaps were fixed by adding a real restart of a legacy empty-options entry
  with an exact `5m` assertion, checking the saved/default interval inside the
  ordinary-unload restart helper, checking the fresh-entry default, and adding
  exact fast source guards for those calls.
- The first narrow follow-up child session `ses_0915ac692ffe1pJ5kj6cRRslJ1`
  returned an empty body, although its supervising OpenCode root reported
  `NO FINDINGS`; that empty child response is not treated as direct Kimi
  evidence. A final current-diff result must therefore come from a separate
  explicit Kimi response after this note. This note does not itself claim that
  final gate. Earlier broad attempts are also not treated as approval:
  `ses_0916bf68fffeKhtFj6YQX6iX4w` returned an empty body and
  `ses_0916a0d91ffeZ6eQ2aLIcRYJyw` reached its step limit.

No commit, push, release, deployment, publication, or live-home change was
performed or authorized.
