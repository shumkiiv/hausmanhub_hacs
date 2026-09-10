"""Fail-closed handover of five exact native Home Assistant automations."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import copy
import hashlib
import json
from types import SimpleNamespace

from .scenario_consolidation_inventory import NATIVE_AUTOMATIONS_TO_DISABLE
from .smart_switch_bindings import SmartSwitchBindings


NATIVE_AUTOMATION_ENTITY_IDS = {
    "hausman_night_absence_small_corridor_light_off": (
        "automation.malyi_koridor_nochiu_vykliuchit_svet_cherez_3_minuty_otsutstviia"
    ),
    "hausman_night_absence_tambur_light_off": (
        "automation.tambur_nochiu_vykliuchit_svet_cherez_3_minuty_otsutstviia"
    ),
    "hausman_shower_cabinet_off_absence_failsafe": (
        "automation.dushevaia_vykliuchit_podsvetku_shkafa_pri_otsutstvii"
    ),
    "hausman_shower_fan_humidity_on_failsafe": (
        "automation.dushevaia_vkliuchit_vytiazhku_po_vysokoi_vlazhnosti"
    ),
    "hausman_shower_fan_off_absence_normal_humidity": (
        "automation.dushevaia_vykliuchit_vytiazhku_pri_otsutstvii_i_normalnoi_vlazhnosti"
    ),
}
NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS = {
    "1715696143987": "automation.vykliuchenie_sveta_po_nazhatiiu_knopki",
    "1715696249639": "automation.vkliuchenie_sveta_po_knopke_detskaia",
    "1716009214484": "automation.obnaruzhenie_protechki_tualet",
    "1735193005877": "automation.perezapusk_ha",
    "1767412144158": "automation.datchik_protechki_kukhnia",
    "1767412233902": "automation.datchik_protechki_tualet",
    "1767417411927": "automation.vneshnii_datchik_temperatury_termogolovki_gostinnaia",
    "1767419808340": "automation.novaia_avtomatizatsiia",
    "1767459525210": "automation.novaia_avtomatizatsiia_2",
    "hausman_tambur_mirror_switch_all_keys": "automation.tambur_svet_po_liuboi_klavishe_vykliuchatelia_zerkala",
    "hausmanhub_shower_leak_yandex_alert": "automation.hausman_dushevaia_golosovaia_trevoga_iandeks",
    "hausmanhub_yandex_dialog_conversation": "automation.hausmanhub_dialog_alisy_cherez_assist",
    "hausmanhub_yandex_welcome_home": "automation.hausmanhub_privetstvie_pri_vozvrashchenii_domoi",
}

# Canonical JSON digests of the exact definitions in native18.json. They are
# release evidence, not names or a permissive shape check.
_NATIVE_DEFINITION_HASHES = {
    "1715696143987": "535a43716df9011d35b642cd47332ffea92d6520df5dbc893e1cb2c4fccc89ed",
    "1715696249639": "ecd8c99445d81b550c576e83db1b56dd7f2a9f87bfb3b3694b1a3afd1879a188",
    "1716009214484": "73c256f1855c0b1d1a6e406edbf23f5c6868021a67ff2baf1d80c62aae7e62a4",
    "1735193005877": "145baa88f8eb5f6881ebb09238754fa85390549d2f11cc5819a96d4f4b293707",
    "1767412144158": "2e67fb3ceac6dc07df5dff073071043f938027ad97abe08d9035e446594a4189",
    "1767412233902": "f6f5b9d589cf901cddc8835ec2d2858a4f14903a8c65811bf7c381f36de1d597",
    "1767417411927": "88828e73d242814d79a355b312808bc27beadeb09780c56f514239069e68306c",
    "1767419808340": "e706016cd0ece2453e26e47a835454afe0089573ea6ae591f06a1db4a598388a",
    "1767459525210": "f94385ef4e6d7e093543129f8248fad3f555c540bfa6883affcb5138892d4242",
    "hausman_night_absence_small_corridor_light_off": "f5e3781ce6ca4800004b86931d99298d28d77eec960f80b39e9f4e1743b199c4",
    "hausman_night_absence_tambur_light_off": "35cf1b7b409094c4940bd652e701acab0605ccbc40e649358bb0149c77da8330",
    "hausman_shower_cabinet_off_absence_failsafe": "d239f31392212a01ed59b4c986aade5bb597e66f66fad43adefe1b6dbc02430b",
    "hausman_shower_fan_humidity_on_failsafe": "57d91b50cae1fa3709fd0e2bf855babac216297f5461acab878165d2d6c4699d",
    "hausman_shower_fan_off_absence_normal_humidity": "b0c5ba451e1caf49a6019b7415334ee7201d29c6180bf15c0087a737f987c93e",
    "hausman_tambur_mirror_switch_all_keys": "9fb1c3960742170f6a83cfe0989c2b8d59516ea4c7dca652dfafc5a9559467a1",
    "hausmanhub_shower_leak_yandex_alert": "ae2c54b501cc43d4e162b8b7f00cc2f5d9ed2be2effbea4ae09f66eba75b0489",
    "hausmanhub_yandex_dialog_conversation": "d3b662cb1372cfac89b76b0c5d8aae841ac265f0279a887c43d8e7d83f5640ea",
    "hausmanhub_yandex_welcome_home": "04f40fa9127951949ef6f02562b5b535e7998d37a286fc2f03506c6e1f6da818",
}
_NATIVE_EXPECTED_STATES = {
    "1715696143987": "on",
    "1715696249639": "on",
    "1716009214484": "off",
    "1735193005877": "off",
    "1767412144158": "off",
    "1767412233902": "off",
    "1767417411927": "on",
    "1767419808340": "on",
    "1767459525210": "on",
    "hausman_night_absence_small_corridor_light_off": "on",
    "hausman_night_absence_tambur_light_off": "on",
    "hausman_shower_cabinet_off_absence_failsafe": "on",
    "hausman_shower_fan_humidity_on_failsafe": "on",
    "hausman_shower_fan_off_absence_normal_humidity": "on",
    "hausman_tambur_mirror_switch_all_keys": "on",
    "hausmanhub_shower_leak_yandex_alert": "on",
    "hausmanhub_yandex_dialog_conversation": "off",
    "hausmanhub_yandex_welcome_home": "off",
}
EXPECTED_NATIVE_AUTOMATIONS = {
    entity_id: {
        "automationId": automation_id,
        "definitionHash": _NATIVE_DEFINITION_HASHES[automation_id],
        "state": _NATIVE_EXPECTED_STATES[automation_id],
    }
    for automation_id, entity_id in {
        **NATIVE_AUTOMATION_ENTITY_IDS,
        **NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS,
    }.items()
}
_DISABLE_ENTITIES = tuple(NATIVE_AUTOMATION_ENTITY_IDS.values())
_PRESERVE_ENTITIES = tuple(NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS.values())
_ALL_ENTITIES = _DISABLE_ENTITIES + _PRESERVE_ENTITIES
_MIRROR_AUTOMATION_ENTITY_ID = NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS[
    "hausman_tambur_mirror_switch_all_keys"
]
_MIRROR_FIXTURE_DEVICE_ID = "synthetic-tambur-mirror-device"
_MIRROR_TRIGGER_KEYS = frozenset(
    {"platform", "domain", "device_id", "type", "subtype"}
)
_MIRROR_TRIGGER_SUBTYPES = ("1_single", "1_double")
_EVIDENCE_KEYS = {
    "state",
    "automationId",
    "definitionHash",
    "contextId",
    "lastUpdated",
}


class NativeAutomationMigrationConflict(RuntimeError):
    """The native handover cannot establish exact before/after evidence."""


class NativeAutomationNotReady(NativeAutomationMigrationConflict):
    """The automation component has not exposed its runtime entities yet."""


def _valid_evidence(entity_id: str, value: object) -> bool:
    expected = EXPECTED_NATIVE_AUTOMATIONS.get(entity_id)
    return bool(
        expected is not None
        and isinstance(value, Mapping)
        and set(value) == _EVIDENCE_KEYS
        and value.get("state") in {"on", "off"}
        and value.get("automationId") == expected["automationId"]
        and value.get("definitionHash") == expected["definitionHash"]
        and isinstance(value.get("contextId"), str)
        and bool(value.get("contextId"))
        and isinstance(value.get("lastUpdated"), str)
        and bool(value.get("lastUpdated"))
    )


def _stable_evidence(value: Mapping[str, object]) -> tuple[object, ...]:
    return (
        value.get("state"),
        value.get("automationId"),
        value.get("definitionHash"),
    )


def _valid_image(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == set(_ALL_ENTITIES)
        and all(
            _valid_evidence(entity_id, evidence)
            for entity_id, evidence in value.items()
        )
    )


def _valid_operation(
    entity_id: str, value: object, before: Mapping[str, object]
) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "phase",
        "operationId",
        "expected",
        "after",
        "restoreId",
        "restored",
    }:
        return False
    phase = value.get("phase")
    expected = value.get("expected")
    if (
        phase not in {"intent", "applied", "restore_intent", "restored"}
        or not isinstance(value.get("operationId"), str)
        or not value.get("operationId")
        or not _valid_evidence(entity_id, expected)
        or expected.get("state") != before[entity_id].get("state")
    ):
        return False
    after = value.get("after")
    if phase == "intent":
        return (
            after is None
            and value.get("restoreId") is None
            and value.get("restored") is None
        )
    if not _valid_evidence(entity_id, after) or after.get("state") != "off":
        return False
    if phase == "applied":
        return value.get("restoreId") is None and value.get("restored") is None
    if not isinstance(value.get("restoreId"), str) or not value.get("restoreId"):
        return False
    if phase == "restore_intent":
        return value.get("restored") is None
    restored = value.get("restored")
    return bool(
        _valid_evidence(entity_id, restored)
        and restored.get("state") == before[entity_id].get("state")
    )


def valid_native_automation_migration_payload(value: object) -> bool:
    """Reject any receipt that is not a complete native state machine image."""

    if not isinstance(value, Mapping) or set(value) != {
        "version",
        "state",
        "mode",
        "before",
        "baseline",
        "operations",
        "after",
    }:
        return False
    if (
        value.get("version") != 1
        or value.get("state") not in {"prepared", "completed"}
        or value.get("mode") not in {"apply", "rollback"}
    ):
        return False
    before = value.get("before")
    baseline = value.get("baseline")
    operations = value.get("operations")
    after = value.get("after")
    if (
        not _valid_image(before)
        or not _valid_image(baseline)
        or not isinstance(operations, Mapping)
        or not set(operations) <= set(_DISABLE_ENTITIES)
        or not all(
            _valid_operation(entity_id, operation, before)
            for entity_id, operation in operations.items()
        )
    ):
        return False
    if value["state"] == "prepared":
        if after is not None:
            return False
        if value["mode"] == "apply":
            return all(
                operation.get("phase") in {"intent", "applied"}
                for operation in operations.values()
            )
        return True
    if (
        value["mode"] != "apply"
        or not _valid_image(after)
        or set(operations) != set(_DISABLE_ENTITIES)
    ):
        return False
    return bool(
        all(
            operations[entity_id].get("phase") == "applied"
            and dict(operations[entity_id]["after"]) == dict(after[entity_id])
            and after[entity_id].get("state") == "off"
            for entity_id in _DISABLE_ENTITIES
        )
        and all(
            _stable_evidence(after[entity_id])
            == _stable_evidence(baseline[entity_id])
            for entity_id in _PRESERVE_ENTITIES
        )
    )


def _clone(value: Mapping[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _modified_image_matches(
    current: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    return all(
        dict(current[entity_id]) == dict(expected[entity_id])
        for entity_id in _DISABLE_ENTITIES
    ) and all(
        _stable_evidence(current[entity_id])
        == _stable_evidence(expected[entity_id])
        for entity_id in _PRESERVE_ENTITIES
    )


def _completed_image_matches(
    current: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    return all(
        _stable_evidence(current[entity_id])
        == _stable_evidence(expected[entity_id])
        for entity_id in _ALL_ENTITIES
    )


class NativeAutomationMigration:
    """Disable five exact rules with durable per-operation CAS evidence."""

    def __init__(self, adapter: object, store: object) -> None:
        if set(NATIVE_AUTOMATION_ENTITY_IDS) != NATIVE_AUTOMATIONS_TO_DISABLE:
            raise RuntimeError("native automation release map is incomplete")
        self._adapter = adapter
        self._store = store

    async def _save(self, journal: dict[str, object]) -> None:
        if not valid_native_automation_migration_payload(journal):
            raise NativeAutomationMigrationConflict(
                "native automation journal is invalid"
            )
        await self._store.async_save(_clone(journal))

    def _operation_id(self) -> str:
        create = getattr(self._adapter, "new_operation_id", None)
        if not callable(create):
            raise NativeAutomationMigrationConflict(
                "native automation CAS context is unavailable"
            )
        value = create()
        if not isinstance(value, str) or not value:
            raise NativeAutomationMigrationConflict(
                "native automation CAS context is invalid"
            )
        return value

    @staticmethod
    def _require_initial_image(image: Mapping[str, object]) -> None:
        if not _valid_image(image) or any(
            image[entity_id].get("state") != expected["state"]
            for entity_id, expected in EXPECTED_NATIVE_AUTOMATIONS.items()
        ):
            raise NativeAutomationMigrationConflict(
                "native automation source image changed"
            )

    async def _snapshot(self) -> dict[str, object]:
        snapshot = await self._adapter.async_snapshot(_ALL_ENTITIES)
        if not _valid_image(snapshot):
            raise NativeAutomationMigrationConflict(
                "native automation evidence is incomplete"
            )
        return _clone(snapshot)

    async def _reconcile_intent(
        self, journal: dict[str, object], entity_id: str
    ) -> None:
        operation = journal["operations"][entity_id]
        if operation["phase"] != "intent":
            return
        current = (await self._adapter.async_snapshot((entity_id,)))[entity_id]
        expected = operation["expected"]
        if dict(current) == dict(expected):
            return
        if (
            _valid_evidence(entity_id, current)
            and current.get("state") == "off"
            and current.get("automationId") == expected.get("automationId")
            and current.get("definitionHash") == expected.get("definitionHash")
            and current.get("contextId") == operation["operationId"]
        ):
            operation["after"] = dict(current)
            operation["phase"] = "applied"
            await self._save(journal)
            return
        raise NativeAutomationMigrationConflict(
            "native automation disable outcome is ambiguous"
        )

    async def _finish_rollback(self, journal: dict[str, object]) -> bool:
        journal["state"] = "prepared"
        journal["mode"] = "rollback"
        journal["after"] = None
        await self._save(journal)
        for entity_id in reversed(_DISABLE_ENTITIES):
            operation = journal["operations"].get(entity_id)
            if operation is None:
                continue
            if operation["phase"] == "intent":
                await self._reconcile_intent(journal, entity_id)
                operation = journal["operations"][entity_id]
                if operation["phase"] == "intent":
                    continue
            if operation["phase"] == "restored":
                current = (
                    await self._adapter.async_snapshot((entity_id,))
                )[entity_id]
                if dict(current) != dict(operation["restored"]):
                    return False
                journal["baseline"][entity_id] = dict(current)
                continue
            if operation["phase"] == "restore_intent":
                current = (
                    await self._adapter.async_snapshot((entity_id,))
                )[entity_id]
                if (
                    current.get("state")
                    == journal["before"][entity_id]["state"]
                    and current.get("contextId") == operation["restoreId"]
                    and current.get("automationId")
                    == operation["after"]["automationId"]
                    and current.get("definitionHash")
                    == operation["after"]["definitionHash"]
                ):
                    operation["restored"] = dict(current)
                    operation["phase"] = "restored"
                    journal["baseline"][entity_id] = dict(current)
                    await self._save(journal)
                    continue
                if dict(current) != dict(operation["after"]):
                    return False
            else:
                current = (
                    await self._adapter.async_snapshot((entity_id,))
                )[entity_id]
                if dict(current) != dict(operation["after"]):
                    return False
                operation["restoreId"] = self._operation_id()
                operation["phase"] = "restore_intent"
                await self._save(journal)
            restored = await self._adapter.async_restore(
                entity_id,
                operation["after"],
                journal["before"][entity_id]["state"],
                operation_id=operation["restoreId"],
            )
            if not _valid_evidence(entity_id, restored):
                return False
            operation["restored"] = dict(restored)
            operation["phase"] = "restored"
            journal["baseline"][entity_id] = dict(restored)
            await self._save(journal)
        journal["operations"] = {}
        journal["mode"] = "apply"
        await self._save(journal)
        return True

    async def async_apply(self) -> None:
        required_adapter = (
            "async_snapshot",
            "async_disable",
            "async_restore",
            "new_operation_id",
        )
        if not all(
            callable(getattr(self._adapter, name, None))
            for name in required_adapter
        ) or not all(
            callable(getattr(self._store, name, None))
            for name in ("async_load", "async_save")
        ):
            raise NativeAutomationMigrationConflict(
                "native automation handover is unavailable"
            )
        loaded = await self._store.async_load()
        if loaded is None:
            before = await self._snapshot()
            self._require_initial_image(before)
            journal: dict[str, object] = {
                "version": 1,
                "state": "prepared",
                "mode": "apply",
                "before": before,
                "baseline": _clone(before),
                "operations": {},
                "after": None,
            }
            await self._save(journal)
        elif valid_native_automation_migration_payload(loaded):
            journal = _clone(loaded)
        else:
            raise NativeAutomationMigrationConflict(
                "native automation journal is invalid"
            )

        if journal["state"] == "completed":
            current = await self._snapshot()
            if not _completed_image_matches(current, journal["after"]):
                raise NativeAutomationMigrationConflict(
                    "native automation completion drifted"
                )
            return
        if journal["mode"] == "rollback" and not await self._finish_rollback(
            journal
        ):
            raise NativeAutomationMigrationConflict(
                "native automation recovery is required"
            )

        for entity_id in tuple(journal["operations"]):
            await self._reconcile_intent(journal, entity_id)
        expected_current = _clone(journal["baseline"])
        for entity_id, operation in journal["operations"].items():
            if operation["phase"] == "applied":
                expected_current[entity_id] = dict(operation["after"])
        current = await self._snapshot()
        if not _modified_image_matches(current, expected_current):
            raise NativeAutomationMigrationConflict(
                "native automation baseline drifted"
            )
        preserve_changed = False
        for entity_id in _PRESERVE_ENTITIES:
            if current[entity_id] != journal["baseline"][entity_id]:
                journal["baseline"][entity_id] = dict(current[entity_id])
                preserve_changed = True
        if preserve_changed:
            await self._save(journal)
        try:
            for entity_id in _DISABLE_ENTITIES:
                operation = journal["operations"].get(entity_id)
                if operation is None:
                    operation = {
                        "phase": "intent",
                        "operationId": self._operation_id(),
                        "expected": dict(journal["baseline"][entity_id]),
                        "after": None,
                        "restoreId": None,
                        "restored": None,
                    }
                    journal["operations"][entity_id] = operation
                    await self._save(journal)
                await self._reconcile_intent(journal, entity_id)
                operation = journal["operations"][entity_id]
                if operation["phase"] == "intent":
                    after = await self._adapter.async_disable(
                        entity_id,
                        operation["expected"],
                        operation_id=operation["operationId"],
                    )
                    if (
                        not _valid_evidence(entity_id, after)
                        or after.get("state") != "off"
                    ):
                        raise NativeAutomationMigrationConflict(
                            "native automation did not stop"
                        )
                    operation["after"] = dict(after)
                    operation["phase"] = "applied"
                    await self._save(journal)
            after = await self._snapshot()
            if any(
                after[entity_id].get("state") != "off"
                or after[entity_id]
                != journal["operations"][entity_id]["after"]
                for entity_id in _DISABLE_ENTITIES
            ) or any(
                _stable_evidence(after[entity_id])
                != _stable_evidence(journal["baseline"][entity_id])
                for entity_id in _PRESERVE_ENTITIES
            ):
                raise NativeAutomationMigrationConflict(
                    "native automation final image changed"
                )
            journal["after"] = after
            journal["state"] = "completed"
            await self._save(journal)
        except BaseException as error:
            try:
                recovered = await asyncio.shield(self._finish_rollback(journal))
            except BaseException as recovery_error:
                raise NativeAutomationMigrationConflict(
                    "native automation recovery is required"
                ) from recovery_error
            if not recovered:
                raise NativeAutomationMigrationConflict(
                    "native automation recovery is required"
                ) from error
            raise

    async def async_require_ready(self) -> None:
        """Read and validate all native objects without writing a receipt."""

        await self._snapshot()

    async def async_verify_completed(self) -> bool:
        loaded = await self._store.async_load()
        if (
            not valid_native_automation_migration_payload(loaded)
            or loaded.get("state") != "completed"
        ):
            return False
        return _completed_image_matches(await self._snapshot(), loaded["after"])

    async def async_rollback(self) -> bool:
        """Restore only five objects still matching this migration's writes."""

        loaded = await self._store.async_load()
        if not valid_native_automation_migration_payload(loaded):
            return False
        journal = _clone(loaded)
        current = await self._snapshot()
        expected = (
            journal["after"]
            if journal["state"] == "completed"
            else journal["baseline"]
        )
        if not _modified_image_matches(current, expected):
            return False
        return await self._finish_rollback(journal)


