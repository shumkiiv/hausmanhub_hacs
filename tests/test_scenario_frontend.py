"""Executable scenario editor contract tests without a browser runtime."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "custom_components/hausman_hub/frontend"


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("node", "--input-type=module", "--eval", script),
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_room_scope_is_backward_compatible_and_duplicate_is_selective() -> None:
    state = (FRONTEND / "hausman-hub-scenario-state.js").as_uri()
    rooms = (FRONTEND / "hausman-hub-scenario-rooms.js").as_uri()
    completed = _run(
        f"""
        import {{ normalizedScenario, duplicateScenarioDraft, scenarioPayload }} from {state!r};
        import {{ scenarioRoomIds }} from {rooms!r};
        const legacy = normalizedScenario({{
          id: "legacy", title: "Legacy", roomId: "living", revision: 7,
          definition: {{version: 1, executionMode: "single", triggers: [{{id: "t1", type: "manual"}}], conditions: [], actions: [{{id: "a1", type: "notification", message: "ok"}}]}},
        }});
        if (scenarioRoomIds(legacy).join(",") !== "living") throw new Error("legacy roomId lost");
        const payload = scenarioPayload(legacy);
        if (payload.roomId !== "living" || payload.roomIds.join(",") !== "living") throw new Error("compatibility mirror broken");
        if (payload.expectedRevision !== 7 || "revision" in payload) throw new Error("optimistic lock missing");
        const copy = duplicateScenarioDraft(legacy, {{keepRooms: false, keepActions: false}});
        if (copy.roomIds.length || copy.definition.actions.length || "revision" in copy) throw new Error("selective duplicate failed");
        """
    )
    assert completed.returncode == 0, completed.stderr


def test_device_tree_groups_physical_devices_and_meets_p95_budget() -> None:
    picker = (FRONTEND / "hausman-hub-scenario-device-picker.js").as_uri()
    completed = _run(
        f"""
        import {{ scenarioPhysicalGroups }} from {picker!r};
        const devices = Array.from({{length: 2000}}, (_, index) => ({{
          target_id: `target_${{index}}`, physical_id: `physical_${{Math.floor(index / 2)}}`,
          physical_name: index % 4 < 2 ? "Лампа" : `Устройство ${{index}}`,
          room_name: `Комната ${{index % 12}}`, device_type_name: index % 3 === 0 ? "Свет" : "Климат",
          actions: [{{action_id: "turn_on"}}],
        }}));
        const durations = [];
        let groups = [];
        for (let run = 0; run < 25; run += 1) {{
          const started = performance.now();
          groups = scenarioPhysicalGroups(devices, true);
          durations.push(performance.now() - started);
        }}
        durations.sort((left, right) => left - right);
        const p95 = durations[Math.ceil(durations.length * .95) - 1];
        if (groups.length !== 1000) throw new Error(`physical grouping failed: ${{groups.length}}`);
        if (p95 > 100) throw new Error(`device tree P95 ${{p95.toFixed(1)}} ms exceeds 100 ms`);
        """
    )
    assert completed.returncode == 0, completed.stderr
