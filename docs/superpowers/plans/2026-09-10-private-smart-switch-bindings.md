# Private Smart Switch Bindings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move physical smart-switch device bindings out of public integration source into strict local Home Assistant storage without sending commands or guessing devices.

**Architecture:** A binding model owns fixed profiles, strict validation and resolved exact triggers. The runtime adapter consumes those resolved triggers, never module-level device constants. Startup validates local storage before Tambur migration or subscriptions; uncertainty leaves the contour inactive.

**Tech Stack:** Python 3.13, Home Assistant Store and device-automation trigger API, VerifiedSafetyStore, pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-private-smart-switch-bindings-design.md`

## Global Constraints

- Public source, tests, fixtures and new documentation never contain a real Home Assistant device ID from the home.
- Only fixed profiles and subtypes are allowed: shower, passthrough and marmitek. No arbitrary MQTT, service, domain or action input.
- Missing, malformed, recovered, stale or ambiguous local bindings fail closed: no subscriptions, room migration or physical command.
- Binding storage and setup make no reload, attach, Home Assistant service call or device action.
- Preserve dedup receipt format, exact trigger validation, manual-action meanings and activation latch.
- Android, public API, contracts, Node-RED schemas, other rooms and global migration are out of scope.
- No live Home Assistant, Node-RED, publish, install or restart operation belongs to this plan.

---

### Task 1: Binding model and local verified storage

**Files:**

- Create: `custom_components/hausman_hub/application/smart_switch_bindings.py`
- Modify: `custom_components/hausman_hub/application/smart_switch_runtime.py`
- Test: `tests/test_smart_switch_bindings.py`

**Interfaces:**

- Produces `SmartSwitchBindings`, `ResolvedSmartSwitchTrigger`, `bindings_from_payload(value) -> SmartSwitchBindings | None`, and `resolve_trigger_bindings(bindings, included_bindings) -> tuple[ResolvedSmartSwitchTrigger, ...]`.
- Produces `HomeAssistantSmartSwitchBindingsStore(hass, entry_id)` with `async_load()`, `async_save(payload)` and `recovered_previous`.
- Consumes the fixed profile table and adapter binding scope.

- [ ] **Step 1: Write failing model tests**

```python
def test_resolver_builds_only_fixed_triggers_for_synthetic_bindings() -> None:
    bindings = bindings_from_payload({
        "version": 1, "revision": 1,
        "devices": {"shower": "device-shower", "passthrough": "device-pass", "marmitek": "device-mirror"},
    })
    assert bindings is not None
    resolved = resolve_trigger_bindings(
        bindings,
        frozenset({"tambur-light-group", "tambur-mirror-left", "tambur-master-off"}),
    )
    assert {item.binding for item in resolved} == {
        "tambur-light-group", "tambur-mirror-left", "tambur-master-off",
    }
    assert all(item.config["domain"] == "mqtt" and item.config["type"] == "action" for item in resolved)


def test_invalid_or_reused_profile_ids_do_not_resolve() -> None:
    assert bindings_from_payload({
        "version": 1, "revision": 1,
        "devices": {"passthrough": "same", "marmitek": "same"},
    }) is None


def test_public_switch_source_contains_no_static_trigger_configs() -> None:
    source = (ROOT / "custom_components/hausman_hub/application/smart_switch_runtime.py").read_text()
    assert "SHOWER_TRIGGER_CONFIGS" not in source
    assert "PASSTHROUGH_TRIGGER_CONFIGS" not in source
    assert "MARMITEK_TRIGGER_CONFIGS" not in source
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_smart_switch_bindings.py`

Expected: FAIL because the binding model does not exist and the runtime still
builds static trigger configurations.

- [ ] **Step 3: Implement the model and store**

```python
@dataclass(frozen=True, slots=True)
class ResolvedSmartSwitchTrigger:
    binding: str
    config: Mapping[str, str]


def resolve_trigger_bindings(
    bindings: SmartSwitchBindings, included_bindings: frozenset[str]
) -> tuple[ResolvedSmartSwitchTrigger, ...]:
    # Build only entries from the fixed profile/subtype table.
```

Use `VerifiedSafetyStore` at `hausman_hub.smart_switch_bindings.<entry_id>`.
Reject unknown keys, bad version or revision, empty IDs and duplicate IDs across
profiles. Do not add real IDs, discovery or config-flow UI.

- [ ] **Step 4: Add storage and recovery tests, then confirm GREEN**

```python
async def test_recovered_binding_store_is_not_trusted() -> None:
    store = HomeAssistantSmartSwitchBindingsStore(fake_hass, "entry-a")
    assert store.recovered_previous is True
```

Run: `PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_smart_switch_bindings.py tests/test_smart_switch_runtime.py`

Expected: PASS.

- [ ] **Step 5: Commit task 1**

```bash
git add custom_components/hausman_hub/application/smart_switch_bindings.py custom_components/hausman_hub/application/smart_switch_runtime.py tests/test_smart_switch_bindings.py
git commit -m "feat: store smart switch bindings locally"
```

### Task 2: Runtime adapter consumes resolved triggers only

**Files:**

- Modify: `custom_components/hausman_hub/application/smart_switch_runtime.py`
- Modify: `tests/test_smart_switch_runtime.py`
- Modify: `tests/test_tambur_task5_controls.py`

**Interfaces:**

- Consumes `tuple[ResolvedSmartSwitchTrigger, ...]` from Task 1.
- Produces `SmartSwitchTriggerAdapter(..., resolved_triggers=...)`.
- Keeps `valid_smart_switch_dedup_payload()` and existing receipt fields unchanged.

- [ ] **Step 1: Write failing adapter tests**

```python
def test_adapter_subscribes_only_to_explicitly_resolved_synthetic_triggers() -> None:
    adapter = SmartSwitchTriggerAdapter(
        hass, service, trigger_api=api, state_store=store,
        resolved_triggers=synthetic_tambur_triggers(),
    )
    assert {item["subtype"] for item in adapter.trigger_configs} == {
        "on_down", "toggle_down", "off_up", "1_single", "1_double", "2_single", "2_double",
    }


