"""One declared V01 comparison. The unchanged oracle has no SUT imports."""

import hashlib
import importlib.util
from pathlib import Path
import sys

REFERENCE_SHA256 = "0a085b3aa6f235b64d35f0b46f52e733bba79a5369d7d532bdbfb8a608bd5de6"


def v01_expected(model_root):
    path = Path(model_root) / "reference.py"
    if hashlib.sha256(path.read_bytes()).hexdigest() != REFERENCE_SHA256:
        raise AssertionError("P1 oracle changed")
    spec = importlib.util.spec_from_file_location("n1_reference", path)
    reference = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = reference
    spec.loader.exec_module(reference)
    room = reference.Room()
    assert room.manual("n1_old_off", "off")
    assert room.begin_return()
    assert all(room.return_barrier() for _ in range(5))
    assert room.plan(reference.Goal("main", brightness=60, kelvin=4000))
    assert room.persist_plan() and room.dispatch()
    assert room.report(room.command.token, "on", brightness=60, kelvin=4000)
    room.assert_invariants()
    return {"outcome": room.return_outcome, "nativeCalls": len(room.effects),
            "autoConfirmed": room.lights["main"].auto_proof is not None,
            "manualBlocked": room.manual_action is not None}


def compare(expected, actual):
    return {key: {"expected": value, "actual": actual[key]}
            for key, value in expected.items() if actual[key] != value}
