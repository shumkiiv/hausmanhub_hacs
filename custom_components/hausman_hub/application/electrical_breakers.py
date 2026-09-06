"""Server-owned electrical-breaker identity and catalog safety policy."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace

from .scenarios import ScenarioCatalog, ScenarioDeviceEntry


_BREAKER_IDENTITY = re.compile(
    r"(?:^|[^\w])(?:"
    r"rcbo|mcb|circuit[\s._/:-]*breaker|breaker|"
    r"диф{1,2}\.?[\s._/:-]*автомат(?:а|у|ом|е|ы|ов|ам|ами|ах)?|"
    r"дифференциальн(?:ый|ая|ое|ые|ого|ой|ому|ым|ом|ых|ыми)"
    r"[\s._/:-]+автомат(?:а|у|ом|е|ы|ов|ам|ами|ах)?|"
    r"автоматическ(?:ий|ого|ому|им|ом|ие|их|ими)"
    r"[\s._/:-]+выключател(?:ь|я|ю|ем|е|и|ей|ям|ями|ях)|"
    r"автомат(?:а|у|ом|е|ы|ов|ам|ами|ах)?"
    r")(?:$|[^\w])",
    re.IGNORECASE,
)
_BREAKER_SEPARATORS = re.compile(r"[_./:\-]+")


def configured_source_device_ids(source_bindings: object) -> tuple[str, ...]:
    """Flatten all meter bindings, including additional meters, safely."""

    if not isinstance(source_bindings, Mapping):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for source_ids in source_bindings.values():
        if not isinstance(source_ids, (list, tuple, set, frozenset)):
            continue
        for source_id in source_ids:
            if isinstance(source_id, str) and source_id not in seen:
                seen.add(source_id)
                result.append(source_id)
    return tuple(result)


def is_electrical_breaker_identity(*values: object) -> bool:
    """Match bounded real-world breaker names without substring promotion."""

    # HA entity identifiers conventionally use underscores (for example,
    # ``switch.kitchen_rcbo``).  ``\w`` treats underscore as part of a word,
    # so normalize identifier separators before applying the bounded matcher.
    identity = _BREAKER_SEPARATORS.sub(
        " ", " ".join(str(value or "") for value in values)
    )
    return _BREAKER_IDENTITY.search(identity) is not None


def is_configured_electrical_breaker(
    device: ScenarioDeviceEntry | None,
    configured_physical_ids: Iterable[str],
) -> bool:
    """Classify one switch from server catalog and configured meter identity."""

    if device is None or not str(device.entity_id).startswith("switch."):
        return False
    configured = {
        value for value in configured_physical_ids if isinstance(value, str)
    }
    if not device.physical_id or device.physical_id not in configured:
        return False
    return is_electrical_breaker_identity(
        device.physical_name,
        device.name,
        device.entity_id,
        device.device_type,
    )


def apply_electrical_breaker_catalog_policy(
    catalog: ScenarioCatalog,
    configured_physical_ids: Iterable[str],
) -> ScenarioCatalog:
    """Remove state-relative toggle from every server-classified breaker."""

    configured = tuple(configured_physical_ids)
    if not configured:
        return catalog
    devices = {
        target_id: (
            replace(
                device,
                actions=tuple(
                    action for action in device.actions if action.action_id != "toggle"
                ),
                device_type="electrical_breaker",
            )
            if is_configured_electrical_breaker(device, configured)
            else device
        )
        for target_id, device in catalog.devices.items()
    }
    if all(
        devices[target_id] is device
        for target_id, device in catalog.devices.items()
    ):
        return catalog
    return replace(catalog, devices=devices)
