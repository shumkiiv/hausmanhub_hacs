# Current Work - HausmanHub 1.25.0 (выпущен и задеплоен)

## Result
- Release 1.25.0 (universal IR-AC, approved variant 1) is RELEASED and DEPLOYED
  on 2026-07-26.
- Release commit `82b29c6` on `origin/main`; tag `v1.25.0` resolves exactly to
  `82b29c6d6c03384d71574fdf0e95df381d257a21`; GitHub Actions run `30195356712`
  passed; public Latest release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.25.0.

## Contents
- Channel is an honest transport label: climate/humidifier facades (SmartIR
  etc.) translate via standard HA services for any channel;
  `unsupported_control_channel` only for raw `remote.*` endpoints.
- Setup options: bounded private-id-free `ir_remotes` (name/room_id/available).
- Panel wizard: "Устройства без комнаты" binding group (HausmanHub registry
  only), SmartIR hint, honest channel copy.
- Full local gate: 815 tests + `tools/check_local_release.py`.

## Deploy verification (live HA, read-only)
- HACS update entity installed `v1.25.0` explicitly; HA restarted.
- `integration_version: 1.25.0`; served panel JS is the 192769-byte build with
  the new strings; setup options return real `ir_remotes` (Broadlink remotes
  in гостиная/кухня); `climate.komanchi_living_smartir` is roomless
  candidate_0001 with `can_add: true`.
- Contour stays `not_configured`; readiness honestly `disabled`.

## Next
- 1.26.0 wizard IR-learning vertical ("2 lite"): SmartIR code DB scan,
  Broadlink `.storage` codes, `remote.learn_command` last.
