#!/usr/bin/env python3
"""Replace legacy Node-RED tabs with managed Hausman and essential bridges."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


SERVICE_TAB_ID = "f6b4e1a27d903c58"
SERVICE_TAB_LABEL = "Hausman: Аварийные уведомления и OAuth"

ESSENTIAL_NODE_IDS = frozenset(
    {
        # Android OAuth client page. Node ids must remain stable.
        "in84fbc62167b1029d",
        "tpl218670437199818a",
        "respc61da39d3d5faef4",
        # Toilet leak -> Telegram.
        "8a0ab956a846b704",
        "19885a8b13b212aa",
        "20574fa1f813bbd1",
        # Kitchen leak -> Telegram.
        "da6cceeaf13d7968",
        "41e8631cb0d4ccf8",
        "8f934d0a3bfa221f",
        # Three shower leak sensors -> one deduplicating Telegram sender.
        "812c46d30b2a7ce9",
        "f1e2d3c4b5a69781",
        "f1e2d3c4b5a69782",
        "8f9ba0eb7e6cae9e",
        "5caf89303256c212",
        # Bathroom leak -> Telegram.
        "7caab8958f0d19c9",
        "daeb6b9a85872f0a",
        "e11a4c71329189f2",
    }
)

NODE_NAMES = {
    "in84fbc62167b1029d": "OAuth планшета: страница client_id",
    "tpl218670437199818a": "OAuth планшета: безопасная страница",
    "respc61da39d3d5faef4": "OAuth планшета: ответ",
    "8a0ab956a846b704": "Протечка туалет: датчик",
    "19885a8b13b212aa": "Протечка туалет: сообщение",
    "20574fa1f813bbd1": "Протечка туалет: Telegram",
    "da6cceeaf13d7968": "Протечка кухня: датчик",
    "41e8631cb0d4ccf8": "Протечка кухня: сообщение",
    "8f934d0a3bfa221f": "Протечка кухня: Telegram",
    "812c46d30b2a7ce9": "Протечка душевая под раковиной: датчик",
    "f1e2d3c4b5a69781": "Протечка душевая под бойлером: датчик",
    "f1e2d3c4b5a69782": "Протечка душевая у люка: датчик",
    "8f9ba0eb7e6cae9e": "Протечка душевая: единое сообщение",
    "5caf89303256c212": "Протечка душевая: Telegram",
    "7caab8958f0d19c9": "Протечка ванная: датчик",
    "daeb6b9a85872f0a": "Протечка ванная: сообщение",
    "e11a4c71329189f2": "Протечка ванная: Telegram",
}


def _is_managed_tab(node: dict[str, object]) -> bool:
    return node.get("type") == "tab" and str(node.get("label", "")).startswith(
        "Hausman:"
    )


def compact(flows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return a fail-closed compact flow set."""

    by_id = {str(node.get("id")): node for node in flows if node.get("id")}
    missing = sorted(ESSENTIAL_NODE_IDS - by_id.keys())
    if missing:
        raise ValueError(f"essential Node-RED nodes are missing: {missing}")

    managed_tabs = [node for node in flows if _is_managed_tab(node)]
    if not managed_tabs:
        raise ValueError("no managed Hausman tabs found")
    managed_tab_ids = {str(node["id"]) for node in managed_tabs}

    service_tab: dict[str, object] = {
        "id": SERVICE_TAB_ID,
        "type": "tab",
        "label": SERVICE_TAB_LABEL,
        "disabled": False,
        "info": (
            "Только действующие служебные подключения: OAuth планшета и "
            "Telegram-оповещения протечек. Физических команд устройствам нет."
        ),
    }

    result: list[dict[str, object]] = [*managed_tabs, service_tab]
    kept_ids = managed_tab_ids | ESSENTIAL_NODE_IDS | {SERVICE_TAB_ID}

    for node in flows:
        node_id = str(node.get("id", ""))
        zone = str(node.get("z", ""))
        if node_id in ESSENTIAL_NODE_IDS:
            moved = dict(node)
            moved["z"] = SERVICE_TAB_ID
            moved["name"] = NODE_NAMES[node_id]
            moved.pop("d", None)
            result.append(moved)
        elif zone in managed_tab_ids:
            result.append(node)
        elif node.get("type") != "tab" and not zone:
            # Keep shared HA, Zigbee2MQTT and Telegram configuration nodes.
            result.append(node)

    ids = {str(node.get("id")) for node in result if node.get("id")}
    if len(ids) != len(result):
        raise ValueError("duplicate or missing node id after compaction")
    if not kept_ids.issubset(ids):
        raise ValueError("compaction lost a required node")

    for node in result:
        for output in node.get("wires", []) if isinstance(node, dict) else []:
            if not isinstance(output, list):
                raise ValueError(f"invalid wires on node {node.get('id')}")
            dangling = [target for target in output if str(target) not in ids]
            if dangling:
                raise ValueError(
                    f"node {node.get('id')} has dangling wires: {dangling}"
                )

    tabs = [node for node in result if node.get("type") == "tab"]
    if any(not str(node.get("label", "")).startswith("Hausman:") for node in tabs):
        raise ValueError("legacy tab survived compaction")
    if any(
        node.get("type") in {"api-call-service", "zigbee2mqtt-out"}
        for node in result
        if str(node.get("z", "")) == SERVICE_TAB_ID
    ):
        raise ValueError("service tab contains a physical command writer")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = json.loads(args.input.read_text())
    if not isinstance(source, list) or not all(isinstance(node, dict) for node in source):
        raise SystemExit("flows.json must contain an array of objects")
    target = compact(source)
    encoded = json.dumps(target, ensure_ascii=False, separators=(",", ":")) + "\n"
    args.output.write_text(encoded)
    os.chmod(args.output, args.input.stat().st_mode)
    print(
        json.dumps(
            {
                "beforeNodes": len(source),
                "afterNodes": len(target),
                "tabs": [node["label"] for node in target if node.get("type") == "tab"],
                "telegramSenders": sum(
                    node.get("type") == "telegram sender" for node in target
                ),
                "physicalWritersOnServiceTab": sum(
                    node.get("type") in {"api-call-service", "zigbee2mqtt-out"}
                    and node.get("z") == SERVICE_TAB_ID
                    for node in target
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
