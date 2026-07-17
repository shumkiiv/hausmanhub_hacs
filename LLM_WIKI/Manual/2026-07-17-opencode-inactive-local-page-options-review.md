# OpenCode fallback review: inactive local page options

Date: 2026-07-17.

## Scope

Independent read-only review of the disposable Home Assistant Core lifecycle
for changing the exact boolean `local_summary_enabled` option while HASC is
ordinarily stopped or deliberately disabled by its user, including after a
full temporary Home Assistant restart.

The review was limited to the related changes in
`tools/check_home_assistant_core.py` and
`tests/test_read_only_skeleton.py`. It checked that saving the option cannot
reload or start inactive HASC, cannot read the home, and applies the saved page
choice only after an explicit setup or user activation.

## Result

Kimi-backed review returned no text because the provider quota was exhausted.
The permitted independent OpenCode fallback review then found two test-only
weaknesses:

- several source-string guards were too broad to prove the intended call and
  ordering;
- the disabled-after-restart scenario preserved `True` instead of exercising a
  real local-page flag change.

Both findings were corrected. The final OpenCode fallback review session
`ses_091a6ffd3ffeAbMWaqG0KDhjr9`, using `openai/gpt-5.6-sol` with a read-only
research helper, returned **NO FINDINGS**. It confirmed that the exact call
blocks and ordering are now guarded and that the restart scenario performs a
real `True` to `False` change before explicit activation.

This fallback review permitted continued local HASC work only and did not
replace the required Kimi gate. After the provider quota renewed, Kimi
completed the mixed-diff review cycle recorded in the [0.3.15 and 0.3.16 Kimi
review note](2026-07-17-kimi-v0-3-15-v0-3-16-review.md). Neither review
authorizes a commit, push, release, deployment, publication, or new authority.

## Evidence

- 139 local synthetic and boundary checks passed.
- `python3 tools/check_local_release.py` passed, including fixtures, HACS
  package checks, and repository-boundary checks.
- The disposable empty Home Assistant Core lifecycle passed with 2026.6.4 and
  2026.7.0.
- The inactive-options helper records zero reload calls, blocks all three HASC
  home-summary adapters, waits for queued work, and requires the entry to stay
  `NOT_LOADED` with its original `disabled_by` state.
- The lifecycle checks `False` after explicit setup, `True` after explicit user
  activation, and `False` after explicit activation following a full restart.
  The last choice remains closed through ordinary unload, another restart, and
  removal.
- The change adds no integration runtime behavior, URL, data field, device,
  service, proxy, direct-execution path, credential, or live-home access.

The review and checks used only repository files, synthetic fixtures, and
temporary empty Home Assistant configurations. They did not connect to a real
home, Node-RED, devices, services, or a remote Home Assistant API.
