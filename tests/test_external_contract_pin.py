"""Keep the HACS runtime explicitly pinned to one external contract draft."""

from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPOSITORY_ROOT / "hausmanhub-contracts.json"


def test_external_contract_pin_is_explicit_and_noncanonical() -> None:
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))

    assert pin == {
        "repository": "shumkiiv/hausmanhub-contracts",
        "version": "0.3.1",
        "commit": "fd0d19f",
        "canonical": False,
        "role": "runtime-source",
    }
