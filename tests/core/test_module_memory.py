"""K-04: algorithm-only memory follows the accepted evaluation, not storage."""
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from conftest import (
    MAIN_RESOURCE, OTHER_RESOURCE, OWNER_MODULE,
    build_file_stand, build_stand, owner_proposal, restore_file_stand,
)
from custom_components.hausman_hub.application.core.command_service import (
    CoreServiceError, StorageUnavailable, UnknownResource,
)
from custom_components.hausman_hub.domain.core.models import ArbitrationMode, Evaluation


@dataclass(frozen=True)
class AlgorithmMemory:
    marker: str
    revision: int


class MemoryModule:
    """Test-only module; the captured argument is the observable boundary."""
    def __init__(self, module_id, next_memory):
        self.module_id = module_id
        self.next_memory = next_memory
        self.previous = []
        self.proposals = ()
        self.invalid = False

    def evaluate(self, frame, previous_memory):
        self.previous.append(previous_memory)
        return Evaluation(
            module_id=self.module_id,
            config_revision=frame.config_revision,
            input_revision=1,
            proposals=(owner_proposal(resource_id="test.not_registered", module_id=self.module_id),)
                if self.invalid else self.proposals,
            next_memory=self.next_memory,
        )


def install(stand, module_id, memory):
    module = MemoryModule(module_id, memory)
    stand.service._modules[module_id] = replace(
        stand.service._modules[module_id], instance=module,
    )
    stand.modules[module_id] = module
    return module


def test_next_evaluation_receives_last_accepted_algorithm_memory():
    stand = build_stand(ArbitrationMode.RANKED_TARGET)
    memory = AlgorithmMemory("first", 1)
    module = install(stand, OWNER_MODULE, memory)
    stand.service.process_module_event(OWNER_MODULE, "memory-first")
    stand.service.process_module_event(OWNER_MODULE, "memory-second")
    assert module.previous == [None, memory]
    assert stand.transport.calls == []


def test_algorithm_memory_is_isolated_between_modules():
    other = "test.other_module"
    stand = build_stand(ArbitrationMode.RANKED_TARGET, module_specs={
        OWNER_MODULE: MAIN_RESOURCE, other: OTHER_RESOURCE,
    })
    first_memory = AlgorithmMemory("first", 1)
    other_memory = AlgorithmMemory("other", 9)
    first = install(stand, OWNER_MODULE, first_memory)
    second = install(stand, other, other_memory)
    for module_id in (OWNER_MODULE, other, OWNER_MODULE, other):
        stand.service.process_module_event(module_id, "isolated-memory")
    assert first.previous == [None, first_memory]
    assert second.previous == [None, other_memory]
    assert stand.transport.calls == []


def test_rejected_evaluation_publishes_neither_memory_nor_proposals():
    stand = build_stand(ArbitrationMode.RANKED_TARGET)
    accepted_memory = AlgorithmMemory("accepted", 1)
    module = install(stand, OWNER_MODULE, accepted_memory)
    module.proposals = (owner_proposal(),)
    stand.service.process_module_event(OWNER_MODULE, "accepted-memory")
    before_needs = dict(stand.service._needs)
    assert before_needs[OWNER_MODULE] == module.proposals
    assert before_needs[OWNER_MODULE]
    module.next_memory = AlgorithmMemory("rejected", 2)
    module.invalid = True
    with pytest.raises(UnknownResource, match="test.not_registered"):
        stand.service.process_module_event(OWNER_MODULE, "rejected-memory")
    assert stand.service._needs == before_needs
    module.invalid = False
    stand.service.process_module_event(OWNER_MODULE, "after-rejected-memory")
    assert module.previous[-1] == accepted_memory
    assert stand.transport.calls == []


def test_new_service_does_not_restore_algorithm_memory_from_control_store(tmp_path):
    stand = build_file_stand(tmp_path, ArbitrationMode.RANKED_TARGET)
    module = install(stand, OWNER_MODULE, AlgorithmMemory("old-process", 1))
    before = Path(stand.store_path).read_bytes()
    stand.service.process_module_event(OWNER_MODULE, "old-process-memory")
    assert Path(stand.store_path).read_bytes() == before
    restored = restore_file_stand(stand)
    restored.service.process_module_event(OWNER_MODULE, "new-process-memory")
    assert module.previous == [None, None]
    assert restored.transport.calls == stand.transport.calls == []


def test_accepted_none_clears_previous_algorithm_memory():
    stand = build_stand(ArbitrationMode.RANKED_TARGET)
    memory = AlgorithmMemory("first", 1)
    module = install(stand, OWNER_MODULE, memory)
    stand.service.process_module_event(OWNER_MODULE, "first")
    module.next_memory = None
    stand.service.process_module_event(OWNER_MODULE, "clear")
    stand.service.process_module_event(OWNER_MODULE, "after-clear")
    assert module.previous == [None, memory, None]
    assert stand.transport.calls == []


