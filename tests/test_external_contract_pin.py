"""Keep the HACS runtime explicitly pinned to the canonical contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPOSITORY_ROOT / "hausmanhub-contracts.json"

VENDORED_CONTRACT_HASHES = {
    "custom_components/hausman_hub/contracts/v1/api-surfaces.json": "b609af49d1a1fe687a812ec69206706dcef426348b7a64aecfe8e108875da060",
    "custom_components/hausman_hub/contracts/v1/correlation-surfaces.json": "c4f5ba4907870ab5c6ed7842afc55939d1dcd3a5753c25431fda0ad0a8de37be",
    "custom_components/hausman_hub/contracts/v1/optional-capabilities.json": "432bb61e1fdeddb9717ec6a54ef88544e40b687b7243174b790ca5f622d9f62d",
    "custom_components/hausman_hub/contracts/v1/manual-light-off-protection.schema.json": "b5bfacfc6ce240adce2299cb14417e409935bb55a89ea8eecafd91a7443decff",
    "custom_components/hausman_hub/contracts/v1/manual-light-off-protection-settings-request.schema.json": "d542048068355535a5410e089b28357bbb9b33ec20ff1748fb064b21bcd11cc7",
    "custom_components/hausman_hub/contracts/v1/manual-light-off-protection-release-request.schema.json": "0149d7ae956e43f471b61f4c8589db9aa87cd022a16d285b7d239373bb4abd6e",
    "custom_components/hausman_hub/contracts/v1/manual-light-off-protection-command-receipt.schema.json": "911a4bb43467f718410dd4d4f98fcfb1f62acc2cf6e049bf8b27c5a56086757e",
    "custom_components/hausman_hub/contracts/v1/fixtures/manual-light-off-protection.json": "20e92471ae16037036e6a704f37ca474fa68e75c4a91dea655df57d82e41cbc0",
    "custom_components/hausman_hub/contracts/v1/fixtures/manual-light-off-protection-settings-request.json": "36d6d5b0e77baa2f8d25ae391b1c4d00ea3ea80fd4d1240093264362969bd690",
    "custom_components/hausman_hub/contracts/v1/fixtures/manual-light-off-protection-release-request.json": "9a92e9387b4216bd4685992c14471571861f9753b72f10fd793df9f486426169",
    "custom_components/hausman_hub/contracts/v1/fixtures/manual-light-off-protection-command-receipt.json": "9ff82f3668ce16b9caa9627f3fe4c29f8af5c5566d14e851508fd26698a82752",
    "custom_components/hausman_hub/contracts/v1/climate-reliability-semantic-rules.json": "1dbd75b40be1cfb5d1c23fce79819a047ba7a28556938782150dabeb88b6a6b2",
    "custom_components/hausman_hub/contracts/v1/climate-room-recovery-receipt.schema.json": "d34b54a177fcc5c77cd48d28233f4f90e6fde07e17596cfdbe27bde4c56246fe",
    "custom_components/hausman_hub/contracts/v1/climate-room-recovery-request.schema.json": "80b560590455e94dd944c2a6bbcbb00b47528e95ce8d036d320d485d156579e9",
    "custom_components/hausman_hub/contracts/v1/climate-runtime.schema.json": "964fa93b11fcee6bd5c705a5631af1609c44bd0324f5b022bc37fb86c61fbaa3",
    "fixtures/hausmanhub_climate_reliability_v1/climate-room-recovery-receipt.json": "b615ab5e034ca7fa97153b81a07c3bb062a0fe9d9224e94a3e6f080bab9c707e",
    "fixtures/hausmanhub_climate_reliability_v1/climate-room-recovery-request.json": "ddd81c5f38871f247466ffed040153eb2c77610885c6dfcd6345b598a5172957",
    "custom_components/hausman_hub/contracts/v1/scenario-definition.schema.json": "60e93c0e3a970c684a9a95acd471ffd9664652e61fc5e29f5f21c7463285ea57",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-status.schema.json": "d1d219653a87c5d68834afd5bea6544924967d42a4053e6ea2269e0780766d6f",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-execution.schema.json": "1780fbeb06bc5777217e3a18059158354f28bce61cdf8aadab67e6a61ae1d597",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-source.schema.json": "4f281f11c55a235b78bb418977f948a06d4d1ba8b90b1fef4e389b3842e9f7ec",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-source-update-request.schema.json": "7712607bfe1e8cbe9a9287ea4812caed2ac2c47e93ff079458fea0a945a677a0",
    "custom_components/hausman_hub/contracts/v1/scenario-node-red-source-update-receipt.schema.json": "b57b3adcbd850af02c3c1112de9eef70fcf2e41483ae4c633b20d6db6dd33040",
    "custom_components/hausman_hub/contracts/v1/api-capabilities.schema.json": "c0ec10eed3325d356795d2c55333667c5643dda91ca17478560780ca970cd20b",
    "custom_components/hausman_hub/contracts/v1/scenario-ai-draft-request.schema.json": "eeb72fc172f8730552fb174a1a0a55f58debeb9b5612068b0c2889190c37b9c1",
    "custom_components/hausman_hub/contracts/v1/scenario-ai-draft.schema.json": "585ec9c8e48c208452f4f80407feacfc04a00c8b580e04a63504fa049adedb5e",
    "custom_components/hausman_hub/contracts/v1/scenario-health.schema.json": "8a14e51bdb531dd674bede8e0e0618a2788df641dc23fc9da2e8af9b821890e4",
    "custom_components/hausman_hub/contracts/v1/device-action-request.schema.json": "ec201063f15aab4614c59e8464da73f9e8490bedd3fc0dc3c2502bf7ecc078cb",
    "custom_components/hausman_hub/contracts/v1/device-action-receipt.schema.json": "82907a7b9326b00a050548665c45fe87e8095fbe31298a02f90b00bea36c4bde",
    "custom_components/hausman_hub/contracts/v1/intercom-release-receipt.schema.json": "ea11c82a77a3fe9195513d1a915336167c81a356fa0b56cf058384daedacf947",
    "custom_components/hausman_hub/contracts/v1/scenario-catalog.schema.json": "3b43fa73e6c254ed0f87188a9f7d50144ee43dd7542233b4dcaf32d3d2629f55",
    "custom_components/hausman_hub/contracts/v1/scenario-dry-run-result.schema.json": "533a303e6e9057efd8660972f3f1216b125cace27a152f9d029767968cc40a9f",
    "custom_components/hausman_hub/contracts/v1/operation-journal.schema.json": "dad001704411f7b40746f20493953d213d8591f1e732cd12ea86a2c0b6104092",
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
    "custom_components/hausman_hub/contracts/v1/event-stream-message.schema.json": "c61f1dbf6371e388cb92a7dd416e94860f75224299b1fe0c58f0521299ca2abc",
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
    "custom_components/hausman_hub/contracts/v1/api-error.schema.json": "5f67860b5a3baedcabd70329e320f4a6d0b3d599c056a648d3b4d71c4fd474e8",
    "custom_components/hausman_hub/contracts/v1/error-taxonomy.json": "a506b7a13a6c8c8ae7a69df629d657c2e7bbbdb73ccfb0a1802eeae675108866",
    "custom_components/hausman_hub/contracts/v1/error-taxonomy.schema.json": "b6884ec927f83bb8907b856d1a4c438a75762342252b20a758b0e2d0b16d0ef4",
    "custom_components/hausman_hub/contracts/v1/fixtures/api-error.json": "24c338d919e0119c38869ff7a54d2a84395b53ab0b731470414eb341123c2c78",
    "custom_components/hausman_hub/contracts/v1/api-error-idempotency-conflict.schema.json": "bcf1642ce7cc3428a1b39f9613df857fdd517d98d0e51cc0c1172541c1d8b0c1",
    "custom_components/hausman_hub/contracts/v1/api-error-not-acceptable.schema.json": "1f54dd304c0fa59e565a258f07d85bd1aea6fd02520abf2115de6223fa3e636a",
    "custom_components/hausman_hub/contracts/v1/device-action-value.schema.json": "bf7856e9584156b9aba1ca08f407d8159dbef1a94c70bf1e2f33b429befdb4cb",
    "custom_components/hausman_hub/contracts/v1/device-state-evidence-reason-code.schema.json": "7180b1d07ede8e77b4fdda23af421b82c3fdf3846e0f53b39b5f6dd140b3007b",
    "custom_components/hausman_hub/contracts/v1/device-state-evidence.schema.json": "4e658a34e9254b49473324ea90eab1c7accc769e746340367afd244269feea6c",
    "custom_components/hausman_hub/contracts/v1/device-state-evidence-reason-codes.json": "667409c7671a11f686dc4872dcebb456cfa1846ce16a5ee2caf170aea943bc90",
    "custom_components/hausman_hub/contracts/v1/error-taxonomy.schema.json": "b6884ec927f83bb8907b856d1a4c438a75762342252b20a758b0e2d0b16d0ef4",
    "custom_components/hausman_hub/contracts/v1/error-taxonomy.json": "a506b7a13a6c8c8ae7a69df629d657c2e7bbbdb73ccfb0a1802eeae675108866",
    "custom_components/hausman_hub/contracts/v1/lighting-profile-snapshots.json": "ef8a54c3a83da87986335c5d41dcfaa489945e4117eecfdc7094770466319aec",
}


def test_external_contract_pin_is_explicit_and_canonical() -> None:
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))

    assert pin == {
        "repository": "shumkiiv/hausmanhub-contracts",
        "version": "0.65.0",
        "commit": "e2ba65a",
        "canonical": True,
        "role": "runtime-consumer",
    }


def test_contract_0_65_0_vendored_files_match_canonical_hashes() -> None:
    for relative_path, expected_hash in VENDORED_CONTRACT_HASHES.items():
        payload = (REPOSITORY_ROOT / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_hash, relative_path


def test_manual_protection_contract_pin_covers_the_complete_surface() -> None:
    required = {
        "api-surfaces.json", "correlation-surfaces.json", "optional-capabilities.json",
        "manual-light-off-protection.schema.json",
        "manual-light-off-protection-settings-request.schema.json",
        "manual-light-off-protection-release-request.schema.json",
        "manual-light-off-protection-command-receipt.schema.json",
        "fixtures/manual-light-off-protection.json",
        "fixtures/manual-light-off-protection-settings-request.json",
        "fixtures/manual-light-off-protection-release-request.json",
        "fixtures/manual-light-off-protection-command-receipt.json",
    }
    covered = {path.removeprefix("custom_components/hausman_hub/contracts/v1/") for path in VENDORED_CONTRACT_HASHES}
    assert required <= covered
