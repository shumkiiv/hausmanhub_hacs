"""Keep the HACS runtime explicitly pinned to the canonical contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPOSITORY_ROOT / "hausmanhub-contracts.json"

VENDORED_CONTRACT_HASHES = {
    "custom_components/hausman_hub/contracts/v1/scenario-definition.schema.json": "71ef6345732e599385522935b794d7e391875fa73bce0352642421fab35ca9bf",
    "custom_components/hausman_hub/contracts/v1/operation-journal.schema.json": "0f442ae837b519254ac3453570460de95976768125a6c7f815eb4ae9f3f861da",
    "fixtures/hausmanhub_operation_journal_v1/journal.json": "e3d3f96011771d0a210ff7bffdb09cf7d2e9a413e844c922a105f9fb350ec45d",
    "custom_components/hausman_hub/contracts/v1/dashboard-snapshot.schema.json": "ec8e79a30fa13776ad4793067381ace6d455449146dc82a03b2cf03182020672",
    "custom_components/hausman_hub/contracts/v1/room-settings.schema.json": "b4903c645f91b87e64a18f672acc72256d7f90716b29bc171dfe9c6c9a540623",
    "custom_components/hausman_hub/contracts/v1/event-stream-message.schema.json": "2942fd3f3edd4e30f1cf27c659644f9f349ad86785962ea5940fcf52fdbd30c2",
    "custom_components/hausman_hub/contracts/v1/water-meter.schema.json": "7fc02e2a271754638a080302e8f28850fdd3e0fdfcc530a272f832ec847dd20c",
    "fixtures/hausmanhub_energy_meter_v1/energy-meter.json": "bc942d4c6bda816c1d7189c4d3a206f1111bd0e80ab776b4eee006e8b7cc9fa3",
    "custom_components/hausman_hub/contracts/v1/scenario-list.schema.json": "13fe0e068bd13a29ef56c06dc6309db90beee0c70cc62b81b97515f38ff425b6",
    "fixtures/hausmanhub_scenario_list_v1/scenario-list.json": "19bf0d35d97e129e2d60c97802198b3a23264f6670b55129dd01f4de64f64bfd",
    "custom_components/hausman_hub/contracts/v1/water-safety.schema.json": "85cbbf912c2b73b6600b456e1c9e42758fe34da305dbf102718e430bad6e8734",
    "fixtures/hausmanhub_water_safety_v1/water-safety.json": "88a8acb38efd003e77950d726e617193cfaa05320de6981e53c486cf3ed74aa4",
}


def test_external_contract_pin_is_explicit_and_canonical() -> None:
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))

    assert pin == {
        "repository": "shumkiiv/hausmanhub-contracts",
        "version": "0.51.0",
        "commit": "d54aa32",
        "canonical": True,
        "role": "runtime-consumer",
    }


def test_contract_0_51_0_vendored_files_match_canonical_hashes() -> None:
    for relative_path, expected_hash in VENDORED_CONTRACT_HASHES.items():
        payload = (REPOSITORY_ROOT / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_hash, relative_path