def test_exception_in_module_keeps_previous_pair(monkeypatch):
    stand = build_stand(ArbitrationMode.RANKED_TARGET)
    memory = AlgorithmMemory("accepted", 1)
    module = install(stand, OWNER_MODULE, memory)
    stand.service.process_module_event(OWNER_MODULE, "accepted")
    before_needs = dict(stand.service._needs)

    def fail(frame, previous_memory):
        assert previous_memory == memory
        raise RuntimeError("synthetic module failure")

    with monkeypatch.context() as patch:
        patch.setattr(module, "evaluate", fail)
        with pytest.raises(RuntimeError, match="synthetic module failure"):
            stand.service.process_module_event(OWNER_MODULE, "module-failure")
    assert stand.service._needs == before_needs
    stand.service.process_module_event(OWNER_MODULE, "after-module-failure")
    assert module.previous[-1] == memory
    assert stand.transport.calls == []


@pytest.mark.parametrize("failure", ["late-resource", "limit"])
def test_late_validation_failure_keeps_the_whole_previous_pair(failure):
    stand = build_stand(ArbitrationMode.RANKED_TARGET)
    memory = AlgorithmMemory("accepted", 1)
    module = install(stand, OWNER_MODULE, memory)
    original = owner_proposal()
    module.proposals = (original,)
    stand.service.process_module_event(OWNER_MODULE, "accepted")
    before_needs = dict(stand.service._needs)
    assert before_needs[OWNER_MODULE] == (original,)
    second = owner_proposal(
        proposal_id="test.second",
        resource_id="test.not_registered" if failure == "late-resource" else MAIN_RESOURCE,
    )
    module.proposals = (original, second)
    module.next_memory = AlgorithmMemory("rejected", 2)
    entry = stand.service._modules[OWNER_MODULE]
    stand.service._modules[OWNER_MODULE] = replace(
        entry, manifest=replace(entry.manifest, max_proposals_per_resource=1),
    )
    error = UnknownResource if failure == "late-resource" else CoreServiceError
    message = "test.not_registered" if failure == "late-resource" else "too many proposals"
    with pytest.raises(error, match=message):
        stand.service.process_module_event(OWNER_MODULE, "late-validation-failure")
    assert stand.service._needs == before_needs
    module.proposals = (original,)
    stand.service.process_module_event(OWNER_MODULE, "after-late-failure")
    assert module.previous[-1] == memory
    assert stand.transport.calls == []


def test_arbitration_failure_does_not_split_the_accepted_pair(monkeypatch):
    stand = build_stand(ArbitrationMode.RANKED_TARGET)
    module = install(stand, OWNER_MODULE, AlgorithmMemory("first", 1))
    stand.service.process_module_event(OWNER_MODULE, "first")
    new_memory = AlgorithmMemory("accepted-before-arbitration", 2)
    module.next_memory = new_memory
    module.proposals = (owner_proposal(),)

    def fail(resource_id, withdrawn):
        raise RuntimeError("synthetic arbitration failure")

    with monkeypatch.context() as patch:
        patch.setattr(stand.service, "_arbitrate_resource", fail)
        with pytest.raises(RuntimeError, match="synthetic arbitration failure"):
            stand.service.process_module_event(OWNER_MODULE, "arbitration-failure")
    assert stand.service._needs[OWNER_MODULE] == module.proposals
    module.proposals = ()
    stand.service.process_module_event(OWNER_MODULE, "next-explicit-event")
    assert module.previous[-1] == new_memory
    assert stand.transport.calls == []


def test_operation_store_failure_does_not_split_the_accepted_pair(tmp_path):
    stand = build_file_stand(tmp_path, ArbitrationMode.RANKED_TARGET)
    module = install(stand, OWNER_MODULE, AlgorithmMemory("first", 1))
    stand.service.process_module_event(OWNER_MODULE, "first")
    before_file = Path(stand.store_path).read_bytes()
    new_memory = AlgorithmMemory("accepted-before-store", 2)
    module.next_memory = new_memory
    module.proposals = (owner_proposal(),)
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable):
        stand.service.process_module_event(OWNER_MODULE, "store-failure")
    assert Path(stand.store_path).read_bytes() == before_file
    assert stand.service._needs[OWNER_MODULE] == module.proposals
    assert not stand.service.operations(MAIN_RESOURCE)
    module.proposals = ()
    stand.service.process_module_event(OWNER_MODULE, "next-explicit-event")
    assert module.previous[-1] == new_memory
    assert stand.transport.calls == []
