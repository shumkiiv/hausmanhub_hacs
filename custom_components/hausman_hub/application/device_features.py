"""Stable upper-bound feature matrix for HausmanHub device controls."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Final


DEVICE_FEATURE_MATRIX_CONTRACT_NAME = "hausman-hub-device-feature-matrix"
DEVICE_FEATURE_MATRIX_CONTRACT_VERSION = 1

_MATRIX_PATH: Final = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "v1"
    / "device-feature-matrix.json"
)
_DEVICE_FEATURE_MATRIX: Final[dict[str, object]] = json.loads(
    _MATRIX_PATH.read_text(encoding="utf-8")
)


def device_feature_matrix_snapshot() -> dict[str, object]:
    """Return an isolated copy of the packaged feature matrix."""

    return deepcopy(_DEVICE_FEATURE_MATRIX)
