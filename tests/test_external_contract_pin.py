"""Keep the HACS runtime explicitly pinned to the canonical contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPOSITORY_ROOT / "hausmanhub-contracts.json"

VENDORED_CONTRACT_HASHES = {
    "custom_components/hausman_hub/contracts/v1/dashboard-snapshot.schema.json":
        "14a1c19a07c468e1912dd8d2a8a9e872c6d745c776562cfdbef266dbd1a6bd34",
    "custom_components/hausman_hub/contracts/v1/water-meter.schema.json":
        "7fc02e2a271754638a080302e8f28850fdd3e0fdfcc530a272f832ec847dd20c",
    "fixtures/hausmanhub_energy_meter_v1/energy-meter.json":
        "bc942d4c6bda816c1d7189c4d3a206f1111bd0e80ab776b4eee006e8b7cc9fa3",
}


def test_external_contract_pin_is_explicit_and_canonical() -> None:
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))

    assert pin == {
        "repository": "shumkiiv/hausmanhub-contracts",
        "version": "0.47.0",
        "commit": "57a1b04",
        "canonical": True,
        "role": "runtime-consumer",
    }


def test_contract_0_47_0_vendored_files_match_canonical_hashes() -> None:
    for relative_path, expected_hash in VENDORED_CONTRACT_HASHES.items():
        payload = (REPOSITORY_ROOT / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_hash, relative_path
