"""Keep the HACS runtime explicitly pinned to the canonical contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPOSITORY_ROOT / "hausmanhub-contracts.json"

VENDORED_CONTRACT_HASHES = {
    "custom_components/hausman_hub/contracts/v1/scenario-definition.schema.json": "71ef6345732e599385522935b794d7e391875fa73bce0352642421fab35ca9bf",
    "custom_components/hausman_hub/contracts/v1/api-capabilities.schema.json": "c2ce35e1754115a8890ee96ad9435b3f91517e6e4874b37e6e033dffd1d859ce",
    "custom_components/hausman_hub/contracts/v1/scenario-catalog.schema.json": "82744824ecbbecfde6274248c2c1985558d557d8cc5392d7b71d51dc4a10fcb1",
    "custom_components/hausman_hub/contracts/v1/operation-journal.schema.json": "79ad67f0e4b7a0eeade2fbc891eb3672ee2c23feea4ef9344196d30d342fc89d",
    "custom_components/hausman_hub/contracts/v1/device-action-batch-request.schema.json": "2976c0039dfca92c5cb11c7bb7bdb419b35b355c4d2c2bb33c94a60a3483e24d",
    "custom_components/hausman_hub/contracts/v1/device-action-batch-receipt.schema.json": "60619fbba18e1c049de9e631379298da8ce6a9dc6193a965c5d0abe1d55a04d4",
    "custom_components/hausman_hub/contracts/v1/device-feature-matrix.json": "ef1f2784f5ffdd0bde84282816f09542e4f7aabf3e9150c1dfe5b1f8c0315455",
    "fixtures/hausmanhub_operation_journal_v1/journal.json": "2429db40627168bb84e39411ec75b0934fe169cd21103d0b05ae0bd28b2e3340",
    "fixtures/hausmanhub_scenario_catalog_v1/catalog.json": "0bb450fb919f4aaf99a9250974c6c5d53e5c2828f349d4ff160ba8cae1078d23",
    "fixtures/hausmanhub_device_action_batch_v1/request.json": "e4fa3073d5de3f716cce634ccfc3248dc2a89e4371766aeab136d267379e3f1f",
    "fixtures/hausmanhub_device_action_batch_v1/receipt.json": "4d3b40d3d89982c059e0e13c300a30a4ff3b9964c1dfc261c3295dfedca1607a",
    "fixtures/hausmanhub_device_feature_matrix_v1/document.json": "ef1f2784f5ffdd0bde84282816f09542e4f7aabf3e9150c1dfe5b1f8c0315455",
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
        "version": "0.53.0",
        "commit": "0db4223d09f22830e9a434376b42ee6c9abdde25",
        "canonical": True,
        "role": "runtime-consumer",
    }


def test_contract_0_53_0_vendored_files_match_canonical_hashes() -> None:
    for relative_path, expected_hash in VENDORED_CONTRACT_HASHES.items():
        payload = (REPOSITORY_ROOT / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_hash, relative_path
