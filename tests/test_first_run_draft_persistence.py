from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
DRAFT_JS = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "frontend"
    / "hausman-hub-first-run-draft.js"
)


def module_source() -> str:
    return (
        DRAFT_JS.read_text(encoding="utf-8")
        .replace("export function ", "function ")
        .replace("export { FIRST_RUN_DRAFT_KEY };", "")
    )


def run_node(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("node", "--input-type=commonjs", "--eval", f"{module_source()}\n{body}"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


PANEL_FACTORY = r"""
function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, String(value)),
  };
}
function panel(snapshotRevision = 9, setupRevision = 4) {
  const result = {
    _activeRoomSetupPane: "devices",
    _firstRunDraftReady: true,
    _notice: "",
    _firstRun: {
      areaAssignments: {}, completed: false, contourSaved: false,
      draft: null, home: null, issues: [], roomId: "living", rooms: {},
      schedule: {enabled: false, dayStart: "07:00", nightStart: "23:00"},
      setupRevision, showRoomDevices: false, step: "room", validRooms: new Set(),
      validation: null,
      options: {
        snapshot_revision: snapshotRevision,
        rooms: [{id: "living", name: "Гостиная"}],
        devices: [{candidate_id: "candidate-1", candidate_key: "device-1", suggested_types: ["air_conditioner"]}],
      },
    },
    _firstRunAreaCandidates() { return []; },
    _firstRunPhysicalGroups() { return []; },
    _firstRunPhysicalGroupId() { return ""; },
    _firstRunRoomState(room) {
      if (!this._firstRun.rooms[room.id]) {
        this._firstRun.rooms[room.id] = {
          day: {humidity: 53, strategy: "normal", temperature: 25},
          devices: {"device-1:air_conditioner": {
            candidateId: "candidate-1", candidateKey: "device-1", channel: null,
            selected: false, type: "air_conditioner",
          }},
          included: false, maxTemperature: 27, minTemperature: 24.5,
          night: {humidity: 50, strategy: "normal", temperature: 25.5},
          report: null, showAllDevices: false,
        };
      }
      return this._firstRun.rooms[room.id];
    },
  };
  result._firstRunRoomState(result._firstRun.options.rooms[0]);
  return result;
}
"""


class FirstRunDraftPersistenceTest(unittest.TestCase):
    def test_refresh_restores_room_selection_and_exact_step(self) -> None:
        result = run_node(
            PANEL_FACTORY
            + r"""
const storage = memoryStorage();
const before = panel();
const state = before._firstRun.rooms.living;
state.included = true;
state.devices["device-1:air_conditioner"].selected = true;
state.devices["device-1:air_conditioner"].channel = "direct_wifi";
before._firstRun.validRooms.add("living");
before._firstRun.validation = {status: "ready", save_allowed: true};
before._firstRun.draft = {contract: {name: "draft", version: 1}};
before._activeRoomSetupPane = "comfort";
if (!persistFirstRunDraft(before, storage, 1000)) throw new Error("draft_not_saved");
const after = panel();
after._firstRunDraftReady = false;
const restored = restoreFirstRunDraft(after, storage, 2000);
const device = after._firstRun.rooms.living.devices["device-1:air_conditioner"];
if (!restored.restored || restored.validationInvalidated) throw new Error("not_restored");
if (after._firstRun.step !== "room" || after._firstRun.roomId !== "living") throw new Error("step_lost");
if (after._activeRoomSetupPane !== "comfort") throw new Error("pane_lost");
if (!device.selected || device.channel !== "direct_wifi") throw new Error("selection_lost");
if (!after._firstRun.validRooms.has("living") || after._firstRun.validation.status !== "ready") throw new Error("validation_lost");
"""
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_inventory_change_keeps_choices_but_requires_recheck(self) -> None:
        result = run_node(
            PANEL_FACTORY
            + r"""
const storage = memoryStorage();
const before = panel(9, 4);
const state = before._firstRun.rooms.living;
state.included = true;
state.devices["device-1:air_conditioner"].selected = true;
before._firstRun.validRooms.add("living");
before._firstRun.validation = {status: "ready", save_allowed: true};
before._firstRun.step = "validation";
persistFirstRunDraft(before, storage, 1000);
const after = panel(10, 4);
after._firstRunDraftReady = false;
const restored = restoreFirstRunDraft(after, storage, 2000);
if (!restored.validationInvalidated) throw new Error("stale_validation_kept");
if (!after._firstRun.rooms.living.devices["device-1:air_conditioner"].selected) throw new Error("choice_lost");
if (after._firstRun.validRooms.size || after._firstRun.validation) throw new Error("stale_validation_restored");
if (after._firstRun.step !== "room") throw new Error("unsafe_step");
"""
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_completed_wizard_removes_browser_draft(self) -> None:
        result = run_node(
            PANEL_FACTORY
            + r"""
const storage = memoryStorage();
const value = panel();
persistFirstRunDraft(value, storage, 1000);
if (!storage.getItem(FIRST_RUN_DRAFT_KEY)) throw new Error("missing_fixture");
value._firstRun.completed = true;
persistFirstRunDraft(value, storage, 2000);
if (storage.getItem(FIRST_RUN_DRAFT_KEY)) throw new Error("completed_draft_not_removed");
"""
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
