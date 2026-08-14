"""Public command-block taxonomy tests."""

from custom_components.hausman_hub.application.command_blocking import (
    public_command_block,
)


def test_internal_reasons_collapse_to_six_public_codes() -> None:
    blocks = [
        public_command_block(reason)
        for reason in (
            "device_unavailable",
            "climate_state_stale",
            "climate_action_unsupported",
            "climate_authority_not_ready",
            "revision_conflict",
            "dependency_unavailable",
        )
    ]
    assert all(block is not None for block in blocks)
    mapped = {block["code"] for block in blocks if block is not None}

    assert mapped == {
        "offline",
        "stale_evidence",
        "unsupported_action",
        "safety_policy",
        "conflict",
        "dependency_unavailable",
    }


def test_public_block_is_bounded_russian_and_has_no_internal_identifier() -> None:
    block = public_command_block("power_source_unavailable")

    assert block == {
        "code": "dependency_unavailable",
        "message": "Сначала проверьте связанное устройство питания.",
        "recommendedAction": "check_device",
    }
    assert "entity" not in repr(block).lower()
    assert public_command_block("invalid_request") is None
