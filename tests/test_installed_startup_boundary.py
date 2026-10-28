"""Check the actual legacy startup, without pretending it has the new core."""
import ast
from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "hausman_hub"


def test_actual_legacy_startup_order():
    source = (ROOT / "__init__.py").read_text()
    setup = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_setup_entry")
    body = ast.get_source_segment(source, setup)
    steps = [
        "manual_light_events.start(buffer_startup=True)",
        "await manual_light_authority.async_load()",
        "await room_lighting_service.async_list_configs()",
        "await manual_light_authority.async_capture_initial_room_scope(",
        "await room_lighting_runtime.start(hass, entry.entry_id)",
        "await manual_light_events.async_complete_startup()",
        "activation_latch.open()",
    ]
    positions = [body.index(step) for step in steps]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)


def test_actual_constructor_compatible_with_new_runtime():
    setup_tree = ast.parse((ROOT / "__init__.py").read_text())
    calls = [n for n in ast.walk(setup_tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "RoomLightingRuntime"]
    assert len(calls) == 1
    runtime_tree = ast.parse((ROOT / "application" / "room_lighting_runtime.py").read_text())
    cls = next(n for n in runtime_tree.body if isinstance(n, ast.ClassDef) and n.name == "RoomLightingRuntime")
    initializer = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    accepted = {a.arg for a in initializer.args.args + initializer.args.kwonlyargs}
    assert len(calls[0].args) == 3
    assert all(k.arg in accepted for k in calls[0].keywords)
