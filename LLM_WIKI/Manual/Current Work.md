# Current Work - HausmanHub 1.24.0

## Goal
- Phase 3: 9-tab Android-style panel + scenario engine per `SCENARIO_EDITOR_API_CONTRACT.md` v1.
- Release 1.24.0.

## Done this session
- Closed scenario-engine P1 blockers:
  - `ScenarioService.async_update_scenario` now keeps `asyncio.Lock` through validation, save, and swap.
  - Payloads accept both `id` and `scenarioId` aliases.
  - `ScenarioExecutor` propagates a `visited` recursion context through nested `run_scenario` calls.
  - Device-action values are normalized before HA calls (brightness/position/temperature/modes).
  - Minimal condition evaluator and dry-run plan added to the executor.
- Added scenario admin API views and connection-settings view; updated capabilities contract + fixture.
- Adjusted tests for the new general-settings fields, view counts, and fake executor signatures.
- Full test suite: 808 OK (4 skipped).

## Remaining
- Refactor `frontend/hausman-hub-panel.js` from 7 to 9 tabs (Home, Scenarios, Climate, Lights, Rooms, Media, Security, Devices, Settings).
- Add Scenarios tab UI (list, enable/disable, run/test/delete, metadata editor).
- Add Settings tab UI for connection mode + two URL fields.
- Update panel tests to include new admin routes.
- Bump `manifest.json` to 1.24.0 and run release gate.

## Blockers
- Task-agent quota exhausted, so the large frontend refactor cannot be delegated to `visual-engineering` in this session. Manual refactor is possible but will take multiple turns and careful testing.
