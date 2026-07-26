# Current Work - HausmanHub 1.25.0 (вариант 1, локально)

## Goal
- Universal IR-AC mechanism, release slicing: 1.25.0 = approved variant 1
  (facade control + roomless binding + hints); 1.26.0 = wizard IR-learning
  vertical ("2 lite": SmartIR code DB scan, Broadlink `.storage` codes,
  `remote.learn_command` last).

## Done this session (2026-07-26, uncommitted)
- `application/climate_ha_adapters.py`: channel is an honest transport label.
  Climate facades (`climate.*`/`humidifier.*` endpoints) translate via standard
  HA services for any channel; `unsupported_control_channel` remains only for
  raw `remote.*` control endpoints (no codebook).
- `climate_ha_state_view.py`: read-only `ir_remote_catalog()` for `remote.*`
  entities with HA-area binding.
- `application/climate_setup.py` + `climate_runtime.py`: setup options gain a
  bounded private-id-free `ir_remotes` list (name/room_id/available only).
- Contract `v1/climate-setup-options.schema.json`: optional additive
  `ir_remotes`; honest roomless-candidate shape (`room_id: ""`,
  `reason: unassigned_room`, null suggestion fields).
- Panel wizard: "Устройства без комнаты" group binds roomless candidates to
  the current room in the HausmanHub registry only; SmartIR hint when a room
  has an IR remote but no climate facade; honest channel copy (control works
  immediately through the facade).
- Tests: adapter channel/facade/raw-remote cases, state-view remote catalog,
  setup-options projection + schema coverage, runtime fakes, panel wizard
  roomless/hint/copy cases. Full gate: 815 tests OK (4 skipped) +
  `tools/check_local_release.py` passed.

## Next
- Version bump 1.25.0 + CHANGELOG + commit/push/release only after explicit
  user go-ahead.
- Then 1.26.0 wizard IR-learning vertical per the approved design.

## Previous (1.24.0, released)
- 9-tab panel, scenario engine, connection settings; release
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.24.0 (Latest).
