"""Stable public room identifiers derived from Home Assistant area ids."""

from __future__ import annotations

import hashlib
import re


_STABLE_ROOM_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def stable_area_room_id(area_id: str, used: set[str]) -> str:
    """Keep normal HA area ids and derive a bounded stable fallback otherwise."""

    if _STABLE_ROOM_ID.fullmatch(area_id) and area_id not in used:
        return area_id
    attempt = 0
    while True:
        material = area_id if attempt == 0 else f"{area_id}:{attempt}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]
        candidate = f"ha_{digest}"
        if candidate not in used:
            return candidate
        attempt += 1
