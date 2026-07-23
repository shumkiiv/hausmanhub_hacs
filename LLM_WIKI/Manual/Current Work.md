# Current Work

## 2026-07-23 - HausmanHub 1.17.0 local release candidate

- Completed the Home Environment settings step and native options menus.
- Oracle review found atomic update, threshold range, and coverage gaps.
  One safe fix iteration added a runtime-locked home update and aligned the
  form with the registry's -40..60 °C range.
- `python3 tools/check_local_release.py` passed through a temporary Git index;
  its full suite reports 618 successful tests.
- Built the manual Home Assistant test archive
  `/home/ivsh/projects/УД-hasc/releases/HausmanHub-1.17.0-test.zip` from the
  current working tree. It contains only `custom_components/hausman_hub`,
  includes `frontend/hausman-hub-panel.js`, declares version `1.17.0`, and
  passes `unzip -t`.
- Archive SHA-256:
  `82f5f8d4a5dc43d642be3d6e4fa9339970ff91e30478890fcb78053153f56b45`.
- Disposable Home Assistant Core checks remain blocked because the expected
  Python environments under `/tmp/hausmanhub-core-2026.6.4` and `2026.7.0`
  are absent.
- Release code commit `909ae3d` was pushed to `origin/main`.
- The latest GitHub Release is `v1.17.0`:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.0.
  Its remote tag resolves exactly to `909ae3d`, the manifest declares `1.17.0`,
  and the sidebar panel asset is present.
- The only GitHub check job concluded successfully:
  https://github.com/shumkiiv/hausmanhub_hacs/actions/runs/29991423859/job/89154943431.
- No live Home Assistant action occurred. Next: refresh the custom repository
  in HACS, install `1.17.0`, restart Home Assistant, and run the deferred
  disposable Core smoke check when its environments are provisioned.
