"""Architecture boundary checks of the FIRST delivery (CA-06, FIRST §5).

Static AST checks over the real production files of the candidate plus
behavioural checks: a second/third module connects through the plain
registry and HA payloads are allowlist-built per domain.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from custom_components.hausman_hub.adapters.core.light import LightDeviceAdapter
from custom_components.hausman_hub.application.core.command_service import (
    CapabilityProfile,
    PropertyRule,
)
from custom_components.hausman_hub.domain.core.models import (
    LightStateTarget,
    OperationPhase,
    ProposalKind,
    SwitchStateTarget,
    ValidationIssue,
)

from conftest import (
    MAIN_RESOURCE,
    OTHER_RESOURCE,
    build_stand,
    owner_proposal,
)

HAUSMAN_REPO_ROOT = Path(__file__).resolve().parents[2]
HAUSMAN_ROOT = HAUSMAN_REPO_ROOT / "custom_components" / "hausman_hub"
CORE_PACKAGES = (
    HAUSMAN_ROOT / "domain" / "core",
    HAUSMAN_ROOT / "application" / "core",
    HAUSMAN_ROOT / "adapters" / "core",
)

# Stand-only strings that must never appear in production code.
STAND_ONLY_STRINGS = (
    "test.main",
    "test.lighting",
    "test.other",
    "test.z2m",
    "test.epoch1",
    "light.test_main",
    "test.episode1",
    "test.ui",
)


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _module_name(file: Path) -> str:
    relative = file.relative_to(HAUSMAN_REPO_ROOT)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(
    node: ast.ImportFrom, module: str, is_package: bool
) -> str | None:
    if node.level == 0:
        return node.module
    package_parts = module.split(".") if is_package else module.split(".")[:-1]
    steps_up = node.level - 1
    if steps_up > len(package_parts):
        raise ValueError(f"unresolvable relative import in {module}")
    if steps_up:
        package_parts = package_parts[: len(package_parts) - steps_up]
    base = ".".join(package_parts)
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base


def _imports_of(file: Path) -> set[str]:
    tree = ast.parse(file.read_text(encoding="utf-8"))
    module = _module_name(file)
    is_package = file.name == "__init__.py"
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_relative(node, module, is_package)
            if resolved:
                found.add(resolved)
    return found


def _transitive_closure(files: list[Path]) -> set[Path]:
    """Resolve first-party imports transitively, including deferred ones."""

    by_module = {_module_name(file): file for file in _python_files(HAUSMAN_ROOT)}
    closure: set[Path] = set(files)
    frontier = list(files)
    while frontier:
        file = frontier.pop()
        for imported in _imports_of(file):
            target = by_module.get(imported)
            while target is None and "." in imported:
                imported = imported.rsplit(".", 1)[0]
                target = by_module.get(imported)
            if target is not None and target not in closure:
                closure.add(target)
                frontier.append(target)
    return closure


def _has_ha_import(files: list[Path]) -> bool:
    for file in _transitive_closure(files):
        for imported in _imports_of(file):
            if imported == "homeassistant" or imported.startswith(
                "homeassistant."
            ):
                return True
    return False


def _touches_other_layers(
    files: list[Path], forbidden_prefixes: tuple[str, ...]
) -> bool:
    for file in _transitive_closure(files):
        for imported in _imports_of(file):
            if any(
                imported == prefix or imported.startswith(prefix + ".")
                for prefix in forbidden_prefixes
            ):
                return True
    return False


def test_domain_core_never_reaches_ha_application_or_adapters():
    domain_files = _python_files(CORE_PACKAGES[0])
    assert domain_files, "domain/core files are missing"
    assert not _has_ha_import(domain_files)
    assert not _touches_other_layers(
        domain_files,
        (
            "custom_components.hausman_hub.application",
            "custom_components.hausman_hub.adapters",
        ),
    )


def test_application_core_is_ha_and_adapter_free():
    application_files = _python_files(CORE_PACKAGES[1])
    assert application_files, "application/core files are missing"
    assert not _has_ha_import(application_files)
    assert not _touches_other_layers(
        application_files,
        ("custom_components.hausman_hub.adapters",),
    )
    # Contour formulas stay out of the core application layer.
    assert not _touches_other_layers(
        application_files,
        (
            "custom_components.hausman_hub.domain.room_lighting",
            "custom_components.hausman_hub.domain.climate",
            "custom_components.hausman_hub.domain.manual_control_return",
            "custom_components.hausman_hub.domain.light_curve",
        ),
    )


def test_adapters_core_has_no_ha_binding_in_first():
    adapter_files = _python_files(CORE_PACKAGES[2])
    assert adapter_files, "adapters/core files are missing"
    assert not _has_ha_import(adapter_files)


def test_core_never_reads_the_wall_clock_directly():
    for file in [*(CORE_PACKAGES[0]).rglob("*.py"), *(CORE_PACKAGES[1]).rglob("*.py")]:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:
                    names = [node.module or ""]
                for name in names:
                    assert not name.startswith("datetime"), file
                    assert name != "time", file


def test_production_code_has_no_stand_specific_branches():
    for package in CORE_PACKAGES:
        for file in package.rglob("*.py"):
            source = file.read_text(encoding="utf-8")
            for stand_string in STAND_ONLY_STRINGS:
                assert stand_string not in source, (file, stand_string)


def test_third_module_connects_through_plain_registry():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(
        ArbitrationMode.RANKED_TARGET,
        module_specs={
            "test.main_module": MAIN_RESOURCE,
            "test.other_module": OTHER_RESOURCE,
            "test.third_module": OTHER_RESOURCE,
        },
    )
    stand.modules["test.third_module"].queue_proposal(
        owner_proposal(
            proposal_id="third.on",
            resource_id=OTHER_RESOURCE,
            module_id="test.third_module",
        )
    )
    stand.service.process_module_event("test.third_module", "ev-third")
    stand.clock.advance(1)
    stand.service.drain()
    assert stand.service.dispatch_count() == 1


def _switch_profile() -> CapabilityProfile:
    return CapabilityProfile(
        profile_id="first-switch-1",
        version=1,
        source="first-stand",
        properties={
            "on": PropertyRule(
                property="on",
                unit="bool",
                readable=True,
                writable=True,
                min_value=None,
                max_value=None,
                step=None,
                allowed_values=("true", "false"),
                report_mode="on_change",
                state_source="test.switch/state",
                max_age_ms=60000,
                confirmation="post_dispatch_report",
                tolerance=None,
                confirmation_timeout_ms=10000,
                dispatch_timeout_ms=1000,
                readback_delay_ms=None,
            )
        },
    )


def test_switch_payload_has_no_brightness_or_transition(counter):
    from custom_components.hausman_hub.adapters.core.light import (
        LightDeviceAdapter as Adapter,
    )

    adapter = Adapter(
        profiles_by_resource={MAIN_RESOURCE: _switch_profile()},
        binding_revisions={MAIN_RESOURCE: 3},
        boot_id="test.boot1",
        id_factory=counter.next,
    )
    prepared = adapter.prepare(
        SwitchStateTarget(on=True), "switch.test", _switch_profile(), "op-1", "pm-1"
    )
    assert prepared.domain == "switch"
    assert prepared.data == {}


def test_light_payload_is_exactly_the_allowed_fields(counter):
    from custom_components.hausman_hub.adapters.core.light import (
        LightDeviceAdapter as Adapter,
    )
    from conftest import light_profile

    adapter = Adapter(
        profiles_by_resource={MAIN_RESOURCE: light_profile()},
        binding_revisions={MAIN_RESOURCE: 3},
        boot_id="test.boot1",
        id_factory=counter.next,
    )
    prepared = adapter.prepare(
        LightStateTarget(
            on=True,
            brightness_pct=Decimal(40),
            color_temperature_k=None,
        ),
        "light.test_main",
        light_profile(),
        "op-1",
        "pm-1",
    )
    assert prepared.domain == "light"
    assert prepared.service == "turn_on"
    assert dict(prepared.data) == {"brightness_pct": 40}


def test_invalid_target_is_rejected_before_any_call(ranked_stand):
    from custom_components.hausman_hub.application.core.command_service import (
        ManualStatus,
    )
    from custom_components.hausman_hub.domain.core.models import LightStateTarget

    stand = ranked_stand
    bad_target = LightStateTarget(
        on=True, brightness_pct=Decimal(150), color_temperature_k=None
    )
    receipt = stand.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id="req-invalid",
        action_id="manual.invalid",
        target=bad_target,
        source="test.ui",
        expected_control_revision=12,
    )
    assert receipt.status is ManualStatus.ACCEPTED
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.REJECTED
    assert operation.reason_code == "invalid_profile"
    assert stand.transport.calls == []
