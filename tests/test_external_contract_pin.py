"""Keep the HACS runtime explicitly pinned to the canonical contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPOSITORY_ROOT / "hausmanhub-contracts.json"

VENDORED_CONTRACT_HASHES = {
    "custom_components/hausman_hub/contracts/v1/scenario-definition.schema.json": "60e93c0e3a970c684a9a95acd471ffd9664652e61fc5e29f5f21c7463285ea57",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-status.schema.json": "d1d219653a87c5d68834afd5bea6544924967d42a4053e6ea2269e0780766d6f",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-execution.schema.json": "1780fbeb06bc5777217e3a18059158354f28bce61cdf8aadab67e6a61ae1d597",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-source.schema.json": "4f281f11c55a235b78bb418977f948a06d4d1ba8b90b1fef4e389b3842e9f7ec",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-source-update-request.schema.json": "7712607bfe1e8cbe9a9287ea4812caed2ac2c47e93ff079458fea0a945a677a0",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-source-update-receipt.schema.json": "b57b3adcbd850af02c3c1112de9eef70fcf2e41483ae4c633b20d6db6dd33040",
    "custom_components/hausman_hub/contracts/v1/api-capabilities.schema.json": "cb6266a7e09733cedda11a5d0e7d4219fd89650d34d8e686614b84dfb22e6eed",
    "custom_components/hausman_hub/contracts/v1/scenario-ai-draft-request.schema.json": "eeb72fc172f8730552fb174a1a0a55f58debeb9b5612068b0c2889190c37b9c1",
    "custom_components/hausman_hub/contracts/v1/scenario-ai-draft.schema.json": "585ec9c8e48c208452f4f80407feacfc04a00c8b580e04a63504fa049adedb5e",
    "custom_components/hausman_hub/contracts/v1/scenario-health.schema.json": "8a14e51bdb531dd674bede8e0e0618a2788df641dc23fc9da2e8af9b821890e4",
    "custom_components/hausman_hub/contracts/v1/device-action-request.schema.json": "ec201063f15aab4614c59e8464da73f9e8490bedd3fc0dc3c2502bf7ecc078cb",
    "custom_components/hausman_hub/contracts/v1/device-action-receipt.schema.json": "82907a7b9326b00a050548665c45fe87e8095fbe31298a02f90b00bea36c4bde",
    "custom_components/hausman_hub/contracts/v1/intercom-release-receipt.schema.json": "ea11c82a77a3fe9195513d1a915336167c81a356fa0b56cf058384daedacf947",
    "custom_components/hausman_hub/contracts/v1/scenario-catalog.schema.json": "3b43fa73e6c254ed0f87188a9f7d50144ee43dd7542233b4dcaf32d3d2629f55",
    "custom_components/hausman_hub/contracts/v1/scenario-dry-run-result.schema.json": "7604a9391d8bf03c5ae098a638c6ddd7412a883bf907f751decdfe7d25db4c7c",
    "custom_components/hausman_hub/contracts/v1/operation-journal.schema.json": "79ad67f0e4b7a0eeade2fbc891eb3672ee2c23feea4ef9344196d30d342fc89d",
    "custom_components/hausman_hub/contracts/v1/device-action-batch-request.schema.json": "ee18b9a830c5c122452477557b32300bba8f0956fa58d2ab7158949b686e00d1",
    "custom_components/hausman_hub/contracts/v1/device-action-batch-receipt.schema.json": "9ebd98dbd40f95c5757cc2751efa67612c6a41caadfc51d292d5968f07263089",
    "custom_components/hausman_hub/contracts/v1/device-feature-matrix.json": "ef1f2784f5ffdd0bde84282816f09542e4f7aabf3e9150c1dfe5b1f8c0315455",
    "fixtures/hausmanhub_operation_journal_v1/journal.json": "2429db40627168bb84e39411ec75b0934fe169cd21103d0b05ae0bd28b2e3340",
    "fixtures/hausmanhub_scenario_catalog_v1/catalog.json": "fc270ee7504175be82baede986030181108ab076f60eca7dea844fd15b0622c3",
    "fixtures/hausmanhub_scenario_dry_run_v1/scenario-dry-run-result.json": "0fa1770b7e17879676e49d8a5e9992902989aedf14f30a4f1b804e782eff61dc",
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
    "custom_components/hausman_hub/contracts/v1/api-error.schema.json": "d326295adb441b0a175e515c37a6f3ec9a37a9e75d82b71da4a78dd4a432b949",
    "custom_components/hausman_hub/contracts/v1/api-error-idempotency-conflict.schema.json": "bcf1642ce7cc3428a1b39f9613df857fdd517d98d0e51cc0c1172541c1d8b0c1",
    "custom_components/hausman_hub/contracts/v1/api-error-not-acceptable.schema.json": "1f54dd304c0fa59e565a258f07d85bd1aea6fd02520abf2115de6223fa3e636a",
    "custom_components/hausman_hub/contracts/v1/device-action-value.schema.json": "bf7856e9584156b9aba1ca08f407d8159dbef1a94c70bf1e2f33b429befdb4cb",
    "custom_components/hausman_hub/contracts/v1/device-state-evidence-reason-code.schema.json": "7180b1d07ede8e77b4fdda23af421b82c3fdf3846e0f53b39b5f6dd140b3007b",
    "custom_components/hausman_hub/contracts/v1/device-state-evidence.schema.json": "4e658a34e9254b49473324ea90eab1c7accc769e746340367afd244269feea6c",
    "custom_components/hausman_hub/contracts/v1/device-state-evidence-reason-codes.json": "667409c7671a11f686dc4872dcebb456cfa1846ce16a5ee2caf170aea943bc90",
    "custom_components/hausman_hub/contracts/v1/error-taxonomy.schema.json": "4d5295c81e33d1fbc9af2912668cd74fc42a1dc8e470ef9cbc9b560ec67f11e8",
    "custom_components/hausman_hub/contracts/v1/error-taxonomy.json": "2041465d8f98e9c46c69fa129bc02f8466b60393965040c42d93952eb90ebb33",
    "custom_components/hausman_hub/contracts/v1/lighting-profile-snapshots.json": "ef8a54c3a83da87986335c5d41dcfaa489945e4117eecfdc7094770466319aec",
}


def test_external_contract_pin_is_explicit_and_canonical() -> None:
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))

    assert pin == {
        "repository": "shumkiiv/hausmanhub-contracts",
        "version": "0.63.0",
        "commit": "283040b7295ae8da6576011d05cfe7125c354833",
        "canonical": True,
        "role": "runtime-consumer",
    }


def test_contract_0_63_0_vendored_files_match_canonical_hashes() -> None:
    for relative_path, expected_hash in VENDORED_CONTRACT_HASHES.items():
        payload = (REPOSITORY_ROOT / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_hash, relative_path
