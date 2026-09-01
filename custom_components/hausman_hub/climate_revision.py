"""Shared bounded control-revision primitives."""

from __future__ import annotations

MAX_JS_SAFE_INTEGER = 9_007_199_254_740_991


def is_control_revision(value: object) -> bool:
    """Return whether a revision is exactly representable by JavaScript."""

    return type(value) is int and 0 <= value <= MAX_JS_SAFE_INTEGER