def test_adapter_rejects_empty_or_foreign_resolved_trigger_set() -> None:
    with pytest.raises(ValueError):
        SmartSwitchTriggerAdapter(hass, service, resolved_triggers=())
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_smart_switch_runtime.py -k 'resolved or explicit'`

Expected: FAIL because the adapter still builds module-level trigger configs.

- [ ] **Step 3: Remove global device constants and refactor adapter input**

```python
class SmartSwitchTriggerAdapter:
    def __init__(
        self, hass: object, service: object, *,
        resolved_triggers: tuple[ResolvedSmartSwitchTrigger, ...], ...
    ) -> None:
        self._configs = tuple(item.config for item in resolved_triggers)
        self._config_bindings = {
            frozenset(item.config.items()): item.binding
            for item in resolved_triggers
        }
```

Require nonempty exact triggers matching the fixed binding/subtype table.
Resolve binding from the supplied record, never a device ID. Update tests to
use a synthetic binding factory. Do not create a literal deny-list of former
home IDs.

- [ ] **Step 4: Confirm GREEN and regression coverage**

Run: `PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_smart_switch_runtime.py tests/test_tambur_task5_controls.py`

Expected: PASS, including exact trigger validation, at-most-once receipt,
manual intent mapping and zero-brightness behavior.

- [ ] **Step 5: Commit task 2**

```bash
git add custom_components/hausman_hub/application/smart_switch_runtime.py tests/test_smart_switch_runtime.py tests/test_tambur_task5_controls.py
git commit -m "refactor: resolve smart switch triggers locally"
```

### Task 3: Fail-closed startup integration and local preparation

**Files:**

- Modify: `custom_components/hausman_hub/__init__.py`
- Modify: `custom_components/hausman_hub/config_flow.py`
- Modify: `custom_components/hausman_hub/strings.json`
- Modify: `tests/test_tambur_room_migration.py`
- Modify: `tests/test_config_flow_adapter.py`
- Modify: `tests/test_smart_switch_bindings.py`

**Interfaces:**

- Consumes the Task 1 store/resolver and Task 2 resolved-trigger constructor.
- Produces local preparation with revision compare-and-save and startup gating
  before `TamburRoomStartupCoordinator` exists.

- [ ] **Step 1: Write failing startup and preparation tests**

```python
async def test_missing_tambur_bindings_prevent_room_migration_and_subscription() -> None:
    result = await async_setup_entry_with_bindings(payload=None)
    assert result.smart_switch_status == "bindings_unavailable"
    assert result.room_migration_calls == 0
    assert result.trigger_subscriptions == 0


async def test_prepare_bindings_saves_only_local_document() -> None:
    response = await prepare_bindings(entry_id="entry-a", payload=synthetic_payload())
    assert response["revision"] == 1
    assert fake_hass.services.calls == []
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_tambur_room_migration.py tests/test_config_flow_adapter.py -k 'binding or prepare'`

Expected: FAIL because startup currently creates the adapter before local bindings exist.

- [ ] **Step 3: Gate startup and add local preparation**

Load verified binding data before the Tambur startup coordinator. If absent,
invalid, recovered or unable to resolve exact local triggers, store diagnostic
status and do not construct or start adapter or room migration. Add preparation
through the existing local options path with strict revision compare-and-save,
but do not alter options or register a reload listener. Never send device IDs
to public API responses, logs or events.

- [ ] **Step 4: Confirm GREEN including no side effects**

Run: `PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_smart_switch_bindings.py tests/test_smart_switch_runtime.py tests/test_tambur_room_migration.py tests/test_config_flow_adapter.py tests/test_tambur_task5_controls.py`

Expected: PASS; missing or uncertain bindings gives zero migration,
subscription and service calls; valid synthetic binding preserves manual
controls.

- [ ] **Step 5: Commit task 3**

```bash
git add custom_components/hausman_hub/__init__.py custom_components/hausman_hub/config_flow.py custom_components/hausman_hub/strings.json tests/test_tambur_room_migration.py tests/test_config_flow_adapter.py tests/test_smart_switch_bindings.py
git commit -m "feat: gate Tambur startup on local switch bindings"
```

### Task 4: Privacy and whole-branch verification

**Files:**

- Modify: `tests/test_smart_switch_bindings.py`
- Test: `tests/test_external_contract_pin.py`

**Interfaces:**

- Consumes all prior tasks.
- Produces a reproducible public-tree privacy scan and inactive release candidate.

- [ ] **Step 1: Verify the privacy boundary without a real-ID deny-list**

Assert current runtime construction and synthetic test data only. Manually run
`git grep` for accidental literal introduction without printing matching
private values into reports.

- [ ] **Step 2: Run full verification**

Run: `PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q`

Expected: PASS. Then run `python3 tools/check_local_release.py`, required
manifest/browser checks and `git diff --check`.

- [ ] **Step 3: Commit task 4**

```bash
git add tests/test_smart_switch_bindings.py tests/test_external_contract_pin.py
git commit -m "test: verify private switch binding boundary"
```
