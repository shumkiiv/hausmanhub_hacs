"""Shared, bounded presentation metadata for Home Assistant devices."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote


ZIGBEE2MQTT_IMAGE_BASE = "https://www.zigbee2mqtt.io/images/devices/"
MAX_DEVICE_MODEL_ID_LENGTH = 160


def zigbee2mqtt_image_url(
    model_id: object,
    identifiers: Iterable[object],
) -> str | None:
    """Return the official Zigbee2MQTT image before any generated fallback.

    Only an HA device explicitly identified as Zigbee2MQTT is eligible. Unknown
    and non-Zigbee devices deliberately return ``None`` so clients can render a
    neutral device placeholder instead of a misleading product image.
    """

    if not isinstance(model_id, str):
        return None
    normalized_model_id = " ".join(model_id.split())[:MAX_DEVICE_MODEL_ID_LENGTH]
    if not normalized_model_id:
        return None
    is_zigbee2mqtt = any(
        isinstance(identifier, (tuple, list))
        and len(identifier) == 2
        and identifier[0] == "mqtt"
        and isinstance(identifier[1], str)
        and identifier[1].startswith("zigbee2mqtt_")
        for identifier in identifiers
    )
    if not is_zigbee2mqtt:
        return None
    return f"{ZIGBEE2MQTT_IMAGE_BASE}{quote(normalized_model_id, safe='')}.png"
