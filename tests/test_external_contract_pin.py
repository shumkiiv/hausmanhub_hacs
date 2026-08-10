"""Keep the HACS runtime explicitly pinned to the canonical contract."""

from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPOSITORY_ROOT / "hausmanhub-contracts.json"


def test_external_contract_pin_is_explicit_and_canonical() -> None:
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))

    assert pin == {
        "repository": "shumkiiv/hausmanhub-contracts",
        "version": "0.23.0",
        "commit": "da52e3c",
        "canonical": True,
        "role": "runtime-consumer",
    }
