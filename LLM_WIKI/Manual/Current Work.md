# Current Work - HausmanHub 1.25.2 (выпущен и задеплоен)

## Result
- Release 1.25.2 (wizard device-selection fix) is RELEASED and DEPLOYED on
  2026-07-26.
- Release commit `3eb8ffe` on `origin/main`; tag `v1.25.2`; GitHub Actions
  run `30219220629` passed; public Latest release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.25.2.
- Full local gate in clean worktree: 812 tests passed, 4 skipped, plus
  `tools/check_local_release.py`.

## Root cause (live bug "не выбирается устройство")
- `climate_ha_state_view.py` `entity_catalog()` read `supported_features`
  with the strict guard `type(x) is int`. Real HA stores it as a
  `ClimateEntityFeature` IntFlag, so the guard zeroed it for every climate
  entity: command_types collapsed to `(climate.set_hvac_mode,)` and every
  air-conditioner candidate failed validation with "device is missing
  required capabilities: power, target_temperature".
- The guard existed since 09aea13 (native discovery, 1.21.0). Tests and
  REST/JSON dumps always carry plain ints, which hid the bug locally.
- Fix: `isinstance` check plus `int()` normalization; regression test
  `test_catalog_accepts_intflag_supported_features`.
- Proven end-to-end before release: clean tag 1.25.1 fed IntFlag features
  reproduced the exact live error; fed plain ints it returned `ready`.

## Diagnostics shipped in 1.25.1
- Commit `4d15037`, tag `v1.25.1`: `detail` field in `unsupported_device_set`
  issues (stage import/setup plus original error text), which pinpointed the
  failure stage on live without server logs.

## Deploy verification (live HA)
- HACS update entity installed `v1.25.2` explicitly; HA restarted;
  `installed_version: v1.25.2`.
- Draft validation for гостиная: `status: ready`, `save_allowed: true`,
  `issues: []`; snapshot_revision `239926551809926` matches the local
  clean-tag reconstruction exactly.
- Four of five AC candidates validate `ready`; candidate_0030 (Electrolux
  air purifier) is honestly blocked on missing `target_temperature` -
  correct behaviour, it is not an air conditioner.

## Next
- 1.26.0 wizard IR-learning vertical ("2 lite"): SmartIR code DB scan,
  Broadlink `.storage` codes, `remote.learn_command` last. WIP files stay
  uncommitted in the working tree.
- Known WIP-scope issues to fix there: frontend `code_source` step uses
  nonexistent `state.choices` (`hausman-hub-panel.js` ~2236); failing test
  `test_raw_remote_endpoint_stays_blocked_for_any_channel`.
