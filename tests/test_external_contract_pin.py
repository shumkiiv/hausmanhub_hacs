"""Keep the HACS runtime explicitly pinned to the canonical contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPOSITORY_ROOT / "hausmanhub-contracts.json"

VENDORED_CONTRACT_HASHES = {
    "custom_components/hausman_hub/contracts/v1/scenario-definition.schema.json": "71ef6345732e599385522935b794d7e391875fa73bce0352642421fab35ca9bf",
    "custom_components/hausman_hub/contracts/v1/api-capabilities.schema.json": "2c8a798657e268ce2210e34a6825886f8f891f70b2459d23f633e12d472e34b5",
    "custom_components/hausman_hub/contracts/v1/scenario-health.schema.json": "8a14e51bdb531dd674bede8e0e0618a2788df641dc23fc9da2e8af9b821890e4",
    "custom_components/hausman_hub/contracts/v1/device-action-request.schema.json": "4ff6f1cb6e7939749e14aa43799e6108c1db7309251d2b7ee5026f047c667b94",
    "custom_components/hausman_hub/contracts/v1/device-action-receipt.schema.json": "38a710d76b1d335e22cf7c8e85907b329ab7d6952375d86756781f10fcd53a3c",
    "custom_components/hausman_hub/contracts/v1/intercom-release-receipt.schema.json": "ea11c82a77a3fe9195513d1a915336167c81a356fa0b56cf058384daedacf947",
    "custom_components/hausman_hub/contracts/v1/scenario-catalog.schema.json": "3b43fa73e6c254ed0f87188a9f7d50144ee43dd7542233b4dcaf32d3d2629f55",
    "custom_components/hausman_hub/contracts/v1/scenario-dry-run-result.schema.json": "6b1fb038f02461c6851cac67239cf46ae3fc5a52801c0d75c28c7e6fdb27ea86",
    "custom_components/hausman_hub/contracts/v1/operation-journal.schema.json": "79ad67f0e4b7a0eeade2fbc891eb3672ee2c23feea4ef9344196d30d342fc89d",
    "custom_components/hausman_hub/contracts/v1/device-action-batch-request.schema.json": "2976c0039dfca92c5cb11c7bb7bdb419b35b355c4d2c2bb33c94a60a3483e24d",
    "custom_components/hausman_hub/contracts/v1/device-action-batch-receipt.schema.json": "60619fbba18e1c049de9e631379298da8ce6a9dc6193a965c5d0abe1d55a04d4",
    "custom_components/hausman_hub/contracts/v1/device-feature-matrix.json": "ef1f2784f5ffdd0bde84282816f09542e4f7aabf3e9150c1dfe5b1f8c0315455",
    "fixtures/hausmanhub_operation_journal_v1/journal.json": "2429db40627168bb84e39411ec75b0934fe169cd21103d0b05ae0bd28b2e3340",
    "fixtures/hausmanhub_scenario_catalog_v1/catalog.json": "fc270ee7504175be82baede986030181108ab076f60eca7dea844fd15b0622c3",
    "fixtures/hausmanhub_scenario_dry_run_v1/scenario-dry-run-result.json": "48e1e87e62baa09213956d0b6905a2721e3bc5c6d513c5c2f819f49bf5c6e5f6",
    "fixtures/hausmanhub_device_action_batch_v1/request.json": "e4fa3073d5de3f716cce634ccfc3248dc2a89e4371766aeab136d267379e3f1f",
    "fixtures/hausmanhub_device_action_batch_v1/receipt.json": "4d3b40d3d89982c059e0e13c300a30a4ff3b9964c1dfc261c3295dfedca1607a",
    "fixtures/hausmanhub_device_feature_matrix_v1/document.json": "ef1f2784f5ffdd0bde84282816f09542e4f7aabf3e9150c1dfe5b1f8c0315455",
    "custom_components/hausman_hub/contracts/v1/dashboard-snapshot.schema.json": "75b1b531f2b0b04f7fb8a73dd6e4ccc2271c78cd0c441967a9d2619cbb5c3f74",
    "custom_components/hausman_hub/contracts/v1/device-power-dependencies.schema.json": "c0b3decdb785ce68ba2048061bed8db9f0caa32303d082c3ad2a0bd2b6e265c5",
    "fixtures/hausmanhub_device_power_dependencies_v1/document.json": "379574fffa1d7f5be2249203fc7d0bf3fcdf5655f3fd4557bdbbc555c52ae1eb",
    "custom_components/hausman_hub/contracts/v1/energy-history.schema.json": "2ff676c72e249b0ea70d34bfb628e187e425ec516a9d46ae8f835cf275c7dea8",
    "custom_components/hausman_hub/contracts/v1/energy-settings.schema.json": "a1bd73766e4d1051214ab427abf08b57c763ca3acff1c2d618b71ffba6fac2d7",
    "custom_components/hausman_hub/contracts/v1/energy-settings-document.schema.json": "b1aaeaa7b7e37cc6ac49ab2cad5fd65e0d9a27f329e810cb0c599ac606c0e670",
    "custom_components/hausman_hub/contracts/v1/energy-meters.schema.json": "21a38ca9e1079d727c57a2f7ada6b3ba9a62bcc5b8a4d6501eb68e66e571713d",
    "custom_components/hausman_hub/contracts/v1/room-settings.schema.json": "b4903c645f91b87e64a18f672acc72256d7f90716b29bc171dfe9c6c9a540623",
    "custom_components/hausman_hub/contracts/v1/event-stream-message.schema.json": "a2cf68b8f7c6bcfd9f429615b9a460dadedbe01e656318b28c680c6bdccbde96",
    "custom_components/hausman_hub/contracts/v1/water-meter.schema.json": "7fc02e2a271754638a080302e8f28850fdd3e0fdfcc530a272f832ec847dd20c",
    "fixtures/hausmanhub_energy_meter_v1/energy-meter.json": "bc942d4c6bda816c1d7189c4d3a206f1111bd0e80ab776b4eee006e8b7cc9fa3",
    "custom_components/hausman_hub/contracts/v1/scenario-list.schema.json": "5565576e1e2046610a246f8bae5c5a57c2db58c979d54865ccd0f79bf6682e38",
    "fixtures/hausmanhub_scenario_list_v1/scenario-list.json": "25efa1573aaee32159fe886ba57111fb3a1b00288d2ea265dbc57ac562e8ae1d",
    "fixtures/hausmanhub_scenario_health_v1/health.json": "37628ebbf8ece709e3b9c45f6834820c40d92688cc872cd54b5a0492d83c522a",
    "custom_components/hausman_hub/contracts/v1/water-safety.schema.json": "85cbbf912c2b73b6600b456e1c9e42758fe34da305dbf102718e430bad6e8734",
    "fixtures/hausmanhub_water_safety_v1/water-safety.json": "88a8acb38efd003e77950d726e617193cfaa05320de6981e53c486cf3ed74aa4",
    "custom_components/hausman_hub/contracts/v1/tablet-power-status-request.schema.json": "a03a17b4c2e2a9973d037c0e4ff2394877e8b0acd5ecede3abd6d5dd181dc905",
    "custom_components/hausman_hub/contracts/v1/tablet-power-status-receipt.schema.json": "84f1a948d87cf30b5518bbebefcbbcce32ad90968366c9b0886bfb195f5bef70",
    "fixtures/hausmanhub_tablet_power_v1/request.json": "0713606c6a19a2598c9cf82d07d69fc67038840f38682bb69c7403ef0f9d6a95",
    "fixtures/hausmanhub_tablet_power_v1/receipt.json": "feb9293e5f39bbb7fdda199ccc208b77114b43655668ab67d032844c380fdd9e",
}


def test_external_contract_pin_is_explicit_and_canonical() -> None:
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))

    assert pin == {
        "repository": "shumkiiv/hausmanhub-contracts",
        "version": "0.59.0",
        "commit": "5189a020c395babb65795b17f35674b31c4c0a45",
        "canonical": True,
        "role": "runtime-consumer",
    }


def test_contract_0_59_0_vendored_files_match_canonical_hashes() -> None:
    for relative_path, expected_hash in VENDORED_CONTRACT_HASHES.items():
        payload = (REPOSITORY_ROOT / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_hash, relative_path
