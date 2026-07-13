"""Read-only validation for synthetic HASC contract artifacts.

The validators deliberately operate on in-repository JSON fixtures only. They
do not import Home Assistant, access a network, inspect Node-RED, or execute
commands. A successful validation proves schema consistency, never authority
or execution readiness.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any


CONTOUR_IDS = frozenset(
    {
        "common",
        "climate",
        "automation",
        "smart_home_center",
        "ha_custom_integration",
    }
)
DECISION_OWNERS = frozenset({"climate", "automation"})
SAFETY_CLASS_IDS = frozenset(
    {
        "read_only",
        "operator_intent",
        "shadow_only",
        "proxy_only",
        "direct_execution_blocked",
    }
)
READ_ONLY_MODES = frozenset({"read-only", "shadow"})
MISMATCH_CATEGORIES = frozenset(
    {
        "mapping_mismatch",
        "owner_mismatch",
        "stale_data",
        "safety_class_mismatch",
        "projection_mismatch",
        "redaction_gap",
        "unresolved_gap",
    }
)
REPAIR_SEVERITIES = {
    "missing_references": "warning",
    "unsafe_mode": "error",
    "unresolved_owner_contour": "error",
    "stale_parity": "warning",
    "redaction_failure": "critical",
}
REPAIR_STATUSES = frozenset(
    {"open", "visible", "acknowledged", "resolved_manually", "dismissed", "reopened"}
)
ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

COMMON_FORBIDDEN_KEYS = (
    "service",
    "command",
    "payload",
    "node_red",
    "node-red",
    "flow",
    "deploy",
    "threshold",
    "cooldown",
    "direct_execution",
)
EVIDENCE_FORBIDDEN_KEYS = tuple(
    key
    for key in COMMON_FORBIDDEN_KEYS
    if key not in {"threshold", "cooldown", "direct_execution"}
) + (
    "token",
    "secret",
    "password",
    "credential",
    "url",
    "hostname",
    "ip_address",
)
DIAGNOSTICS_FORBIDDEN_KEYS = EVIDENCE_FORBIDDEN_KEYS + (
    "entity_id",
    "device_id_raw",
    "area_id_raw",
    "mac_address",
    "serial_number",
    "unique_id",
    "session_id",
    "webhook",
)
FORBIDDEN_TEXT = ("service call", "physical command", "node-red", "deploy")


def _error(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _mapping(value: Any, path: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        _error(errors, path, "must be an object")
        return None
    return value


def _list(value: Any, path: str, errors: list[str]) -> Sequence[Any] | None:
    if not isinstance(value, list):
        _error(errors, path, "must be a list")
        return None
    return value


def _string(value: Any, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        _error(errors, path, "must be a non-empty string")
        return None
    return value


def _identifier(value: Any, path: str, errors: list[str]) -> str | None:
    value = _string(value, path, errors)
    if value is not None and not ID_RE.fullmatch(value):
        _error(errors, path, "must be a stable lowercase synthetic identifier")
    return value


def _known_contour(value: Any, path: str, errors: list[str]) -> str | None:
    value = _identifier(value, path, errors)
    if value is not None and value not in CONTOUR_IDS:
        _error(errors, path, f"unknown contour_id '{value}'")
    return value


def _known_safety_class(value: Any, path: str, errors: list[str]) -> str | None:
    value = _identifier(value, path, errors)
    if value is not None and value not in SAFETY_CLASS_IDS:
        _error(errors, path, f"unknown safety_class_id '{value}'")
    return value


def _required(item: Mapping[str, Any], key: str, path: str, errors: list[str]) -> Any:
    if key not in item:
        _error(errors, path, f"missing required field '{key}'")
        return None
    return item[key]


def _check_forbidden_keys(
    value: Any, path: str, errors: list[str], forbidden: Sequence[str]
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(term in key_text for term in forbidden):
                _error(errors, f"{path}.{key}", "contains a forbidden execution or sensitive field")
            _check_forbidden_keys(child, f"{path}.{key}", errors, forbidden)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_forbidden_keys(child, f"{path}[{index}]", errors, forbidden)
    elif isinstance(value, str):
        lowered = value.lower()
        if any(term in lowered for term in FORBIDDEN_TEXT):
            _error(errors, path, "contains forbidden execution-oriented text")


def _validate_contour_list(value: Any, path: str, errors: list[str]) -> list[str]:
    items = _list(value, path, errors)
    if items is None:
        return []
    contours: list[str] = []
    for index, item in enumerate(items):
        contour = _known_contour(item, f"{path}[{index}]", errors)
        if contour is not None:
            contours.append(contour)
    return contours


def _validate_reference(
    value: Any, path: str, errors: list[str], targets: Mapping[str, set[str]]
) -> None:
    ref = _string(value, path, errors)
    if ref is None:
        return
    try:
        kind, identifier = ref.split(":", 1)
    except ValueError:
        _error(errors, path, "must use a kind:identifier reference")
        return
    if kind not in targets:
        _error(errors, path, f"uses unknown reference kind '{kind}'")
    elif identifier not in targets[kind]:
        _error(errors, path, f"references unknown {kind} '{identifier}'")


def validate_common_inventory(data: Any) -> list[str]:
    """Return static Common-contract validation errors for a synthetic fixture."""

    errors: list[str] = []
    root = _mapping(data, "$", errors)
    if root is None:
        return errors
    _check_forbidden_keys(root, "$", errors, COMMON_FORBIDDEN_KEYS)

    version = _string(_required(root, "inventory_version", "$", errors), "$.inventory_version", errors)
    if version is not None and not version.startswith("synthetic-"):
        _error(errors, "$.inventory_version", "must start with 'synthetic-'")
    generated_from = _string(_required(root, "generated_from", "$", errors), "$.generated_from", errors)
    if generated_from is not None and not generated_from.startswith("synthetic_"):
        _error(errors, "$.generated_from", "must identify a synthetic source")

    rooms_raw = _list(_required(root, "rooms", "$", errors), "$.rooms", errors) or []
    devices_raw = _list(_required(root, "devices", "$", errors), "$.devices", errors) or []
    capabilities_raw = _list(_required(root, "capabilities", "$", errors), "$.capabilities", errors) or []
    safety_raw = _list(_required(root, "safety_classes", "$", errors), "$.safety_classes", errors) or []
    membership_raw = _list(
        _required(root, "contour_membership", "$", errors), "$.contour_membership", errors
    ) or []
    descriptors_raw = _list(root.get("action_descriptors", []), "$.action_descriptors", errors) or []
    audit_raw = _list(root.get("audit_events", []), "$.audit_events", errors) or []

    room_ids: set[str] = set()
    for index, raw in enumerate(rooms_raw):
        path = f"$.rooms[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        room_id = _identifier(_required(item, "room_id", path, errors), f"{path}.room_id", errors)
        if room_id in room_ids:
            _error(errors, f"{path}.room_id", "must be unique")
        elif room_id is not None:
            room_ids.add(room_id)
        _string(_required(item, "name", path, errors), f"{path}.name", errors)
        if "ha_area_ref" in item:
            _identifier(item["ha_area_ref"], f"{path}.ha_area_ref", errors)
        _validate_contour_list(_required(item, "contour_membership", path, errors), f"{path}.contour_membership", errors)
        _string(_required(item, "inventory_notes", path, errors), f"{path}.inventory_notes", errors)

    safety_ids: set[str] = set()
    for index, raw in enumerate(safety_raw):
        path = f"$.safety_classes[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        safety_id = _known_safety_class(
            _required(item, "safety_class_id", path, errors), f"{path}.safety_class_id", errors
        )
        if safety_id in safety_ids:
            _error(errors, f"{path}.safety_class_id", "must be unique")
        elif safety_id is not None:
            safety_ids.add(safety_id)
        _string(_required(item, "label", path, errors), f"{path}.label", errors)

    capability_ids: set[str] = set()
    for index, raw in enumerate(capabilities_raw):
        path = f"$.capabilities[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        capability_id = _identifier(
            _required(item, "capability_id", path, errors), f"{path}.capability_id", errors
        )
        if capability_id in capability_ids:
            _error(errors, f"{path}.capability_id", "must be unique")
        elif capability_id is not None:
            capability_ids.add(capability_id)
        _string(_required(item, "kind", path, errors), f"{path}.kind", errors)
        _string(_required(item, "value_shape", path, errors), f"{path}.value_shape", errors)
        _validate_contour_list(_required(item, "readable_by", path, errors), f"{path}.readable_by", errors)
        owner = _known_contour(_required(item, "owned_by", path, errors), f"{path}.owned_by", errors)
        if owner not in DECISION_OWNERS:
            _error(errors, f"{path}.owned_by", "must remain a Climate or Automation owner")

    device_ids: set[str] = set()
    for index, raw in enumerate(devices_raw):
        path = f"$.devices[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        device_id = _identifier(_required(item, "device_id", path, errors), f"{path}.device_id", errors)
        if device_id in device_ids:
            _error(errors, f"{path}.device_id", "must be unique")
        elif device_id is not None:
            device_ids.add(device_id)
        _string(_required(item, "name", path, errors), f"{path}.name", errors)
        room_id = _identifier(_required(item, "room_id", path, errors), f"{path}.room_id", errors)
        if room_id is not None and room_id not in room_ids:
            _error(errors, f"{path}.room_id", "must reference an existing room")
        _identifier(_required(item, "ha_device_ref", path, errors), f"{path}.ha_device_ref", errors)
        for entity_index, entity_ref in enumerate(
            _list(_required(item, "ha_entity_refs", path, errors), f"{path}.ha_entity_refs", errors) or []
        ):
            _identifier(entity_ref, f"{path}.ha_entity_refs[{entity_index}]", errors)
        for capability_index, capability_id in enumerate(
            _list(_required(item, "capability_ids", path, errors), f"{path}.capability_ids", errors) or []
        ):
            capability_id = _identifier(capability_id, f"{path}.capability_ids[{capability_index}]", errors)
            if capability_id is not None and capability_id not in capability_ids:
                _error(errors, f"{path}.capability_ids[{capability_index}]", "must reference an existing capability")
        owner = _known_contour(
            _required(item, "owner_contour_id", path, errors), f"{path}.owner_contour_id", errors
        )
        if owner not in DECISION_OWNERS:
            _error(errors, f"{path}.owner_contour_id", "must remain a Climate or Automation owner")
        safety_id = _known_safety_class(
            _required(item, "safety_class_id", path, errors), f"{path}.safety_class_id", errors
        )
        if safety_id is not None and safety_id not in safety_ids:
            _error(errors, f"{path}.safety_class_id", "must be listed in safety_classes")

    targets = {"room": room_ids, "device": device_ids, "capability": capability_ids, "action_descriptor": set()}
    descriptor_ids: set[str] = set()
    for index, raw in enumerate(descriptors_raw):
        path = f"$.action_descriptors[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        descriptor_id = _identifier(
            _required(item, "action_descriptor_id", path, errors), f"{path}.action_descriptor_id", errors
        )
        if descriptor_id in descriptor_ids:
            _error(errors, f"{path}.action_descriptor_id", "must be unique")
        elif descriptor_id is not None:
            descriptor_ids.add(descriptor_id)
        _string(_required(item, "label", path, errors), f"{path}.label", errors)
        owner = _known_contour(
            _required(item, "owner_contour_id", path, errors), f"{path}.owner_contour_id", errors
        )
        if owner not in DECISION_OWNERS:
            _error(errors, f"{path}.owner_contour_id", "cannot make Common, facade, or integration a decision owner")
        safety_id = _known_safety_class(
            _required(item, "safety_class_id", path, errors), f"{path}.safety_class_id", errors
        )
        if safety_id is not None and safety_id not in safety_ids:
            _error(errors, f"{path}.safety_class_id", "must be listed in safety_classes")
        callers = _validate_contour_list(
            _required(item, "allowed_callers", path, errors), f"{path}.allowed_callers", errors
        )
        if not callers:
            _error(errors, f"{path}.allowed_callers", "must name at least one router or reader")
        requires_authority = _required(item, "requires_authority", path, errors)
        if not isinstance(requires_authority, bool):
            _error(errors, f"{path}.requires_authority", "must be a boolean")
        _string(_required(item, "notes", path, errors), f"{path}.notes", errors)
    targets["action_descriptor"] = descriptor_ids

    for index, raw in enumerate(descriptors_raw):
        path = f"$.action_descriptors[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        for target_index, ref in enumerate(
            _list(_required(item, "target_refs", path, errors), f"{path}.target_refs", errors) or []
        ):
            _validate_reference(ref, f"{path}.target_refs[{target_index}]", errors, targets)

    for index, raw in enumerate(membership_raw):
        path = f"$.contour_membership[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        _validate_reference(_required(item, "subject_ref", path, errors), f"{path}.subject_ref", errors, targets)
        _validate_contour_list(_required(item, "contour_ids", path, errors), f"{path}.contour_ids", errors)

    for index, raw in enumerate(audit_raw):
        path = f"$.audit_events[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        _identifier(_required(item, "event_id", path, errors), f"{path}.event_id", errors)
        _string(_required(item, "occurred_at", path, errors), f"{path}.occurred_at", errors)
        _string(_required(item, "actor", path, errors), f"{path}.actor", errors)
        _known_contour(_required(item, "contour_id", path, errors), f"{path}.contour_id", errors)
        _identifier(_required(item, "event_type", path, errors), f"{path}.event_type", errors)
        result = _string(_required(item, "result", path, errors), f"{path}.result", errors)
        if result is not None and result not in {"recorded", "routed", "rejected", "ignored"}:
            _error(errors, f"{path}.result", "must be recorded, routed, rejected, or ignored; never executed")
        _string(_required(item, "reason", path, errors), f"{path}.reason", errors)
        for subject_index, ref in enumerate(
            _list(_required(item, "subject_refs", path, errors), f"{path}.subject_refs", errors) or []
        ):
            _validate_reference(ref, f"{path}.subject_refs[{subject_index}]", errors, targets)

    return errors


def validate_shadow_evidence(data: Any) -> list[str]:
    """Return validation errors for a synthetic read-only shadow evidence record."""

    errors: list[str] = []
    root = _mapping(data, "$", errors)
    if root is None:
        return errors
    _check_forbidden_keys(root, "$", errors, EVIDENCE_FORBIDDEN_KEYS)
    _identifier(_required(root, "evidence_id", "$", errors), "$.evidence_id", errors)
    version = _string(_required(root, "evidence_version", "$", errors), "$.evidence_version", errors)
    if version is not None and version != "synthetic-shadow-v1":
        _error(errors, "$.evidence_version", "must be 'synthetic-shadow-v1'")
    mode = _string(_required(root, "mode", "$", errors), "$.mode", errors)
    if mode not in READ_ONLY_MODES:
        _error(errors, "$.mode", "must be read-only or shadow")
    status = _string(_required(root, "direct_execution_status", "$", errors), "$.direct_execution_status", errors)
    if status != "direct_execution_blocked":
        _error(errors, "$.direct_execution_status", "must remain direct_execution_blocked")
    parity_status = _string(_required(root, "parity_status", "$", errors), "$.parity_status", errors)
    if parity_status != "unresolved":
        _error(errors, "$.parity_status", "must remain unresolved while thresholds and owner approval are TBD")

    snapshot = _mapping(_required(root, "snapshot", "$", errors), "$.snapshot", errors)
    if snapshot is not None:
        _identifier(_required(snapshot, "snapshot_id", "$.snapshot", errors), "$.snapshot.snapshot_id", errors)
        _string(_required(snapshot, "timestamp_bucket", "$.snapshot", errors), "$.snapshot.timestamp_bucket", errors)
        source_label = _string(_required(snapshot, "source_label", "$.snapshot", errors), "$.snapshot.source_label", errors)
        if source_label is not None and not source_label.startswith("synthetic_"):
            _error(errors, "$.snapshot.source_label", "must identify a synthetic source")

    for index, raw in enumerate(_list(_required(root, "references", "$", errors), "$.references", errors) or []):
        path = f"$.references[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        for key in ("reference_label", "room_id", "device_id", "capability_id", "descriptor_id"):
            _identifier(_required(item, key, path, errors), f"{path}.{key}", errors)

    owner_review = _mapping(_required(root, "owner_review", "$", errors), "$.owner_review", errors)
    review_status = None
    if owner_review is not None:
        review_status = _string(_required(owner_review, "status", "$.owner_review", errors), "$.owner_review.status", errors)
        if review_status not in {"pending", "completed", "missing"}:
            _error(errors, "$.owner_review.status", "must be pending, completed, or missing")
        _string(_required(owner_review, "scope", "$.owner_review", errors), "$.owner_review.scope", errors)

    for index, raw in enumerate(_list(_required(root, "comparisons", "$", errors), "$.comparisons", errors) or []):
        path = f"$.comparisons[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        _identifier(_required(item, "descriptor_id", path, errors), f"{path}.descriptor_id", errors)
        owner = _known_contour(_required(item, "owner_contour_id", path, errors), f"{path}.owner_contour_id", errors)
        if owner not in DECISION_OWNERS:
            _error(errors, f"{path}.owner_contour_id", "must remain a domain owner")
        _known_safety_class(_required(item, "safety_class_id", path, errors), f"{path}.safety_class_id", errors)
        _string(_required(item, "owner_projection_summary", path, errors), f"{path}.owner_projection_summary", errors)
        _string(_required(item, "future_projection_summary", path, errors), f"{path}.future_projection_summary", errors)
        comparison_status = _string(_required(item, "status", path, errors), f"{path}.status", errors)
        if comparison_status not in {"accepted", "rejected", "unresolved"}:
            _error(errors, f"{path}.status", "must be accepted, rejected, or unresolved")
        if comparison_status == "accepted" and review_status != "completed":
            _error(errors, f"{path}.status", "cannot be accepted before completed owner review")

    for index, raw in enumerate(_list(_required(root, "mismatches", "$", errors), "$.mismatches", errors) or []):
        path = f"$.mismatches[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        category = _string(_required(item, "category", path, errors), f"{path}.category", errors)
        if category not in MISMATCH_CATEGORIES:
            _error(errors, f"{path}.category", "must use a documented mismatch category")
        _identifier(_required(item, "descriptor_id", path, errors), f"{path}.descriptor_id", errors)
        _string(_required(item, "summary", path, errors), f"{path}.summary", errors)

    freshness = _mapping(_required(root, "freshness", "$", errors), "$.freshness", errors)
    if freshness is not None:
        if _string(_required(freshness, "threshold", "$.freshness", errors), "$.freshness.threshold", errors) != "TBD_BY_OWNER_CONTOUR":
            _error(errors, "$.freshness.threshold", "must remain a policy placeholder")
        _string(_required(freshness, "status", "$.freshness", errors), "$.freshness.status", errors)
    confidence = _mapping(_required(root, "confidence", "$", errors), "$.confidence", errors)
    if confidence is not None:
        if _string(_required(confidence, "minimum", "$.confidence", errors), "$.confidence.minimum", errors) != "TBD_BY_OWNER_CONTOUR":
            _error(errors, "$.confidence.minimum", "must remain a policy placeholder")
        _string(_required(confidence, "status", "$.confidence", errors), "$.confidence.status", errors)

    redaction = _mapping(_required(root, "redaction", "$", errors), "$.redaction", errors)
    if redaction is not None:
        redaction_status = _string(_required(redaction, "status", "$.redaction", errors), "$.redaction.status", errors)
        if redaction_status not in {"redacted", "blocked"}:
            _error(errors, "$.redaction.status", "must be redacted or blocked")
        _list(_required(redaction, "removed_categories", "$.redaction", errors), "$.redaction.removed_categories", errors)

    thresholds = _mapping(_required(root, "acceptance_thresholds", "$", errors), "$.acceptance_thresholds", errors)
    if thresholds is not None:
        for key in ("required_scenarios", "freshness_window", "minimum_confidence", "mismatch_budget", "mandatory_zero_categories", "review_signoff"):
            _string(_required(thresholds, key, "$.acceptance_thresholds", errors), f"$.acceptance_thresholds.{key}", errors)

    _list(_required(root, "unresolved_gaps", "$", errors), "$.unresolved_gaps", errors)
    for index, raw in enumerate(_list(_required(root, "audit_summary", "$", errors), "$.audit_summary", errors) or []):
        path = f"$.audit_summary[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        _identifier(_required(item, "event_kind", path, errors), f"{path}.event_kind", errors)
        result = _string(_required(item, "result", path, errors), f"{path}.result", errors)
        if result not in {"recorded", "accepted", "rejected", "unresolved"}:
            _error(errors, f"{path}.result", "must stay a read-only evidence result")
    return errors


def validate_diagnostics_contract(data: Any) -> list[str]:
    """Return validation errors for a redacted, manual-only diagnostics fixture."""

    errors: list[str] = []
    root = _mapping(data, "$", errors)
    if root is None:
        return errors
    _check_forbidden_keys(root, "$", errors, DIAGNOSTICS_FORBIDDEN_KEYS)
    if _string(_required(root, "contract_version", "$", errors), "$.contract_version", errors) != "synthetic-diagnostics-v1":
        _error(errors, "$.contract_version", "must be 'synthetic-diagnostics-v1'")
    mode = _string(_required(root, "mode", "$", errors), "$.mode", errors)
    if mode not in READ_ONLY_MODES:
        _error(errors, "$.mode", "must be read-only or shadow")
    if _string(_required(root, "direct_execution_status", "$", errors), "$.direct_execution_status", errors) != "direct_execution_blocked":
        _error(errors, "$.direct_execution_status", "must remain direct_execution_blocked")

    entry = _mapping(_required(root, "entry_summary", "$", errors), "$.entry_summary", errors)
    if entry is not None:
        _string(_required(entry, "integration_label", "$.entry_summary", errors), "$.entry_summary.integration_label", errors)
        _string(_required(entry, "single_config_entry_status", "$.entry_summary", errors), "$.entry_summary.single_config_entry_status", errors)

    for section in ("selected_references", "common_mapping", "owner_contours", "repairs_summary"):
        _list(_required(root, section, "$", errors), f"$.{section}", errors)
    for index, raw in enumerate(root.get("selected_references", [])):
        path = f"$.selected_references[{index}]"
        item = _mapping(raw, path, errors)
        if item is not None:
            for key in ("reference_label", "kind", "redaction_label"):
                _identifier(_required(item, key, path, errors), f"{path}.{key}", errors)
    for index, raw in enumerate(root.get("common_mapping", [])):
        path = f"$.common_mapping[{index}]"
        item = _mapping(raw, path, errors)
        if item is not None:
            for key in ("reference_label", "room_id", "device_id", "capability_id", "descriptor_id", "status"):
                _identifier(_required(item, key, path, errors), f"{path}.{key}", errors)
    for index, raw in enumerate(root.get("owner_contours", [])):
        path = f"$.owner_contours[{index}]"
        item = _mapping(raw, path, errors)
        if item is not None:
            _identifier(_required(item, "descriptor_id", path, errors), f"{path}.descriptor_id", errors)
            owner = _known_contour(_required(item, "owner_contour_id", path, errors), f"{path}.owner_contour_id", errors)
            if owner not in DECISION_OWNERS:
                _error(errors, f"{path}.owner_contour_id", "must remain a domain owner")
            status = _string(_required(item, "status", path, errors), f"{path}.status", errors)
            if status not in {"resolved", "unresolved"}:
                _error(errors, f"{path}.status", "must be resolved or unresolved")

    safety_model = _mapping(_required(root, "safety_model", "$", errors), "$.safety_model", errors)
    if safety_model is not None:
        if _string(_required(safety_model, "direct_execution_status", "$.safety_model", errors), "$.safety_model.direct_execution_status", errors) != "direct_execution_blocked":
            _error(errors, "$.safety_model.direct_execution_status", "must remain direct_execution_blocked")
        for index, safety_class in enumerate(_list(_required(safety_model, "safety_classes", "$.safety_model", errors), "$.safety_model.safety_classes", errors) or []):
            _known_safety_class(safety_class, f"$.safety_model.safety_classes[{index}]", errors)

    parity = _mapping(_required(root, "shadow_parity", "$", errors), "$.shadow_parity", errors)
    if parity is not None:
        if _string(_required(parity, "status", "$.shadow_parity", errors), "$.shadow_parity.status", errors) != "unresolved":
            _error(errors, "$.shadow_parity.status", "must remain unresolved")
        _string(_required(parity, "freshness", "$.shadow_parity", errors), "$.shadow_parity.freshness", errors)
        _string(_required(parity, "confidence", "$.shadow_parity", errors), "$.shadow_parity.confidence", errors)
        _list(_required(parity, "mismatch_categories", "$.shadow_parity", errors), "$.shadow_parity.mismatch_categories", errors)

    redaction = _mapping(_required(root, "redaction_report", "$", errors), "$.redaction_report", errors)
    redaction_status = None
    if redaction is not None:
        redaction_status = _string(_required(redaction, "status", "$.redaction_report", errors), "$.redaction_report.status", errors)
        if redaction_status not in {"redacted", "blocked"}:
            _error(errors, "$.redaction_report.status", "must be redacted or blocked")
        _list(_required(redaction, "removed_categories", "$.redaction_report", errors), "$.redaction_report.removed_categories", errors)

    has_critical_redaction_issue = False
    for index, raw in enumerate(root.get("repairs_summary", [])):
        path = f"$.repairs_summary[{index}]"
        item = _mapping(raw, path, errors)
        if item is None:
            continue
        _identifier(_required(item, "issue_id", path, errors), f"{path}.issue_id", errors)
        category = _string(_required(item, "category", path, errors), f"{path}.category", errors)
        severity = _string(_required(item, "severity", path, errors), f"{path}.severity", errors)
        if category not in REPAIR_SEVERITIES:
            _error(errors, f"{path}.category", "must use a documented manual-only repair category")
        elif severity != REPAIR_SEVERITIES[category]:
            _error(errors, f"{path}.severity", "does not match the category's fixed safety severity")
        if category == "redaction_failure" and severity == "critical":
            has_critical_redaction_issue = True
        status = _string(_required(item, "status", path, errors), f"{path}.status", errors)
        if status not in REPAIR_STATUSES:
            _error(errors, f"{path}.status", "must use the manual repair lifecycle")
        guidance = _string(_required(item, "manual_guidance_state", path, errors), f"{path}.manual_guidance_state", errors)
        if guidance not in {"available", "needed", "blocked"}:
            _error(errors, f"{path}.manual_guidance_state", "must remain manual-only")
    if redaction_status == "blocked" and not has_critical_redaction_issue:
        _error(errors, "$.repairs_summary", "must contain a critical redaction_failure issue when export is blocked")
    return errors
