"""Safety stores verify the actual atomic file, not HA Store return alone."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from custom_components.hausman_hub.verified_safety_storage import (
    VerifiedSafetyStore,
)
from custom_components.hausman_hub.application.manual_light_off_protection import (
    valid_manual_light_off_protection_payload,
)


class FakeBackend:
    version = 1
    key = "hausman_hub.safety.test"

    def __init__(self, path: Path, *, swallow_write: bool = False) -> None:
        self.path = str(path)
        self.payload: object | None = None
        self.swallow_write = swallow_write

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        if self.swallow_write:
            return
        Path(self.path).write_text(
            json.dumps(
                {
                    "version": self.version,
                    "minor_version": 1,
                    "key": self.key,
                    "data": payload,
                }
            ),
            encoding="utf-8",
        )


async def _run_sync(function, *args):
    return function(*args)


def test_missing_store_is_new_but_existing_unreadable_store_fails_closed() -> None:
    async def exercise(path: Path) -> None:
        backend = FakeBackend(path)
        store = VerifiedSafetyStore(backend, _run_sync)
        assert await store.async_load() is None

        path.write_text("{broken", encoding="utf-8")
        try:
            await store.async_load()
        except RuntimeError as error:
            assert "unreadable" in str(error)
        else:
            raise AssertionError("existing unreadable safety store must fail closed")

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(exercise(Path(directory, "store.json")))


def test_swallowed_write_is_detected_by_file_readback() -> None:
    async def exercise(path: Path) -> None:
        backend = FakeBackend(path, swallow_write=True)
        store = VerifiedSafetyStore(backend, _run_sync)
        try:
            await store.async_save({"generation": 1})
        except RuntimeError as error:
            assert "not written" in str(error)
        else:
            raise AssertionError("swallowed safety-store write must fail closed")

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(exercise(Path(directory, "store.json")))


def test_stale_file_after_swallowed_write_fails_checksum_comparison() -> None:
    async def exercise(path: Path) -> None:
        backend = FakeBackend(path)
        store = VerifiedSafetyStore(backend, _run_sync)
        await store.async_save({"generation": 1})
        backend.swallow_write = True
        try:
            await store.async_save({"generation": 2})
        except RuntimeError as error:
            assert "verification failed" in str(error)
        else:
            raise AssertionError("stale safety-store file must fail closed")

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(exercise(Path(directory, "store.json")))


def test_corrupt_current_store_restores_verified_previous_generation() -> None:
    async def exercise(path: Path) -> None:
        backend = FakeBackend(path)
        validator = lambda value: (
            isinstance(value, dict) and type(value.get("generation")) is int
        )
        store = VerifiedSafetyStore(
            backend,
            _run_sync,
            payload_validator=validator,
        )
        await store.async_save({"generation": 1})
        await store.async_save({"generation": 2})

        path.write_text("{broken", encoding="utf-8")
        backend.payload = None
        restored = await store.async_load()

        assert restored == {"generation": 1}
        assert store.recovered_previous is True
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["data"] == {"generation": 1}

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(exercise(Path(directory, "store.json")))


def test_invalid_previous_generation_does_not_bypass_payload_validator() -> None:
    async def exercise(path: Path) -> None:
        backend = FakeBackend(path)
        store = VerifiedSafetyStore(
            backend,
            _run_sync,
            payload_validator=lambda value: value == {"safe": True},
        )
        previous = Path(f"{path}.previous")
        previous.write_text(
            json.dumps(
                {
                    "version": backend.version,
                    "key": backend.key,
                    "data": {"safe": False},
                }
            ),
            encoding="utf-8",
        )
        path.write_text("{broken", encoding="utf-8")

        try:
            await store.async_load()
        except RuntimeError as error:
            assert "unreadable" in str(error)
        else:
            raise AssertionError("invalid N-1 safety payload must fail closed")

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(exercise(Path(directory, "store.json")))


def test_manual_light_protection_corruption_does_not_restore_an_invalid_payload() -> None:
    async def exercise(path: Path) -> None:
        backend = FakeBackend(path)
        store = VerifiedSafetyStore(
            backend,
            _run_sync,
            payload_validator=valid_manual_light_off_protection_payload,
        )
        backend.payload = {"version": 1}
        try:
            await store.async_load()
        except RuntimeError as error:
            assert "unreadable" in str(error)
        else:
            raise AssertionError("corrupt manual-light protection must fail closed")

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(exercise(Path(directory, "store.json")))


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case))
    return suite
