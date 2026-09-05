"""Fail-closed gate shared by runtime scenario subscriptions."""

from __future__ import annotations


class ActivationLatch:
    """Open only after every startup subscription has been prepared."""

    def __init__(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False
