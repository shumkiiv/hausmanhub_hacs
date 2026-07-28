"""Unit tests for the immutable HausmanHub IR-code domain model."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.domain.ir_codes import (
    IR_CODE_REGISTRY_VERSION,
    IRCodeRegistry,
    IRCodeSource,
    IRCodeViolation,
    IRCommandCode,
    generate_code_id,
    ir_code_registry_from_payload,
    ir_code_registry_to_payload,
)


def _code(
    *,
    code_id: str = "ir_living_cool",
    command_name: str = "cool_24",
    source: IRCodeSource = IRCodeSource.MANUAL,
    created_at: int = 1_800_000_000,
) -> IRCommandCode:
    return IRCommandCode(
        code_id=code_id,
        device_id="living_ac",
        remote_entity_id="remote.living_ir",
        command_name=command_name,
        code_data="JgBQAAABK5QUERQRFDUUNRQ1FDYUNRQ1FDYUNRQ1FTUUNhQ1FAAFKxU=",
        source=source,
        created_at=created_at,
    )


class IRCommandCodeTest(unittest.TestCase):
    """Validate individual IR codes at the pure domain boundary."""

    def test_code_keeps_all_validated_fields(self) -> None:
        code = _code(source=IRCodeSource.BROADLINK)

        self.assertEqual("ir_living_cool", code.code_id)
        self.assertEqual("living_ac", code.device_id)
        self.assertEqual("remote.living_ir", code.remote_entity_id)
        self.assertEqual("cool_24", code.command_name)
        self.assertIs(IRCodeSource.BROADLINK, code.source)
        self.assertEqual(1_800_000_000, code.created_at)

    def test_source_enum_has_only_approved_source_values(self) -> None:
        self.assertEqual("smartir", IRCodeSource.SMARTIR.value)
        self.assertEqual("broadlink", IRCodeSource.BROADLINK.value)
        self.assertEqual("manual", IRCodeSource.MANUAL.value)

    def test_code_rejects_missing_or_invalid_required_values(self) -> None:
        with self.assertRaises(IRCodeViolation):
            _code(code_id="")
        with self.assertRaises(IRCodeViolation):
            IRCommandCode(
                code_id="ir_living_cool",
                device_id="living_ac",
                remote_entity_id="remote.living_ir",
                command_name="",
                code_data="payload",
                source=IRCodeSource.MANUAL,
                created_at=1,
            )
        with self.assertRaises(IRCodeViolation):
            _code(source="manual")  # type: ignore[arg-type]


class IRCodeRegistryTest(unittest.TestCase):
    """Test registry identity, serialization, and immutable updates."""

    def test_generate_code_id_is_deterministic_and_distinct_per_command(self) -> None:
        first = generate_code_id("living_ac", "cool_24")
        same = generate_code_id("living_ac", "cool_24")
        other = generate_code_id("living_ac", "heat_22")

        self.assertEqual(first, same)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("ir_"))

    def test_payload_round_trip_preserves_registry(self) -> None:
        registry = IRCodeRegistry(codes=(_code(source=IRCodeSource.SMARTIR),))

        payload = ir_code_registry_to_payload(registry)
        restored = ir_code_registry_from_payload(payload)

        self.assertEqual(registry, restored)
        self.assertEqual(IR_CODE_REGISTRY_VERSION, payload["version"])
        self.assertEqual("smartir", payload["codes"][0]["source"])  # type: ignore[index]

    def test_registry_filters_looks_up_and_replaces_codes_immutably(self) -> None:
        earlier = _code(created_at=1)
        newer = _code(code_id="ir_living_cool_new", created_at=2)
        registry = IRCodeRegistry(codes=(earlier, newer))

        self.assertEqual((earlier, newer), registry.codes_for_device("living_ac"))
        self.assertIs(newer, registry.code_for_command("living_ac", "cool_24"))
        self.assertIs(earlier, registry.code("ir_living_cool"))
        self.assertEqual((newer,), registry.without(earlier.code_id).codes)

        replacement = _code(code_id=earlier.code_id, command_name="off")
        self.assertEqual(
            (newer, replacement), registry.with_code(replacement).codes
        )

    def test_payload_rejects_wrong_version_and_missing_required_fields(self) -> None:
        with self.assertRaises(IRCodeViolation):
            ir_code_registry_from_payload({"version": 2, "codes": []})
        with self.assertRaises((IRCodeViolation, ValueError)):
            ir_code_registry_from_payload(
                {
                    "version": IR_CODE_REGISTRY_VERSION,
                    "codes": [{"code_id": "ir_missing"}],
                }
            )


if __name__ == "__main__":
    unittest.main()
