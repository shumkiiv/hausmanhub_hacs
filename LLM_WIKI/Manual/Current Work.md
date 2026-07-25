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
- Refactored `frontend/hausman-hub-panel.js` to 9 tabs; grouped climate sections under the Climate tab.
- Added Scenarios tab UI (list, run/test/delete) and Settings tab UI (connection mode + two URL fields).
- Updated panel tests for 9 tabs and new admin routes; raised panel size limit to 200 KB.
- Bumped `manifest.json` to 1.24.0 and added CHANGELOG entry.
- Full test suite: 808 OK (4 skipped); `tools/check_local_release.py` passed.
- Committed to `main` (`97547ad`) and pushed tag `v1.24.0` to origin.

## Next
- Verify HACS refresh and install on a live Home Assistant instance.