def _normalize_definition(value: object, *, top_level: bool = False) -> object:
    if isinstance(value, Mapping):
        aliases = {
            "triggers": "trigger",
            "conditions": "condition",
            "actions": "action",
        }
        normalized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise NativeAutomationMigrationConflict(
                    "native automation definition is invalid"
                )
            key = aliases.get(raw_key, raw_key) if top_level else raw_key
            if key in normalized:
                raise NativeAutomationMigrationConflict(
                    "native automation definition aliases conflict"
                )
            normalized[key] = _normalize_definition(raw_value)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_definition(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise NativeAutomationMigrationConflict(
        "native automation definition is invalid"
    )


def _canonical_native_definition(
    entity_id: str,
    value: object,
    smart_switch_bindings: SmartSwitchBindings | None,
) -> object:
    normalized = _normalize_definition(value, top_level=True)
    if entity_id != _MIRROR_AUTOMATION_ENTITY_ID:
        return normalized
    if not isinstance(smart_switch_bindings, SmartSwitchBindings):
        raise NativeAutomationMigrationConflict(
            "native automation mirror binding is unavailable"
        )
    devices = smart_switch_bindings.devices
    device_id = devices.get("marmitek")
    if (
        set(devices) != {"shower", "passthrough", "marmitek"}
        or not isinstance(device_id, str)
        or not device_id
        or not isinstance(normalized, Mapping)
    ):
        raise NativeAutomationMigrationConflict(
            "native automation mirror binding is invalid"
        )
    triggers = normalized.get("trigger")
    if not isinstance(triggers, list) or len(triggers) != len(_MIRROR_TRIGGER_SUBTYPES):
        raise NativeAutomationMigrationConflict(
            "native automation mirror definition is invalid"
        )
    for trigger, subtype in zip(triggers, _MIRROR_TRIGGER_SUBTYPES, strict=True):
        if (
            not isinstance(trigger, Mapping)
            or set(trigger) != _MIRROR_TRIGGER_KEYS
            or trigger.get("platform") != "device"
            or trigger.get("domain") != "mqtt"
            or trigger.get("type") != "action"
            or trigger.get("subtype") != subtype
            or trigger.get("device_id") != device_id
        ):
            raise NativeAutomationMigrationConflict(
                "native automation mirror definition is invalid"
            )
    copied = copy.deepcopy(normalized)
    assert isinstance(copied, dict)
    copied["trigger"] = [
        {**dict(trigger), "device_id": _MIRROR_FIXTURE_DEVICE_ID}
        for trigger in triggers
    ]
    return copied


def _definition_hash(
    entity_id: str,
    value: object,
    smart_switch_bindings: SmartSwitchBindings | None,
) -> str:
    try:
        payload = json.dumps(
            _canonical_native_definition(
                entity_id, value, smart_switch_bindings
            ),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise NativeAutomationMigrationConflict(
            "native automation definition is invalid"
        ) from error
    return hashlib.sha256(payload.encode()).hexdigest()


class HomeAssistantNativeAutomationAdapter:
    """Narrow runtime-object boundary for native automation CAS."""

    def __init__(
        self, hass: object, smart_switch_bindings: SmartSwitchBindings | None = None
    ) -> None:
        self._hass = hass
        self._smart_switch_bindings = smart_switch_bindings

    def _automation_component(self) -> object:
        data = getattr(self._hass, "data", None)
        if not isinstance(data, Mapping):
            raise NativeAutomationNotReady(
                "native automation runtime is unavailable"
            )
        try:
            from homeassistant.components.automation import (  # noqa: PLC0415
                DATA_COMPONENT,
            )
        except ModuleNotFoundError:  # Unit tests deliberately omit HA.
            DATA_COMPONENT = "automation"
        component = data.get(DATA_COMPONENT)
        if component is None and DATA_COMPONENT != "automation":
            component = data.get("automation")
        if component is None or not callable(
            getattr(component, "get_entity", None)
        ):
            raise NativeAutomationNotReady(
                "native automation runtime is unavailable"
            )
        return component

    async def async_snapshot(
        self, entity_ids: tuple[str, ...]
    ) -> dict[str, dict[str, str]]:
        if (
            len(entity_ids) != len(set(entity_ids))
            or not set(entity_ids) <= set(_ALL_ENTITIES)
        ):
            raise NativeAutomationMigrationConflict(
                "native automation snapshot scope is invalid"
            )
        states = getattr(self._hass, "states", None)
        get = getattr(states, "get", None)
        if not callable(get):
            raise NativeAutomationNotReady(
                "native automation state is unavailable"
            )
        component = self._automation_component()
        result: dict[str, dict[str, str]] = {}
        for entity_id in entity_ids:
            expected = EXPECTED_NATIVE_AUTOMATIONS[entity_id]
            state = get(entity_id)
            value = getattr(state, "state", None)
            attributes = getattr(state, "attributes", None)
            context = getattr(state, "context", None)
            context_id = getattr(context, "id", None)
            updated = getattr(state, "last_updated", None)
            entity = component.get_entity(entity_id)
            if state is None or entity is None:
                raise NativeAutomationNotReady(
                    "native automation entities are not ready"
                )
            raw_config = getattr(entity, "raw_config", None)
            automation_id = (
                raw_config.get("id") if isinstance(raw_config, Mapping) else None
            )
            attribute_id = (
                attributes.get("id") if isinstance(attributes, Mapping) else None
            )
            definition_hash = _definition_hash(
                entity_id, raw_config, self._smart_switch_bindings
            )
            if (
                value not in {"on", "off"}
                or automation_id != expected["automationId"]
                or attribute_id != expected["automationId"]
                or definition_hash != expected["definitionHash"]
                or not isinstance(context_id, str)
                or not context_id
                or updated is None
            ):
                raise NativeAutomationMigrationConflict(
                    "native automation identity or definition changed"
                )
            last_updated = (
                updated.isoformat()
                if callable(getattr(updated, "isoformat", None))
                else str(updated)
            )
            result[entity_id] = {
                "state": value,
                "automationId": automation_id,
                "definitionHash": definition_hash,
                "contextId": context_id,
                "lastUpdated": last_updated,
            }
        return result

    def new_operation_id(self) -> str:
        try:
            from homeassistant.core import Context  # noqa: PLC0415
        except ModuleNotFoundError:
            from uuid import uuid4  # noqa: PLC0415

            return uuid4().hex
        return Context().id

    async def async_disable(
        self,
        entity_id: str,
        expected: Mapping[str, str],
        *,
        operation_id: str,
    ) -> dict[str, str]:
        current = (await self.async_snapshot((entity_id,)))[entity_id]
        if current != dict(expected):
            raise NativeAutomationMigrationConflict(
                "native automation disable CAS changed"
            )
        if current["state"] == "off":
            return current
        await self._call(
            "turn_off", entity_id, operation_id, stop_actions=True
        )
        after = (await self.async_snapshot((entity_id,)))[entity_id]
        if (
            after["state"] != "off"
            or after["automationId"] != current["automationId"]
            or after["definitionHash"] != current["definitionHash"]
            or after["contextId"] != operation_id
        ):
            raise NativeAutomationMigrationConflict(
                "native automation disable was not confirmed"
            )
        return after

    async def async_restore(
        self,
        entity_id: str,
        expected_current: Mapping[str, str],
        desired_state: str,
        *,
        operation_id: str,
    ) -> dict[str, str]:
        current = (await self.async_snapshot((entity_id,)))[entity_id]
        if current != dict(expected_current):
            raise NativeAutomationMigrationConflict(
                "native automation restore CAS changed"
            )
        if current["state"] == desired_state:
            return current
        if desired_state != "on" or current["state"] != "off":
            raise NativeAutomationMigrationConflict(
                "native automation restore state is invalid"
            )
        await self._call("turn_on", entity_id, operation_id)
        restored = (await self.async_snapshot((entity_id,)))[entity_id]
        if (
            restored["state"] != "on"
            or restored["automationId"] != current["automationId"]
            or restored["definitionHash"] != current["definitionHash"]
            or restored["contextId"] != operation_id
        ):
            raise NativeAutomationMigrationConflict(
                "native automation restore was not confirmed"
            )
        return restored

    async def _call(
        self,
        service: str,
        entity_id: str,
        operation_id: str,
        *,
        stop_actions: bool | None = None,
    ) -> None:
        services = getattr(self._hass, "services", None)
        call = getattr(services, "async_call", None)
        if not callable(call):
            raise NativeAutomationMigrationConflict(
                "native automation service is unavailable"
            )
        try:
            from homeassistant.core import Context  # noqa: PLC0415
        except ModuleNotFoundError:
            context = SimpleNamespace(id=operation_id)
        else:
            context = Context(id=operation_id)
        data: dict[str, object] = {"entity_id": entity_id}
        if service == "turn_off":
            if stop_actions is not True:
                raise NativeAutomationMigrationConflict(
                    "native automation stop policy is invalid"
                )
            data["stop_actions"] = True
        elif stop_actions is not None:
            raise NativeAutomationMigrationConflict(
                "native automation service schema is invalid"
            )
        await call(
            "automation",
            service,
            data,
            blocking=True,
            context=context,
        )


class HomeAssistantNativeAutomationMigrationStore:
    """Atomic verified receipt store for the native handover."""

    def __init__(self, hass: object, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store  # noqa: PLC0415

        from ..verified_safety_storage import (  # noqa: PLC0415
            VerifiedSafetyStore,
        )

        store = Store(
            hass,
            1,
            f"hausman_hub.native_automation_migration.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            store,
            hass.async_add_executor_job,
            payload_validator=valid_native_automation_migration_payload,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)
