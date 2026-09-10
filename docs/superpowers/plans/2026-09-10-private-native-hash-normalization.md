# Private Native Hash Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate the real Tambur mirror automation against an anonymised public fixture without placing a local identifier in public source.

**Architecture:** Startup passes one immutable `SmartSwitchBindings` object to trigger resolution and native validation. The adapter verifies exactly two mirror triggers against the supplied `marmitek` binding, substitutes only those checked fields in a copy with the fixture placeholder, then hashes the complete definition.

**Tech Stack:** Python 3.13, Home Assistant automation adapter, pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-private-smart-switch-bindings-design.md`

## Global Constraints

- No public source, test, fixture or document receives a real device ID.
- Bindings are loaded once and never reread during native validation.
- Exactly two verified mirror `device_id` fields may be substituted. All mismatch cases fail closed before migration, subscription or command.
- No live, release, browser or full-suite operation belongs here without separate owner approval.

---

### Task 1: Immutable binding-aware native validation

**Files:**

- Modify: `custom_components/hausman_hub/__init__.py`
- Modify: `custom_components/hausman_hub/application/native_automation_migration.py`
- Modify: `custom_components/hausman_hub/application/tambur_room_migration.py`
- Test: `tests/test_native_automation_migration.py`
- Test: `tests/test_tambur_room_migration.py`
- Test: `tests/test_smart_switch_bindings.py`

**Interfaces:**

- Startup passes the same verified `SmartSwitchBindings` object to resolved triggers and native migration.
- Native validation accepts only two exact mirror triggers equal to `bindings.devices["marmitek"]` and hashes a copied definition with only those fields replaced by the fixture placeholder.

- [ ] **Step 1: Write failing tests**

Use two different synthetic matching bindings and synthetic native definitions to prove a matching pair passes. Cover missing bindings, either mismatched trigger, extra trigger, wrong subtype and conflicting aliases. Every nonmatching case must stop before a migration action.

- [ ] **Step 2: Run RED**

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_native_automation_migration.py tests/test_tambur_room_migration.py tests/test_smart_switch_bindings.py -k 'marmitek or native or snapshot'
```

Expected: a matching synthetic definition fails the current raw-config hash.

- [ ] **Step 3: Implement and run GREEN**

Normalize existing aliases, validate the exact two-entry list and equality before copying and substituting two fields. Do not mutate raw config or make canonical hashing a generic scrubber.

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_native_automation_migration.py tests/test_tambur_room_migration.py tests/test_smart_switch_bindings.py tests/test_managed_switch_migration_service.py
git diff --check
```

Expected: PASS with no local identifier in logs, events or durable records.

- [ ] **Step 4: Commit**

```sh
git add custom_components/hausman_hub/__init__.py custom_components/hausman_hub/application/native_automation_migration.py custom_components/hausman_hub/application/tambur_room_migration.py tests/test_native_automation_migration.py tests/test_tambur_room_migration.py tests/test_smart_switch_bindings.py
git commit -m "fix: validate anonymized Tambur native automation"
```

### Task 2: Public-boundary regression proof

**Files:**

- Modify: `tests/test_native_automation_migration.py`
- Modify: `tests/test_smart_switch_bindings.py`

**Interfaces:**

- Consumes Task 1 and proves the raw input remains unchanged and a mismatched binding cannot pass through the fixture hash.

- [ ] **Step 1: Write failing tests**

Use only synthetic inputs. Prove raw definition immutability and mismatch rejection without a historic-value deny-list.

- [ ] **Step 2: Run RED and GREEN**

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_native_automation_migration.py tests/test_smart_switch_bindings.py
git diff --check
```

Scan tracked public source, tests and fixtures using the former value only from the base revision, reporting paths and count only. Expected: zero matching paths.

- [ ] **Step 3: Commit**

```sh
git add tests/test_native_automation_migration.py tests/test_smart_switch_bindings.py
git commit -m "test: prove private native hash boundary"
```
